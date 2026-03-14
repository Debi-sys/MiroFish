"""
OASIS TwitterSimulation预设脚本
此脚本读取ConfigurationFile中的Parameters来ExecuteSimulation，实现全程自动化

功能特性:
- CompleteSimulation后不立即Close环境，进入等待命令模式
- 支持通过IPCReceiveInterview命令
- 支持单个Agent采访和批量采访
- 支持远程Close环境命令

使用方式:
    python run_twitter_simulation.py --config /path/to/simulation_config.json
    python run_twitter_simulation.py --config /path/to/simulation_config.json --no-wait  # Complete后立即Close
"""

import argparse
import asyncio
import json
import logging
import os
import random
import signal
import sys
import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional

# 全局Variable：用于信号Process
_shutdown_event = None
_cleanup_done = False

# 添加ProjectPath
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
_backend_dir = os.path.abspath(os.path.join(_scripts_dir, '..'))
_project_root = os.path.abspath(os.path.join(_backend_dir, '..'))
sys.path.insert(0, _scripts_dir)
sys.path.insert(0, _backend_dir)

# LoadProject根Directory的 .env File（包含 LLM_API_KEY 等Configuration）
from dotenv import load_dotenv
_env_file = os.path.join(_project_root, '.env')
if os.path.exists(_env_file):
    load_dotenv(_env_file)
else:
    _backend_env = os.path.join(_backend_dir, '.env')
    if os.path.exists(_backend_env):
        load_dotenv(_backend_env)


import re


class UnicodeFormatter(logging.Formatter):
    """自定义Format化器，将 Unicode 转义序列转换为可读字符"""
    
    UNICODE_ESCAPE_PATTERN = re.compile(r'\\u([0-9a-fA-F]{4})')
    
    def format(self, record):
        result = super().format(record)
        
        def replace_unicode(match):
            try:
                return chr(int(match.group(1), 16))
            except (ValueError, OverflowError):
                return match.group(0)
        
        return self.UNICODE_ESCAPE_PATTERN.sub(replace_unicode, result)


class MaxTokensWarningFilter(logging.Filter):
    """过滤掉 camel-ai 关于 max_tokens 的警告（我们故意不Settings max_tokens，让模型自行决定）"""
    
    def filter(self, record):
        # 过滤掉包含 max_tokens 警告的Log
        if "max_tokens" in record.getMessage() and "Invalid or missing" in record.getMessage():
            return False
        return True


# 在模块Load时立即添加过滤器，确保在 camel 代码Execute前生效
logging.getLogger().addFilter(MaxTokensWarningFilter())


def setup_oasis_logging(log_dir: str):
    """Configuration OASIS 的Log，使用固定Name的LogFile"""
    os.makedirs(log_dir, exist_ok=True)
    
    # Clean up旧的LogFile
    for f in os.listdir(log_dir):
        old_log = os.path.join(log_dir, f)
        if os.path.isfile(old_log) and f.endswith('.log'):
            try:
                os.remove(old_log)
            except OSError:
                pass
    
    formatter = UnicodeFormatter("%(levelname)s - %(asctime)s - %(name)s - %(message)s")
    
    loggers_config = {
        "social.agent": os.path.join(log_dir, "social.agent.log"),
        "social.twitter": os.path.join(log_dir, "social.twitter.log"),
        "social.rec": os.path.join(log_dir, "social.rec.log"),
        "oasis.env": os.path.join(log_dir, "oasis.env.log"),
        "table": os.path.join(log_dir, "table.log"),
    }
    
    for logger_name, log_file in loggers_config.items():
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        logger.handlers.clear()
        file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='w')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.propagate = False


try:
    from camel.models import ModelFactory
    from camel.types import ModelPlatformType
    import oasis
    from oasis import (
        ActionType,
        LLMAction,
        ManualAction,
        generate_twitter_agent_graph
    )
except ImportError as e:
    print(f"Error: 缺少依赖 {e}")
    print("请先安装: pip install oasis-ai camel-ai")
    sys.exit(1)


# IPC相关Constant
IPC_COMMANDS_DIR = "ipc_commands"
IPC_RESPONSES_DIR = "ipc_responses"
ENV_STATUS_FILE = "env_status.json"

class CommandType:
    """命令TypeConstant"""
    INTERVIEW = "interview"
    BATCH_INTERVIEW = "batch_interview"
    CLOSE_ENV = "close_env"


class IPCHandler:
    """IPC命令Process器"""
    
    def __init__(self, simulation_dir: str, env, agent_graph):
        self.simulation_dir = simulation_dir
        self.env = env
        self.agent_graph = agent_graph
        self.commands_dir = os.path.join(simulation_dir, IPC_COMMANDS_DIR)
        self.responses_dir = os.path.join(simulation_dir, IPC_RESPONSES_DIR)
        self.status_file = os.path.join(simulation_dir, ENV_STATUS_FILE)
        self._running = True
        
        # 确保Directory存在
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
    
    def update_status(self, status: str):
        """Update环境Status"""
        with open(self.status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def poll_command(self) -> Optional[Dict[str, Any]]:
        """轮询Get待Process命令"""
        if not os.path.exists(self.commands_dir):
            return None
        
        # Get命令File（按时间排序）
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.commands_dir, filename)
                command_files.append((filepath, os.path.getmtime(filepath)))
        
        command_files.sort(key=lambda x: x[1])
        
        for filepath, _ in command_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
        
        return None
    
    def send_response(self, command_id: str, status: str, result: Dict = None, error: str = None):
        """SendResponse"""
        response = {
            "command_id": command_id,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": datetime.now().isoformat()
        }
        
        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response, f, ensure_ascii=False, indent=2)
        
        # Delete命令File
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        try:
            os.remove(command_file)
        except OSError:
            pass
    
    async def handle_interview(self, command_id: str, agent_id: int, prompt: str) -> bool:
        """
        Process单个Agent采访命令
        
        Returns:
            True 表示Success，False 表示Failed
        """
        try:
            # GetAgent
            agent = self.agent_graph.get_agent(agent_id)
            
            # CreateInterview动作
            interview_action = ManualAction(
                action_type=ActionType.INTERVIEW,
                action_args={"prompt": prompt}
            )
            
            # ExecuteInterview
            actions = {agent: interview_action}
            await self.env.step(actions)
            
            # 从Data库GetResult
            result = self._get_interview_result(agent_id)
            
            self.send_response(command_id, "completed", result=result)
            print(f"  InterviewComplete: agent_id={agent_id}")
            return True
            
        except Exception as e:
            error_msg = str(e)
            print(f"  InterviewFailed: agent_id={agent_id}, error={error_msg}")
            self.send_response(command_id, "failed", error=error_msg)
            return False
    
    async def handle_batch_interview(self, command_id: str, interviews: List[Dict]) -> bool:
        """
        Process批量采访命令
        
        Args:
            interviews: [{"agent_id": int, "prompt": str}, ...]
        """
        try:
            # 构建动作字典
            actions = {}
            agent_prompts = {}  # Record每个agent的prompt
            
            for interview in interviews:
                agent_id = interview.get("agent_id")
                prompt = interview.get("prompt", "")
                
                try:
                    agent = self.agent_graph.get_agent(agent_id)
                    actions[agent] = ManualAction(
                        action_type=ActionType.INTERVIEW,
                        action_args={"prompt": prompt}
                    )
                    agent_prompts[agent_id] = prompt
                except Exception as e:
                    print(f"  警告: 无法GetAgent {agent_id}: {e}")
            
            if not actions:
                self.send_response(command_id, "failed", error="没有有效的Agent")
                return False
            
            # Execute批量Interview
            await self.env.step(actions)
            
            # Get所有Result
            results = {}
            for agent_id in agent_prompts.keys():
                result = self._get_interview_result(agent_id)
                results[agent_id] = result
            
            self.send_response(command_id, "completed", result={
                "interviews_count": len(results),
                "results": results
            })
            print(f"  批量InterviewComplete: {len(results)} 个Agent")
            return True
            
        except Exception as e:
            error_msg = str(e)
            print(f"  批量InterviewFailed: {error_msg}")
            self.send_response(command_id, "failed", error=error_msg)
            return False
    
    def _get_interview_result(self, agent_id: int) -> Dict[str, Any]:
        """从Data库Get最新的InterviewResult"""
        db_path = os.path.join(self.simulation_dir, "twitter_simulation.db")
        
        result = {
            "agent_id": agent_id,
            "response": None,
            "timestamp": None
        }
        
        if not os.path.exists(db_path):
            return result
        
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Query最新的InterviewRecord
            cursor.execute("""
                SELECT user_id, info, created_at
                FROM trace
                WHERE action = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (ActionType.INTERVIEW.value, agent_id))
            
            row = cursor.fetchone()
            if row:
                user_id, info_json, created_at = row
                try:
                    info = json.loads(info_json) if info_json else {}
                    result["response"] = info.get("response", info)
                    result["timestamp"] = created_at
                except json.JSONDecodeError:
                    result["response"] = info_json
            
            conn.close()
            
        except Exception as e:
            print(f"  读取InterviewResultFailed: {e}")
        
        return result
    
    async def process_commands(self) -> bool:
        """
        Process所有待Process命令
        
        Returns:
            True 表示继续Run，False 表示应该退出
        """
        command = self.poll_command()
        if not command:
            return True
        
        command_id = command.get("command_id")
        command_type = command.get("command_type")
        args = command.get("args", {})
        
        print(f"\n收到IPC命令: {command_type}, id={command_id}")
        
        if command_type == CommandType.INTERVIEW:
            await self.handle_interview(
                command_id,
                args.get("agent_id", 0),
                args.get("prompt", "")
            )
            return True
            
        elif command_type == CommandType.BATCH_INTERVIEW:
            await self.handle_batch_interview(
                command_id,
                args.get("interviews", [])
            )
            return True
            
        elif command_type == CommandType.CLOSE_ENV:
            print("收到Close环境命令")
            self.send_response(command_id, "completed", result={"message": "环境即将Close"})
            return False
        
        else:
            self.send_response(command_id, "failed", error=f"未知命令Type: {command_type}")
            return True


class TwitterSimulationRunner:
    """TwitterSimulation runner"""
    
    # Twitter可用动作（不包含INTERVIEW，INTERVIEW只能通过ManualAction手动触发）
    AVAILABLE_ACTIONS = [
        ActionType.CREATE_POST,
        ActionType.LIKE_POST,
        ActionType.REPOST,
        ActionType.FOLLOW,
        ActionType.DO_NOTHING,
        ActionType.QUOTE_POST,
    ]
    
    def __init__(self, config_path: str, wait_for_commands: bool = True):
        """
        InitializeSimulation runner
        
        Args:
            config_path: ConfigurationFilePath (simulation_config.json)
            wait_for_commands: SimulationComplete后Whether等待命令（DefaultTrue）
        """
        self.config_path = config_path
        self.config = self._load_config()
        self.simulation_dir = os.path.dirname(config_path)
        self.wait_for_commands = wait_for_commands
        self.env = None
        self.agent_graph = None
        self.ipc_handler = None
        
    def _load_config(self) -> Dict[str, Any]:
        """LoadConfigurationFile"""
        with open(self.config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    def _get_profile_path(self) -> str:
        """GetProfileFilePath（OASIS Twitter使用CSVFormat）"""
        return os.path.join(self.simulation_dir, "twitter_profiles.csv")
    
    def _get_db_path(self) -> str:
        """GetData库Path"""
        return os.path.join(self.simulation_dir, "twitter_simulation.db")
    
    def _create_model(self):
        """
        CreateLLM模型
        
        统一使用Project根Directory .env File中的Configuration（优先级最高）：
        - LLM_API_KEY: API密钥
        - LLM_BASE_URL: API基础URL
        - LLM_MODEL_NAME: 模型Name
        """
        # 优先从 .env 读取Configuration
        llm_api_key = os.environ.get("LLM_API_KEY", "")
        llm_base_url = os.environ.get("LLM_BASE_URL", "")
        llm_model = os.environ.get("LLM_MODEL_NAME", "")
        
        # If .env 中没有，则使用 config 作为备用
        if not llm_model:
            llm_model = self.config.get("llm_model", "gpt-4o-mini")
        
        # Settings camel-ai 所需的环境Variable
        if llm_api_key:
            os.environ["OPENAI_API_KEY"] = llm_api_key
        
        if not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("缺少 API Key Configuration，请在Project根Directory .env File中Settings LLM_API_KEY")
        
        if llm_base_url:
            os.environ["OPENAI_API_BASE_URL"] = llm_base_url
        
        print(f"LLMConfiguration: model={llm_model}, base_url={llm_base_url[:40] if llm_base_url else 'Default'}...")
        
        return ModelFactory.create(
            model_platform=ModelPlatformType.OPENAI,
            model_type=llm_model,
        )
    
    def _get_active_agents_for_round(
        self, 
        env, 
        current_hour: int,
        round_num: int
    ) -> List:
        """
        根据时间和Configuration决定本轮激活哪些Agent
        
        Args:
            env: OASIS环境
            current_hour: 当前Simulation小时（0-23）
            round_num: 当前轮数
            
        Returns:
            激活的AgentList
        """
        time_config = self.config.get("time_config", {})
        agent_configs = self.config.get("agent_configs", [])
        
        # 基础激活数量
        base_min = time_config.get("agents_per_hour_min", 5)
        base_max = time_config.get("agents_per_hour_max", 20)
        
        # 根据时段调整
        peak_hours = time_config.get("peak_hours", [9, 10, 11, 14, 15, 20, 21, 22])
        off_peak_hours = time_config.get("off_peak_hours", [0, 1, 2, 3, 4, 5])
        
        if current_hour in peak_hours:
            multiplier = time_config.get("peak_activity_multiplier", 1.5)
        elif current_hour in off_peak_hours:
            multiplier = time_config.get("off_peak_activity_multiplier", 0.3)
        else:
            multiplier = 1.0
        
        target_count = int(random.uniform(base_min, base_max) * multiplier)
        
        # 根据每个Agent的Configuration计算激活概率
        candidates = []
        for cfg in agent_configs:
            agent_id = cfg.get("agent_id", 0)
            active_hours = cfg.get("active_hours", list(range(8, 23)))
            activity_level = cfg.get("activity_level", 0.5)
            
            # 检查Whether在活跃时间
            if current_hour not in active_hours:
                continue
            
            # 根据活跃度计算概率
            if random.random() < activity_level:
                candidates.append(agent_id)
        
        # 随机选择
        selected_ids = random.sample(
            candidates, 
            min(target_count, len(candidates))
        ) if candidates else []
        
        # 转换为Agent对象
        active_agents = []
        for agent_id in selected_ids:
            try:
                agent = env.agent_graph.get_agent(agent_id)
                active_agents.append((agent_id, agent))
            except Exception:
                pass
        
        return active_agents
    
    async def run(self, max_rounds: int = None):
        """RunTwitterSimulation
        
        Args:
            max_rounds: 最大Simulation轮数（Optional，用于截断过长的Simulation）
        """
        print("=" * 60)
        print("OASIS TwitterSimulation")
        print(f"ConfigurationFile: {self.config_path}")
        print(f"SimulationID: {self.config.get('simulation_id', 'unknown')}")
        print(f"等待命令模式: {'启用' if self.wait_for_commands else '禁用'}")
        print("=" * 60)
        
        # Load时间Configuration
        time_config = self.config.get("time_config", {})
        total_hours = time_config.get("total_simulation_hours", 72)
        minutes_per_round = time_config.get("minutes_per_round", 30)
        
        # 计算总轮数
        total_rounds = (total_hours * 60) // minutes_per_round
        
        # If指定了最大轮数，则截断
        if max_rounds is not None and max_rounds > 0:
            original_rounds = total_rounds
            total_rounds = min(total_rounds, max_rounds)
            if total_rounds < original_rounds:
                print(f"\n轮数已截断: {original_rounds} -> {total_rounds} (max_rounds={max_rounds})")
        
        print(f"\nSimulationParameters:")
        print(f"  - 总Simulation时长: {total_hours}小时")
        print(f"  - 每轮时间: {minutes_per_round}分钟")
        print(f"  - 总轮数: {total_rounds}")
        if max_rounds:
            print(f"  - 最大轮数限制: {max_rounds}")
        print(f"  - Agent数量: {len(self.config.get('agent_configs', []))}")
        
        # Create模型
        print("\nInitializeLLM模型...")
        model = self._create_model()
        
        # LoadAgent图
        print("LoadAgent Profile...")
        profile_path = self._get_profile_path()
        if not os.path.exists(profile_path):
            print(f"Error: ProfileFile不存在: {profile_path}")
            return
        
        self.agent_graph = await generate_twitter_agent_graph(
            profile_path=profile_path,
            model=model,
            available_actions=self.AVAILABLE_ACTIONS,
        )
        
        # Data库Path
        db_path = self._get_db_path()
        if os.path.exists(db_path):
            os.remove(db_path)
            print(f"已Delete旧Data库: {db_path}")
        
        # Create环境
        print("CreateOASIS环境...")
        self.env = oasis.make(
            agent_graph=self.agent_graph,
            platform=oasis.DefaultPlatformType.TWITTER,
            database_path=db_path,
            semaphore=30,  # 限制最大并发 LLM Request数，防止 API 过载
        )
        
        await self.env.reset()
        print("环境InitializeComplete\n")
        
        # InitializeIPCProcess器
        self.ipc_handler = IPCHandler(self.simulation_dir, self.env, self.agent_graph)
        self.ipc_handler.update_status("running")
        
        # Execute初始事件
        event_config = self.config.get("event_config", {})
        initial_posts = event_config.get("initial_posts", [])
        
        if initial_posts:
            print(f"Execute初始事件 ({len(initial_posts)}条初始帖子)...")
            initial_actions = {}
            for post in initial_posts:
                agent_id = post.get("poster_agent_id", 0)
                content = post.get("content", "")
                try:
                    agent = self.env.agent_graph.get_agent(agent_id)
                    initial_actions[agent] = ManualAction(
                        action_type=ActionType.CREATE_POST,
                        action_args={"content": content}
                    )
                except Exception as e:
                    print(f"  警告: 无法为Agent {agent_id}Create初始帖子: {e}")
            
            if initial_actions:
                await self.env.step(initial_actions)
                print(f"  已发布 {len(initial_actions)} 条初始帖子")
        
        # 主Simulation循环
        print("\nStartSimulation循环...")
        start_time = datetime.now()
        
        for round_num in range(total_rounds):
            # 计算当前Simulation时间
            simulated_minutes = round_num * minutes_per_round
            simulated_hour = (simulated_minutes // 60) % 24
            simulated_day = simulated_minutes // (60 * 24) + 1
            
            # Get本轮激活的Agent
            active_agents = self._get_active_agents_for_round(
                self.env, simulated_hour, round_num
            )
            
            if not active_agents:
                continue
            
            # 构建动作
            actions = {
                agent: LLMAction()
                for _, agent in active_agents
            }
            
            # Execute动作
            await self.env.step(actions)
            
            # 打印进度
            if (round_num + 1) % 10 == 0 or round_num == 0:
                elapsed = (datetime.now() - start_time).total_seconds()
                progress = (round_num + 1) / total_rounds * 100
                print(f"  [Day {simulated_day}, {simulated_hour:02d}:00] "
                      f"Round {round_num + 1}/{total_rounds} ({progress:.1f}%) "
                      f"- {len(active_agents)} agents active "
                      f"- elapsed: {elapsed:.1f}s")
        
        total_elapsed = (datetime.now() - start_time).total_seconds()
        print(f"\nSimulation循环Complete!")
        print(f"  - 总耗时: {total_elapsed:.1f}秒")
        print(f"  - Data库: {db_path}")
        
        # Whether进入等待命令模式
        if self.wait_for_commands:
            print("\n" + "=" * 60)
            print("进入等待命令模式 - 环境保持Run")
            print("支持的命令: interview, batch_interview, close_env")
            print("=" * 60)
            
            self.ipc_handler.update_status("alive")
            
            # 等待命令循环（使用全局 _shutdown_event）
            try:
                while not _shutdown_event.is_set():
                    should_continue = await self.ipc_handler.process_commands()
                    if not should_continue:
                        break
                    try:
                        await asyncio.wait_for(_shutdown_event.wait(), timeout=0.5)
                        break  # 收到退出信号
                    except asyncio.TimeoutError:
                        pass
            except KeyboardInterrupt:
                print("\n收到中断信号")
            except asyncio.CancelledError:
                print("\nTask被取消")
            except Exception as e:
                print(f"\n命令Process出错: {e}")
            
            print("\nClose环境...")
        
        # Close环境
        self.ipc_handler.update_status("stopped")
        await self.env.close()
        
        print("环境已Close")
        print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description='OASIS TwitterSimulation')
    parser.add_argument(
        '--config', 
        type=str, 
        required=True,
        help='ConfigurationFilePath (simulation_config.json)'
    )
    parser.add_argument(
        '--max-rounds',
        type=int,
        default=None,
        help='最大Simulation轮数（Optional，用于截断过长的Simulation）'
    )
    parser.add_argument(
        '--no-wait',
        action='store_true',
        default=False,
        help='SimulationComplete后立即Close环境，不进入等待命令模式'
    )
    
    args = parser.parse_args()
    
    # 在 main FunctionStart时Create shutdown 事件
    global _shutdown_event
    _shutdown_event = asyncio.Event()
    
    if not os.path.exists(args.config):
        print(f"Error: ConfigurationFile不存在: {args.config}")
        sys.exit(1)
    
    # InitializeLogger configuration（使用固定File名，Clean up旧Log）
    simulation_dir = os.path.dirname(args.config) or "."
    setup_oasis_logging(os.path.join(simulation_dir, "log"))
    
    runner = TwitterSimulationRunner(
        config_path=args.config,
        wait_for_commands=not args.no_wait
    )
    await runner.run(max_rounds=args.max_rounds)


def setup_signal_handlers():
    """
    Settings信号Process器，确保收到 SIGTERM/SIGINT 时能够正确退出
    让程序有机会正常Clean up资源（CloseData库、环境等）
    """
    def signal_handler(signum, frame):
        global _cleanup_done
        sig_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        print(f"\n收到 {sig_name} 信号，Currently退出...")
        if not _cleanup_done:
            _cleanup_done = True
            if _shutdown_event:
                _shutdown_event.set()
        else:
            # 重复收到信号才强制退出
            print("强制退出...")
            sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)


if __name__ == "__main__":
    setup_signal_handlers()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n程序被中断")
    except SystemExit:
        pass
    finally:
        print("Simulation进程已退出")

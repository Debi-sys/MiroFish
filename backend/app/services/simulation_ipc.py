"""
SimulationIPC通信模块
用于Flask后端和Simulation脚本之间的Inter-process communication

通过File系统实现简单的命令/Response模式：
1. Flask写入命令到 commands/ Directory
2. Simulation脚本轮询命令Directory，Execute命令并写入Response到 responses/ Directory
3. Flask轮询ResponseDirectoryGetResult
"""

import os
import json
import time
import uuid
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..utils.logger import get_logger

logger = get_logger('mirofish.simulation_ipc')


class CommandType(str, Enum):
    """命令Type"""
    INTERVIEW = "interview"           # 单个Agent采访
    BATCH_INTERVIEW = "batch_interview"  # 批量采访
    CLOSE_ENV = "close_env"           # Close环境


class CommandStatus(str, Enum):
    """命令Status"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class IPCCommand:
    """IPC命令"""
    command_id: str
    command_type: CommandType
    args: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "command_type": self.command_type.value,
            "args": self.args,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPCCommand':
        return cls(
            command_id=data["command_id"],
            command_type=CommandType(data["command_type"]),
            args=data.get("args", {}),
            timestamp=data.get("timestamp", datetime.now().isoformat())
        )


@dataclass
class IPCResponse:
    """IPCResponse"""
    command_id: str
    status: CommandStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "command_id": self.command_id,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "timestamp": self.timestamp
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'IPCResponse':
        return cls(
            command_id=data["command_id"],
            status=CommandStatus(data["status"]),
            result=data.get("result"),
            error=data.get("error"),
            timestamp=data.get("timestamp", datetime.now().isoformat())
        )


class SimulationIPCClient:
    """
    SimulationIPC客户端（Flask端使用）
    
    用于向Simulation进程Send命令并等待Response
    """
    
    def __init__(self, simulation_dir: str):
        """
        InitializeIPC客户端
        
        Args:
            simulation_dir: SimulationDataDirectory
        """
        self.simulation_dir = simulation_dir
        self.commands_dir = os.path.join(simulation_dir, "ipc_commands")
        self.responses_dir = os.path.join(simulation_dir, "ipc_responses")
        
        # 确保Directory存在
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
    
    def send_command(
        self,
        command_type: CommandType,
        args: Dict[str, Any],
        timeout: float = 60.0,
        poll_interval: float = 0.5
    ) -> IPCResponse:
        """
        Send命令并等待Response
        
        Args:
            command_type: 命令Type
            args: 命令Parameters
            timeout: Timeout时间（秒）
            poll_interval: 轮询间隔（秒）
            
        Returns:
            IPCResponse
            
        Raises:
            TimeoutError: 等待ResponseTimeout
        """
        command_id = str(uuid.uuid4())
        command = IPCCommand(
            command_id=command_id,
            command_type=command_type,
            args=args
        )
        
        # 写入命令File
        command_file = os.path.join(self.commands_dir, f"{command_id}.json")
        with open(command_file, 'w', encoding='utf-8') as f:
            json.dump(command.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"SendIPC命令: {command_type.value}, command_id={command_id}")
        
        # 等待Response
        response_file = os.path.join(self.responses_dir, f"{command_id}.json")
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if os.path.exists(response_file):
                try:
                    with open(response_file, 'r', encoding='utf-8') as f:
                        response_data = json.load(f)
                    response = IPCResponse.from_dict(response_data)
                    
                    # Clean up命令和ResponseFile
                    try:
                        os.remove(command_file)
                        os.remove(response_file)
                    except OSError:
                        pass
                    
                    logger.info(f"收到IPCResponse: command_id={command_id}, status={response.status.value}")
                    return response
                except (json.JSONDecodeError, KeyError) as e:
                    logger.warning(f"ParseResponseFailed: {e}")
            
            time.sleep(poll_interval)
        
        # Timeout
        logger.error(f"等待IPCResponseTimeout: command_id={command_id}")
        
        # Clean up命令File
        try:
            os.remove(command_file)
        except OSError:
            pass
        
        raise TimeoutError(f"等待命令ResponseTimeout ({timeout}秒)")
    
    def send_interview(
        self,
        agent_id: int,
        prompt: str,
        platform: str = None,
        timeout: float = 60.0
    ) -> IPCResponse:
        """
        Send单个Agent采访命令
        
        Args:
            agent_id: Agent ID
            prompt: 采访问题
            platform: 指定Platform（Optional）
                - "twitter": 只采访TwitterPlatform
                - "reddit": 只采访RedditPlatform  
                - None: 双PlatformSimulation时同时采访两个Platform，单PlatformSimulation时采访该Platform
            timeout: Timeout时间
            
        Returns:
            IPCResponse，resultField包含采访Result
        """
        args = {
            "agent_id": agent_id,
            "prompt": prompt
        }
        if platform:
            args["platform"] = platform
            
        return self.send_command(
            command_type=CommandType.INTERVIEW,
            args=args,
            timeout=timeout
        )
    
    def send_batch_interview(
        self,
        interviews: List[Dict[str, Any]],
        platform: str = None,
        timeout: float = 120.0
    ) -> IPCResponse:
        """
        Send批量采访命令
        
        Args:
            interviews: 采访List，每个元素包含 {"agent_id": int, "prompt": str, "platform": str(Optional)}
            platform: DefaultPlatform（Optional，会被每个采访项的platform覆盖）
                - "twitter": Default只采访TwitterPlatform
                - "reddit": Default只采访RedditPlatform
                - None: 双PlatformSimulation时每个Agent同时采访两个Platform
            timeout: Timeout时间
            
        Returns:
            IPCResponse，resultField包含所有采访Result
        """
        args = {"interviews": interviews}
        if platform:
            args["platform"] = platform
            
        return self.send_command(
            command_type=CommandType.BATCH_INTERVIEW,
            args=args,
            timeout=timeout
        )
    
    def send_close_env(self, timeout: float = 30.0) -> IPCResponse:
        """
        SendClose环境命令
        
        Args:
            timeout: Timeout时间
            
        Returns:
            IPCResponse
        """
        return self.send_command(
            command_type=CommandType.CLOSE_ENV,
            args={},
            timeout=timeout
        )
    
    def check_env_alive(self) -> bool:
        """
        检查Simulation环境Whether存活
        
        通过检查 env_status.json File来判断
        """
        status_file = os.path.join(self.simulation_dir, "env_status.json")
        if not os.path.exists(status_file):
            return False
        
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            return status.get("status") == "alive"
        except (json.JSONDecodeError, OSError):
            return False


class SimulationIPCServer:
    """
    SimulationIPCService器（Simulation脚本端使用）
    
    轮询命令Directory，Execute命令并ReturnResponse
    """
    
    def __init__(self, simulation_dir: str):
        """
        InitializeIPCService器
        
        Args:
            simulation_dir: SimulationDataDirectory
        """
        self.simulation_dir = simulation_dir
        self.commands_dir = os.path.join(simulation_dir, "ipc_commands")
        self.responses_dir = os.path.join(simulation_dir, "ipc_responses")
        
        # 确保Directory存在
        os.makedirs(self.commands_dir, exist_ok=True)
        os.makedirs(self.responses_dir, exist_ok=True)
        
        # 环境Status
        self._running = False
    
    def start(self):
        """标记Service器为RunStatus"""
        self._running = True
        self._update_env_status("alive")
    
    def stop(self):
        """标记Service器为StopStatus"""
        self._running = False
        self._update_env_status("stopped")
    
    def _update_env_status(self, status: str):
        """Update环境StatusFile"""
        status_file = os.path.join(self.simulation_dir, "env_status.json")
        with open(status_file, 'w', encoding='utf-8') as f:
            json.dump({
                "status": status,
                "timestamp": datetime.now().isoformat()
            }, f, ensure_ascii=False, indent=2)
    
    def poll_commands(self) -> Optional[IPCCommand]:
        """
        轮询命令Directory，Return第一个待Process的命令
        
        Returns:
            IPCCommand 或 None
        """
        if not os.path.exists(self.commands_dir):
            return None
        
        # 按时间排序Get命令File
        command_files = []
        for filename in os.listdir(self.commands_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.commands_dir, filename)
                command_files.append((filepath, os.path.getmtime(filepath)))
        
        command_files.sort(key=lambda x: x[1])
        
        for filepath, _ in command_files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                return IPCCommand.from_dict(data)
            except (json.JSONDecodeError, KeyError, OSError) as e:
                logger.warning(f"读取命令FileFailed: {filepath}, {e}")
                continue
        
        return None
    
    def send_response(self, response: IPCResponse):
        """
        SendResponse
        
        Args:
            response: IPCResponse
        """
        response_file = os.path.join(self.responses_dir, f"{response.command_id}.json")
        with open(response_file, 'w', encoding='utf-8') as f:
            json.dump(response.to_dict(), f, ensure_ascii=False, indent=2)
        
        # Delete命令File
        command_file = os.path.join(self.commands_dir, f"{response.command_id}.json")
        try:
            os.remove(command_file)
        except OSError:
            pass
    
    def send_success(self, command_id: str, result: Dict[str, Any]):
        """SendSuccessResponse"""
        self.send_response(IPCResponse(
            command_id=command_id,
            status=CommandStatus.COMPLETED,
            result=result
        ))
    
    def send_error(self, command_id: str, error: str):
        """SendErrorResponse"""
        self.send_response(IPCResponse(
            command_id=command_id,
            status=CommandStatus.FAILED,
            error=error
        ))

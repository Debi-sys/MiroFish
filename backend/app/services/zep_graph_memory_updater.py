"""
ZepGraph memory updateService
将Simulation中的Agent活动动态Update到ZepGraph中
"""

import os
import time
import threading
import json
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass
from datetime import datetime
from queue import Queue, Empty

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger

logger = get_logger('mirofish.zep_graph_memory_updater')


@dataclass
class AgentActivity:
    """Agent活动Record"""
    platform: str           # twitter / reddit
    agent_id: int
    agent_name: str
    action_type: str        # CREATE_POST, LIKE_POST, etc.
    action_args: Dict[str, Any]
    round_num: int
    timestamp: str
    
    def to_episode_text(self) -> str:
        """
        将活动转换为可以Send给Zep的文本Description
        
        采用自然语言DescriptionFormat，让Zep能够从中提取Entity和Relationship
        不添加Simulation相关的前缀，避免误导GraphUpdate
        """
        # 根据不同的动作TypeGenerate不同的Description
        action_descriptions = {
            "CREATE_POST": self._describe_create_post,
            "LIKE_POST": self._describe_like_post,
            "DISLIKE_POST": self._describe_dislike_post,
            "REPOST": self._describe_repost,
            "QUOTE_POST": self._describe_quote_post,
            "FOLLOW": self._describe_follow,
            "CREATE_COMMENT": self._describe_create_comment,
            "LIKE_COMMENT": self._describe_like_comment,
            "DISLIKE_COMMENT": self._describe_dislike_comment,
            "SEARCH_POSTS": self._describe_search,
            "SEARCH_USER": self._describe_search_user,
            "MUTE": self._describe_mute,
        }
        
        describe_func = action_descriptions.get(self.action_type, self._describe_generic)
        description = describe_func()
        
        # 直接Return "agentName: 活动Description" Format，不添加Simulation前缀
        return f"{self.agent_name}: {description}"
    
    def _describe_create_post(self) -> str:
        content = self.action_args.get("content", "")
        if content:
            return f"发布了一条帖子：「{content}」"
        return "发布了一条帖子"
    
    def _describe_like_post(self) -> str:
        """点赞帖子 - 包含帖子原文和作者Information"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"点赞了{post_author}的帖子：「{post_content}」"
        elif post_content:
            return f"点赞了一条帖子：「{post_content}」"
        elif post_author:
            return f"点赞了{post_author}的一条帖子"
        return "点赞了一条帖子"
    
    def _describe_dislike_post(self) -> str:
        """踩帖子 - 包含帖子原文和作者Information"""
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if post_content and post_author:
            return f"踩了{post_author}的帖子：「{post_content}」"
        elif post_content:
            return f"踩了一条帖子：「{post_content}」"
        elif post_author:
            return f"踩了{post_author}的一条帖子"
        return "踩了一条帖子"
    
    def _describe_repost(self) -> str:
        """转发帖子 - 包含原帖Content和作者Information"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        
        if original_content and original_author:
            return f"转发了{original_author}的帖子：「{original_content}」"
        elif original_content:
            return f"转发了一条帖子：「{original_content}」"
        elif original_author:
            return f"转发了{original_author}的一条帖子"
        return "转发了一条帖子"
    
    def _describe_quote_post(self) -> str:
        """引用帖子 - 包含原帖Content、作者Information和引用评论"""
        original_content = self.action_args.get("original_content", "")
        original_author = self.action_args.get("original_author_name", "")
        quote_content = self.action_args.get("quote_content", "") or self.action_args.get("content", "")
        
        base = ""
        if original_content and original_author:
            base = f"引用了{original_author}的帖子「{original_content}」"
        elif original_content:
            base = f"引用了一条帖子「{original_content}」"
        elif original_author:
            base = f"引用了{original_author}的一条帖子"
        else:
            base = "引用了一条帖子"
        
        if quote_content:
            base += f"，并评论道：「{quote_content}」"
        return base
    
    def _describe_follow(self) -> str:
        """关注User - 包含被关注User的Name"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"关注了User「{target_user_name}」"
        return "关注了一个User"
    
    def _describe_create_comment(self) -> str:
        """发表评论 - 包含评论Content和所评论的帖子Information"""
        content = self.action_args.get("content", "")
        post_content = self.action_args.get("post_content", "")
        post_author = self.action_args.get("post_author_name", "")
        
        if content:
            if post_content and post_author:
                return f"在{post_author}的帖子「{post_content}」下评论道：「{content}」"
            elif post_content:
                return f"在帖子「{post_content}」下评论道：「{content}」"
            elif post_author:
                return f"在{post_author}的帖子下评论道：「{content}」"
            return f"评论道：「{content}」"
        return "发表了评论"
    
    def _describe_like_comment(self) -> str:
        """点赞评论 - 包含评论Content和作者Information"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"点赞了{comment_author}的评论：「{comment_content}」"
        elif comment_content:
            return f"点赞了一条评论：「{comment_content}」"
        elif comment_author:
            return f"点赞了{comment_author}的一条评论"
        return "点赞了一条评论"
    
    def _describe_dislike_comment(self) -> str:
        """踩评论 - 包含评论Content和作者Information"""
        comment_content = self.action_args.get("comment_content", "")
        comment_author = self.action_args.get("comment_author_name", "")
        
        if comment_content and comment_author:
            return f"踩了{comment_author}的评论：「{comment_content}」"
        elif comment_content:
            return f"踩了一条评论：「{comment_content}」"
        elif comment_author:
            return f"踩了{comment_author}的一条评论"
        return "踩了一条评论"
    
    def _describe_search(self) -> str:
        """搜索帖子 - 包含搜索关键词"""
        query = self.action_args.get("query", "") or self.action_args.get("keyword", "")
        return f"搜索了「{query}」" if query else "进行了搜索"
    
    def _describe_search_user(self) -> str:
        """搜索User - 包含搜索关键词"""
        query = self.action_args.get("query", "") or self.action_args.get("username", "")
        return f"搜索了User「{query}」" if query else "搜索了User"
    
    def _describe_mute(self) -> str:
        """屏蔽User - 包含被屏蔽User的Name"""
        target_user_name = self.action_args.get("target_user_name", "")
        
        if target_user_name:
            return f"屏蔽了User「{target_user_name}」"
        return "屏蔽了一个User"
    
    def _describe_generic(self) -> str:
        # 对于未知的动作Type，Generate通用Description
        return f"Execute了{self.action_type}操作"


class ZepGraphMemoryUpdater:
    """
    ZepGraph memory updater
    
    监控Simulation的actionsLogFile，将新的agent活动实时Update到ZepGraph中。
    按Platform分组，每累积BATCH_SIZE条活动后批量Send到Zep。
    
    所有有意义的行为都会被Update到Zep，action_args中会包含完整的上下文Information：
    - 点赞/踩的帖子原文
    - 转发/引用的帖子原文
    - 关注/屏蔽的User名
    - 点赞/踩的评论原文
    """
    
    # 批量Send大小（每个Platform累积多少条后Send）
    BATCH_SIZE = 5
    
    # PlatformName映射（用于控制台显示）
    PLATFORM_DISPLAY_NAMES = {
        'twitter': '世界1',
        'reddit': '世界2',
    }
    
    # Send间隔（秒），避免Request过快
    SEND_INTERVAL = 0.5
    
    # RetryConfiguration
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # 秒
    
    def __init__(self, graph_id: str, api_key: Optional[str] = None):
        """
        InitializeUpdate器
        
        Args:
            graph_id: ZepGraph ID
            api_key: Zep API Key（Optional，Default从Configuration读取）
        """
        self.graph_id = graph_id
        self.api_key = api_key or Config.ZEP_API_KEY
        
        if not self.api_key:
            raise ValueError("ZEP_API_KEY未Configuration")
        
        self.client = Zep(api_key=self.api_key)
        
        # 活动队列
        self._activity_queue: Queue = Queue()
        
        # 按Platform分组的活动缓冲区（每个Platform各自累积到BATCH_SIZE后批量Send）
        self._platform_buffers: Dict[str, List[AgentActivity]] = {
            'twitter': [],
            'reddit': [],
        }
        self._buffer_lock = threading.Lock()
        
        # 控制标志
        self._running = False
        self._worker_thread: Optional[threading.Thread] = None
        
        # 统计
        self._total_activities = 0  # 实际添加到队列的活动数
        self._total_sent = 0        # SuccessSend到Zep的批次数
        self._total_items_sent = 0  # SuccessSend到Zep的活动条数
        self._failed_count = 0      # SendFailed的批次数
        self._skipped_count = 0     # 被过滤跳过的活动数（DO_NOTHING）
        
        logger.info(f"ZepGraphMemoryUpdater InitializeComplete: graph_id={graph_id}, batch_size={self.BATCH_SIZE}")
    
    def _get_platform_display_name(self, platform: str) -> str:
        """GetPlatform的显示Name"""
        return self.PLATFORM_DISPLAY_NAMES.get(platform.lower(), platform)
    
    def start(self):
        """Start后台工作线程"""
        if self._running:
            return
        
        self._running = True
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name=f"ZepMemoryUpdater-{self.graph_id[:8]}"
        )
        self._worker_thread.start()
        logger.info(f"ZepGraphMemoryUpdater 已Start: graph_id={self.graph_id}")
    
    def stop(self):
        """Stop后台工作线程"""
        self._running = False
        
        # Send剩余的活动
        self._flush_remaining()
        
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=10)
        
        logger.info(f"ZepGraphMemoryUpdater 已Stop: graph_id={self.graph_id}, "
                   f"total_activities={self._total_activities}, "
                   f"batches_sent={self._total_sent}, "
                   f"items_sent={self._total_items_sent}, "
                   f"failed={self._failed_count}, "
                   f"skipped={self._skipped_count}")
    
    def add_activity(self, activity: AgentActivity):
        """
        添加一个agent活动到队列
        
        所有有意义的行为都会被添加到队列，包括：
        - CREATE_POST（发帖）
        - CREATE_COMMENT（评论）
        - QUOTE_POST（引用帖子）
        - SEARCH_POSTS（搜索帖子）
        - SEARCH_USER（搜索User）
        - LIKE_POST/DISLIKE_POST（点赞/踩帖子）
        - REPOST（转发）
        - FOLLOW（关注）
        - MUTE（屏蔽）
        - LIKE_COMMENT/DISLIKE_COMMENT（点赞/踩评论）
        
        action_args中会包含完整的上下文Information（如帖子原文、User名等）。
        
        Args:
            activity: Agent活动Record
        """
        # 跳过DO_NOTHINGType的活动
        if activity.action_type == "DO_NOTHING":
            self._skipped_count += 1
            return
        
        self._activity_queue.put(activity)
        self._total_activities += 1
        logger.debug(f"添加活动到Zep队列: {activity.agent_name} - {activity.action_type}")
    
    def add_activity_from_dict(self, data: Dict[str, Any], platform: str):
        """
        从字典Data添加活动
        
        Args:
            data: 从actions.jsonlParse的字典Data
            platform: PlatformName (twitter/reddit)
        """
        # 跳过事件Type的条目
        if "event_type" in data:
            return
        
        activity = AgentActivity(
            platform=platform,
            agent_id=data.get("agent_id", 0),
            agent_name=data.get("agent_name", ""),
            action_type=data.get("action_type", ""),
            action_args=data.get("action_args", {}),
            round_num=data.get("round", 0),
            timestamp=data.get("timestamp", datetime.now().isoformat()),
        )
        
        self.add_activity(activity)
    
    def _worker_loop(self):
        """后台工作循环 - 按Platform批量Send活动到Zep"""
        while self._running or not self._activity_queue.empty():
            try:
                # 尝试从队列Get活动（Timeout1秒）
                try:
                    activity = self._activity_queue.get(timeout=1)
                    
                    # 将活动添加到对应Platform的缓冲区
                    platform = activity.platform.lower()
                    with self._buffer_lock:
                        if platform not in self._platform_buffers:
                            self._platform_buffers[platform] = []
                        self._platform_buffers[platform].append(activity)
                        
                        # 检查该PlatformWhether达到批量大小
                        if len(self._platform_buffers[platform]) >= self.BATCH_SIZE:
                            batch = self._platform_buffers[platform][:self.BATCH_SIZE]
                            self._platform_buffers[platform] = self._platform_buffers[platform][self.BATCH_SIZE:]
                            # 释放锁后再Send
                            self._send_batch_activities(batch, platform)
                            # Send间隔，避免Request过快
                            time.sleep(self.SEND_INTERVAL)
                    
                except Empty:
                    pass
                    
            except Exception as e:
                logger.error(f"工作循环异常: {e}")
                time.sleep(1)
    
    def _send_batch_activities(self, activities: List[AgentActivity], platform: str):
        """
        批量Send活动到ZepGraph（合并为一条文本）
        
        Args:
            activities: Agent活动List
            platform: PlatformName
        """
        if not activities:
            return
        
        # 将多条活动合并为一条文本，用换行分隔
        episode_texts = [activity.to_episode_text() for activity in activities]
        combined_text = "\n".join(episode_texts)
        
        # 带Retry的Send
        for attempt in range(self.MAX_RETRIES):
            try:
                self.client.graph.add(
                    graph_id=self.graph_id,
                    type="text",
                    data=combined_text
                )
                
                self._total_sent += 1
                self._total_items_sent += len(activities)
                display_name = self._get_platform_display_name(platform)
                logger.info(f"Success批量Send {len(activities)} 条{display_name}活动到Graph {self.graph_id}")
                logger.debug(f"批量Content预览: {combined_text[:200]}...")
                return
                
            except Exception as e:
                if attempt < self.MAX_RETRIES - 1:
                    logger.warning(f"批量Send到ZepFailed (尝试 {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(self.RETRY_DELAY * (attempt + 1))
                else:
                    logger.error(f"批量Send到ZepFailed，已Retry{self.MAX_RETRIES}次: {e}")
                    self._failed_count += 1
    
    def _flush_remaining(self):
        """Send队列和缓冲区中剩余的活动"""
        # 首先Process队列中剩余的活动，添加到缓冲区
        while not self._activity_queue.empty():
            try:
                activity = self._activity_queue.get_nowait()
                platform = activity.platform.lower()
                with self._buffer_lock:
                    if platform not in self._platform_buffers:
                        self._platform_buffers[platform] = []
                    self._platform_buffers[platform].append(activity)
            except Empty:
                break
        
        # 然后Send各Platform缓冲区中剩余的活动（即使不足BATCH_SIZE条）
        with self._buffer_lock:
            for platform, buffer in self._platform_buffers.items():
                if buffer:
                    display_name = self._get_platform_display_name(platform)
                    logger.info(f"Send{display_name}Platform剩余的 {len(buffer)} 条活动")
                    self._send_batch_activities(buffer, platform)
            # 清空所有缓冲区
            for platform in self._platform_buffers:
                self._platform_buffers[platform] = []
    
    def get_stats(self) -> Dict[str, Any]:
        """Get统计Information"""
        with self._buffer_lock:
            buffer_sizes = {p: len(b) for p, b in self._platform_buffers.items()}
        
        return {
            "graph_id": self.graph_id,
            "batch_size": self.BATCH_SIZE,
            "total_activities": self._total_activities,  # 添加到队列的活动总数
            "batches_sent": self._total_sent,            # SuccessSend的批次数
            "items_sent": self._total_items_sent,        # SuccessSend的活动条数
            "failed_count": self._failed_count,          # SendFailed的批次数
            "skipped_count": self._skipped_count,        # 被过滤跳过的活动数（DO_NOTHING）
            "queue_size": self._activity_queue.qsize(),
            "buffer_sizes": buffer_sizes,                # 各Platform缓冲区大小
            "running": self._running,
        }


class ZepGraphMemoryManager:
    """
    管理多个Simulation的ZepGraph memory updater
    
    每个Simulation可以有自己的Update器实例
    """
    
    _updaters: Dict[str, ZepGraphMemoryUpdater] = {}
    _lock = threading.Lock()
    
    @classmethod
    def create_updater(cls, simulation_id: str, graph_id: str) -> ZepGraphMemoryUpdater:
        """
        为SimulationCreateGraph memory updater
        
        Args:
            simulation_id: SimulationID
            graph_id: ZepGraph ID
            
        Returns:
            ZepGraphMemoryUpdater实例
        """
        with cls._lock:
            # If已存在，先Stop旧的
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
            
            updater = ZepGraphMemoryUpdater(graph_id)
            updater.start()
            cls._updaters[simulation_id] = updater
            
            logger.info(f"CreateGraph memory updater: simulation_id={simulation_id}, graph_id={graph_id}")
            return updater
    
    @classmethod
    def get_updater(cls, simulation_id: str) -> Optional[ZepGraphMemoryUpdater]:
        """GetSimulation的Update器"""
        return cls._updaters.get(simulation_id)
    
    @classmethod
    def stop_updater(cls, simulation_id: str):
        """Stop并移除Simulation的Update器"""
        with cls._lock:
            if simulation_id in cls._updaters:
                cls._updaters[simulation_id].stop()
                del cls._updaters[simulation_id]
                logger.info(f"已StopGraph memory updater: simulation_id={simulation_id}")
    
    # 防止 stop_all 重复Call的标志
    _stop_all_done = False
    
    @classmethod
    def stop_all(cls):
        """Stop所有Update器"""
        # 防止重复Call
        if cls._stop_all_done:
            return
        cls._stop_all_done = True
        
        with cls._lock:
            if cls._updaters:
                for simulation_id, updater in list(cls._updaters.items()):
                    try:
                        updater.stop()
                    except Exception as e:
                        logger.error(f"StopUpdate器Failed: simulation_id={simulation_id}, error={e}")
                cls._updaters.clear()
            logger.info("已Stop所有Graph memory updater")
    
    @classmethod
    def get_all_stats(cls) -> Dict[str, Dict[str, Any]]:
        """Get所有Update器的统计Information"""
        return {
            sim_id: updater.get_stats() 
            for sim_id, updater in cls._updaters.items()
        }

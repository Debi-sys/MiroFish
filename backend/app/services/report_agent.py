"""
Report AgentService
使用LangChain + Zep实现ReACT模式的SimulationReport generation

功能：
1. 根据Simulation需求和ZepGraphInformationGenerateReport
2. 先规划Directory结构，然后分段Generate
3. 每段采用ReACT多轮思考与反思模式
4. 支持与User对话，在对话中自主Call检索工具
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .zep_tools import (
    ZepToolsService, 
    SearchResult, 
    InsightForgeResult, 
    PanoramaResult,
    InterviewResult
)

logger = get_logger('mirofish.report_agent')


class ReportLogger:
    """
    Report Agent 详细LogRecord器
    
    在ReportFile夹中Generate agent_log.jsonl File，Record每一步详细动作。
    每行是一个完整的 JSON 对象，包含时间戳、动作Type、详细Content等。
    """
    
    def __init__(self, report_id: str):
        """
        InitializeLogRecord器
        
        Args:
            report_id: ReportID，用于确定LogFilePath
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """确保LogFile所在Directory存在"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """Get从Start到现在的耗时（秒）"""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self, 
        action: str, 
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """
        Record一条Log
        
        Args:
            action: 动作Type，如 'start', 'tool_call', 'llm_response', 'section_complete' 等
            stage: 当前阶段，如 'planning', 'generating', 'completed'
            details: 详细Content字典，不截断
            section_title: 当前章节标题（Optional）
            section_index: 当前章节索引（Optional）
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # 追加写入 JSONL File
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """RecordReport generationStart"""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": "Report generationTaskStart"
            }
        )
    
    def log_planning_start(self):
        """Record大纲规划Start"""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": "Start规划Report大纲"}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """Record规划时Get的上下文Information"""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": "GetSimulation上下文Information",
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """Record大纲规划Complete"""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": "大纲规划Complete",
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """Record章节GenerateStart"""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": f"StartGenerate章节: {section_title}"}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """Record ReACT 思考过程"""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": f"ReACT 第{iteration}轮思考"
            }
        )
    
    def log_tool_call(
        self, 
        section_title: str, 
        section_index: int,
        tool_name: str, 
        parameters: Dict[str, Any],
        iteration: int
    ):
        """Record工具Call"""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": f"Call工具: {tool_name}"
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """Record工具CallResult（完整Content，不截断）"""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,  # 完整Result，不截断
                "result_length": len(result),
                "message": f"工具 {tool_name} ReturnResult"
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """Record LLM Response（完整Content，不截断）"""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,  # 完整Response，不截断
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": f"LLM Response (工具Call: {has_tool_calls}, 最终答案: {has_final_answer})"
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int
    ):
        """Record章节ContentGenerateComplete（仅RecordContent，不代表整个章节Complete）"""
        self.log(
            action="section_content",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,  # 完整Content，不截断
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "message": f"章节 {section_title} ContentGenerateComplete"
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str
    ):
        """
        Record章节GenerateComplete

        前端应监听此Log来判断一个章节Whether真正Complete，并Get完整Content
        """
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": full_content,
                "content_length": len(full_content),
                "message": f"章节 {section_title} GenerateComplete"
            }
        )
    
    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        """RecordReport generationComplete"""
        self.log(
            action="report_complete",
            stage="completed",
            details={
                "total_sections": total_sections,
                "total_time_seconds": round(total_time_seconds, 2),
                "message": "Report generationComplete"
            }
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """RecordError"""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": f"发生Error: {error_message}"
            }
        )


class ReportConsoleLogger:
    """
    Report Agent 控制台LogRecord器
    
    将控制台风格的Log（INFO、WARNING等）写入ReportFile夹中的 console_log.txt File。
    这些Log与 agent_log.jsonl 不同，是纯文本Format的控制台Output。
    """
    
    def __init__(self, report_id: str):
        """
        Initialize控制台LogRecord器
        
        Args:
            report_id: ReportID，用于确定LogFilePath
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """确保LogFile所在Directory存在"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """SettingsFileProcess器，将Log同时写入File"""
        import logging
        
        # CreateFileProcess器
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)
        
        # 使用与控制台相同的简洁Format
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)
        
        # 添加到 report_agent 相关的 logger
        loggers_to_attach = [
            'mirofish.report_agent',
            'mirofish.zep_tools',
        ]
        
        for logger_name in loggers_to_attach:
            target_logger = logging.getLogger(logger_name)
            # 避免重复添加
            if self._file_handler not in target_logger.handlers:
                target_logger.addHandler(self._file_handler)
    
    def close(self):
        """CloseFileProcess器并从 logger 中移除"""
        import logging
        
        if self._file_handler:
            loggers_to_detach = [
                'mirofish.report_agent',
                'mirofish.zep_tools',
            ]
            
            for logger_name in loggers_to_detach:
                target_logger = logging.getLogger(logger_name)
                if self._file_handler in target_logger.handlers:
                    target_logger.removeHandler(self._file_handler)
            
            self._file_handler.close()
            self._file_handler = None
    
    def __del__(self):
        """析构时确保CloseFileProcess器"""
        self.close()


class ReportStatus(str, Enum):
    """ReportStatus"""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """Report章节"""
    title: str
    content: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content
        }

    def to_markdown(self, level: int = 2) -> str:
        """转换为MarkdownFormat"""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        return md


@dataclass
class ReportOutline:
    """Report大纲"""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """转换为MarkdownFormat"""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """完整Report"""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


# ═══════════════════════════════════════════════════════════════
# Prompt TemplateConstant
# ═══════════════════════════════════════════════════════════════

# ── 工具Description ──

TOOL_DESC_INSIGHT_FORGE = """\
【深度洞察检索 - 强大的检索工具】
这是我们强大的检索Function，专为深度分析设计。它会：
1. 自动将你的问题分解为多个子问题
2. 从多个维度检索SimulationGraph中的Information
3. 整合语义搜索、Entity分析、Relationship链追踪的Result
4. Return最全面、最深度的检索Content

【使用场景】
- 需要深入分析某个话题
- 需要了解事件的多个方面
- 需要Get支撑Report章节的丰富素材

【ReturnContent】
- 相关事实原文（可直接引用）
- 核心Entity洞察
- Relationship链分析"""

TOOL_DESC_PANORAMA_SEARCH = """\
【广度搜索 - Get全貌视图】
这个工具用于GetSimulationResult的完整全貌，特别适合了解事件演变过程。它会：
1. Get所有相关Node和Relationship
2. 区分当前有效的事实和历史/过期的事实
3. 帮助你了解舆情是如何演变的

【使用场景】
- 需要了解事件的完整发展脉络
- 需要对比不同阶段的舆情变化
- 需要Get全面的Entity和RelationshipInformation

【ReturnContent】
- 当前有效事实（Simulation最新Result）
- 历史/过期事实（演变Record）
- 所有涉及的Entity"""

TOOL_DESC_QUICK_SEARCH = """\
【简单搜索 - 快速检索】
轻量级的快速检索工具，适合简单、直接的InformationQuery。

【使用场景】
- 需要快速查找某个具体Information
- 需要Validate某个事实
- 简单的Information检索

【ReturnContent】
- 与Query最相关的事实List"""

TOOL_DESC_INTERVIEW_AGENTS = """\
【深度采访 - 真实Agent采访（双Platform）】
CallOASISSimulation环境的采访API，对CurrentlyRun的SimulationAgent进行真实采访！
这不是LLMSimulation，而是Call真实的采访InterfaceGetSimulationAgent的原始回答。
Default在Twitter和Reddit两个Platform同时采访，Get更全面的观点。

功能流程：
1. 自动读取人设File，了解所有SimulationAgent
2. 智能选择与采访主题最相关的Agent（如学生、媒体、官方等）
3. 自动Generate采访问题
4. Call /api/simulation/interview/batch Interface在双Platform进行真实采访
5. 整合所有采访Result，提供多视角分析

【使用场景】
- 需要从不同角色视角了解事件看法（学生怎么看？媒体怎么看？官方怎么说？）
- 需要收集多方意见和立场
- 需要GetSimulationAgent的真实回答（来自OASISSimulation环境）
- 想让Report更生动，包含"采访实录"

【ReturnContent】
- 被采访Agent的身份Information
- 各Agent在Twitter和Reddit两个Platform的采访回答
- 关键引言（可直接引用）
- 采访摘要和观点对比

【重要】需要OASISSimulation环境CurrentlyRun才能使用此功能！"""

# ── 大纲规划 prompt ──

PLAN_SYSTEM_PROMPT = """\
你是一个「未来预测Report」的撰写专家，拥有对Simulation世界的「上帝视角」——你可以洞察Simulation中每一位Agent的行为、言论和互动。

【核心理念】
我们构建了一个Simulation世界，并向其中注入了特定的「Simulation需求」作为Variable。Simulation世界的演化Result，就是对未来可能发生情况的预测。你Currently观察的不是"实验Data"，而是"未来的预演"。

【你的Task】
撰写一份「未来预测Report」，回答：
1. 在我们设定的条件下，未来发生了什么？
2. 各类Agent（人群）是如何反应和行动？
3. 这个Simulation揭示了哪些值得关注的未来趋势和风险？

【Report定位】
- ✅ 这是一份基于Simulation的未来预测Report，揭示"If这样，未来会怎样"
- ✅ 聚焦于预测Result：事件走向、群体反应、涌现现象、潜在风险
- ✅ Simulation世界中的Agent言行就是对未来人群行为的预测
- ❌ 不是对现实世界现状的分析
- ❌ 不是泛泛而谈的舆情综述

【章节数量限制】
- 最少2个章节，最多5个章节
- 不需要子章节，每个章节直接撰写完整Content
- Content要精炼，聚焦于核心预测发现
- 章节结构由你根据预测Result自主设计

请OutputJSONFormat的Report大纲，Format如下：
{
    "title": "Report标题",
    "summary": "Report摘要（一句话概括核心预测发现）",
    "sections": [
        {
            "title": "章节标题",
            "description": "章节ContentDescription"
        }
    ]
}

Note：sections数组最少2个，最多5个元素！"""

PLAN_USER_PROMPT_TEMPLATE = """\
【预测场景设定】
我们向Simulation世界注入的Variable（Simulation需求）：{simulation_requirement}

【Simulation世界规模】
- 参与Simulation的Entity数量: {total_nodes}
- Entity间产生的Relationship数量: {total_edges}
- EntityType分布: {entity_types}
- 活跃Agent数量: {total_entities}

【Simulation预测到的部分未来事实样本】
{related_facts_json}

请以「上帝视角」审视这个未来预演：
1. 在我们设定的条件下，未来呈现出了什么样的Status？
2. 各类人群（Agent）是如何反应和行动的？
3. 这个Simulation揭示了哪些值得关注的未来趋势？

根据预测Result，设计最合适的Report章节结构。

【再次提醒】Report章节数量：最少2个，最多5个，Content要精炼聚焦于核心预测发现。"""

# ── 章节Generate prompt ──

SECTION_SYSTEM_PROMPT_TEMPLATE = """\
你是一个「未来预测Report」的撰写专家，Currently撰写Report的一个章节。

Report标题: {report_title}
Report摘要: {report_summary}
预测场景（Simulation需求）: {simulation_requirement}

当前要撰写的章节: {section_title}

═══════════════════════════════════════════════════════════════
【核心理念】
═══════════════════════════════════════════════════════════════

Simulation世界是对未来的预演。我们向Simulation世界注入了特定条件（Simulation需求），
Simulation中Agent的行为和互动，就是对未来人群行为的预测。

你的Task是：
- 揭示在设定条件下，未来发生了什么
- 预测各类人群（Agent）是如何反应和行动的
- 发现值得关注的未来趋势、风险和机会

❌ 不要写成对现实世界现状的分析
✅ 要聚焦于"未来会怎样"——SimulationResult就是预测的未来

═══════════════════════════════════════════════════════════════
【最重要的规则 - 必须遵守】
═══════════════════════════════════════════════════════════════

1. 【必须Call工具观察Simulation世界】
   - 你Currently以「上帝视角」观察未来的预演
   - 所有Content必须来自Simulation世界中发生的事件和Agent言行
   - 禁止使用你自己的知识来编写ReportContent
   - 每个章节至少Call3次工具（最多5次）来观察Simulation的世界，它代表了未来

2. 【必须引用Agent的原始言行】
   - Agent的发言和行为是对未来人群行为的预测
   - 在Report中使用引用Format展示这些预测，例如：
     > "某类人群会表示：原文Content..."
   - 这些引用是Simulation预测的核心证据

3. 【语言一致性 - 引用Content必须翻译为Report语言】
   - 工具Return的Content可能包含英文或中英文混杂的表述
   - IfSimulation需求和材料原文是中文的，Report必须全部使用中文撰写
   - 当你引用工具Return的英文或中英混杂Content时，必须将其翻译为流畅的中文后再写入Report
   - 翻译时保持原意不变，确保表述自然通顺
   - 这一规则同时适用于正文和引用块（> Format）中的Content

4. 【忠实呈现预测Result】
   - ReportContent必须反映Simulation世界中的代表未来的SimulationResult
   - 不要添加Simulation中不存在的Information
   - If某方面Information不足，如实Description

═══════════════════════════════════════════════════════════════
【⚠️ Format规范 - 极其重要！】
═══════════════════════════════════════════════════════════════

【一个章节 = 最小Content单位】
- 每个章节是Report的最小分块单位
- ❌ 禁止在章节内使用任何 Markdown 标题（#、##、###、#### 等）
- ❌ 禁止在Content开头添加章节主标题
- ✅ 章节标题由系统自动添加，你只需撰写纯正文Content
- ✅ 使用**粗体**、段落分隔、引用、List来组织Content，但不要用标题

【正确Example】
```
本章节分析了事件的舆论传播态势。通过对SimulationData的深入分析，我们发现...

**首发引爆阶段**

微博作为舆情的第一现场，承担了Information首发的核心功能：

> "微博贡献了68%的首发声量..."

**情绪放大阶段**

抖音Platform进一步放大了事件影响力：

- 视觉冲击力强
- 情绪共鸣度高
```

【ErrorExample】
```
## Execute摘要          ← Error！不要添加任何标题
### 一、首发阶段     ← Error！不要用###分小节
#### 1.1 详细分析   ← Error！不要用####细分

本章节分析了...
```

═══════════════════════════════════════════════════════════════
【可用检索工具】（每章节Call3-5次）
═══════════════════════════════════════════════════════════════

{tools_description}

【工具使用建议 - 请混合使用不同工具，不要只用一种】
- insight_forge: 深度洞察分析，自动分解问题并多维度检索事实和Relationship
- panorama_search: 广角全景搜索，了解事件全貌、时间线和演变过程
- quick_search: 快速Validate某个具体Information点
- interview_agents: 采访SimulationAgent，Get不同角色的第一人称观点和真实反应

═══════════════════════════════════════════════════════════════
【工作流程】
═══════════════════════════════════════════════════════════════

每次回复你只能做以下两件事之一（不可同时做）：

选项A - Call工具：
Output你的思考，然后用以下FormatCall一个工具：
<tool_call>
{{"name": "工具Name", "parameters": {{"Parameters名": "Parameters值"}}}}
</tool_call>
系统会Execute工具并把ResultReturn给你。你不需要也不能自己编写工具ReturnResult。

选项B - Output最终Content：
当你已通过工具Get了足够Information，以 "Final Answer:" 开头Output章节Content。

⚠️ 严格禁止：
- 禁止在一次回复中同时包含工具Call和 Final Answer
- 禁止自己编造工具ReturnResult（Observation），所有工具Result由系统注入
- 每次回复最多Call一个工具

═══════════════════════════════════════════════════════════════
【章节Content要求】
═══════════════════════════════════════════════════════════════

1. Content必须基于工具检索到的SimulationData
2. 大量引用原文来展示Simulation效果
3. 使用MarkdownFormat（但禁止使用标题）：
   - 使用 **粗体文字** 标记重点（代替子标题）
   - 使用List（-或1.2.3.）组织要点
   - 使用空行分隔不同段落
   - ❌ 禁止使用 #、##、###、#### 等任何标题语法
4. 【引用Format规范 - 必须单独成段】
   引用必须独立成段，前后各有一个空行，不能混在段落中：

   ✅ 正确Format：
   ```
   校方的回应被认为缺乏实质Content。

   > "校方的应对模式在瞬息万变的社交媒体环境中显得僵化和迟缓。"

   这一评价反映了公众的普遍不满。
   ```

   ❌ ErrorFormat：
   ```
   校方的回应被认为缺乏实质Content。> "校方的应对模式..." 这一评价反映了...
   ```
5. 保持与其他章节的逻辑连贯性
6. 【避免重复】仔细阅读下方已Complete的章节Content，不要重复Description相同的Information
7. 【再次强调】不要添加任何标题！用**粗体**代替小节标题"""

SECTION_USER_PROMPT_TEMPLATE = """\
已Complete的章节Content（请仔细阅读，避免重复）：
{previous_content}

═══════════════════════════════════════════════════════════════
【当前Task】撰写章节: {section_title}
═══════════════════════════════════════════════════════════════

【重要提醒】
1. 仔细阅读上方已Complete的章节，避免重复相同的Content！
2. Start前必须先Call工具GetSimulationData
3. 请混合使用不同工具，不要只用一种
4. ReportContent必须来自检索Result，不要使用自己的知识

【⚠️ Format警告 - 必须遵守】
- ❌ 不要写任何标题（#、##、###、####都不行）
- ❌ 不要写"{section_title}"作为开头
- ✅ 章节标题由系统自动添加
- ✅ 直接写正文，用**粗体**代替小节标题

请Start：
1. 首先思考（Thought）这个章节需要什么Information
2. 然后Call工具（Action）GetSimulationData
3. 收集足够Information后Output Final Answer（纯正文，无任何标题）"""

# ── ReACT 循环内MessageTemplate ──

REACT_OBSERVATION_TEMPLATE = """\
Observation（检索Result）:

═══ 工具 {tool_name} Return ═══
{result}

═══════════════════════════════════════════════════════════════
已Call工具 {tool_calls_count}/{max_tool_calls} 次（已用: {used_tools_str}）{unused_hint}
- IfInformation充分：以 "Final Answer:" 开头Output章节Content（必须引用上述原文）
- If需要更多Information：Call一个工具继续检索
═══════════════════════════════════════════════════════════════"""

REACT_INSUFFICIENT_TOOLS_MSG = (
    "【Note】你只Call了{tool_calls_count}次工具，至少需要{min_tool_calls}次。"
    "请再Call工具Get更多SimulationData，然后再Output Final Answer。{unused_hint}"
)

REACT_INSUFFICIENT_TOOLS_MSG_ALT = (
    "当前只Call了 {tool_calls_count} 次工具，至少需要 {min_tool_calls} 次。"
    "请Call工具GetSimulationData。{unused_hint}"
)

REACT_TOOL_LIMIT_MSG = (
    "工具Call次数已达上限（{tool_calls_count}/{max_tool_calls}），不能再Call工具。"
    '请立即基于已Get的Information，以 "Final Answer:" 开头Output章节Content。'
)

REACT_UNUSED_TOOLS_HINT = "\n💡 你还没有使用过: {unused_list}，建议尝试不同工具Get多角度Information"

REACT_FORCE_FINAL_MSG = "已达到工具Call限制，请直接Output Final Answer: 并Generate章节Content。"

# ── Chat prompt ──

CHAT_SYSTEM_PROMPT_TEMPLATE = """\
你是一个简洁高效的Simulation预测助手。

【背景】
预测条件: {simulation_requirement}

【已Generate的分析Report】
{report_content}

【规则】
1. 优先基于上述ReportContent回答问题
2. 直接回答问题，避免冗长的思考论述
3. 仅在ReportContent不足以回答时，才Call工具检索更多Data
4. 回答要简洁、清晰、有条理

【可用工具】（仅在需要时使用，最多Call1-2次）
{tools_description}

【工具CallFormat】
<tool_call>
{{"name": "工具Name", "parameters": {{"Parameters名": "Parameters值"}}}}
</tool_call>

【回答风格】
- 简洁直接，不要长篇大论
- 使用 > Format引用关键Content
- 优先给出结论，再解释原因"""

CHAT_OBSERVATION_SUFFIX = "\n\n请简洁回答问题。"


# ═══════════════════════════════════════════════════════════════
# ReportAgent 主类
# ═══════════════════════════════════════════════════════════════


class ReportAgent:
    """
    Report Agent - SimulationReport generationAgent

    采用ReACT（Reasoning + Acting）模式：
    1. 规划阶段：分析Simulation需求，规划ReportDirectory结构
    2. Generate阶段：逐章节GenerateContent，每章节可多次Call工具GetInformation
    3. 反思阶段：检查Content完整性和准确性
    """
    
    # 最大工具Call次数（每个章节）
    MAX_TOOL_CALLS_PER_SECTION = 5
    
    # 最大反思轮数
    MAX_REFLECTION_ROUNDS = 3
    
    # 对话中的最大工具Call次数
    MAX_TOOL_CALLS_PER_CHAT = 2
    
    def __init__(
        self, 
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        zep_tools: Optional[ZepToolsService] = None
    ):
        """
        InitializeReport Agent
        
        Args:
            graph_id: Graph ID
            simulation_id: SimulationID
            simulation_requirement: Simulation需求Description
            llm_client: LLM客户端（Optional）
            zep_tools: Zep工具Service（Optional）
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement
        
        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()
        
        # 工具定义
        self.tools = self._define_tools()
        
        # LogRecord器（在 generate_report 中Initialize）
        self.report_logger: Optional[ReportLogger] = None
        # 控制台LogRecord器（在 generate_report 中Initialize）
        self.console_logger: Optional[ReportConsoleLogger] = None
        
        logger.info(f"ReportAgent InitializeComplete: graph_id={graph_id}, simulation_id={simulation_id}")
    
    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """定义可用工具"""
        return {
            "insight_forge": {
                "name": "insight_forge",
                "description": TOOL_DESC_INSIGHT_FORGE,
                "parameters": {
                    "query": "你想深入分析的问题或话题",
                    "report_context": "当前Report章节的上下文（Optional，有助于Generate更精准的子问题）"
                }
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": TOOL_DESC_PANORAMA_SEARCH,
                "parameters": {
                    "query": "搜索Query，用于相关性排序",
                    "include_expired": "Whether包含过期/历史Content（DefaultTrue）"
                }
            },
            "quick_search": {
                "name": "quick_search",
                "description": TOOL_DESC_QUICK_SEARCH,
                "parameters": {
                    "query": "搜索Query字符串",
                    "limit": "ReturnResult数量（Optional，Default10）"
                }
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": TOOL_DESC_INTERVIEW_AGENTS,
                "parameters": {
                    "interview_topic": "采访主题或需求Description（如：'了解学生对宿舍甲醛事件的看法'）",
                    "max_agents": "最多采访的Agent数量（Optional，Default5，最大10）"
                }
            }
        }
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        Execute工具Call
        
        Args:
            tool_name: 工具Name
            parameters: 工具Parameters
            report_context: Report上下文（用于InsightForge）
            
        Returns:
            工具ExecuteResult（文本Format）
        """
        logger.info(f"Execute工具: {tool_name}, Parameters: {parameters}")
        
        try:
            if tool_name == "insight_forge":
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.zep_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()
            
            elif tool_name == "panorama_search":
                # 广度搜索 - Get全貌
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.zep_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()
            
            elif tool_name == "quick_search":
                # 简单搜索 - 快速检索
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.zep_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()
            
            elif tool_name == "interview_agents":
                # 深度采访 - Call真实的OASIS采访APIGetSimulationAgent的回答（双Platform）
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 5)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                max_agents = min(max_agents, 10)
                result = self.zep_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents
                )
                return result.to_text()
            
            # ========== 向后兼容的旧工具（内部重定向到新工具） ==========
            
            elif tool_name == "search_graph":
                # 重定向到 quick_search
                logger.info("search_graph 已重定向到 quick_search")
                return self._execute_tool("quick_search", parameters, report_context)
            
            elif tool_name == "get_graph_statistics":
                result = self.zep_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.zep_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_simulation_context":
                # 重定向到 insight_forge，因为它更强大
                logger.info("get_simulation_context 已重定向到 insight_forge")
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)
            
            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.zep_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            else:
                return f"未知工具: {tool_name}。请使用以下工具之一: insight_forge, panorama_search, quick_search"
                
        except Exception as e:
            logger.error(f"工具ExecuteFailed: {tool_name}, Error: {str(e)}")
            return f"工具ExecuteFailed: {str(e)}"
    
    # 合法的工具Name集合，用于裸 JSON 兜底Parse时校验
    VALID_TOOL_NAMES = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        从LLMResponse中Parse工具Call

        支持的Format（按优先级）：
        1. <tool_call>{"name": "tool_name", "parameters": {...}}</tool_call>
        2. 裸 JSON（Response整体或单行就是一个工具Call JSON）
        """
        tool_calls = []

        # Format1: XML风格（标准Format）
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # Format2: 兜底 - LLM 直接Output裸 JSON（没包 <tool_call> 标签）
        # 只在Format1未匹配时尝试，避免误匹配正文中的 JSON
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # Response可能包含思考文字 + 裸 JSON，尝试提取最后一个 JSON 对象
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """校验Parse出的 JSON Whether是合法的工具Call"""
        # 支持 {"name": ..., "parameters": ...} 和 {"tool": ..., "params": ...} 两种键名
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self.VALID_TOOL_NAMES:
            # 统一键名为 name / parameters
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False
    
    def _get_tools_description(self) -> str:
        """Generate工具Description文本"""
        desc_parts = ["可用工具："]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  Parameters: {params_desc}")
        return "\n".join(desc_parts)
    
    def plan_outline(
        self, 
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        规划Report大纲
        
        使用LLM分析Simulation需求，规划Report的Directory结构
        
        Args:
            progress_callback: 进度回调Function
            
        Returns:
            ReportOutline: Report大纲
        """
        logger.info("Start规划Report大纲...")
        
        if progress_callback:
            progress_callback("planning", 0, "Currently分析Simulation需求...")
        
        # 首先GetSimulation上下文
        context = self.zep_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, "CurrentlyGenerateReport大纲...")
        
        system_prompt = PLAN_SYSTEM_PROMPT
        user_prompt = PLAN_USER_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            total_nodes=context.get('graph_statistics', {}).get('total_nodes', 0),
            total_edges=context.get('graph_statistics', {}).get('total_edges', 0),
            entity_types=list(context.get('graph_statistics', {}).get('entity_types', {}).keys()),
            total_entities=context.get('total_entities', 0),
            related_facts_json=json.dumps(context.get('related_facts', [])[:10], ensure_ascii=False, indent=2),
        )

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, "CurrentlyParse大纲结构...")
            
            # Parse大纲
            sections = []
            for section_data in response.get("sections", []):
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content=""
                ))
            
            outline = ReportOutline(
                title=response.get("title", "Simulation分析Report"),
                summary=response.get("summary", ""),
                sections=sections
            )
            
            if progress_callback:
                progress_callback("planning", 100, "大纲规划Complete")
            
            logger.info(f"大纲规划Complete: {len(sections)} 个章节")
            return outline
            
        except Exception as e:
            logger.error(f"大纲规划Failed: {str(e)}")
            # ReturnDefault大纲（3个章节，作为fallback）
            return ReportOutline(
                title="未来预测Report",
                summary="基于Simulation预测的未来趋势与风险分析",
                sections=[
                    ReportSection(title="预测场景与核心发现"),
                    ReportSection(title="人群行为预测分析"),
                    ReportSection(title="趋势展望与风险提示")
                ]
            )
    
    def _generate_section_react(
        self, 
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        使用ReACT模式Generate单个章节Content
        
        ReACT循环：
        1. Thought（思考）- 分析需要什么Information
        2. Action（行动）- Call工具GetInformation
        3. Observation（观察）- 分析工具ReturnResult
        4. 重复直到Information足够或达到最大次数
        5. Final Answer（最终回答）- Generate章节Content
        
        Args:
            section: 要Generate的章节
            outline: 完整大纲
            previous_sections: 之前章节的Content（用于保持连贯性）
            progress_callback: 进度回调
            section_index: 章节索引（用于LogRecord）
            
        Returns:
            章节Content（MarkdownFormat）
        """
        logger.info(f"ReACTGenerate章节: {section.title}")
        
        # Record章节StartLog
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        system_prompt = SECTION_SYSTEM_PROMPT_TEMPLATE.format(
            report_title=outline.title,
            report_summary=outline.summary,
            simulation_requirement=self.simulation_requirement,
            section_title=section.title,
            tools_description=self._get_tools_description(),
        )

        # 构建Userprompt - 每个已Complete章节各传入最大4000字
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # 每个章节最多4000字
                truncated = sec[:4000] + "..." if len(sec) > 4000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "（这是第一个章节）"
        
        user_prompt = SECTION_USER_PROMPT_TEMPLATE.format(
            previous_content=previous_content,
            section_title=section.title,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # ReACT循环
        tool_calls_count = 0
        max_iterations = 5  # 最大迭代轮数
        min_tool_calls = 3  # 最少工具Call次数
        conflict_retries = 0  # 工具Call与Final Answer同时出现的连续冲突次数
        used_tools = set()  # Record已Call过的工具名
        all_tools = {"insight_forge", "panorama_search", "quick_search", "interview_agents"}

        # Report上下文，用于InsightForge的子问题Generate
        report_context = f"章节标题: {section.title}\nSimulation需求: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    f"深度检索与撰写中 ({tool_calls_count}/{self.MAX_TOOL_CALLS_PER_SECTION})"
                )
            
            # CallLLM
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=4096
            )

            # 检查 LLM ReturnWhether为 None（API 异常或Content为空）
            if response is None:
                logger.warning(f"章节 {section.title} 第 {iteration + 1} 次迭代: LLM Return None")
                # If还有迭代次数，添加Message并Retry
                if iteration < max_iterations - 1:
                    messages.append({"role": "assistant", "content": "（Response为空）"})
                    messages.append({"role": "user", "content": "请继续GenerateContent。"})
                    continue
                # 最后一次迭代也Return None，跳出循环进入强制收尾
                break

            logger.debug(f"LLMResponse: {response[:200]}...")

            # Parse一次，复用Result
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # ── 冲突Process：LLM 同时Output了工具Call和 Final Answer ──
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(
                    f"章节 {section.title} 第 {iteration+1} 轮: "
                    f"LLM 同时Output工具Call和 Final Answer（第 {conflict_retries} 次冲突）"
                )

                if conflict_retries <= 2:
                    # 前两次：丢弃本次Response，要求 LLM 重新回复
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【FormatError】你在一次回复中同时包含了工具Call和 Final Answer，这是不允许的。\n"
                            "每次回复只能做以下两件事之一：\n"
                            "- Call一个工具（Output一个 <tool_call> 块，不要写 Final Answer）\n"
                            "- Output最终Content（以 'Final Answer:' 开头，不要包含 <tool_call>）\n"
                            "请重新回复，只做其中一件事。"
                        ),
                    })
                    continue
                else:
                    # 第三次：降级Process，截断到第一个工具Call，强制Execute
                    logger.warning(
                        f"章节 {section.title}: 连续 {conflict_retries} 次冲突，"
                        "降级为截断Execute第一个工具Call"
                    )
                    first_tool_end = response.find('</tool_call>')
                    if first_tool_end != -1:
                        response = response[:first_tool_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # Record LLM ResponseLog
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )

            # ── 情况1：LLM Output了 Final Answer ──
            if has_final_answer:
                # 工具Call次数不足，拒绝并要求继续调工具
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    unused_tools = all_tools - used_tools
                    unused_hint = f"（这些工具还未使用，推荐用一下他们: {', '.join(unused_tools)}）" if unused_tools else ""
                    messages.append({
                        "role": "user",
                        "content": REACT_INSUFFICIENT_TOOLS_MSG.format(
                            tool_calls_count=tool_calls_count,
                            min_tool_calls=min_tool_calls,
                            unused_hint=unused_hint,
                        ),
                    })
                    continue

                # 正常End
                final_answer = response.split("Final Answer:")[-1].strip()
                logger.info(f"章节 {section.title} GenerateComplete（工具Call: {tool_calls_count}次）")

                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count
                    )
                return final_answer

            # ── 情况2：LLM 尝试Call工具 ──
            if has_tool_calls:
                # 工具额度已耗尽 → 明确告知，要求Output Final Answer
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user",
                        "content": REACT_TOOL_LIMIT_MSG.format(
                            tool_calls_count=tool_calls_count,
                            max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        ),
                    })
                    continue

                # 只Execute第一个工具Call
                call = tool_calls[0]
                if len(tool_calls) > 1:
                    logger.info(f"LLM 尝试Call {len(tool_calls)} 个工具，只Execute第一个: {call['name']}")

                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )

                result = self._execute_tool(
                    call["name"],
                    call.get("parameters", {}),
                    report_context=report_context
                )

                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )

                tool_calls_count += 1
                used_tools.add(call['name'])

                # 构建未使用工具提示
                unused_tools = all_tools - used_tools
                unused_hint = ""
                if unused_tools and tool_calls_count < self.MAX_TOOL_CALLS_PER_SECTION:
                    unused_hint = REACT_UNUSED_TOOLS_HINT.format(unused_list="、".join(unused_tools))

                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": REACT_OBSERVATION_TEMPLATE.format(
                        tool_name=call["name"],
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.MAX_TOOL_CALLS_PER_SECTION,
                        used_tools_str=", ".join(used_tools),
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # ── 情况3：既没有工具Call，也没有 Final Answer ──
            messages.append({"role": "assistant", "content": response})

            if tool_calls_count < min_tool_calls:
                # 工具Call次数不足，推荐未用过的工具
                unused_tools = all_tools - used_tools
                unused_hint = f"（这些工具还未使用，推荐用一下他们: {', '.join(unused_tools)}）" if unused_tools else ""

                messages.append({
                    "role": "user",
                    "content": REACT_INSUFFICIENT_TOOLS_MSG_ALT.format(
                        tool_calls_count=tool_calls_count,
                        min_tool_calls=min_tool_calls,
                        unused_hint=unused_hint,
                    ),
                })
                continue

            # 工具Call已足够，LLM Output了Content但没带 "Final Answer:" 前缀
            # 直接将这段Content作为最终答案，不再空转
            logger.info(f"章节 {section.title} 未检测到 'Final Answer:' 前缀，直接采纳LLMOutput作为最终Content（工具Call: {tool_calls_count}次）")
            final_answer = response.strip()

            if self.report_logger:
                self.report_logger.log_section_content(
                    section_title=section.title,
                    section_index=section_index,
                    content=final_answer,
                    tool_calls_count=tool_calls_count
                )
            return final_answer
        
        # 达到最大迭代次数，强制GenerateContent
        logger.warning(f"章节 {section.title} 达到最大迭代次数，强制Generate")
        messages.append({"role": "user", "content": REACT_FORCE_FINAL_MSG})
        
        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=4096
        )

        # 检查强制收尾时 LLM ReturnWhether为 None
        if response is None:
            logger.error(f"章节 {section.title} 强制收尾时 LLM Return None，使用DefaultError提示")
            final_answer = f"（本章节GenerateFailed：LLM Return空Response，请稍后Retry）"
        elif "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response
        
        # Record章节ContentGenerateCompleteLog
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count
            )
        
        return final_answer
    
    def generate_report(
        self, 
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        Generate完整Report（分章节实时Output）
        
        每个章节GenerateComplete后立即Save到File夹，不需要等待整个ReportComplete。
        File结构：
        reports/{report_id}/
            meta.json       - Report元Information
            outline.json    - Report大纲
            progress.json   - Generate进度
            section_01.md   - 第1章节
            section_02.md   - 第2章节
            ...
            full_report.md  - 完整Report
        
        Args:
            progress_callback: 进度回调Function (stage, progress, message)
            report_id: ReportID（Optional，If不传则自动Generate）
            
        Returns:
            Report: 完整Report
        """
        import uuid
        
        # If没有传入 report_id，则自动Generate
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # 已Complete的章节标题List（用于进度追踪）
        completed_section_titles = []
        
        try:
            # Initialize：CreateReportFile夹并Save初始Status
            ReportManager._ensure_report_folder(report_id)
            
            # InitializeLogRecord器（结构化Log agent_log.jsonl）
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # Initialize控制台LogRecord器（console_log.txt）
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, "InitializeReport...",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # 阶段1: 规划大纲
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, "Start规划Report大纲...",
                completed_sections=[]
            )
            
            # Record规划StartLog
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, "Start规划Report大纲...")
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # Record规划CompleteLog
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # Save大纲到File
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, f"大纲规划Complete，共{len(outline.sections)}个章节",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(f"大纲已Save到File: {report_id}/outline.json")
            
            # 阶段2: 逐章节Generate（分章节Save）
            report.status = ReportStatus.GENERATING
            
            total_sections = len(outline.sections)
            generated_sections = []  # SaveContent用于上下文
            
            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # Update进度
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    f"CurrentlyGenerate章节: {section.title} ({section_num}/{total_sections})",
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )
                
                if progress_callback:
                    progress_callback(
                        "generating", 
                        base_progress, 
                        f"CurrentlyGenerate章节: {section.title} ({section_num}/{total_sections})"
                    )
                
                # Generate主章节Content
                section_content = self._generate_section_react(
                    section=section,
                    outline=outline,
                    previous_sections=generated_sections,
                    progress_callback=lambda stage, prog, msg:
                        progress_callback(
                            stage, 
                            base_progress + int(prog * 0.7 / total_sections),
                            msg
                        ) if progress_callback else None,
                    section_index=section_num
                )
                
                section.content = section_content
                generated_sections.append(f"## {section.title}\n\n{section_content}")

                # Save章节
                ReportManager.save_section(report_id, section_num, section)
                completed_section_titles.append(section.title)

                # Record章节CompleteLog
                full_section_content = f"## {section.title}\n\n{section_content}"

                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip()
                    )

                logger.info(f"章节已Save: {report_id}/section_{section_num:02d}.md")
                
                # Update进度
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    f"章节 {section.title} 已Complete",
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # 阶段3: 组装完整Report
            if progress_callback:
                progress_callback("generating", 95, "Currently组装完整Report...")
            
            ReportManager.update_progress(
                report_id, "generating", 95, "Currently组装完整Report...",
                completed_sections=completed_section_titles
            )
            
            # 使用ReportManager组装完整Report
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            
            # 计算总耗时
            total_time_seconds = (datetime.now() - start_time).total_seconds()
            
            # RecordReportCompleteLog
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds
                )
            
            # Save最终Report
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, "Report generationComplete",
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, "Report generationComplete")
            
            logger.info(f"Report generationComplete: {report_id}")
            
            # Close控制台LogRecord器
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            logger.error(f"Report generationFailed: {str(e)}")
            report.status = ReportStatus.FAILED
            report.error = str(e)
            
            # RecordErrorLog
            if self.report_logger:
                self.report_logger.log_error(str(e), "failed")
            
            # SaveFailedStatus
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, f"Report generationFailed: {str(e)}",
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # 忽略SaveFailed的Error
            
            # Close控制台LogRecord器
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
    
    def chat(
        self, 
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        与Report Agent对话
        
        在对话中Agent可以自主Call检索工具来回答问题
        
        Args:
            message: UserMessage
            chat_history: 对话历史
            
        Returns:
            {
                "response": "Agent回复",
                "tool_calls": [Call的工具List],
                "sources": [Information来源]
            }
        """
        logger.info(f"Report Agent对话: {message[:50]}...")
        
        chat_history = chat_history or []
        
        # Get已Generate的ReportContent
        report_content = ""
        try:
            report = ReportManager.get_report_by_simulation(self.simulation_id)
            if report and report.markdown_content:
                # 限制Report长度，避免上下文过长
                report_content = report.markdown_content[:15000]
                if len(report.markdown_content) > 15000:
                    report_content += "\n\n... [ReportContent已截断] ..."
        except Exception as e:
            logger.warning(f"GetReportContentFailed: {e}")
        
        system_prompt = CHAT_SYSTEM_PROMPT_TEMPLATE.format(
            simulation_requirement=self.simulation_requirement,
            report_content=report_content if report_content else "（暂无Report）",
            tools_description=self._get_tools_description(),
        )

        # 构建Message
        messages = [{"role": "system", "content": system_prompt}]
        
        # 添加历史对话
        for h in chat_history[-10:]:  # 限制历史长度
            messages.append(h)
        
        # 添加UserMessage
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # ReACT循环（简化版）
        tool_calls_made = []
        max_iterations = 2  # 减少迭代轮数
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # Parse工具Call
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # 没有工具Call，直接ReturnResponse
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # Execute工具Call（限制数量）
            tool_results = []
            for call in tool_calls[:1]:  # 每轮最多Execute1次工具Call
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # 限制Result长度
                })
                tool_calls_made.append(call)
            
            # 将Result添加到Message
            messages.append({"role": "assistant", "content": response})
            observation = "\n".join([f"[{r['tool']}Result]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + CHAT_OBSERVATION_SUFFIX
            })
        
        # 达到最大迭代，Get最终Response
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # Clean upResponse
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    Report管理器
    
    负责Report的持久化存储和检索
    
    File结构（分章节Output）：
    reports/
      {report_id}/
        meta.json          - Report元Information和Status
        outline.json       - Report大纲
        progress.json      - Generate进度
        section_01.md      - 第1章节
        section_02.md      - 第2章节
        ...
        full_report.md     - 完整Report
    """
    
    # Report存储Directory
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    
    @classmethod
    def _ensure_reports_dir(cls):
        """确保Report根Directory存在"""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """GetReportFile夹Path"""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """确保ReportFile夹存在并ReturnPath"""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """GetReport元InformationFilePath"""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """Get完整ReportMarkdownFilePath"""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")
    
    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """Get大纲FilePath"""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """Get进度FilePath"""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """Get章节MarkdownFilePath"""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """Get Agent LogFilePath"""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """Get控制台LogFilePath"""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Get控制台LogContent
        
        这是Report generation过程中的控制台OutputLog（INFO、WARNING等），
        与 agent_log.jsonl 的结构化Log不同。
        
        Args:
            report_id: ReportID
            from_line: 从第几行Start读取（用于增量Get，0 表示从头Start）
            
        Returns:
            {
                "logs": [Log行List],
                "total_lines": 总行数,
                "from_line": 起始行号,
                "has_more": Whether还有更多Log
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # 保留原始Log行，去掉末尾换行符
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # 已读取到末尾
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        Get完整的控制台Log（一次性Get全部）
        
        Args:
            report_id: ReportID
            
        Returns:
            Log行List
        """
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Get Agent LogContent
        
        Args:
            report_id: ReportID
            from_line: 从第几行Start读取（用于增量Get，0 表示从头Start）
            
        Returns:
            {
                "logs": [Log条目List],
                "total_lines": 总行数,
                "from_line": 起始行号,
                "has_more": Whether还有更多Log
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # 跳过ParseFailed的行
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # 已读取到末尾
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Get完整的 Agent Log（用于一次性Get全部）
        
        Args:
            report_id: ReportID
            
        Returns:
            Log条目List
        """
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        SaveReport大纲
        
        在规划阶段Complete后立即Call
        """
        cls._ensure_report_folder(report_id)
        
        with open(cls._get_outline_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(outline.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"大纲已Save: {report_id}")
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection
    ) -> str:
        """
        Save单个章节

        在每个章节GenerateComplete后立即Call，实现分章节Output

        Args:
            report_id: ReportID
            section_index: 章节索引（从1Start）
            section: 章节对象

        Returns:
            Save的FilePath
        """
        cls._ensure_report_folder(report_id)

        # 构建章节MarkdownContent - Clean up可能存在的重复标题
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"

        # SaveFile
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)

        logger.info(f"章节已Save: {report_id}/{file_suffix}")
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        Clean up章节Content
        
        1. 移除Content开头与章节标题重复的Markdown标题行
        2. 将所有 ### 及以下级别的标题转换为粗体文本
        
        Args:
            content: 原始Content
            section_title: 章节标题
            
        Returns:
            Clean up后的Content
        """
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # 检查Whether是Markdown标题行
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # 检查Whether是与章节标题重复的标题（跳过前5行内的重复）
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # 将所有级别的标题（#, ##, ###, ####等）转换为粗体
                # 因为章节标题由系统添加，Content中不应有任何标题
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # 添加空行
                continue
            
            # If上一行是被跳过的标题，且当前行为空，也跳过
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # 移除开头的空行
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # 移除开头的分隔线
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # 同时移除分隔线后的空行
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls, 
        report_id: str, 
        status: str, 
        progress: int, 
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        UpdateReport generation进度
        
        前端可以通过读取progress.jsonGet实时进度
        """
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """GetReport generation进度"""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Get已Generate的章节List
        
        Return所有已Save的章节FileInformation
        """
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # 从File名Parse章节索引
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])

                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "content": content
                })

        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        组装完整Report
        
        从已Save的章节File组装完整Report，并进行标题Clean up
        """
        folder = cls._get_report_folder(report_id)
        
        # 构建Report头部
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # 按顺序读取所有章节File
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            md_content += section_info["content"]
        
        # 后Process：Clean up整个Report的标题问题
        md_content = cls._post_process_report(md_content, outline)
        
        # Save完整Report
        full_path = cls._get_report_markdown_path(report_id)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(f"完整Report已组装: {report_id}")
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        后ProcessReportContent
        
        1. 移除重复的标题
        2. 保留Report主标题(#)和章节标题(##)，移除其他级别的标题(###, ####等)
        3. Clean up多余的空行和分隔线
        
        Args:
            content: 原始ReportContent
            outline: Report大纲
            
        Returns:
            Process后的Content
        """
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # 收集大纲中的所有章节标题
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # 检查Whether是标题行
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # 检查Whether是重复标题（在连续5行内出现相同Content的标题）
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # 跳过重复标题及其后的空行
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # 标题层级Process：
                # - # (level=1) 只保留Report主标题
                # - ## (level=2) 保留章节标题
                # - ### 及以下 (level>=3) 转换为粗体文本
                
                if level == 1:
                    if title == outline.title:
                        # 保留Report主标题
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # 章节标题Error使用了#，修正为##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # 其他一级标题转为粗体
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # 保留章节标题
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # 非章节的二级标题转为粗体
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # ### 及以下级别的标题转换为粗体文本
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # 跳过标题后紧跟的分隔线
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # 标题后只保留一个空行
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # Clean up连续的多个空行（保留最多2个）
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """SaveReport元Information和完整Report"""
        cls._ensure_report_folder(report.report_id)
        
        # Save元InformationJSON
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        
        # Save大纲
        if report.outline:
            cls.save_outline(report.report_id, report.outline)
        
        # Save完整MarkdownReport
        if report.markdown_content:
            with open(cls._get_report_markdown_path(report.report_id), 'w', encoding='utf-8') as f:
                f.write(report.markdown_content)
        
        logger.info(f"Report已Save: {report.report_id}")
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """GetReport"""
        path = cls._get_report_path(report_id)
        
        if not os.path.exists(path):
            # 兼容旧Format：检查直接存储在reportsDirectory下的File
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # 重建Report对象
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', '')
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # Ifmarkdown_content为空，尝试从full_report.md读取
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """根据SimulationIDGetReport"""
        cls._ensure_reports_dir()
        
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # 新Format：File夹
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
            # 兼容旧Format：JSONFile
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    return report
        
        return None
    
    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """列出Report"""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # 新Format：File夹
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # 兼容旧Format：JSONFile
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # 按Create时间倒序
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """DeleteReport（整个File夹）"""
        import shutil
        
        folder_path = cls._get_report_folder(report_id)
        
        # 新Format：Delete整个File夹
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            logger.info(f"ReportFile夹已Delete: {report_id}")
            return True
        
        # 兼容旧Format：Delete单独的File
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")
        
        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True
        
        return deleted

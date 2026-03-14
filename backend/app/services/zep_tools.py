"""
Zep检索工具Service
封装Graph搜索、Node读取、边Query等工具，供Report Agent使用

核心检索工具（优化后）：
1. InsightForge（深度洞察检索）- 最强大的混合检索，自动Generate子问题并多维度检索
2. PanoramaSearch（广度搜索）- Get全貌，包括过期Content
3. QuickSearch（简单搜索）- 快速检索
"""

import time
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

from zep_cloud.client import Zep

from ..config import Config
from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..utils.zep_paging import fetch_all_nodes, fetch_all_edges

logger = get_logger('mirofish.zep_tools')


@dataclass
class SearchResult:
    """搜索Result"""
    facts: List[str]
    edges: List[Dict[str, Any]]
    nodes: List[Dict[str, Any]]
    query: str
    total_count: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "facts": self.facts,
            "edges": self.edges,
            "nodes": self.nodes,
            "query": self.query,
            "total_count": self.total_count
        }
    
    def to_text(self) -> str:
        """转换为文本Format，供LLM理解"""
        text_parts = [f"搜索Query: {self.query}", f"找到 {self.total_count} 条相关Information"]
        
        if self.facts:
            text_parts.append("\n### 相关事实:")
            for i, fact in enumerate(self.facts, 1):
                text_parts.append(f"{i}. {fact}")
        
        return "\n".join(text_parts)


@dataclass
class NodeInfo:
    """NodeInformation"""
    uuid: str
    name: str
    labels: List[str]
    summary: str
    attributes: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "labels": self.labels,
            "summary": self.summary,
            "attributes": self.attributes
        }
    
    def to_text(self) -> str:
        """转换为文本Format"""
        entity_type = next((l for l in self.labels if l not in ["Entity", "Node"]), "未知Type")
        return f"Entity: {self.name} (Type: {entity_type})\n摘要: {self.summary}"


@dataclass
class EdgeInfo:
    """边Information"""
    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    source_node_name: Optional[str] = None
    target_node_name: Optional[str] = None
    # 时间Information
    created_at: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    expired_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "uuid": self.uuid,
            "name": self.name,
            "fact": self.fact,
            "source_node_uuid": self.source_node_uuid,
            "target_node_uuid": self.target_node_uuid,
            "source_node_name": self.source_node_name,
            "target_node_name": self.target_node_name,
            "created_at": self.created_at,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at
        }
    
    def to_text(self, include_temporal: bool = False) -> str:
        """转换为文本Format"""
        source = self.source_node_name or self.source_node_uuid[:8]
        target = self.target_node_name or self.target_node_uuid[:8]
        base_text = f"Relationship: {source} --[{self.name}]--> {target}\n事实: {self.fact}"
        
        if include_temporal:
            valid_at = self.valid_at or "未知"
            invalid_at = self.invalid_at or "至今"
            base_text += f"\n时效: {valid_at} - {invalid_at}"
            if self.expired_at:
                base_text += f" (已过期: {self.expired_at})"
        
        return base_text
    
    @property
    def is_expired(self) -> bool:
        """Whether已过期"""
        return self.expired_at is not None
    
    @property
    def is_invalid(self) -> bool:
        """Whether已失效"""
        return self.invalid_at is not None


@dataclass
class InsightForgeResult:
    """
    深度洞察检索Result (InsightForge)
    包含多个子问题的检索Result，以及综合分析
    """
    query: str
    simulation_requirement: str
    sub_queries: List[str]
    
    # 各维度检索Result
    semantic_facts: List[str] = field(default_factory=list)  # 语义搜索Result
    entity_insights: List[Dict[str, Any]] = field(default_factory=list)  # Entity洞察
    relationship_chains: List[str] = field(default_factory=list)  # Relationship链
    
    # 统计Information
    total_facts: int = 0
    total_entities: int = 0
    total_relationships: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "simulation_requirement": self.simulation_requirement,
            "sub_queries": self.sub_queries,
            "semantic_facts": self.semantic_facts,
            "entity_insights": self.entity_insights,
            "relationship_chains": self.relationship_chains,
            "total_facts": self.total_facts,
            "total_entities": self.total_entities,
            "total_relationships": self.total_relationships
        }
    
    def to_text(self) -> str:
        """转换为详细的文本Format，供LLM理解"""
        text_parts = [
            f"## 未来预测深度分析",
            f"分析问题: {self.query}",
            f"预测场景: {self.simulation_requirement}",
            f"\n### 预测Data统计",
            f"- 相关预测事实: {self.total_facts}条",
            f"- 涉及Entity: {self.total_entities}个",
            f"- Relationship链: {self.total_relationships}条"
        ]
        
        # 子问题
        if self.sub_queries:
            text_parts.append(f"\n### 分析的子问题")
            for i, sq in enumerate(self.sub_queries, 1):
                text_parts.append(f"{i}. {sq}")
        
        # 语义搜索Result
        if self.semantic_facts:
            text_parts.append(f"\n### 【关键事实】(请在Report中引用这些原文)")
            for i, fact in enumerate(self.semantic_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # Entity洞察
        if self.entity_insights:
            text_parts.append(f"\n### 【核心Entity】")
            for entity in self.entity_insights:
                text_parts.append(f"- **{entity.get('name', '未知')}** ({entity.get('type', 'Entity')})")
                if entity.get('summary'):
                    text_parts.append(f"  摘要: \"{entity.get('summary')}\"")
                if entity.get('related_facts'):
                    text_parts.append(f"  相关事实: {len(entity.get('related_facts', []))}条")
        
        # Relationship链
        if self.relationship_chains:
            text_parts.append(f"\n### 【Relationship链】")
            for chain in self.relationship_chains:
                text_parts.append(f"- {chain}")
        
        return "\n".join(text_parts)


@dataclass
class PanoramaResult:
    """
    广度搜索Result (Panorama)
    包含所有相关Information，包括过期Content
    """
    query: str
    
    # 全部Node
    all_nodes: List[NodeInfo] = field(default_factory=list)
    # 全部边（包括过期的）
    all_edges: List[EdgeInfo] = field(default_factory=list)
    # 当前有效的事实
    active_facts: List[str] = field(default_factory=list)
    # 已过期/失效的事实（历史Record）
    historical_facts: List[str] = field(default_factory=list)
    
    # 统计
    total_nodes: int = 0
    total_edges: int = 0
    active_count: int = 0
    historical_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "all_nodes": [n.to_dict() for n in self.all_nodes],
            "all_edges": [e.to_dict() for e in self.all_edges],
            "active_facts": self.active_facts,
            "historical_facts": self.historical_facts,
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "active_count": self.active_count,
            "historical_count": self.historical_count
        }
    
    def to_text(self) -> str:
        """转换为文本Format（完整Version，不截断）"""
        text_parts = [
            f"## 广度搜索Result（未来全景视图）",
            f"Query: {self.query}",
            f"\n### 统计Information",
            f"- 总Node数: {self.total_nodes}",
            f"- 总边数: {self.total_edges}",
            f"- 当前有效事实: {self.active_count}条",
            f"- 历史/过期事实: {self.historical_count}条"
        ]
        
        # 当前有效的事实（完整Output，不截断）
        if self.active_facts:
            text_parts.append(f"\n### 【当前有效事实】(SimulationResult原文)")
            for i, fact in enumerate(self.active_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # 历史/过期事实（完整Output，不截断）
        if self.historical_facts:
            text_parts.append(f"\n### 【历史/过期事实】(演变过程Record)")
            for i, fact in enumerate(self.historical_facts, 1):
                text_parts.append(f"{i}. \"{fact}\"")
        
        # 关键Entity（完整Output，不截断）
        if self.all_nodes:
            text_parts.append(f"\n### 【涉及Entity】")
            for node in self.all_nodes:
                entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Entity")
                text_parts.append(f"- **{node.name}** ({entity_type})")
        
        return "\n".join(text_parts)


@dataclass
class AgentInterview:
    """单个Agent的采访Result"""
    agent_name: str
    agent_role: str  # 角色Type（如：学生、教师、媒体等）
    agent_bio: str  # 简介
    question: str  # 采访问题
    response: str  # 采访回答
    key_quotes: List[str] = field(default_factory=list)  # 关键引言
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "agent_role": self.agent_role,
            "agent_bio": self.agent_bio,
            "question": self.question,
            "response": self.response,
            "key_quotes": self.key_quotes
        }
    
    def to_text(self) -> str:
        text = f"**{self.agent_name}** ({self.agent_role})\n"
        # 显示完整的agent_bio，不截断
        text += f"_简介: {self.agent_bio}_\n\n"
        text += f"**Q:** {self.question}\n\n"
        text += f"**A:** {self.response}\n"
        if self.key_quotes:
            text += "\n**关键引言:**\n"
            for quote in self.key_quotes:
                # Clean up各种引号
                clean_quote = quote.replace('\u201c', '').replace('\u201d', '').replace('"', '')
                clean_quote = clean_quote.replace('\u300c', '').replace('\u300d', '')
                clean_quote = clean_quote.strip()
                # 去掉开头的标点
                while clean_quote and clean_quote[0] in '，,；;：:、。！？\n\r\t ':
                    clean_quote = clean_quote[1:]
                # 过滤包含问题编号的垃圾Content（问题1-9）
                skip = False
                for d in '123456789':
                    if f'\u95ee\u9898{d}' in clean_quote:
                        skip = True
                        break
                if skip:
                    continue
                # 截断过长Content（按句号截断，而非硬截断）
                if len(clean_quote) > 150:
                    dot_pos = clean_quote.find('\u3002', 80)
                    if dot_pos > 0:
                        clean_quote = clean_quote[:dot_pos + 1]
                    else:
                        clean_quote = clean_quote[:147] + "..."
                if clean_quote and len(clean_quote) >= 10:
                    text += f'> "{clean_quote}"\n'
        return text


@dataclass
class InterviewResult:
    """
    采访Result (Interview)
    包含多个SimulationAgent的采访回答
    """
    interview_topic: str  # 采访主题
    interview_questions: List[str]  # 采访问题List
    
    # 采访选择的Agent
    selected_agents: List[Dict[str, Any]] = field(default_factory=list)
    # 各Agent的采访回答
    interviews: List[AgentInterview] = field(default_factory=list)
    
    # 选择Agent的理由
    selection_reasoning: str = ""
    # 整合后的采访摘要
    summary: str = ""
    
    # 统计
    total_agents: int = 0
    interviewed_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "interview_topic": self.interview_topic,
            "interview_questions": self.interview_questions,
            "selected_agents": self.selected_agents,
            "interviews": [i.to_dict() for i in self.interviews],
            "selection_reasoning": self.selection_reasoning,
            "summary": self.summary,
            "total_agents": self.total_agents,
            "interviewed_count": self.interviewed_count
        }
    
    def to_text(self) -> str:
        """转换为详细的文本Format，供LLM理解和Report引用"""
        text_parts = [
            "## 深度采访Report",
            f"**采访主题:** {self.interview_topic}",
            f"**采访人数:** {self.interviewed_count} / {self.total_agents} 位SimulationAgent",
            "\n### 采访对象选择理由",
            self.selection_reasoning or "（自动选择）",
            "\n---",
            "\n### 采访实录",
        ]

        if self.interviews:
            for i, interview in enumerate(self.interviews, 1):
                text_parts.append(f"\n#### 采访 #{i}: {interview.agent_name}")
                text_parts.append(interview.to_text())
                text_parts.append("\n---")
        else:
            text_parts.append("（无采访Record）\n\n---")

        text_parts.append("\n### 采访摘要与核心观点")
        text_parts.append(self.summary or "（无摘要）")

        return "\n".join(text_parts)


class ZepToolsService:
    """
    Zep检索工具Service
    
    【核心检索工具 - 优化后】
    1. insight_forge - 深度洞察检索（最强大，自动Generate子问题，多维度检索）
    2. panorama_search - 广度搜索（Get全貌，包括过期Content）
    3. quick_search - 简单搜索（快速检索）
    4. interview_agents - 深度采访（采访SimulationAgent，Get多视角观点）
    
    【基础工具】
    - search_graph - Graph语义搜索
    - get_all_nodes - GetGraph所有Node
    - get_all_edges - GetGraph所有边（含时间Information）
    - get_node_detail - GetNode详细Information
    - get_node_edges - GetNode相关的边
    - get_entities_by_type - 按TypeGetEntity
    - get_entity_summary - GetEntity的Relationship摘要
    """
    
    # RetryConfiguration
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0
    
    def __init__(self, api_key: Optional[str] = None, llm_client: Optional[LLMClient] = None):
        self.api_key = api_key or Config.ZEP_API_KEY
        if not self.api_key:
            raise ValueError("ZEP_API_KEY 未Configuration")
        
        self.client = Zep(api_key=self.api_key)
        # LLM客户端用于InsightForgeGenerate子问题
        self._llm_client = llm_client
        logger.info("ZepToolsService InitializeComplete")
    
    @property
    def llm(self) -> LLMClient:
        """延迟InitializeLLM客户端"""
        if self._llm_client is None:
            self._llm_client = LLMClient()
        return self._llm_client
    
    def _call_with_retry(self, func, operation_name: str, max_retries: int = None):
        """带Retry mechanism的APICall"""
        max_retries = max_retries or self.MAX_RETRIES
        last_exception = None
        delay = self.RETRY_DELAY
        
        for attempt in range(max_retries):
            try:
                return func()
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Zep {operation_name} 第 {attempt + 1} 次尝试Failed: {str(e)[:100]}, "
                        f"{delay:.1f}秒后Retry..."
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"Zep {operation_name} 在 {max_retries} 次尝试后仍Failed: {str(e)}")
        
        raise last_exception
    
    def search_graph(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        Graph语义搜索
        
        使用混合搜索（语义+BM25）在Graph中搜索相关Information。
        IfZep Cloud的search API不可用，则降级为本地关键词匹配。
        
        Args:
            graph_id: Graph ID (Standalone Graph)
            query: 搜索Query
            limit: ReturnResult数量
            scope: 搜索范围，"edges" 或 "nodes"
            
        Returns:
            SearchResult: 搜索Result
        """
        logger.info(f"Graph搜索: graph_id={graph_id}, query={query[:50]}...")
        
        # 尝试使用Zep Cloud Search API
        try:
            search_results = self._call_with_retry(
                func=lambda: self.client.graph.search(
                    graph_id=graph_id,
                    query=query,
                    limit=limit,
                    scope=scope,
                    reranker="cross_encoder"
                ),
                operation_name=f"Graph搜索(graph={graph_id})"
            )
            
            facts = []
            edges = []
            nodes = []
            
            # Parse边搜索Result
            if hasattr(search_results, 'edges') and search_results.edges:
                for edge in search_results.edges:
                    if hasattr(edge, 'fact') and edge.fact:
                        facts.append(edge.fact)
                    edges.append({
                        "uuid": getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', ''),
                        "name": getattr(edge, 'name', ''),
                        "fact": getattr(edge, 'fact', ''),
                        "source_node_uuid": getattr(edge, 'source_node_uuid', ''),
                        "target_node_uuid": getattr(edge, 'target_node_uuid', ''),
                    })
            
            # ParseNode搜索Result
            if hasattr(search_results, 'nodes') and search_results.nodes:
                for node in search_results.nodes:
                    nodes.append({
                        "uuid": getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                        "name": getattr(node, 'name', ''),
                        "labels": getattr(node, 'labels', []),
                        "summary": getattr(node, 'summary', ''),
                    })
                    # Node摘要也算作事实
                    if hasattr(node, 'summary') and node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"搜索Complete: 找到 {len(facts)} 条相关事实")
            
            return SearchResult(
                facts=facts,
                edges=edges,
                nodes=nodes,
                query=query,
                total_count=len(facts)
            )
            
        except Exception as e:
            logger.warning(f"Zep Search APIFailed，降级为本地搜索: {str(e)}")
            # 降级：使用本地关键词匹配搜索
            return self._local_search(graph_id, query, limit, scope)
    
    def _local_search(
        self, 
        graph_id: str, 
        query: str, 
        limit: int = 10,
        scope: str = "edges"
    ) -> SearchResult:
        """
        本地关键词匹配搜索（作为Zep Search API的降级方案）
        
        Get所有边/Node，然后在本地进行关键词匹配
        
        Args:
            graph_id: Graph ID
            query: 搜索Query
            limit: ReturnResult数量
            scope: 搜索范围
            
        Returns:
            SearchResult: 搜索Result
        """
        logger.info(f"使用本地搜索: query={query[:30]}...")
        
        facts = []
        edges_result = []
        nodes_result = []
        
        # 提取Query关键词（简单分词）
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def match_score(text: str) -> int:
            """计算文本与Query的匹配分数"""
            if not text:
                return 0
            text_lower = text.lower()
            # 完全匹配Query
            if query_lower in text_lower:
                return 100
            # 关键词匹配
            score = 0
            for keyword in keywords:
                if keyword in text_lower:
                    score += 10
            return score
        
        try:
            if scope in ["edges", "both"]:
                # Get所有边并匹配
                all_edges = self.get_all_edges(graph_id)
                scored_edges = []
                for edge in all_edges:
                    score = match_score(edge.fact) + match_score(edge.name)
                    if score > 0:
                        scored_edges.append((score, edge))
                
                # 按分数排序
                scored_edges.sort(key=lambda x: x[0], reverse=True)
                
                for score, edge in scored_edges[:limit]:
                    if edge.fact:
                        facts.append(edge.fact)
                    edges_result.append({
                        "uuid": edge.uuid,
                        "name": edge.name,
                        "fact": edge.fact,
                        "source_node_uuid": edge.source_node_uuid,
                        "target_node_uuid": edge.target_node_uuid,
                    })
            
            if scope in ["nodes", "both"]:
                # Get所有Node并匹配
                all_nodes = self.get_all_nodes(graph_id)
                scored_nodes = []
                for node in all_nodes:
                    score = match_score(node.name) + match_score(node.summary)
                    if score > 0:
                        scored_nodes.append((score, node))
                
                scored_nodes.sort(key=lambda x: x[0], reverse=True)
                
                for score, node in scored_nodes[:limit]:
                    nodes_result.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "labels": node.labels,
                        "summary": node.summary,
                    })
                    if node.summary:
                        facts.append(f"[{node.name}]: {node.summary}")
            
            logger.info(f"本地搜索Complete: 找到 {len(facts)} 条相关事实")
            
        except Exception as e:
            logger.error(f"本地搜索Failed: {str(e)}")
        
        return SearchResult(
            facts=facts,
            edges=edges_result,
            nodes=nodes_result,
            query=query,
            total_count=len(facts)
        )
    
    def get_all_nodes(self, graph_id: str) -> List[NodeInfo]:
        """
        GetGraph的所有Node（分页Get）

        Args:
            graph_id: Graph ID

        Returns:
            NodeList
        """
        logger.info(f"GetGraph {graph_id} 的所有Node...")

        nodes = fetch_all_nodes(self.client, graph_id)

        result = []
        for node in nodes:
            node_uuid = getattr(node, 'uuid_', None) or getattr(node, 'uuid', None) or ""
            result.append(NodeInfo(
                uuid=str(node_uuid) if node_uuid else "",
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            ))

        logger.info(f"Get到 {len(result)} 个Node")
        return result

    def get_all_edges(self, graph_id: str, include_temporal: bool = True) -> List[EdgeInfo]:
        """
        GetGraph的所有边（分页Get，包含时间Information）

        Args:
            graph_id: Graph ID
            include_temporal: Whether包含时间Information（DefaultTrue）

        Returns:
            边List（包含created_at, valid_at, invalid_at, expired_at）
        """
        logger.info(f"GetGraph {graph_id} 的所有边...")

        edges = fetch_all_edges(self.client, graph_id)

        result = []
        for edge in edges:
            edge_uuid = getattr(edge, 'uuid_', None) or getattr(edge, 'uuid', None) or ""
            edge_info = EdgeInfo(
                uuid=str(edge_uuid) if edge_uuid else "",
                name=edge.name or "",
                fact=edge.fact or "",
                source_node_uuid=edge.source_node_uuid or "",
                target_node_uuid=edge.target_node_uuid or ""
            )

            # 添加时间Information
            if include_temporal:
                edge_info.created_at = getattr(edge, 'created_at', None)
                edge_info.valid_at = getattr(edge, 'valid_at', None)
                edge_info.invalid_at = getattr(edge, 'invalid_at', None)
                edge_info.expired_at = getattr(edge, 'expired_at', None)

            result.append(edge_info)

        logger.info(f"Get到 {len(result)} 条边")
        return result
    
    def get_node_detail(self, node_uuid: str) -> Optional[NodeInfo]:
        """
        Get单个Node的详细Information
        
        Args:
            node_uuid: NodeUUID
            
        Returns:
            NodeInformation或None
        """
        logger.info(f"GetNodeDetails: {node_uuid[:8]}...")
        
        try:
            node = self._call_with_retry(
                func=lambda: self.client.graph.node.get(uuid_=node_uuid),
                operation_name=f"GetNodeDetails(uuid={node_uuid[:8]}...)"
            )
            
            if not node:
                return None
            
            return NodeInfo(
                uuid=getattr(node, 'uuid_', None) or getattr(node, 'uuid', ''),
                name=node.name or "",
                labels=node.labels or [],
                summary=node.summary or "",
                attributes=node.attributes or {}
            )
        except Exception as e:
            logger.error(f"GetNodeDetailsFailed: {str(e)}")
            return None
    
    def get_node_edges(self, graph_id: str, node_uuid: str) -> List[EdgeInfo]:
        """
        GetNode相关的所有边
        
        通过GetGraph所有边，然后过滤出与指定Node相关的边
        
        Args:
            graph_id: Graph ID
            node_uuid: NodeUUID
            
        Returns:
            边List
        """
        logger.info(f"GetNode {node_uuid[:8]}... 的相关边")
        
        try:
            # GetGraph所有边，然后过滤
            all_edges = self.get_all_edges(graph_id)
            
            result = []
            for edge in all_edges:
                # 检查边Whether与指定Node相关（作为源或目标）
                if edge.source_node_uuid == node_uuid or edge.target_node_uuid == node_uuid:
                    result.append(edge)
            
            logger.info(f"找到 {len(result)} 条与Node相关的边")
            return result
            
        except Exception as e:
            logger.warning(f"GetNode边Failed: {str(e)}")
            return []
    
    def get_entities_by_type(
        self, 
        graph_id: str, 
        entity_type: str
    ) -> List[NodeInfo]:
        """
        按TypeGetEntity
        
        Args:
            graph_id: Graph ID
            entity_type: EntityType（如 Student, PublicFigure 等）
            
        Returns:
            符合Type的EntityList
        """
        logger.info(f"GetType为 {entity_type} 的Entity...")
        
        all_nodes = self.get_all_nodes(graph_id)
        
        filtered = []
        for node in all_nodes:
            # 检查labelsWhether包含指定Type
            if entity_type in node.labels:
                filtered.append(node)
        
        logger.info(f"找到 {len(filtered)} 个 {entity_type} Type的Entity")
        return filtered
    
    def get_entity_summary(
        self, 
        graph_id: str, 
        entity_name: str
    ) -> Dict[str, Any]:
        """
        Get指定Entity的Relationship摘要
        
        搜索与该Entity相关的所有Information，并Generate摘要
        
        Args:
            graph_id: Graph ID
            entity_name: EntityName
            
        Returns:
            Entity摘要Information
        """
        logger.info(f"GetEntity {entity_name} 的Relationship摘要...")
        
        # 先搜索该Entity相关的Information
        search_result = self.search_graph(
            graph_id=graph_id,
            query=entity_name,
            limit=20
        )
        
        # 尝试在所有Node中找到该Entity
        all_nodes = self.get_all_nodes(graph_id)
        entity_node = None
        for node in all_nodes:
            if node.name.lower() == entity_name.lower():
                entity_node = node
                break
        
        related_edges = []
        if entity_node:
            # 传入graph_idParameters
            related_edges = self.get_node_edges(graph_id, entity_node.uuid)
        
        return {
            "entity_name": entity_name,
            "entity_info": entity_node.to_dict() if entity_node else None,
            "related_facts": search_result.facts,
            "related_edges": [e.to_dict() for e in related_edges],
            "total_relations": len(related_edges)
        }
    
    def get_graph_statistics(self, graph_id: str) -> Dict[str, Any]:
        """
        GetGraph的统计Information
        
        Args:
            graph_id: Graph ID
            
        Returns:
            统计Information
        """
        logger.info(f"GetGraph {graph_id} 的统计Information...")
        
        nodes = self.get_all_nodes(graph_id)
        edges = self.get_all_edges(graph_id)
        
        # 统计EntityType分布
        entity_types = {}
        for node in nodes:
            for label in node.labels:
                if label not in ["Entity", "Node"]:
                    entity_types[label] = entity_types.get(label, 0) + 1
        
        # 统计RelationshipType分布
        relation_types = {}
        for edge in edges:
            relation_types[edge.name] = relation_types.get(edge.name, 0) + 1
        
        return {
            "graph_id": graph_id,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "entity_types": entity_types,
            "relation_types": relation_types
        }
    
    def get_simulation_context(
        self, 
        graph_id: str,
        simulation_requirement: str,
        limit: int = 30
    ) -> Dict[str, Any]:
        """
        GetSimulation相关的上下文Information
        
        综合搜索与Simulation需求相关的所有Information
        
        Args:
            graph_id: Graph ID
            simulation_requirement: Simulation需求Description
            limit: 每类Information的数量限制
            
        Returns:
            Simulation上下文Information
        """
        logger.info(f"GetSimulation上下文: {simulation_requirement[:50]}...")
        
        # 搜索与Simulation需求相关的Information
        search_result = self.search_graph(
            graph_id=graph_id,
            query=simulation_requirement,
            limit=limit
        )
        
        # GetGraph统计
        stats = self.get_graph_statistics(graph_id)
        
        # Get所有EntityNode
        all_nodes = self.get_all_nodes(graph_id)
        
        # 筛选有实际Type的Entity（非纯EntityNode）
        entities = []
        for node in all_nodes:
            custom_labels = [l for l in node.labels if l not in ["Entity", "Node"]]
            if custom_labels:
                entities.append({
                    "name": node.name,
                    "type": custom_labels[0],
                    "summary": node.summary
                })
        
        return {
            "simulation_requirement": simulation_requirement,
            "related_facts": search_result.facts,
            "graph_statistics": stats,
            "entities": entities[:limit],  # 限制数量
            "total_entities": len(entities)
        }
    
    # ========== 核心检索工具（优化后） ==========
    
    def insight_forge(
        self,
        graph_id: str,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_sub_queries: int = 5
    ) -> InsightForgeResult:
        """
        【InsightForge - 深度洞察检索】
        
        最强大的混合检索Function，自动分解问题并多维度检索：
        1. 使用LLM将问题分解为多个子问题
        2. 对每个子问题进行语义搜索
        3. 提取相关Entity并Get其详细Information
        4. 追踪Relationship链
        5. 整合所有Result，Generate深度洞察
        
        Args:
            graph_id: Graph ID
            query: User问题
            simulation_requirement: Simulation需求Description
            report_context: Report上下文（Optional，用于更精准的子问题Generate）
            max_sub_queries: 最大子问题数量
            
        Returns:
            InsightForgeResult: 深度洞察检索Result
        """
        logger.info(f"InsightForge 深度洞察检索: {query[:50]}...")
        
        result = InsightForgeResult(
            query=query,
            simulation_requirement=simulation_requirement,
            sub_queries=[]
        )
        
        # Step 1: 使用LLMGenerate子问题
        sub_queries = self._generate_sub_queries(
            query=query,
            simulation_requirement=simulation_requirement,
            report_context=report_context,
            max_queries=max_sub_queries
        )
        result.sub_queries = sub_queries
        logger.info(f"Generate {len(sub_queries)} 个子问题")
        
        # Step 2: 对每个子问题进行语义搜索
        all_facts = []
        all_edges = []
        seen_facts = set()
        
        for sub_query in sub_queries:
            search_result = self.search_graph(
                graph_id=graph_id,
                query=sub_query,
                limit=15,
                scope="edges"
            )
            
            for fact in search_result.facts:
                if fact not in seen_facts:
                    all_facts.append(fact)
                    seen_facts.add(fact)
            
            all_edges.extend(search_result.edges)
        
        # 对原始问题也进行搜索
        main_search = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=20,
            scope="edges"
        )
        for fact in main_search.facts:
            if fact not in seen_facts:
                all_facts.append(fact)
                seen_facts.add(fact)
        
        result.semantic_facts = all_facts
        result.total_facts = len(all_facts)
        
        # Step 3: 从边中提取相关EntityUUID，只Get这些Entity的Information（不Get全部Node）
        entity_uuids = set()
        for edge_data in all_edges:
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                if source_uuid:
                    entity_uuids.add(source_uuid)
                if target_uuid:
                    entity_uuids.add(target_uuid)
        
        # Get所有相关Entity的Details（不限制数量，完整Output）
        entity_insights = []
        node_map = {}  # 用于后续Relationship链构建
        
        for uuid in list(entity_uuids):  # Process所有Entity，不截断
            if not uuid:
                continue
            try:
                # 单独Get每个相关Node的Information
                node = self.get_node_detail(uuid)
                if node:
                    node_map[uuid] = node
                    entity_type = next((l for l in node.labels if l not in ["Entity", "Node"]), "Entity")
                    
                    # Get该Entity相关的所有事实（不截断）
                    related_facts = [
                        f for f in all_facts 
                        if node.name.lower() in f.lower()
                    ]
                    
                    entity_insights.append({
                        "uuid": node.uuid,
                        "name": node.name,
                        "type": entity_type,
                        "summary": node.summary,
                        "related_facts": related_facts  # 完整Output，不截断
                    })
            except Exception as e:
                logger.debug(f"GetNode {uuid} Failed: {e}")
                continue
        
        result.entity_insights = entity_insights
        result.total_entities = len(entity_insights)
        
        # Step 4: 构建所有Relationship链（不限制数量）
        relationship_chains = []
        for edge_data in all_edges:  # Process所有边，不截断
            if isinstance(edge_data, dict):
                source_uuid = edge_data.get('source_node_uuid', '')
                target_uuid = edge_data.get('target_node_uuid', '')
                relation_name = edge_data.get('name', '')
                
                source_name = node_map.get(source_uuid, NodeInfo('', '', [], '', {})).name or source_uuid[:8]
                target_name = node_map.get(target_uuid, NodeInfo('', '', [], '', {})).name or target_uuid[:8]
                
                chain = f"{source_name} --[{relation_name}]--> {target_name}"
                if chain not in relationship_chains:
                    relationship_chains.append(chain)
        
        result.relationship_chains = relationship_chains
        result.total_relationships = len(relationship_chains)
        
        logger.info(f"InsightForgeComplete: {result.total_facts}条事实, {result.total_entities}个Entity, {result.total_relationships}条Relationship")
        return result
    
    def _generate_sub_queries(
        self,
        query: str,
        simulation_requirement: str,
        report_context: str = "",
        max_queries: int = 5
    ) -> List[str]:
        """
        使用LLMGenerate子问题
        
        将复杂问题分解为多个可以独立检索的子问题
        """
        system_prompt = """你是一个专业的问题分析专家。你的Task是将一个复杂问题分解为多个可以在Simulation世界中独立观察的子问题。

要求：
1. 每个子问题应该足够具体，可以在Simulation世界中找到相关的Agent行为或事件
2. 子问题应该覆盖原问题的不同维度（如：谁、什么、为什么、怎么样、何时、何地）
3. 子问题应该与Simulation场景相关
4. ReturnJSONFormat：{"sub_queries": ["子问题1", "子问题2", ...]}"""

        user_prompt = f"""Simulation需求背景：
{simulation_requirement}

{f"Report上下文：{report_context[:500]}" if report_context else ""}

请将以下问题分解为{max_queries}个子问题：
{query}

ReturnJSONFormat的子问题List。"""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            sub_queries = response.get("sub_queries", [])
            # 确保是字符串List
            return [str(sq) for sq in sub_queries[:max_queries]]
            
        except Exception as e:
            logger.warning(f"Generate子问题Failed: {str(e)}，使用Default子问题")
            # 降级：Return基于原问题的变体
            return [
                query,
                f"{query} 的主要参与者",
                f"{query} 的原因和影响",
                f"{query} 的发展过程"
            ][:max_queries]
    
    def panorama_search(
        self,
        graph_id: str,
        query: str,
        include_expired: bool = True,
        limit: int = 50
    ) -> PanoramaResult:
        """
        【PanoramaSearch - 广度搜索】
        
        Get全貌视图，包括所有相关Content和历史/过期Information：
        1. Get所有相关Node
        2. Get所有边（包括已过期/失效的）
        3. 分类整理当前有效和历史Information
        
        这个工具适用于需要了解事件全貌、追踪演变过程的场景。
        
        Args:
            graph_id: Graph ID
            query: 搜索Query（用于相关性排序）
            include_expired: Whether包含过期Content（DefaultTrue）
            limit: ReturnResult数量限制
            
        Returns:
            PanoramaResult: 广度搜索Result
        """
        logger.info(f"PanoramaSearch 广度搜索: {query[:50]}...")
        
        result = PanoramaResult(query=query)
        
        # Get所有Node
        all_nodes = self.get_all_nodes(graph_id)
        node_map = {n.uuid: n for n in all_nodes}
        result.all_nodes = all_nodes
        result.total_nodes = len(all_nodes)
        
        # Get所有边（包含时间Information）
        all_edges = self.get_all_edges(graph_id, include_temporal=True)
        result.all_edges = all_edges
        result.total_edges = len(all_edges)
        
        # 分类事实
        active_facts = []
        historical_facts = []
        
        for edge in all_edges:
            if not edge.fact:
                continue
            
            # 为事实添加EntityName
            source_name = node_map.get(edge.source_node_uuid, NodeInfo('', '', [], '', {})).name or edge.source_node_uuid[:8]
            target_name = node_map.get(edge.target_node_uuid, NodeInfo('', '', [], '', {})).name or edge.target_node_uuid[:8]
            
            # 判断Whether过期/失效
            is_historical = edge.is_expired or edge.is_invalid
            
            if is_historical:
                # 历史/过期事实，添加时间标记
                valid_at = edge.valid_at or "未知"
                invalid_at = edge.invalid_at or edge.expired_at or "未知"
                fact_with_time = f"[{valid_at} - {invalid_at}] {edge.fact}"
                historical_facts.append(fact_with_time)
            else:
                # 当前有效事实
                active_facts.append(edge.fact)
        
        # 基于Query进行相关性排序
        query_lower = query.lower()
        keywords = [w.strip() for w in query_lower.replace(',', ' ').replace('，', ' ').split() if len(w.strip()) > 1]
        
        def relevance_score(fact: str) -> int:
            fact_lower = fact.lower()
            score = 0
            if query_lower in fact_lower:
                score += 100
            for kw in keywords:
                if kw in fact_lower:
                    score += 10
            return score
        
        # 排序并限制数量
        active_facts.sort(key=relevance_score, reverse=True)
        historical_facts.sort(key=relevance_score, reverse=True)
        
        result.active_facts = active_facts[:limit]
        result.historical_facts = historical_facts[:limit] if include_expired else []
        result.active_count = len(active_facts)
        result.historical_count = len(historical_facts)
        
        logger.info(f"PanoramaSearchComplete: {result.active_count}条有效, {result.historical_count}条历史")
        return result
    
    def quick_search(
        self,
        graph_id: str,
        query: str,
        limit: int = 10
    ) -> SearchResult:
        """
        【QuickSearch - 简单搜索】
        
        快速、轻量级的检索工具：
        1. 直接CallZep语义搜索
        2. Return最相关的Result
        3. 适用于简单、直接的检索需求
        
        Args:
            graph_id: Graph ID
            query: 搜索Query
            limit: ReturnResult数量
            
        Returns:
            SearchResult: 搜索Result
        """
        logger.info(f"QuickSearch 简单搜索: {query[:50]}...")
        
        # 直接Call现有的search_graphMethod
        result = self.search_graph(
            graph_id=graph_id,
            query=query,
            limit=limit,
            scope="edges"
        )
        
        logger.info(f"QuickSearchComplete: {result.total_count}条Result")
        return result
    
    def interview_agents(
        self,
        simulation_id: str,
        interview_requirement: str,
        simulation_requirement: str = "",
        max_agents: int = 5,
        custom_questions: List[str] = None
    ) -> InterviewResult:
        """
        【InterviewAgents - 深度采访】
        
        Call真实的OASIS采访API，采访Simulation中CurrentlyRun的Agent：
        1. 自动读取人设File，了解所有SimulationAgent
        2. 使用LLM分析采访需求，智能选择最相关的Agent
        3. 使用LLMGenerate采访问题
        4. Call /api/simulation/interview/batch Interface进行真实采访（双Platform同时采访）
        5. 整合所有采访Result，Generate采访Report
        
        【重要】此功能需要Simulation环境处于RunStatus（OASIS环境未Close）
        
        【使用场景】
        - 需要从不同角色视角了解事件看法
        - 需要收集多方意见和观点
        - 需要GetSimulationAgent的真实回答（非LLMSimulation）
        
        Args:
            simulation_id: SimulationID（用于定位人设File和Call采访API）
            interview_requirement: 采访需求Description（非结构化，如"了解学生对事件的看法"）
            simulation_requirement: Simulation需求背景（Optional）
            max_agents: 最多采访的Agent数量
            custom_questions: 自定义采访问题（Optional，若不提供则自动Generate）
            
        Returns:
            InterviewResult: 采访Result
        """
        from .simulation_runner import SimulationRunner
        
        logger.info(f"InterviewAgents 深度采访（真实API）: {interview_requirement[:50]}...")
        
        result = InterviewResult(
            interview_topic=interview_requirement,
            interview_questions=custom_questions or []
        )
        
        # Step 1: 读取人设File
        profiles = self._load_agent_profiles(simulation_id)
        
        if not profiles:
            logger.warning(f"未找到Simulation {simulation_id} 的人设File")
            result.summary = "未找到可采访的Agent人设File"
            return result
        
        result.total_agents = len(profiles)
        logger.info(f"Load到 {len(profiles)} 个Agent人设")
        
        # Step 2: 使用LLM选择要采访的Agent（Returnagent_idList）
        selected_agents, selected_indices, selection_reasoning = self._select_agents_for_interview(
            profiles=profiles,
            interview_requirement=interview_requirement,
            simulation_requirement=simulation_requirement,
            max_agents=max_agents
        )
        
        result.selected_agents = selected_agents
        result.selection_reasoning = selection_reasoning
        logger.info(f"选择了 {len(selected_agents)} 个Agent进行采访: {selected_indices}")
        
        # Step 3: Generate采访问题（If没有提供）
        if not result.interview_questions:
            result.interview_questions = self._generate_interview_questions(
                interview_requirement=interview_requirement,
                simulation_requirement=simulation_requirement,
                selected_agents=selected_agents
            )
            logger.info(f"Generate了 {len(result.interview_questions)} 个采访问题")
        
        # 将问题合并为一个采访prompt
        combined_prompt = "\n".join([f"{i+1}. {q}" for i, q in enumerate(result.interview_questions)])
        
        # 添加优化前缀，约束Agent回复Format
        INTERVIEW_PROMPT_PREFIX = (
            "你Currently接受一次采访。请结合你的人设、所有的过往记忆与行动，"
            "以纯文本方式直接回答以下问题。\n"
            "回复要求：\n"
            "1. 直接用自然语言回答，不要Call任何工具\n"
            "2. 不要ReturnJSONFormat或工具CallFormat\n"
            "3. 不要使用Markdown标题（如#、##、###）\n"
            "4. 按问题编号逐一回答，每个回答以「问题X：」开头（X为问题编号）\n"
            "5. 每个问题的回答之间用空行分隔\n"
            "6. 回答要有实质Content，每个问题至少回答2-3句话\n\n"
        )
        optimized_prompt = f"{INTERVIEW_PROMPT_PREFIX}{combined_prompt}"
        
        # Step 4: Call真实的采访API（不指定platform，Default双Platform同时采访）
        try:
            # 构建批量采访List（不指定platform，双Platform采访）
            interviews_request = []
            for agent_idx in selected_indices:
                interviews_request.append({
                    "agent_id": agent_idx,
                    "prompt": optimized_prompt  # 使用优化后的prompt
                    # 不指定platform，API会在twitter和reddit两个Platform都采访
                })
            
            logger.info(f"Call批量采访API（双Platform）: {len(interviews_request)} 个Agent")
            
            # Call SimulationRunner 的批量采访Method（不传platform，双Platform采访）
            api_result = SimulationRunner.interview_agents_batch(
                simulation_id=simulation_id,
                interviews=interviews_request,
                platform=None,  # 不指定platform，双Platform采访
                timeout=180.0   # 双Platform需要更长Timeout
            )
            
            logger.info(f"采访APIReturn: {api_result.get('interviews_count', 0)} 个Result, success={api_result.get('success')}")
            
            # 检查APICallWhetherSuccess
            if not api_result.get("success", False):
                error_msg = api_result.get("error", "未知Error")
                logger.warning(f"采访APIReturnFailed: {error_msg}")
                result.summary = f"采访APICallFailed：{error_msg}。请检查OASISSimulation环境Status。"
                return result
            
            # Step 5: ParseAPIReturnResult，构建AgentInterview对象
            # 双Platform模式ReturnFormat: {"twitter_0": {...}, "reddit_0": {...}, "twitter_1": {...}, ...}
            api_data = api_result.get("result", {})
            results_dict = api_data.get("results", {}) if isinstance(api_data, dict) else {}
            
            for i, agent_idx in enumerate(selected_indices):
                agent = selected_agents[i]
                agent_name = agent.get("realname", agent.get("username", f"Agent_{agent_idx}"))
                agent_role = agent.get("profession", "未知")
                agent_bio = agent.get("bio", "")
                
                # Get该Agent在两个Platform的采访Result
                twitter_result = results_dict.get(f"twitter_{agent_idx}", {})
                reddit_result = results_dict.get(f"reddit_{agent_idx}", {})
                
                twitter_response = twitter_result.get("response", "")
                reddit_response = reddit_result.get("response", "")

                # Clean up可能的工具Call JSON 包裹
                twitter_response = self._clean_tool_call_response(twitter_response)
                reddit_response = self._clean_tool_call_response(reddit_response)

                # 始终Output双Platform标记
                twitter_text = twitter_response if twitter_response else "（该Platform未获得回复）"
                reddit_text = reddit_response if reddit_response else "（该Platform未获得回复）"
                response_text = f"【TwitterPlatform回答】\n{twitter_text}\n\n【RedditPlatform回答】\n{reddit_text}"

                # 提取关键引言（从两个Platform的回答中）
                import re
                combined_responses = f"{twitter_response} {reddit_response}"

                # Clean upResponse文本：去掉标记、编号、Markdown 等干扰
                clean_text = re.sub(r'#{1,6}\s+', '', combined_responses)
                clean_text = re.sub(r'\{[^}]*tool_name[^}]*\}', '', clean_text)
                clean_text = re.sub(r'[*_`|>~\-]{2,}', '', clean_text)
                clean_text = re.sub(r'问题\d+[：:]\s*', '', clean_text)
                clean_text = re.sub(r'【[^】]+】', '', clean_text)

                # 策略1（主）: 提取完整的有实质Content的句子
                sentences = re.split(r'[。！？]', clean_text)
                meaningful = [
                    s.strip() for s in sentences
                    if 20 <= len(s.strip()) <= 150
                    and not re.match(r'^[\s\W，,；;：:、]+', s.strip())
                    and not s.strip().startswith(('{', '问题'))
                ]
                meaningful.sort(key=len, reverse=True)
                key_quotes = [s + "。" for s in meaningful[:3]]

                # 策略2（补充）: 正确配对的中文引号「」内长文本
                if not key_quotes:
                    paired = re.findall(r'\u201c([^\u201c\u201d]{15,100})\u201d', clean_text)
                    paired += re.findall(r'\u300c([^\u300c\u300d]{15,100})\u300d', clean_text)
                    key_quotes = [q for q in paired if not re.match(r'^[，,；;：:、]', q)][:3]
                
                interview = AgentInterview(
                    agent_name=agent_name,
                    agent_role=agent_role,
                    agent_bio=agent_bio[:1000],  # 扩大bio长度限制
                    question=combined_prompt,
                    response=response_text,
                    key_quotes=key_quotes[:5]
                )
                result.interviews.append(interview)
            
            result.interviewed_count = len(result.interviews)
            
        except ValueError as e:
            # Simulation环境未Run
            logger.warning(f"采访APICallFailed（环境未Run？）: {e}")
            result.summary = f"采访Failed：{str(e)}。Simulation环境可能已Close，请确保OASIS环境CurrentlyRun。"
            return result
        except Exception as e:
            logger.error(f"采访APICall异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            result.summary = f"采访过程发生Error：{str(e)}"
            return result
        
        # Step 6: Generate采访摘要
        if result.interviews:
            result.summary = self._generate_interview_summary(
                interviews=result.interviews,
                interview_requirement=interview_requirement
            )
        
        logger.info(f"InterviewAgentsComplete: 采访了 {result.interviewed_count} 个Agent（双Platform）")
        return result
    
    @staticmethod
    def _clean_tool_call_response(response: str) -> str:
        """Clean up Agent 回复中的 JSON 工具Call包裹，提取实际Content"""
        if not response or not response.strip().startswith('{'):
            return response
        text = response.strip()
        if 'tool_name' not in text[:80]:
            return response
        import re as _re
        try:
            data = json.loads(text)
            if isinstance(data, dict) and 'arguments' in data:
                for key in ('content', 'text', 'body', 'message', 'reply'):
                    if key in data['arguments']:
                        return str(data['arguments'][key])
        except (json.JSONDecodeError, KeyError, TypeError):
            match = _re.search(r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
            if match:
                return match.group(1).replace('\\n', '\n').replace('\\"', '"')
        return response

    def _load_agent_profiles(self, simulation_id: str) -> List[Dict[str, Any]]:
        """LoadSimulation的Agent人设File"""
        import os
        import csv
        
        # 构建人设FilePath
        sim_dir = os.path.join(
            os.path.dirname(__file__), 
            f'../../uploads/simulations/{simulation_id}'
        )
        
        profiles = []
        
        # 优先尝试读取Reddit JSONFormat
        reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
        if os.path.exists(reddit_profile_path):
            try:
                with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                    profiles = json.load(f)
                logger.info(f"从 reddit_profiles.json Load了 {len(profiles)} 个人设")
                return profiles
            except Exception as e:
                logger.warning(f"读取 reddit_profiles.json Failed: {e}")
        
        # 尝试读取Twitter CSVFormat
        twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
        if os.path.exists(twitter_profile_path):
            try:
                with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # CSVFormat转换为统一Format
                        profiles.append({
                            "realname": row.get("name", ""),
                            "username": row.get("username", ""),
                            "bio": row.get("description", ""),
                            "persona": row.get("user_char", ""),
                            "profession": "未知"
                        })
                logger.info(f"从 twitter_profiles.csv Load了 {len(profiles)} 个人设")
                return profiles
            except Exception as e:
                logger.warning(f"读取 twitter_profiles.csv Failed: {e}")
        
        return profiles
    
    def _select_agents_for_interview(
        self,
        profiles: List[Dict[str, Any]],
        interview_requirement: str,
        simulation_requirement: str,
        max_agents: int
    ) -> tuple:
        """
        使用LLM选择要采访的Agent
        
        Returns:
            tuple: (selected_agents, selected_indices, reasoning)
                - selected_agents: 选中Agent的完整InformationList
                - selected_indices: 选中Agent的索引List（用于APICall）
                - reasoning: 选择理由
        """
        
        # 构建Agent摘要List
        agent_summaries = []
        for i, profile in enumerate(profiles):
            summary = {
                "index": i,
                "name": profile.get("realname", profile.get("username", f"Agent_{i}")),
                "profession": profile.get("profession", "未知"),
                "bio": profile.get("bio", "")[:200],
                "interested_topics": profile.get("interested_topics", [])
            }
            agent_summaries.append(summary)
        
        system_prompt = """你是一个专业的采访策划专家。你的Task是根据采访需求，从SimulationAgentList中选择最适合采访的对象。

选择标准：
1. Agent的身份/职业与采访主题相关
2. Agent可能持有独特或有价值的观点
3. 选择多样化的视角（如：支持方、反对方、中立方、专业人士等）
4. 优先选择与事件直接相关的角色

ReturnJSONFormat：
{
    "selected_indices": [选中Agent的索引List],
    "reasoning": "选择理由Description"
}"""

        user_prompt = f"""采访需求：
{interview_requirement}

Simulation背景：
{simulation_requirement if simulation_requirement else "未提供"}

Optional择的AgentList（共{len(agent_summaries)}个）：
{json.dumps(agent_summaries, ensure_ascii=False, indent=2)}

请选择最多{max_agents}个最适合采访的Agent，并Description选择理由。"""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            selected_indices = response.get("selected_indices", [])[:max_agents]
            reasoning = response.get("reasoning", "基于相关性自动选择")
            
            # Get选中的Agent完整Information
            selected_agents = []
            valid_indices = []
            for idx in selected_indices:
                if 0 <= idx < len(profiles):
                    selected_agents.append(profiles[idx])
                    valid_indices.append(idx)
            
            return selected_agents, valid_indices, reasoning
            
        except Exception as e:
            logger.warning(f"LLM选择AgentFailed，使用Default选择: {e}")
            # 降级：选择前N个
            selected = profiles[:max_agents]
            indices = list(range(min(max_agents, len(profiles))))
            return selected, indices, "使用Default选择策略"
    
    def _generate_interview_questions(
        self,
        interview_requirement: str,
        simulation_requirement: str,
        selected_agents: List[Dict[str, Any]]
    ) -> List[str]:
        """使用LLMGenerate采访问题"""
        
        agent_roles = [a.get("profession", "未知") for a in selected_agents]
        
        system_prompt = """你是一个专业的记者/采访者。根据采访需求，Generate3-5个深度采访问题。

问题要求：
1. 开放性问题，鼓励详细回答
2. 针对不同角色可能有不同答案
3. 涵盖事实、观点、感受等多个维度
4. 语言自然，像真实采访一样
5. 每个问题控制在50字以内，简洁明了
6. 直接提问，不要包含背景Description或前缀

ReturnJSONFormat：{"questions": ["问题1", "问题2", ...]}"""

        user_prompt = f"""采访需求：{interview_requirement}

Simulation背景：{simulation_requirement if simulation_requirement else "未提供"}

采访对象角色：{', '.join(agent_roles)}

请Generate3-5个采访问题。"""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5
            )
            
            return response.get("questions", [f"关于{interview_requirement}，您有什么看法？"])
            
        except Exception as e:
            logger.warning(f"Generate采访问题Failed: {e}")
            return [
                f"关于{interview_requirement}，您的观点是什么？",
                "这件事对您或您所代表的群体有什么影响？",
                "您认为应该如何解决或改进这个问题？"
            ]
    
    def _generate_interview_summary(
        self,
        interviews: List[AgentInterview],
        interview_requirement: str
    ) -> str:
        """Generate采访摘要"""
        
        if not interviews:
            return "未Complete任何采访"
        
        # 收集所有采访Content
        interview_texts = []
        for interview in interviews:
            interview_texts.append(f"【{interview.agent_name}（{interview.agent_role}）】\n{interview.response[:500]}")
        
        system_prompt = """你是一个专业的新闻编辑。请根据多位受访者的回答，Generate一份采访摘要。

摘要要求：
1. 提炼各方主要观点
2. 指出观点的共识和分歧
3. 突出有价值的引言
4. 客观中立，不偏袒任何一方
5. 控制在1000字内

Format约束（必须遵守）：
- 使用纯文本段落，用空行分隔不同部分
- 不要使用Markdown标题（如#、##、###）
- 不要使用分割线（如---、***）
- 引用受访者原话时使用中文引号「」
- 可以使用**加粗**标记关键词，但不要使用其他Markdown语法"""

        user_prompt = f"""采访主题：{interview_requirement}

采访Content：
{"".join(interview_texts)}

请Generate采访摘要。"""

        try:
            summary = self.llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3,
                max_tokens=800
            )
            return summary
            
        except Exception as e:
            logger.warning(f"Generate采访摘要Failed: {e}")
            # 降级：简单拼接
            return f"共采访了{len(interviews)}位受访者，包括：" + "、".join([i.agent_name for i in interviews])

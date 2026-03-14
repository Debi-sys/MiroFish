"""
Ontology generationService
Interface1：分析文本Content，Generate适合社会Simulation的Entity和RelationshipType定义
"""

import json
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient


# Ontology generation的系统提示词
ONTOLOGY_SYSTEM_PROMPT = """你是一个专业的Knowledge graph本体设计专家。你的Task是分析给定的文本Content和Simulation需求，设计适合**社交媒体舆论Simulation**的EntityType和RelationshipType。

**重要：你必须Output有效的JSONFormatData，不要Output任何其他Content。**

## 核心Task背景

我们Currently构建一个**Social media opinion simulation system**。在这个系统中：
- 每个Entity都是一个可以在社交媒体上发声、互动、传播Information的"账号"或"主体"
- Entity之间会相互影响、转发、评论、回应
- 我们需要Simulation舆论事件中各方的反应和Information传播Path

因此，**Entity必须是现实中真实存在的、可以在社媒上发声和互动的主体**：

**可以是**：
- 具体的个人（公众人物、当事人、意见领袖、专家学者、普通人）
- 公司、企业（包括其官方账号）
- 组织机构（大学、协会、NGO、工会等）
- 政府部门、监管机构
- 媒体机构（报纸、电视台、自媒体、网站）
- 社交媒体Platform本身
- 特定群体代表（如校友会、粉丝团、维权群体等）

**不可以是**：
- 抽象概念（如"舆论"、"情绪"、"趋势"）
- 主题/话题（如"学术诚信"、"教育改革"）
- 观点/态度（如"支持方"、"反对方"）

## OutputFormat

请OutputJSONFormat，包含以下结构：

```json
{
    "entity_types": [
        {
            "name": "EntityTypeName（英文，PascalCase）",
            "description": "简短Description（英文，不超过100字符）",
            "attributes": [
                {
                    "name": "Property名（英文，snake_case）",
                    "type": "text",
                    "description": "PropertyDescription"
                }
            ],
            "examples": ["ExampleEntity1", "ExampleEntity2"]
        }
    ],
    "edge_types": [
        {
            "name": "RelationshipTypeName（英文，UPPER_SNAKE_CASE）",
            "description": "简短Description（英文，不超过100字符）",
            "source_targets": [
                {"source": "源EntityType", "target": "目标EntityType"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "对文本Content的简要分析Description（中文）"
}
```

## 设计指南（极其重要！）

### 1. EntityType设计 - 必须严格遵守

**数量要求：必须正好10个EntityType**

**层次结构要求（必须同时包含具体Type和兜底Type）**：

你的10个EntityType必须包含以下层次：

A. **兜底Type（必须包含，放在List最后2个）**：
   - `Person`: 任何自然人个体的兜底Type。当一个人不属于其他更具体的人物Type时，归入此类。
   - `Organization`: 任何组织机构的兜底Type。当一个组织不属于其他更具体的组织Type时，归入此类。

B. **具体Type（8个，根据文本Content设计）**：
   - 针对文本中出现的主要角色，设计更具体的Type
   - 例如：If文本涉及学术事件，可以有 `Student`, `Professor`, `University`
   - 例如：If文本涉及商业事件，可以有 `Company`, `CEO`, `Employee`

**为什么需要兜底Type**：
- 文本中会出现各种人物，如"中小学教师"、"路人甲"、"某位网友"
- If没有专门的Type匹配，他们应该被归入 `Person`
- 同理，小型组织、临时团体等应该归入 `Organization`

**具体Type的设计原则**：
- 从文本中识别出高频出现或关键的角色Type
- 每个具体Type应该有明确的边界，避免重叠
- description 必须清晰Description这个Type和兜底Type的区别

### 2. RelationshipType设计

- 数量：6-10个
- Relationship应该反映社媒互动中的真实联系
- 确保Relationship的 source_targets 涵盖你定义的EntityType

### 3. Property设计

- 每个EntityType1-3个关键Property
- **Note**：Property名不能使用 `name`、`uuid`、`group_id`、`created_at`、`summary`（这些是系统保留字）
- 推荐使用：`full_name`, `title`, `role`, `position`, `location`, `description` 等

## EntityType参考

**个人类（具体）**：
- Student: 学生
- Professor: 教授/学者
- Journalist: 记者
- Celebrity: 明星/网红
- Executive: 高管
- Official: 政府官员
- Lawyer: 律师
- Doctor: 医生

**个人类（兜底）**：
- Person: 任何自然人（不属于上述具体Type时使用）

**组织类（具体）**：
- University: 高校
- Company: 公司企业
- GovernmentAgency: 政府机构
- MediaOutlet: 媒体机构
- Hospital: 医院
- School: 中小学
- NGO: 非政府组织

**组织类（兜底）**：
- Organization: 任何组织机构（不属于上述具体Type时使用）

## RelationshipType参考

- WORKS_FOR: 工作于
- STUDIES_AT: 就读于
- AFFILIATED_WITH: 隶属于
- REPRESENTS: 代表
- REGULATES: 监管
- REPORTS_ON: 报道
- COMMENTS_ON: 评论
- RESPONDS_TO: 回应
- SUPPORTS: 支持
- OPPOSES: 反对
- COLLABORATES_WITH: 合作
- COMPETES_WITH: 竞争
"""


class OntologyGenerator:
    """
    Ontology generator
    分析文本Content，GenerateEntity和RelationshipType定义
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate ontology定义
        
        Args:
            document_texts: 文档文本List
            simulation_requirement: Simulation需求Description
            additional_context: 额外上下文
            
        Returns:
            本体定义（entity_types, edge_types等）
        """
        # 构建UserMessage
        user_message = self._build_user_message(
            document_texts, 
            simulation_requirement,
            additional_context
        )
        
        messages = [
            {"role": "system", "content": ONTOLOGY_SYSTEM_PROMPT},
            {"role": "user", "content": user_message}
        ]
        
        # CallLLM
        result = self.llm_client.chat_json(
            messages=messages,
            temperature=0.3,
            max_tokens=4096
        )
        
        # Validate和后Process
        result = self._validate_and_process(result)
        
        return result
    
    # 传给 LLM 的文本最大长度（5万字）
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """构建UserMessage"""
        
        # 合并文本
        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)
        
        # If文本超过5万字，截断（仅影响传给LLM的Content，不影响Graph building）
        if len(combined_text) > self.MAX_TEXT_LENGTH_FOR_LLM:
            combined_text = combined_text[:self.MAX_TEXT_LENGTH_FOR_LLM]
            combined_text += f"\n\n...(原文共{original_length}字，已截取前{self.MAX_TEXT_LENGTH_FOR_LLM}字用于本体分析)..."
        
        message = f"""## Simulation需求

{simulation_requirement}

## 文档Content

{combined_text}
"""
        
        if additional_context:
            message += f"""
## 额外Description

{additional_context}
"""
        
        message += """
请根据以上Content，设计适合社会舆论Simulation的EntityType和RelationshipType。

**必须遵守的规则**：
1. 必须正好Output10个EntityType
2. 最后2个必须是兜底Type：Person（个人兜底）和 Organization（组织兜底）
3. 前8个是根据文本Content设计的具体Type
4. 所有EntityType必须是现实中可以发声的主体，不能是抽象概念
5. Property名不能使用 name、uuid、group_id 等保留字，用 full_name、org_name 等替代
"""
        
        return message
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate和后ProcessResult"""
        
        # 确保必要Field存在
        if "entity_types" not in result:
            result["entity_types"] = []
        if "edge_types" not in result:
            result["edge_types"] = []
        if "analysis_summary" not in result:
            result["analysis_summary"] = ""
        
        # ValidateEntityType
        for entity in result["entity_types"]:
            if "attributes" not in entity:
                entity["attributes"] = []
            if "examples" not in entity:
                entity["examples"] = []
            # 确保description不超过100字符
            if len(entity.get("description", "")) > 100:
                entity["description"] = entity["description"][:97] + "..."
        
        # ValidateRelationshipType
        for edge in result["edge_types"]:
            if "source_targets" not in edge:
                edge["source_targets"] = []
            if "attributes" not in edge:
                edge["attributes"] = []
            if len(edge.get("description", "")) > 100:
                edge["description"] = edge["description"][:97] + "..."
        
        # Zep API 限制：最多 10 个自定义EntityType，最多 10 个自定义边Type
        MAX_ENTITY_TYPES = 10
        MAX_EDGE_TYPES = 10
        
        # 兜底Type定义
        person_fallback = {
            "name": "Person",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # 检查Whether已有兜底Type
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # 需要添加的兜底Type
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # If添加后会超过 10 个，需要移除一些现有Type
            if current_count + needed_slots > MAX_ENTITY_TYPES:
                # 计算需要移除多少个
                to_remove = current_count + needed_slots - MAX_ENTITY_TYPES
                # 从末尾移除（保留前面更重要的具体Type）
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # 添加兜底Type
            result["entity_types"].extend(fallbacks_to_add)
        
        # 最终确保不超过限制（防御性编程）
        if len(result["entity_types"]) > MAX_ENTITY_TYPES:
            result["entity_types"] = result["entity_types"][:MAX_ENTITY_TYPES]
        
        if len(result["edge_types"]) > MAX_EDGE_TYPES:
            result["edge_types"] = result["edge_types"][:MAX_EDGE_TYPES]
        
        return result
    
    def generate_python_code(self, ontology: Dict[str, Any]) -> str:
        """
        将本体定义转换为Python代码（类似ontology.py）
        
        Args:
            ontology: 本体定义
            
        Returns:
            Python代码字符串
        """
        code_lines = [
            '"""',
            '自定义EntityType定义',
            '由MiroFish自动Generate，用于社会舆论Simulation',
            '"""',
            '',
            'from pydantic import Field',
            'from zep_cloud.external_clients.ontology import EntityModel, EntityText, EdgeModel',
            '',
            '',
            '# ============== EntityType定义 ==============',
            '',
        ]
        
        # GenerateEntityType
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            desc = entity.get("description", f"A {name} entity.")
            
            code_lines.append(f'class {name}(EntityModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = entity.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        code_lines.append('# ============== RelationshipType定义 ==============')
        code_lines.append('')
        
        # GenerateRelationshipType
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            # 转换为PascalCase类名
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            desc = edge.get("description", f"A {name} relationship.")
            
            code_lines.append(f'class {class_name}(EdgeModel):')
            code_lines.append(f'    """{desc}"""')
            
            attrs = edge.get("attributes", [])
            if attrs:
                for attr in attrs:
                    attr_name = attr["name"]
                    attr_desc = attr.get("description", attr_name)
                    code_lines.append(f'    {attr_name}: EntityText = Field(')
                    code_lines.append(f'        description="{attr_desc}",')
                    code_lines.append(f'        default=None')
                    code_lines.append(f'    )')
            else:
                code_lines.append('    pass')
            
            code_lines.append('')
            code_lines.append('')
        
        # GenerateType字典
        code_lines.append('# ============== TypeConfiguration ==============')
        code_lines.append('')
        code_lines.append('ENTITY_TYPES = {')
        for entity in ontology.get("entity_types", []):
            name = entity["name"]
            code_lines.append(f'    "{name}": {name},')
        code_lines.append('}')
        code_lines.append('')
        code_lines.append('EDGE_TYPES = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            class_name = ''.join(word.capitalize() for word in name.split('_'))
            code_lines.append(f'    "{name}": {class_name},')
        code_lines.append('}')
        code_lines.append('')
        
        # Generate边的source_targets映射
        code_lines.append('EDGE_SOURCE_TARGETS = {')
        for edge in ontology.get("edge_types", []):
            name = edge["name"]
            source_targets = edge.get("source_targets", [])
            if source_targets:
                st_list = ', '.join([
                    f'{{"source": "{st.get("source", "Entity")}", "target": "{st.get("target", "Entity")}"}}'
                    for st in source_targets
                ])
                code_lines.append(f'    "{name}": [{st_list}],')
        code_lines.append('}')
        
        return '\n'.join(code_lines)


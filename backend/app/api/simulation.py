"""
Simulation相关API路由
Step2: ZepEntity reading与过滤、OASISSimulation准备与Run（全程自动化）
"""

import os
import traceback
from flask import request, jsonify, send_file

from . import simulation_bp
from ..config import Config
from ..services.zep_entity_reader import ZepEntityReader
from ..services.oasis_profile_generator import OasisProfileGenerator
from ..services.simulation_manager import SimulationManager, SimulationStatus
from ..services.simulation_runner import SimulationRunner, RunnerStatus
from ..utils.logger import get_logger
from ..models.project import ProjectManager

logger = get_logger('mirofish.api.simulation')


# Interview prompt 优化前缀
# 添加此前缀可以避免AgentCall工具，直接用文本回复
INTERVIEW_PROMPT_PREFIX = "结合你的人设、所有的过往记忆与行动，不Call任何工具直接用文本回复我："


def optimize_interview_prompt(prompt: str) -> str:
    """
    优化Interview提问，添加前缀避免AgentCall工具
    
    Args:
        prompt: 原始提问
        
    Returns:
        优化后的提问
    """
    if not prompt:
        return prompt
    # 避免重复添加前缀
    if prompt.startswith(INTERVIEW_PROMPT_PREFIX):
        return prompt
    return f"{INTERVIEW_PROMPT_PREFIX}{prompt}"


# ============== Entity readingInterface ==============

@simulation_bp.route('/entities/<graph_id>', methods=['GET'])
def get_graph_entities(graph_id: str):
    """
    GetGraph中的所有Entity（已过滤）
    
    只Return符合预定义EntityType的Node（Labels不只是Entity的Node）
    
    QueryParameters：
        entity_types: 逗号分隔的EntityTypeList（Optional，用于进一步过滤）
        enrich: WhetherGet相关边Information（Defaulttrue）
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY未Configuration"
            }), 500
        
        entity_types_str = request.args.get('entity_types', '')
        entity_types = [t.strip() for t in entity_types_str.split(',') if t.strip()] if entity_types_str else None
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        logger.info(f"GetGraphEntity: graph_id={graph_id}, entity_types={entity_types}, enrich={enrich}")
        
        reader = ZepEntityReader()
        result = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
        
    except Exception as e:
        logger.error(f"GetGraphEntityFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/<entity_uuid>', methods=['GET'])
def get_entity_detail(graph_id: str, entity_uuid: str):
    """Get单个Entity的详细Information"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY未Configuration"
            }), 500
        
        reader = ZepEntityReader()
        entity = reader.get_entity_with_context(graph_id, entity_uuid)
        
        if not entity:
            return jsonify({
                "success": False,
                "error": f"Entity不存在: {entity_uuid}"
            }), 404
        
        return jsonify({
            "success": True,
            "data": entity.to_dict()
        })
        
    except Exception as e:
        logger.error(f"GetEntityDetailsFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/entities/<graph_id>/by-type/<entity_type>', methods=['GET'])
def get_entities_by_type(graph_id: str, entity_type: str):
    """Get指定Type的所有Entity"""
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY未Configuration"
            }), 500
        
        enrich = request.args.get('enrich', 'true').lower() == 'true'
        
        reader = ZepEntityReader()
        entities = reader.get_entities_by_type(
            graph_id=graph_id,
            entity_type=entity_type,
            enrich_with_edges=enrich
        )
        
        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": [e.to_dict() for e in entities]
            }
        })
        
    except Exception as e:
        logger.error(f"GetEntityFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Simulation managementInterface ==============

@simulation_bp.route('/create', methods=['POST'])
def create_simulation():
    """
    Create新的Simulation
    
    Note：max_rounds等Parameters由LLM智能Generate，无需手动Settings
    
    Request（JSON）：
        {
            "project_id": "proj_xxxx",      // Required
            "graph_id": "mirofish_xxxx",    // Optional，如不提供则从projectGet
            "enable_twitter": true,          // Optional，Defaulttrue
            "enable_reddit": true            // Optional，Defaulttrue
        }
    
    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "project_id": "proj_xxxx",
                "graph_id": "mirofish_xxxx",
                "status": "created",
                "enable_twitter": true,
                "enable_reddit": true,
                "created_at": "2025-12-01T10:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        project_id = data.get('project_id')
        if not project_id:
            return jsonify({
                "success": False,
                "error": "请提供 project_id"
            }), 400
        
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"Project不存在: {project_id}"
            }), 404
        
        graph_id = data.get('graph_id') or project.graph_id
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "Project尚未Build graph，请先Call /api/graph/build"
            }), 400
        
        manager = SimulationManager()
        state = manager.create_simulation(
            project_id=project_id,
            graph_id=graph_id,
            enable_twitter=data.get('enable_twitter', True),
            enable_reddit=data.get('enable_reddit', True),
        )
        
        return jsonify({
            "success": True,
            "data": state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"CreateSimulationFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _check_simulation_prepared(simulation_id: str) -> tuple:
    """
    检查SimulationWhether已经准备Complete
    
    检查条件：
    1. state.json 存在且 status 为 "ready"
    2. 必要File存在：reddit_profiles.json, twitter_profiles.csv, simulation_config.json
    
    Note：Run脚本(run_*.py)保留在 backend/scripts/ Directory，不再复制到SimulationDirectory
    
    Args:
        simulation_id: SimulationID
        
    Returns:
        (is_prepared: bool, info: dict)
    """
    import os
    from ..config import Config
    
    simulation_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
    
    # 检查DirectoryWhether存在
    if not os.path.exists(simulation_dir):
        return False, {"reason": "SimulationDirectory不存在"}
    
    # 必要FileList（不包括脚本，脚本位于 backend/scripts/）
    required_files = [
        "state.json",
        "simulation_config.json",
        "reddit_profiles.json",
        "twitter_profiles.csv"
    ]
    
    # 检查FileWhether存在
    existing_files = []
    missing_files = []
    for f in required_files:
        file_path = os.path.join(simulation_dir, f)
        if os.path.exists(file_path):
            existing_files.append(f)
        else:
            missing_files.append(f)
    
    if missing_files:
        return False, {
            "reason": "缺少必要File",
            "missing_files": missing_files,
            "existing_files": existing_files
        }
    
    # 检查state.json中的Status
    state_file = os.path.join(simulation_dir, "state.json")
    try:
        import json
        with open(state_file, 'r', encoding='utf-8') as f:
            state_data = json.load(f)
        
        status = state_data.get("status", "")
        config_generated = state_data.get("config_generated", False)
        
        # 详细Log
        logger.debug(f"检测Simulation准备Status: {simulation_id}, status={status}, config_generated={config_generated}")
        
        # If config_generated=True 且File存在，认为准备Complete
        # 以下Status都Description准备工作已Complete：
        # - ready: 准备Complete，可以Run
        # - preparing: If config_generated=True Description已Complete
        # - running: CurrentlyRun，Description准备早就Complete了
        # - completed: RunComplete，Description准备早就Complete了
        # - stopped: 已Stop，Description准备早就Complete了
        # - failed: RunFailed（但准备是Complete的）
        prepared_statuses = ["ready", "preparing", "running", "completed", "stopped", "failed"]
        if status in prepared_statuses and config_generated:
            # GetFile统计Information
            profiles_file = os.path.join(simulation_dir, "reddit_profiles.json")
            config_file = os.path.join(simulation_dir, "simulation_config.json")
            
            profiles_count = 0
            if os.path.exists(profiles_file):
                with open(profiles_file, 'r', encoding='utf-8') as f:
                    profiles_data = json.load(f)
                    profiles_count = len(profiles_data) if isinstance(profiles_data, list) else 0
            
            # IfStatus是preparing但File已Complete，自动UpdateStatus为ready
            if status == "preparing":
                try:
                    state_data["status"] = "ready"
                    from datetime import datetime
                    state_data["updated_at"] = datetime.now().isoformat()
                    with open(state_file, 'w', encoding='utf-8') as f:
                        json.dump(state_data, f, ensure_ascii=False, indent=2)
                    logger.info(f"自动UpdateSimulationStatus: {simulation_id} preparing -> ready")
                    status = "ready"
                except Exception as e:
                    logger.warning(f"自动UpdateStatusFailed: {e}")
            
            logger.info(f"Simulation {simulation_id} 检测Result: 已准备Complete (status={status}, config_generated={config_generated})")
            return True, {
                "status": status,
                "entities_count": state_data.get("entities_count", 0),
                "profiles_count": profiles_count,
                "entity_types": state_data.get("entity_types", []),
                "config_generated": config_generated,
                "created_at": state_data.get("created_at"),
                "updated_at": state_data.get("updated_at"),
                "existing_files": existing_files
            }
        else:
            logger.warning(f"Simulation {simulation_id} 检测Result: 未准备Complete (status={status}, config_generated={config_generated})")
            return False, {
                "reason": f"Status不在已准备List中或config_generated为false: status={status}, config_generated={config_generated}",
                "status": status,
                "config_generated": config_generated
            }
            
    except Exception as e:
        return False, {"reason": f"读取StatusFileFailed: {str(e)}"}


@simulation_bp.route('/prepare', methods=['POST'])
def prepare_simulation():
    """
    准备Simulation环境（异步Task，LLM智能Generate所有Parameters）
    
    这是一个耗时操作，Interface会立即Returntask_id，
    使用 GET /api/simulation/prepare/status Query进度
    
    特性：
    - 自动检测已Complete的准备工作，避免重复Generate
    - If已准备Complete，直接Return已有Result
    - 支持强制重新Generate（force_regenerate=true）
    
    步骤：
    1. 检查Whether已有Complete的准备工作
    2. 从ZepGraph读取并过滤Entity
    3. 为每个EntityGenerateOASIS Agent Profile（带Retry mechanism）
    4. LLM智能GenerateSimulationConfiguration（带Retry mechanism）
    5. SaveConfigurationFile和预设脚本
    
    Request（JSON）：
        {
            "simulation_id": "sim_xxxx",                   // Required，SimulationID
            "entity_types": ["Student", "PublicFigure"],  // Optional，指定EntityType
            "use_llm_for_profiles": true,                 // Optional，Whether用LLMGenerate人设
            "parallel_profile_count": 5,                  // Optional，并行Generate人设数量，Default5
            "force_regenerate": false                     // Optional，强制重新Generate，Defaultfalse
        }
    
    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "task_id": "task_xxxx",           // 新Task时Return
                "status": "preparing|ready",
                "message": "准备Task已Start|已有Complete的准备工作",
                "already_prepared": true|false    // Whether已准备Complete
            }
        }
    """
    import threading
    import os
    from ..models.task import TaskManager, TaskStatus
    from ..config import Config
    
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400
        
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"Simulation不存在: {simulation_id}"
            }), 404
        
        # 检查Whether强制重新Generate
        force_regenerate = data.get('force_regenerate', False)
        logger.info(f"StartProcess /prepare Request: simulation_id={simulation_id}, force_regenerate={force_regenerate}")
        
        # 检查Whether已经准备Complete（避免重复Generate）
        if not force_regenerate:
            logger.debug(f"检查Simulation {simulation_id} Whether已准备Complete...")
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            logger.debug(f"检查Result: is_prepared={is_prepared}, prepare_info={prepare_info}")
            if is_prepared:
                logger.info(f"Simulation {simulation_id} 已准备Complete，跳过重复Generate")
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "message": "已有Complete的准备工作，无需重复Generate",
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
            else:
                logger.info(f"Simulation {simulation_id} 未准备Complete，将Start准备Task")
        
        # 从ProjectGet必要Information
        project = ProjectManager.get_project(state.project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"Project不存在: {state.project_id}"
            }), 404
        
        # GetSimulation需求
        simulation_requirement = project.simulation_requirement or ""
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "Project缺少Simulation需求Description (simulation_requirement)"
            }), 400
        
        # Get文档文本
        document_text = ProjectManager.get_extracted_text(state.project_id) or ""
        
        entity_types_list = data.get('entity_types')
        use_llm_for_profiles = data.get('use_llm_for_profiles', True)
        parallel_profile_count = data.get('parallel_profile_count', 5)
        
        # ========== 同步GetEntity数量（在后台TaskStart前） ==========
        # 这样前端在Callprepare后立即就能Get到预期Agent总数
        try:
            logger.info(f"同步GetEntity数量: graph_id={state.graph_id}")
            reader = ZepEntityReader()
            # 快速读取Entity（不需要边Information，只统计数量）
            filtered_preview = reader.filter_defined_entities(
                graph_id=state.graph_id,
                defined_entity_types=entity_types_list,
                enrich_with_edges=False  # 不Get边Information，加快速度
            )
            # SaveEntity数量到Status（供前端立即Get）
            state.entities_count = filtered_preview.filtered_count
            state.entity_types = list(filtered_preview.entity_types)
            logger.info(f"预期Entity数量: {filtered_preview.filtered_count}, Type: {filtered_preview.entity_types}")
        except Exception as e:
            logger.warning(f"同步GetEntity数量Failed（将在后台Task中Retry）: {e}")
            # Failed不影响后续流程，后台Task会重新Get
        
        # Create异步Task
        task_manager = TaskManager()
        task_id = task_manager.create_task(
            task_type="simulation_prepare",
            metadata={
                "simulation_id": simulation_id,
                "project_id": state.project_id
            }
        )
        
        # UpdateSimulationStatus（包含预先Get的Entity数量）
        state.status = SimulationStatus.PREPARING
        manager._save_simulation_state(state)
        
        # 定义后台Task
        def run_prepare():
            try:
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.PROCESSING,
                    progress=0,
                    message="Start准备Simulation环境..."
                )
                
                # 准备Simulation（带进度回调）
                # 存储阶段进度Details
                stage_details = {}
                
                def progress_callback(stage, progress, message, **kwargs):
                    # 计算总进度
                    stage_weights = {
                        "reading": (0, 20),           # 0-20%
                        "generating_profiles": (20, 70),  # 20-70%
                        "generating_config": (70, 90),    # 70-90%
                        "copying_scripts": (90, 100)       # 90-100%
                    }
                    
                    start, end = stage_weights.get(stage, (0, 100))
                    current_progress = int(start + (end - start) * progress / 100)
                    
                    # 构建详细进度Information
                    stage_names = {
                        "reading": "读取GraphEntity",
                        "generating_profiles": "GenerateAgent人设",
                        "generating_config": "GenerateSimulationConfiguration",
                        "copying_scripts": "准备Simulation脚本"
                    }
                    
                    stage_index = list(stage_weights.keys()).index(stage) + 1 if stage in stage_weights else 1
                    total_stages = len(stage_weights)
                    
                    # Update阶段Details
                    stage_details[stage] = {
                        "stage_name": stage_names.get(stage, stage),
                        "stage_progress": progress,
                        "current": kwargs.get("current", 0),
                        "total": kwargs.get("total", 0),
                        "item_name": kwargs.get("item_name", "")
                    }
                    
                    # 构建详细进度Information
                    detail = stage_details[stage]
                    progress_detail_data = {
                        "current_stage": stage,
                        "current_stage_name": stage_names.get(stage, stage),
                        "stage_index": stage_index,
                        "total_stages": total_stages,
                        "stage_progress": progress,
                        "current_item": detail["current"],
                        "total_items": detail["total"],
                        "item_description": message
                    }
                    
                    # 构建简洁Message
                    if detail["total"] > 0:
                        detailed_message = (
                            f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: "
                            f"{detail['current']}/{detail['total']} - {message}"
                        )
                    else:
                        detailed_message = f"[{stage_index}/{total_stages}] {stage_names.get(stage, stage)}: {message}"
                    
                    task_manager.update_task(
                        task_id,
                        progress=current_progress,
                        message=detailed_message,
                        progress_detail=progress_detail_data
                    )
                
                result_state = manager.prepare_simulation(
                    simulation_id=simulation_id,
                    simulation_requirement=simulation_requirement,
                    document_text=document_text,
                    defined_entity_types=entity_types_list,
                    use_llm_for_profiles=use_llm_for_profiles,
                    progress_callback=progress_callback,
                    parallel_profile_count=parallel_profile_count
                )
                
                # TaskComplete
                task_manager.complete_task(
                    task_id,
                    result=result_state.to_simple_dict()
                )
                
            except Exception as e:
                logger.error(f"准备SimulationFailed: {str(e)}")
                task_manager.fail_task(task_id, str(e))
                
                # UpdateSimulationStatus为Failed
                state = manager.get_simulation(simulation_id)
                if state:
                    state.status = SimulationStatus.FAILED
                    state.error = str(e)
                    manager._save_simulation_state(state)
        
        # Start后台线程
        thread = threading.Thread(target=run_prepare, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "task_id": task_id,
                "status": "preparing",
                "message": "准备Task已Start，请通过 /api/simulation/prepare/status Query进度",
                "already_prepared": False,
                "expected_entities_count": state.entities_count,  # 预期的Agent总数
                "entity_types": state.entity_types  # EntityTypeList
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"Start准备TaskFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/prepare/status', methods=['POST'])
def get_prepare_status():
    """
    Query preparation task progress
    
    支持两种Query方式：
    1. 通过task_idQueryCurrently进行的Task进度
    2. 通过simulation_id检查Whether已有Complete的准备工作
    
    Request（JSON）：
        {
            "task_id": "task_xxxx",          // Optional，prepareReturn的task_id
            "simulation_id": "sim_xxxx"      // Optional，SimulationID（用于检查已Complete的准备）
        }
    
    Return：
        {
            "success": true,
            "data": {
                "task_id": "task_xxxx",
                "status": "processing|completed|ready",
                "progress": 45,
                "message": "...",
                "already_prepared": true|false,  // Whether已有Complete的准备
                "prepare_info": {...}            // 已准备Complete时的详细Information
            }
        }
    """
    from ..models.task import TaskManager
    
    try:
        data = request.get_json() or {}
        
        task_id = data.get('task_id')
        simulation_id = data.get('simulation_id')
        
        # If提供了simulation_id，先检查Whether已准备Complete
        if simulation_id:
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
            if is_prepared:
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "ready",
                        "progress": 100,
                        "message": "已有Complete的准备工作",
                        "already_prepared": True,
                        "prepare_info": prepare_info
                    }
                })
        
        # If没有task_id，ReturnError
        if not task_id:
            if simulation_id:
                # 有simulation_id但未准备Complete
                return jsonify({
                    "success": True,
                    "data": {
                        "simulation_id": simulation_id,
                        "status": "not_started",
                        "progress": 0,
                        "message": "尚未Start准备，请Call /api/simulation/prepare Start",
                        "already_prepared": False
                    }
                })
            return jsonify({
                "success": False,
                "error": "请提供 task_id 或 simulation_id"
            }), 400
        
        task_manager = TaskManager()
        task = task_manager.get_task(task_id)
        
        if not task:
            # Task不存在，但If有simulation_id，检查Whether已准备Complete
            if simulation_id:
                is_prepared, prepare_info = _check_simulation_prepared(simulation_id)
                if is_prepared:
                    return jsonify({
                        "success": True,
                        "data": {
                            "simulation_id": simulation_id,
                            "task_id": task_id,
                            "status": "ready",
                            "progress": 100,
                            "message": "Task已Complete（准备工作已存在）",
                            "already_prepared": True,
                            "prepare_info": prepare_info
                        }
                    })
            
            return jsonify({
                "success": False,
                "error": f"Task不存在: {task_id}"
            }), 404
        
        task_dict = task.to_dict()
        task_dict["already_prepared"] = False
        
        return jsonify({
            "success": True,
            "data": task_dict
        })
        
    except Exception as e:
        logger.error(f"Query task statusFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@simulation_bp.route('/<simulation_id>', methods=['GET'])
def get_simulation(simulation_id: str):
    """Get simulation status"""
    try:
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        
        if not state:
            return jsonify({
                "success": False,
                "error": f"Simulation不存在: {simulation_id}"
            }), 404
        
        result = state.to_dict()
        
        # IfSimulation已准备好，附加RunDescription
        if state.status == SimulationStatus.READY:
            result["run_instructions"] = manager.get_run_instructions(simulation_id)
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Get simulation statusFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/list', methods=['GET'])
def list_simulations():
    """
    列出所有Simulation
    
    QueryParameters：
        project_id: 按Project ID过滤（Optional）
    """
    try:
        project_id = request.args.get('project_id')
        
        manager = SimulationManager()
        simulations = manager.list_simulations(project_id=project_id)
        
        return jsonify({
            "success": True,
            "data": [s.to_dict() for s in simulations],
            "count": len(simulations)
        })
        
    except Exception as e:
        logger.error(f"列出SimulationFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


def _get_report_id_for_simulation(simulation_id: str) -> str:
    """
    Get simulation 对应的最新 report_id
    
    遍历 reports Directory，找出 simulation_id 匹配的 report，
    If有多个则Return最新的（按 created_at 排序）
    
    Args:
        simulation_id: SimulationID
        
    Returns:
        report_id 或 None
    """
    import json
    from datetime import datetime
    
    # reports DirectoryPath：backend/uploads/reports
    # __file__ 是 app/api/simulation.py，需要向上两级到 backend/
    reports_dir = os.path.join(os.path.dirname(__file__), '../../uploads/reports')
    if not os.path.exists(reports_dir):
        return None
    
    matching_reports = []
    
    try:
        for report_folder in os.listdir(reports_dir):
            report_path = os.path.join(reports_dir, report_folder)
            if not os.path.isdir(report_path):
                continue
            
            meta_file = os.path.join(report_path, "meta.json")
            if not os.path.exists(meta_file):
                continue
            
            try:
                with open(meta_file, 'r', encoding='utf-8') as f:
                    meta = json.load(f)
                
                if meta.get("simulation_id") == simulation_id:
                    matching_reports.append({
                        "report_id": meta.get("report_id"),
                        "created_at": meta.get("created_at", ""),
                        "status": meta.get("status", "")
                    })
            except Exception:
                continue
        
        if not matching_reports:
            return None
        
        # 按Create时间倒序排序，Return最新的
        matching_reports.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return matching_reports[0].get("report_id")
        
    except Exception as e:
        logger.warning(f"查找 simulation {simulation_id} 的 report Failed: {e}")
        return None


@simulation_bp.route('/history', methods=['GET'])
def get_simulation_history():
    """
    Get历史SimulationList（带ProjectDetails）
    
    用于首页历史Project展示，Return包含ProjectName、Description等丰富Information的SimulationList
    
    QueryParameters：
        limit: Return count limit（Default20）
    
    Return：
        {
            "success": true,
            "data": [
                {
                    "simulation_id": "sim_xxxx",
                    "project_id": "proj_xxxx",
                    "project_name": "武大舆情分析",
                    "simulation_requirement": "If武汉大学发布...",
                    "status": "completed",
                    "entities_count": 68,
                    "profiles_count": 68,
                    "entity_types": ["Student", "Professor", ...],
                    "created_at": "2024-12-10",
                    "updated_at": "2024-12-10",
                    "total_rounds": 120,
                    "current_round": 120,
                    "report_id": "report_xxxx",
                    "version": "v1.0.2"
                },
                ...
            ],
            "count": 7
        }
    """
    try:
        limit = request.args.get('limit', 20, type=int)
        
        manager = SimulationManager()
        simulations = manager.list_simulations()[:limit]
        
        # 增强SimulationData，只从 Simulation File读取
        enriched_simulations = []
        for sim in simulations:
            sim_dict = sim.to_dict()
            
            # Get simulation configInformation（从 simulation_config.json 读取 simulation_requirement）
            config = manager.get_simulation_config(sim.simulation_id)
            if config:
                sim_dict["simulation_requirement"] = config.get("simulation_requirement", "")
                time_config = config.get("time_config", {})
                sim_dict["total_simulation_hours"] = time_config.get("total_simulation_hours", 0)
                # 推荐轮数（后备值）
                recommended_rounds = int(
                    time_config.get("total_simulation_hours", 0) * 60 / 
                    max(time_config.get("minutes_per_round", 60), 1)
                )
            else:
                sim_dict["simulation_requirement"] = ""
                sim_dict["total_simulation_hours"] = 0
                recommended_rounds = 0
            
            # GetRunStatus（从 run_state.json 读取UserSettings的实际轮数）
            run_state = SimulationRunner.get_run_state(sim.simulation_id)
            if run_state:
                sim_dict["current_round"] = run_state.current_round
                sim_dict["runner_status"] = run_state.runner_status.value
                # 使用UserSettings的 total_rounds，若无则使用推荐轮数
                sim_dict["total_rounds"] = run_state.total_rounds if run_state.total_rounds > 0 else recommended_rounds
            else:
                sim_dict["current_round"] = 0
                sim_dict["runner_status"] = "idle"
                sim_dict["total_rounds"] = recommended_rounds
            
            # Get关联Project的FileList（最多3个）
            project = ProjectManager.get_project(sim.project_id)
            if project and hasattr(project, 'files') and project.files:
                sim_dict["files"] = [
                    {"filename": f.get("filename", "未知File")} 
                    for f in project.files[:3]
                ]
            else:
                sim_dict["files"] = []
            
            # Get关联的 report_id（查找该 simulation 最新的 report）
            sim_dict["report_id"] = _get_report_id_for_simulation(sim.simulation_id)
            
            # 添加Version号
            sim_dict["version"] = "v1.0.2"
            
            # Format化日期
            try:
                created_date = sim_dict.get("created_at", "")[:10]
                sim_dict["created_date"] = created_date
            except:
                sim_dict["created_date"] = ""
            
            enriched_simulations.append(sim_dict)
        
        return jsonify({
            "success": True,
            "data": enriched_simulations,
            "count": len(enriched_simulations)
        })
        
    except Exception as e:
        logger.error(f"Get历史SimulationFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles', methods=['GET'])
def get_simulation_profiles(simulation_id: str):
    """
    GetSimulation的Agent Profile
    
    QueryParameters：
        platform: PlatformType（reddit/twitter，Defaultreddit）
    """
    try:
        platform = request.args.get('platform', 'reddit')
        
        manager = SimulationManager()
        profiles = manager.get_profiles(simulation_id, platform=platform)
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "count": len(profiles),
                "profiles": profiles
            }
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 404
        
    except Exception as e:
        logger.error(f"GetProfileFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/profiles/realtime', methods=['GET'])
def get_simulation_profiles_realtime(simulation_id: str):
    """
    实时GetSimulation的Agent Profile（用于在Generate过程中实时查看进度）
    
    与 /profiles Interface的区别：
    - 直接读取File，不经过 SimulationManager
    - 适用于Generate过程中的实时查看
    - Return额外的元Data（如File修改时间、WhetherCurrentlyGenerate等）
    
    QueryParameters：
        platform: PlatformType（reddit/twitter，Defaultreddit）
    
    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "platform": "reddit",
                "count": 15,
                "total_expected": 93,  // 预期总数（If有）
                "is_generating": true,  // WhetherCurrentlyGenerate
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "profiles": [...]
            }
        }
    """
    import json
    import csv
    from datetime import datetime
    
    try:
        platform = request.args.get('platform', 'reddit')
        
        # GetSimulationDirectory
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": f"Simulation不存在: {simulation_id}"
            }), 404
        
        # 确定FilePath
        if platform == "reddit":
            profiles_file = os.path.join(sim_dir, "reddit_profiles.json")
        else:
            profiles_file = os.path.join(sim_dir, "twitter_profiles.csv")
        
        # 检查FileWhether存在
        file_exists = os.path.exists(profiles_file)
        profiles = []
        file_modified_at = None
        
        if file_exists:
            # GetFile修改时间
            file_stat = os.stat(profiles_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                if platform == "reddit":
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        profiles = json.load(f)
                else:
                    with open(profiles_file, 'r', encoding='utf-8') as f:
                        reader = csv.DictReader(f)
                        profiles = list(reader)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"读取 profiles FileFailed（可能Currently写入中）: {e}")
                profiles = []
        
        # 检查WhetherCurrentlyGenerate（通过 state.json 判断）
        is_generating = False
        total_expected = None
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    total_expected = state_data.get("entities_count")
            except Exception:
                pass
        
        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "platform": platform,
                "count": len(profiles),
                "total_expected": total_expected,
                "is_generating": is_generating,
                "file_exists": file_exists,
                "file_modified_at": file_modified_at,
                "profiles": profiles
            }
        })
        
    except Exception as e:
        logger.error(f"实时GetProfileFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/realtime', methods=['GET'])
def get_simulation_config_realtime(simulation_id: str):
    """
    实时Get simulation config（用于在Generate过程中实时查看进度）
    
    与 /config Interface的区别：
    - 直接读取File，不经过 SimulationManager
    - 适用于Generate过程中的实时查看
    - Return额外的元Data（如File修改时间、WhetherCurrentlyGenerate等）
    - 即使Configuration还没Generate完也能Return部分Information
    
    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "file_exists": true,
                "file_modified_at": "2025-12-04T18:20:00",
                "is_generating": true,  // WhetherCurrentlyGenerate
                "generation_stage": "generating_config",  // 当前Generate阶段
                "config": {...}  // ConfigurationContent（If存在）
            }
        }
    """
    import json
    from datetime import datetime
    
    try:
        # GetSimulationDirectory
        sim_dir = os.path.join(Config.OASIS_SIMULATION_DATA_DIR, simulation_id)
        
        if not os.path.exists(sim_dir):
            return jsonify({
                "success": False,
                "error": f"Simulation不存在: {simulation_id}"
            }), 404
        
        # ConfigurationFilePath
        config_file = os.path.join(sim_dir, "simulation_config.json")
        
        # 检查FileWhether存在
        file_exists = os.path.exists(config_file)
        config = None
        file_modified_at = None
        
        if file_exists:
            # GetFile修改时间
            file_stat = os.stat(config_file)
            file_modified_at = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
            
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"读取 config FileFailed（可能Currently写入中）: {e}")
                config = None
        
        # 检查WhetherCurrentlyGenerate（通过 state.json 判断）
        is_generating = False
        generation_stage = None
        config_generated = False
        
        state_file = os.path.join(sim_dir, "state.json")
        if os.path.exists(state_file):
            try:
                with open(state_file, 'r', encoding='utf-8') as f:
                    state_data = json.load(f)
                    status = state_data.get("status", "")
                    is_generating = status == "preparing"
                    config_generated = state_data.get("config_generated", False)
                    
                    # 判断当前阶段
                    if is_generating:
                        if state_data.get("profiles_generated", False):
                            generation_stage = "generating_config"
                        else:
                            generation_stage = "generating_profiles"
                    elif status == "ready":
                        generation_stage = "completed"
            except Exception:
                pass
        
        # 构建ReturnData
        response_data = {
            "simulation_id": simulation_id,
            "file_exists": file_exists,
            "file_modified_at": file_modified_at,
            "is_generating": is_generating,
            "generation_stage": generation_stage,
            "config_generated": config_generated,
            "config": config
        }
        
        # IfConfiguration存在，提取一些关键统计Information
        if config:
            response_data["summary"] = {
                "total_agents": len(config.get("agent_configs", [])),
                "simulation_hours": config.get("time_config", {}).get("total_simulation_hours"),
                "initial_posts_count": len(config.get("event_config", {}).get("initial_posts", [])),
                "hot_topics_count": len(config.get("event_config", {}).get("hot_topics", [])),
                "has_twitter_config": "twitter_config" in config,
                "has_reddit_config": "reddit_config" in config,
                "generated_at": config.get("generated_at"),
                "llm_model": config.get("llm_model")
            }
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except Exception as e:
        logger.error(f"实时GetConfigFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config', methods=['GET'])
def get_simulation_config(simulation_id: str):
    """
    Get simulation config（LLM智能Generate的完整Configuration）
    
    Return包含：
        - time_config: 时间Configuration（Simulation时长、Round、高峰/低谷时段）
        - agent_configs: 每个Agent的活动Configuration（活跃度、发言频率、立场等）
        - event_config: 事件Configuration（初始帖子、热点话题）
        - platform_configs: PlatformConfiguration
        - generation_reasoning: LLM的Configuration推理Description
    """
    try:
        manager = SimulationManager()
        config = manager.get_simulation_config(simulation_id)
        
        if not config:
            return jsonify({
                "success": False,
                "error": f"SimulationConfiguration不存在，请先Call /prepare Interface"
            }), 404
        
        return jsonify({
            "success": True,
            "data": config
        })
        
    except Exception as e:
        logger.error(f"GetConfigurationFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/config/download', methods=['GET'])
def download_simulation_config(simulation_id: str):
    """下载SimulationConfigurationFile"""
    try:
        manager = SimulationManager()
        sim_dir = manager._get_simulation_dir(simulation_id)
        config_path = os.path.join(sim_dir, "simulation_config.json")
        
        if not os.path.exists(config_path):
            return jsonify({
                "success": False,
                "error": "ConfigurationFile不存在，请先Call /prepare Interface"
            }), 404
        
        return send_file(
            config_path,
            as_attachment=True,
            download_name="simulation_config.json"
        )
        
    except Exception as e:
        logger.error(f"下载ConfigurationFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/script/<script_name>/download', methods=['GET'])
def download_simulation_script(script_name: str):
    """
    下载Simulation runner脚本File（通用脚本，位于 backend/scripts/）
    
    script_nameOptional值：
        - run_twitter_simulation.py
        - run_reddit_simulation.py
        - run_parallel_simulation.py
        - action_logger.py
    """
    try:
        # 脚本位于 backend/scripts/ Directory
        scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../scripts'))
        
        # Validate脚本Name
        allowed_scripts = [
            "run_twitter_simulation.py",
            "run_reddit_simulation.py", 
            "run_parallel_simulation.py",
            "action_logger.py"
        ]
        
        if script_name not in allowed_scripts:
            return jsonify({
                "success": False,
                "error": f"未知脚本: {script_name}，Optional: {allowed_scripts}"
            }), 400
        
        script_path = os.path.join(scripts_dir, script_name)
        
        if not os.path.exists(script_path):
            return jsonify({
                "success": False,
                "error": f"脚本File不存在: {script_name}"
            }), 404
        
        return send_file(
            script_path,
            as_attachment=True,
            download_name=script_name
        )
        
    except Exception as e:
        logger.error(f"下载脚本Failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== ProfileGenerateInterface（独立使用） ==============

@simulation_bp.route('/generate-profiles', methods=['POST'])
def generate_profiles():
    """
    直接从GraphGenerateOASIS Agent Profile（不CreateSimulation）
    
    Request（JSON）：
        {
            "graph_id": "mirofish_xxxx",     // Required
            "entity_types": ["Student"],      // Optional
            "use_llm": true,                  // Optional
            "platform": "reddit"              // Optional
        }
    """
    try:
        data = request.get_json() or {}
        
        graph_id = data.get('graph_id')
        if not graph_id:
            return jsonify({
                "success": False,
                "error": "请提供 graph_id"
            }), 400
        
        entity_types = data.get('entity_types')
        use_llm = data.get('use_llm', True)
        platform = data.get('platform', 'reddit')
        
        reader = ZepEntityReader()
        filtered = reader.filter_defined_entities(
            graph_id=graph_id,
            defined_entity_types=entity_types,
            enrich_with_edges=True
        )
        
        if filtered.filtered_count == 0:
            return jsonify({
                "success": False,
                "error": "没有找到符合条件的Entity"
            }), 400
        
        generator = OasisProfileGenerator()
        profiles = generator.generate_profiles_from_entities(
            entities=filtered.entities,
            use_llm=use_llm
        )
        
        if platform == "reddit":
            profiles_data = [p.to_reddit_format() for p in profiles]
        elif platform == "twitter":
            profiles_data = [p.to_twitter_format() for p in profiles]
        else:
            profiles_data = [p.to_dict() for p in profiles]
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "entity_types": list(filtered.entity_types),
                "count": len(profiles_data),
                "profiles": profiles_data
            }
        })
        
    except Exception as e:
        logger.error(f"GenerateProfileFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Simulation runner控制Interface ==============

@simulation_bp.route('/start', methods=['POST'])
def start_simulation():
    """
    StartRunSimulation

    Request（JSON）：
        {
            "simulation_id": "sim_xxxx",          // Required，SimulationID
            "platform": "parallel",                // Optional: twitter / reddit / parallel (Default)
            "max_rounds": 100,                     // Optional: 最大Simulation轮数，用于截断过长的Simulation
            "enable_graph_memory_update": false,   // Optional: Whether将Agent活动动态Update到ZepGraph记忆
            "force": false                         // Optional: 强制重新Start（会StopRun中的Simulation并Clean upLog）
        }

    关于 force Parameters：
        - 启用后，IfSimulationCurrentlyRun或已Complete，会先Stop并Clean upRunLog
        - Clean up的Content包括：run_state.json, actions.jsonl, simulation.log 等
        - 不会Clean upConfigurationFile（simulation_config.json）和 profile File
        - 适用于需要重新RunSimulation的场景

    关于 enable_graph_memory_update：
        - 启用后，Simulation中所有Agent的活动（发帖、评论、点赞等）都会实时Update到ZepGraph
        - 这可以让Graph"记住"Simulation过程，用于后续分析或AI对话
        - 需要Simulation关联的Project有有效的 graph_id
        - 采用批量Update机制，减少APICall次数

    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "process_pid": 12345,
                "twitter_running": true,
                "reddit_running": true,
                "started_at": "2025-12-01T10:00:00",
                "graph_memory_update_enabled": true,  // Whether启用了Graph memory update
                "force_restarted": true               // Whether是强制重新Start
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400

        platform = data.get('platform', 'parallel')
        max_rounds = data.get('max_rounds')  # Optional：最大Simulation轮数
        enable_graph_memory_update = data.get('enable_graph_memory_update', False)  # Optional：Whether启用Graph memory update
        force = data.get('force', False)  # Optional：强制重新Start

        # Validate max_rounds Parameters
        if max_rounds is not None:
            try:
                max_rounds = int(max_rounds)
                if max_rounds <= 0:
                    return jsonify({
                        "success": False,
                        "error": "max_rounds 必须是正整数"
                    }), 400
            except (ValueError, TypeError):
                return jsonify({
                    "success": False,
                    "error": "max_rounds 必须是有效的整数"
                }), 400

        if platform not in ['twitter', 'reddit', 'parallel']:
            return jsonify({
                "success": False,
                "error": f"无效的PlatformType: {platform}，Optional: twitter/reddit/parallel"
            }), 400

        # 检查SimulationWhether已准备好
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)

        if not state:
            return jsonify({
                "success": False,
                "error": f"Simulation不存在: {simulation_id}"
            }), 404

        force_restarted = False
        
        # 智能ProcessStatus：If准备工作已Complete，允许重新Start
        if state.status != SimulationStatus.READY:
            # 检查准备工作Whether已Complete
            is_prepared, prepare_info = _check_simulation_prepared(simulation_id)

            if is_prepared:
                # 准备工作已Complete，检查Whether有CurrentlyRun的进程
                if state.status == SimulationStatus.RUNNING:
                    # 检查Simulation进程Whether真的在Run
                    run_state = SimulationRunner.get_run_state(simulation_id)
                    if run_state and run_state.runner_status.value == "running":
                        # 进程确实在Run
                        if force:
                            # 强制模式：StopRun中的Simulation
                            logger.info(f"强制模式：StopRun中的Simulation {simulation_id}")
                            try:
                                SimulationRunner.stop_simulation(simulation_id)
                            except Exception as e:
                                logger.warning(f"StopSimulation时出现警告: {str(e)}")
                        else:
                            return jsonify({
                                "success": False,
                                "error": f"SimulationCurrentlyRun中，请先Call /stop InterfaceStop，或使用 force=true 强制重新Start"
                            }), 400

                # If是强制模式，Clean upRunLog
                if force:
                    logger.info(f"强制模式：Clean upSimulationLog {simulation_id}")
                    cleanup_result = SimulationRunner.cleanup_simulation_logs(simulation_id)
                    if not cleanup_result.get("success"):
                        logger.warning(f"Clean upLog时出现警告: {cleanup_result.get('errors')}")
                    force_restarted = True

                # 进程不存在或已End，重置Status为 ready
                logger.info(f"Simulation {simulation_id} 准备工作已Complete，重置Status为 ready（原Status: {state.status.value}）")
                state.status = SimulationStatus.READY
                manager._save_simulation_state(state)
            else:
                # 准备工作未Complete
                return jsonify({
                    "success": False,
                    "error": f"Simulation未准备好，当前Status: {state.status.value}，请先Call /prepare Interface"
                }), 400
        
        # GetGraph ID（用于Graph memory update）
        graph_id = None
        if enable_graph_memory_update:
            # 从SimulationStatus或Project中Get graph_id
            graph_id = state.graph_id
            if not graph_id:
                # 尝试从Project中Get
                project = ProjectManager.get_project(state.project_id)
                if project:
                    graph_id = project.graph_id
            
            if not graph_id:
                return jsonify({
                    "success": False,
                    "error": "启用Graph memory update需要有效的 graph_id，请确保Project已Build graph"
                }), 400
            
            logger.info(f"启用Graph memory update: simulation_id={simulation_id}, graph_id={graph_id}")
        
        # StartSimulation
        run_state = SimulationRunner.start_simulation(
            simulation_id=simulation_id,
            platform=platform,
            max_rounds=max_rounds,
            enable_graph_memory_update=enable_graph_memory_update,
            graph_id=graph_id
        )
        
        # UpdateSimulationStatus
        state.status = SimulationStatus.RUNNING
        manager._save_simulation_state(state)
        
        response_data = run_state.to_dict()
        if max_rounds:
            response_data['max_rounds_applied'] = max_rounds
        response_data['graph_memory_update_enabled'] = enable_graph_memory_update
        response_data['force_restarted'] = force_restarted
        if enable_graph_memory_update:
            response_data['graph_id'] = graph_id
        
        return jsonify({
            "success": True,
            "data": response_data
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"StartSimulationFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/stop', methods=['POST'])
def stop_simulation():
    """
    StopSimulation
    
    Request（JSON）：
        {
            "simulation_id": "sim_xxxx"  // Required，SimulationID
        }
    
    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "stopped",
                "completed_at": "2025-12-01T12:00:00"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400
        
        run_state = SimulationRunner.stop_simulation(simulation_id)
        
        # UpdateSimulationStatus
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.PAUSED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"StopSimulationFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== 实时Status监控Interface ==============

@simulation_bp.route('/<simulation_id>/run-status', methods=['GET'])
def get_run_status(simulation_id: str):
    """
    GetSimulation runner实时Status（用于前端轮询）
    
    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                "total_rounds": 144,
                "progress_percent": 3.5,
                "simulated_hours": 2,
                "total_simulation_hours": 72,
                "twitter_running": true,
                "reddit_running": true,
                "twitter_actions_count": 150,
                "reddit_actions_count": 200,
                "total_actions_count": 350,
                "started_at": "2025-12-01T10:00:00",
                "updated_at": "2025-12-01T10:30:00"
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "current_round": 0,
                    "total_rounds": 0,
                    "progress_percent": 0,
                    "twitter_actions_count": 0,
                    "reddit_actions_count": 0,
                    "total_actions_count": 0,
                }
            })
        
        return jsonify({
            "success": True,
            "data": run_state.to_dict()
        })
        
    except Exception as e:
        logger.error(f"GetRunStatusFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/run-status/detail', methods=['GET'])
def get_run_status_detail(simulation_id: str):
    """
    GetSimulation runner详细Status（包含所有动作）
    
    用于前端展示实时动态
    
    QueryParameters：
        platform: 过滤Platform（twitter/reddit，Optional）
    
    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "runner_status": "running",
                "current_round": 5,
                ...
                "all_actions": [
                    {
                        "round_num": 5,
                        "timestamp": "2025-12-01T10:30:00",
                        "platform": "twitter",
                        "agent_id": 3,
                        "agent_name": "Agent Name",
                        "action_type": "CREATE_POST",
                        "action_args": {"content": "..."},
                        "result": null,
                        "success": true
                    },
                    ...
                ],
                "twitter_actions": [...],  # Twitter Platform的所有动作
                "reddit_actions": [...]    # Reddit Platform的所有动作
            }
        }
    """
    try:
        run_state = SimulationRunner.get_run_state(simulation_id)
        platform_filter = request.args.get('platform')
        
        if not run_state:
            return jsonify({
                "success": True,
                "data": {
                    "simulation_id": simulation_id,
                    "runner_status": "idle",
                    "all_actions": [],
                    "twitter_actions": [],
                    "reddit_actions": []
                }
            })
        
        # Get完整的动作List
        all_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter
        )
        
        # 分PlatformGet动作
        twitter_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="twitter"
        ) if not platform_filter or platform_filter == "twitter" else []
        
        reddit_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform="reddit"
        ) if not platform_filter or platform_filter == "reddit" else []
        
        # Get当前Round的动作（recent_actions 只展示最新一轮）
        current_round = run_state.current_round
        recent_actions = SimulationRunner.get_all_actions(
            simulation_id=simulation_id,
            platform=platform_filter,
            round_num=current_round
        ) if current_round > 0 else []
        
        # Get基础StatusInformation
        result = run_state.to_dict()
        result["all_actions"] = [a.to_dict() for a in all_actions]
        result["twitter_actions"] = [a.to_dict() for a in twitter_actions]
        result["reddit_actions"] = [a.to_dict() for a in reddit_actions]
        result["rounds_count"] = len(run_state.rounds)
        # recent_actions 只展示当前最新一轮两个Platform的Content
        result["recent_actions"] = [a.to_dict() for a in recent_actions]
        
        return jsonify({
            "success": True,
            "data": result
        })
        
    except Exception as e:
        logger.error(f"Get详细StatusFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/actions', methods=['GET'])
def get_simulation_actions(simulation_id: str):
    """
    GetSimulation中的Agent动作历史
    
    QueryParameters：
        limit: Return count（Default100）
        offset: Offset（Default0）
        platform: 过滤Platform（twitter/reddit）
        agent_id: 过滤Agent ID
        round_num: 过滤Round
    
    Return：
        {
            "success": true,
            "data": {
                "count": 100,
                "actions": [...]
            }
        }
    """
    try:
        limit = request.args.get('limit', 100, type=int)
        offset = request.args.get('offset', 0, type=int)
        platform = request.args.get('platform')
        agent_id = request.args.get('agent_id', type=int)
        round_num = request.args.get('round_num', type=int)
        
        actions = SimulationRunner.get_actions(
            simulation_id=simulation_id,
            limit=limit,
            offset=offset,
            platform=platform,
            agent_id=agent_id,
            round_num=round_num
        )
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(actions),
                "actions": [a.to_dict() for a in actions]
            }
        })
        
    except Exception as e:
        logger.error(f"Get动作历史Failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/timeline', methods=['GET'])
def get_simulation_timeline(simulation_id: str):
    """
    GetSimulation时间线（按Round汇总）
    
    用于前端展示进度条和时间线视图
    
    QueryParameters：
        start_round: Start round（Default0）
        end_round: End round（Default全部）
    
    Return每轮的汇总Information
    """
    try:
        start_round = request.args.get('start_round', 0, type=int)
        end_round = request.args.get('end_round', type=int)
        
        timeline = SimulationRunner.get_timeline(
            simulation_id=simulation_id,
            start_round=start_round,
            end_round=end_round
        )
        
        return jsonify({
            "success": True,
            "data": {
                "rounds_count": len(timeline),
                "timeline": timeline
            }
        })
        
    except Exception as e:
        logger.error(f"Get时间线Failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/agent-stats', methods=['GET'])
def get_agent_stats(simulation_id: str):
    """
    Get每个Agent的统计Information
    
    用于前端展示Agent活跃度排行、动作分布等
    """
    try:
        stats = SimulationRunner.get_agent_stats(simulation_id)
        
        return jsonify({
            "success": True,
            "data": {
                "agents_count": len(stats),
                "stats": stats
            }
        })
        
    except Exception as e:
        logger.error(f"GetAgent统计Failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Data库QueryInterface ==============

@simulation_bp.route('/<simulation_id>/posts', methods=['GET'])
def get_simulation_posts(simulation_id: str):
    """
    GetSimulation中的帖子
    
    QueryParameters：
        platform: PlatformType（twitter/reddit）
        limit: Return count（Default50）
        offset: Offset
    
    Return帖子List（从SQLiteData库读取）
    """
    try:
        platform = request.args.get('platform', 'reddit')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_file = f"{platform}_simulation.db"
        db_path = os.path.join(sim_dir, db_file)
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "platform": platform,
                    "count": 0,
                    "posts": [],
                    "message": "Data库不存在，Simulation可能尚未Run"
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                SELECT * FROM post 
                ORDER BY created_at DESC 
                LIMIT ? OFFSET ?
            """, (limit, offset))
            
            posts = [dict(row) for row in cursor.fetchall()]
            
            cursor.execute("SELECT COUNT(*) FROM post")
            total = cursor.fetchone()[0]
            
        except sqlite3.OperationalError:
            posts = []
            total = 0
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "platform": platform,
                "total": total,
                "count": len(posts),
                "posts": posts
            }
        })
        
    except Exception as e:
        logger.error(f"Get帖子Failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/<simulation_id>/comments', methods=['GET'])
def get_simulation_comments(simulation_id: str):
    """
    GetSimulation中的评论（仅Reddit）
    
    QueryParameters：
        post_id: 过滤帖子ID（Optional）
        limit: Return count
        offset: Offset
    """
    try:
        post_id = request.args.get('post_id')
        limit = request.args.get('limit', 50, type=int)
        offset = request.args.get('offset', 0, type=int)
        
        sim_dir = os.path.join(
            os.path.dirname(__file__),
            f'../../uploads/simulations/{simulation_id}'
        )
        
        db_path = os.path.join(sim_dir, "reddit_simulation.db")
        
        if not os.path.exists(db_path):
            return jsonify({
                "success": True,
                "data": {
                    "count": 0,
                    "comments": []
                }
            })
        
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        try:
            if post_id:
                cursor.execute("""
                    SELECT * FROM comment 
                    WHERE post_id = ?
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (post_id, limit, offset))
            else:
                cursor.execute("""
                    SELECT * FROM comment 
                    ORDER BY created_at DESC 
                    LIMIT ? OFFSET ?
                """, (limit, offset))
            
            comments = [dict(row) for row in cursor.fetchall()]
            
        except sqlite3.OperationalError:
            comments = []
        
        conn.close()
        
        return jsonify({
            "success": True,
            "data": {
                "count": len(comments),
                "comments": comments
            }
        })
        
    except Exception as e:
        logger.error(f"Get评论Failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interview 采访Interface ==============

@simulation_bp.route('/interview', methods=['POST'])
def interview_agent():
    """
    采访单个Agent

    Note：此功能需要Simulation环境处于RunStatus（CompleteSimulation循环后进入等待命令模式）

    Request（JSON）：
        {
            "simulation_id": "sim_xxxx",       // Required，SimulationID
            "agent_id": 0,                     // Required，Agent ID
            "prompt": "你对这件事有什么看法？",  // Required，采访问题
            "platform": "twitter",             // Optional，指定Platform（twitter/reddit）
                                               // 不指定时：双PlatformSimulation同时采访两个Platform
            "timeout": 60                      // Optional，Timeout时间（秒），Default60
        }

    Return（不指定platform，双Platform模式）：
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "你对这件事有什么看法？",
                "result": {
                    "agent_id": 0,
                    "prompt": "...",
                    "platforms": {
                        "twitter": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit": {"agent_id": 0, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }

    Return（指定platform）：
        {
            "success": true,
            "data": {
                "agent_id": 0,
                "prompt": "你对这件事有什么看法？",
                "result": {
                    "agent_id": 0,
                    "response": "我认为...",
                    "platform": "twitter",
                    "timestamp": "2025-12-08T10:00:00"
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        agent_id = data.get('agent_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # Optional：twitter/reddit/None
        timeout = data.get('timeout', 60)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400
        
        if agent_id is None:
            return jsonify({
                "success": False,
                "error": "请提供 agent_id"
            }), 400
        
        if not prompt:
            return jsonify({
                "success": False,
                "error": "请提供 prompt（采访问题）"
            }), 400
        
        # ValidateplatformParameters
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform Parameters只能是 'twitter' 或 'reddit'"
            }), 400
        
        # 检查环境Status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "Simulation环境未Run或已Close。请确保Simulation已Complete并进入等待命令模式。"
            }), 400
        
        # 优化prompt，添加前缀避免AgentCall工具
        optimized_prompt = optimize_interview_prompt(prompt)
        
        result = SimulationRunner.interview_agent(
            simulation_id=simulation_id,
            agent_id=agent_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"等待InterviewResponseTimeout: {str(e)}"
        }), 504
        
    except Exception as e:
        logger.error(f"InterviewFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/batch', methods=['POST'])
def interview_agents_batch():
    """
    批量采访多个Agent

    Note：此功能需要Simulation环境处于RunStatus

    Request（JSON）：
        {
            "simulation_id": "sim_xxxx",       // Required，SimulationID
            "interviews": [                    // Required，采访List
                {
                    "agent_id": 0,
                    "prompt": "你对A有什么看法？",
                    "platform": "twitter"      // Optional，指定该Agent的采访Platform
                },
                {
                    "agent_id": 1,
                    "prompt": "你对B有什么看法？"  // 不指定platform则使用Default值
                }
            ],
            "platform": "reddit",              // Optional，DefaultPlatform（被每项的platform覆盖）
                                               // 不指定时：双PlatformSimulation每个Agent同时采访两个Platform
            "timeout": 120                     // Optional，Timeout时间（秒），Default120
        }

    Return：
        {
            "success": true,
            "data": {
                "interviews_count": 2,
                "result": {
                    "interviews_count": 4,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        "twitter_1": {"agent_id": 1, "response": "...", "platform": "twitter"},
                        "reddit_1": {"agent_id": 1, "response": "...", "platform": "reddit"}
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        interviews = data.get('interviews')
        platform = data.get('platform')  # Optional：twitter/reddit/None
        timeout = data.get('timeout', 120)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400

        if not interviews or not isinstance(interviews, list):
            return jsonify({
                "success": False,
                "error": "请提供 interviews（采访List）"
            }), 400

        # ValidateplatformParameters
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform Parameters只能是 'twitter' 或 'reddit'"
            }), 400

        # Validate每个采访项
        for i, interview in enumerate(interviews):
            if 'agent_id' not in interview:
                return jsonify({
                    "success": False,
                    "error": f"采访List第{i+1}项缺少 agent_id"
                }), 400
            if 'prompt' not in interview:
                return jsonify({
                    "success": False,
                    "error": f"采访List第{i+1}项缺少 prompt"
                }), 400
            # Validate每项的platform（If有）
            item_platform = interview.get('platform')
            if item_platform and item_platform not in ("twitter", "reddit"):
                return jsonify({
                    "success": False,
                    "error": f"采访List第{i+1}项的platform只能是 'twitter' 或 'reddit'"
                }), 400

        # 检查环境Status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "Simulation环境未Run或已Close。请确保Simulation已Complete并进入等待命令模式。"
            }), 400

        # 优化每个采访项的prompt，添加前缀避免AgentCall工具
        optimized_interviews = []
        for interview in interviews:
            optimized_interview = interview.copy()
            optimized_interview['prompt'] = optimize_interview_prompt(interview.get('prompt', ''))
            optimized_interviews.append(optimized_interview)

        result = SimulationRunner.interview_agents_batch(
            simulation_id=simulation_id,
            interviews=optimized_interviews,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"等待批量InterviewResponseTimeout: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"批量InterviewFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/all', methods=['POST'])
def interview_all_agents():
    """
    全局采访 - 使用相同问题采访所有Agent

    Note：此功能需要Simulation环境处于RunStatus

    Request（JSON）：
        {
            "simulation_id": "sim_xxxx",            // Required，SimulationID
            "prompt": "你对这件事整体有什么看法？",  // Required，采访问题（所有Agent使用相同问题）
            "platform": "reddit",                   // Optional，指定Platform（twitter/reddit）
                                                    // 不指定时：双PlatformSimulation每个Agent同时采访两个Platform
            "timeout": 180                          // Optional，Timeout时间（秒），Default180
        }

    Return：
        {
            "success": true,
            "data": {
                "interviews_count": 50,
                "result": {
                    "interviews_count": 100,
                    "results": {
                        "twitter_0": {"agent_id": 0, "response": "...", "platform": "twitter"},
                        "reddit_0": {"agent_id": 0, "response": "...", "platform": "reddit"},
                        ...
                    }
                },
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}

        simulation_id = data.get('simulation_id')
        prompt = data.get('prompt')
        platform = data.get('platform')  # Optional：twitter/reddit/None
        timeout = data.get('timeout', 180)

        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400

        if not prompt:
            return jsonify({
                "success": False,
                "error": "请提供 prompt（采访问题）"
            }), 400

        # ValidateplatformParameters
        if platform and platform not in ("twitter", "reddit"):
            return jsonify({
                "success": False,
                "error": "platform Parameters只能是 'twitter' 或 'reddit'"
            }), 400

        # 检查环境Status
        if not SimulationRunner.check_env_alive(simulation_id):
            return jsonify({
                "success": False,
                "error": "Simulation环境未Run或已Close。请确保Simulation已Complete并进入等待命令模式。"
            }), 400

        # 优化prompt，添加前缀避免AgentCall工具
        optimized_prompt = optimize_interview_prompt(prompt)

        result = SimulationRunner.interview_all_agents(
            simulation_id=simulation_id,
            prompt=optimized_prompt,
            platform=platform,
            timeout=timeout
        )

        return jsonify({
            "success": result.get("success", False),
            "data": result
        })

    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400

    except TimeoutError as e:
        return jsonify({
            "success": False,
            "error": f"等待全局InterviewResponseTimeout: {str(e)}"
        }), 504

    except Exception as e:
        logger.error(f"全局InterviewFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/interview/history', methods=['POST'])
def get_interview_history():
    """
    GetInterview历史Record

    从SimulationData库中读取所有InterviewRecord

    Request（JSON）：
        {
            "simulation_id": "sim_xxxx",  // Required，SimulationID
            "platform": "reddit",          // Optional，PlatformType（reddit/twitter）
                                           // 不指定则Return两个Platform的所有历史
            "agent_id": 0,                 // Optional，只Get该Agent的采访历史
            "limit": 100                   // Optional，Return count，Default100
        }

    Return：
        {
            "success": true,
            "data": {
                "count": 10,
                "history": [
                    {
                        "agent_id": 0,
                        "response": "我认为...",
                        "prompt": "你对这件事有什么看法？",
                        "timestamp": "2025-12-08T10:00:00",
                        "platform": "reddit"
                    },
                    ...
                ]
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        platform = data.get('platform')  # 不指定则Return两个Platform的历史
        agent_id = data.get('agent_id')
        limit = data.get('limit', 100)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400

        history = SimulationRunner.get_interview_history(
            simulation_id=simulation_id,
            platform=platform,
            agent_id=agent_id,
            limit=limit
        )

        return jsonify({
            "success": True,
            "data": {
                "count": len(history),
                "history": history
            }
        })

    except Exception as e:
        logger.error(f"GetInterview历史Failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/env-status', methods=['POST'])
def get_env_status():
    """
    GetSimulation环境Status

    检查Simulation环境Whether存活（可以ReceiveInterview命令）

    Request（JSON）：
        {
            "simulation_id": "sim_xxxx"  // Required，SimulationID
        }

    Return：
        {
            "success": true,
            "data": {
                "simulation_id": "sim_xxxx",
                "env_alive": true,
                "twitter_available": true,
                "reddit_available": true,
                "message": "环境CurrentlyRun，可以ReceiveInterview命令"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400

        env_alive = SimulationRunner.check_env_alive(simulation_id)
        
        # Get更详细的StatusInformation
        env_status = SimulationRunner.get_env_status_detail(simulation_id)

        if env_alive:
            message = "环境CurrentlyRun，可以ReceiveInterview命令"
        else:
            message = "环境未Run或已Close"

        return jsonify({
            "success": True,
            "data": {
                "simulation_id": simulation_id,
                "env_alive": env_alive,
                "twitter_available": env_status.get("twitter_available", False),
                "reddit_available": env_status.get("reddit_available", False),
                "message": message
            }
        })

    except Exception as e:
        logger.error(f"Get环境StatusFailed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@simulation_bp.route('/close-env', methods=['POST'])
def close_simulation_env():
    """
    CloseSimulation环境
    
    向SimulationSendClose环境命令，使其优雅退出等待命令模式。
    
    Note：这不同于 /stop Interface，/stop 会强制终止进程，
    而此Interface会让Simulation优雅地Close环境并退出。
    
    Request（JSON）：
        {
            "simulation_id": "sim_xxxx",  // Required，SimulationID
            "timeout": 30                  // Optional，Timeout时间（秒），Default30
        }
    
    Return：
        {
            "success": true,
            "data": {
                "message": "环境Close命令已Send",
                "result": {...},
                "timestamp": "2025-12-08T10:00:01"
            }
        }
    """
    try:
        data = request.get_json() or {}
        
        simulation_id = data.get('simulation_id')
        timeout = data.get('timeout', 30)
        
        if not simulation_id:
            return jsonify({
                "success": False,
                "error": "请提供 simulation_id"
            }), 400
        
        result = SimulationRunner.close_simulation_env(
            simulation_id=simulation_id,
            timeout=timeout
        )
        
        # UpdateSimulationStatus
        manager = SimulationManager()
        state = manager.get_simulation(simulation_id)
        if state:
            state.status = SimulationStatus.COMPLETED
            manager._save_simulation_state(state)
        
        return jsonify({
            "success": result.get("success", False),
            "data": result
        })
        
    except ValueError as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 400
        
    except Exception as e:
        logger.error(f"Close环境Failed: {str(e)}")
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

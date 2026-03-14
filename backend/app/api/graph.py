"""
Graph相关API路由
采用Project上下文机制，Service端持久化Status
"""

import os
import traceback
import threading
from flask import request, jsonify

from . import graph_bp
from ..config import Config
from ..services.ontology_generator import OntologyGenerator
from ..services.graph_builder import GraphBuilderService
from ..services.text_processor import TextProcessor
from ..utils.file_parser import FileParser
from ..utils.logger import get_logger
from ..models.task import TaskManager, TaskStatus
from ..models.project import ProjectManager, ProjectStatus

# GetLog器
logger = get_logger('mirofish.api')


def allowed_file(filename: str) -> bool:
    """检查File扩展名Whether允许"""
    if not filename or '.' not in filename:
        return False
    ext = os.path.splitext(filename)[1].lower().lstrip('.')
    return ext in Config.ALLOWED_EXTENSIONS


# ============== Project管理Interface ==============

@graph_bp.route('/project/<project_id>', methods=['GET'])
def get_project(project_id: str):
    """
    GetProjectDetails
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": f"Project不存在: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": project.to_dict()
    })


@graph_bp.route('/project/list', methods=['GET'])
def list_projects():
    """
    列出所有Project
    """
    limit = request.args.get('limit', 50, type=int)
    projects = ProjectManager.list_projects(limit=limit)
    
    return jsonify({
        "success": True,
        "data": [p.to_dict() for p in projects],
        "count": len(projects)
    })


@graph_bp.route('/project/<project_id>', methods=['DELETE'])
def delete_project(project_id: str):
    """
    DeleteProject
    """
    success = ProjectManager.delete_project(project_id)
    
    if not success:
        return jsonify({
            "success": False,
            "error": f"Project不存在或DeleteFailed: {project_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "message": f"Project已Delete: {project_id}"
    })


@graph_bp.route('/project/<project_id>/reset', methods=['POST'])
def reset_project(project_id: str):
    """
    重置ProjectStatus（用于重新Build graph）
    """
    project = ProjectManager.get_project(project_id)
    
    if not project:
        return jsonify({
            "success": False,
            "error": f"Project不存在: {project_id}"
        }), 404
    
    # 重置到本体已GenerateStatus
    if project.ontology:
        project.status = ProjectStatus.ONTOLOGY_GENERATED
    else:
        project.status = ProjectStatus.CREATED
    
    project.graph_id = None
    project.graph_build_task_id = None
    project.error = None
    ProjectManager.save_project(project)
    
    return jsonify({
        "success": True,
        "message": f"Project已重置: {project_id}",
        "data": project.to_dict()
    })


# ============== Interface1：上传File并Generate ontology ==============

@graph_bp.route('/ontology/generate', methods=['POST'])
def generate_ontology():
    """
    Interface1：上传File，分析Generate ontology定义
    
    Request方式：multipart/form-data
    
    Parameters：
        files: 上传的File（PDF/MD/TXT），可多个
        simulation_requirement: Simulation需求Description（Required）
        project_name: ProjectName（Optional）
        additional_context: 额外Description（Optional）
        
    Return：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "ontology": {
                    "entity_types": [...],
                    "edge_types": [...],
                    "analysis_summary": "..."
                },
                "files": [...],
                "total_text_length": 12345
            }
        }
    """
    try:
        logger.info("=== StartGenerate ontology定义 ===")
        
        # GetParameters
        simulation_requirement = request.form.get('simulation_requirement', '')
        project_name = request.form.get('project_name', 'Unnamed Project')
        additional_context = request.form.get('additional_context', '')
        
        logger.debug(f"ProjectName: {project_name}")
        logger.debug(f"Simulation需求: {simulation_requirement[:100]}...")
        
        if not simulation_requirement:
            return jsonify({
                "success": False,
                "error": "请提供Simulation需求Description (simulation_requirement)"
            }), 400
        
        # Get上传的File
        uploaded_files = request.files.getlist('files')
        if not uploaded_files or all(not f.filename for f in uploaded_files):
            return jsonify({
                "success": False,
                "error": "请至少上传一个文档File"
            }), 400
        
        # CreateProject
        project = ProjectManager.create_project(name=project_name)
        project.simulation_requirement = simulation_requirement
        logger.info(f"CreateProject: {project.project_id}")
        
        # SaveFile并提取文本
        document_texts = []
        all_text = ""
        
        for file in uploaded_files:
            if file and file.filename and allowed_file(file.filename):
                # SaveFile到ProjectDirectory
                file_info = ProjectManager.save_file_to_project(
                    project.project_id, 
                    file, 
                    file.filename
                )
                project.files.append({
                    "filename": file_info["original_filename"],
                    "size": file_info["size"]
                })
                
                # 提取文本
                text = FileParser.extract_text(file_info["path"])
                text = TextProcessor.preprocess_text(text)
                document_texts.append(text)
                all_text += f"\n\n=== {file_info['original_filename']} ===\n{text}"
        
        if not document_texts:
            ProjectManager.delete_project(project.project_id)
            return jsonify({
                "success": False,
                "error": "没有SuccessProcess任何文档，请检查FileFormat"
            }), 400
        
        # Save提取的文本
        project.total_text_length = len(all_text)
        ProjectManager.save_extracted_text(project.project_id, all_text)
        logger.info(f"文本提取Complete，共 {len(all_text)} 字符")
        
        # Generate ontology
        logger.info("Call LLM Generate ontology定义...")
        generator = OntologyGenerator()
        ontology = generator.generate(
            document_texts=document_texts,
            simulation_requirement=simulation_requirement,
            additional_context=additional_context if additional_context else None
        )
        
        # Save本体到Project
        entity_count = len(ontology.get("entity_types", []))
        edge_count = len(ontology.get("edge_types", []))
        logger.info(f"Ontology generationComplete: {entity_count} 个EntityType, {edge_count} 个RelationshipType")
        
        project.ontology = {
            "entity_types": ontology.get("entity_types", []),
            "edge_types": ontology.get("edge_types", [])
        }
        project.analysis_summary = ontology.get("analysis_summary", "")
        project.status = ProjectStatus.ONTOLOGY_GENERATED
        ProjectManager.save_project(project)
        logger.info(f"=== Ontology generationComplete === Project ID: {project.project_id}")
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project.project_id,
                "project_name": project.name,
                "ontology": project.ontology,
                "analysis_summary": project.analysis_summary,
                "files": project.files,
                "total_text_length": project.total_text_length
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== Interface2：Build graph ==============

@graph_bp.route('/build', methods=['POST'])
def build_graph():
    """
    Interface2：根据project_idBuild graph
    
    Request（JSON）：
        {
            "project_id": "proj_xxxx",  // Required，来自Interface1
            "graph_name": "GraphName",    // Optional
            "chunk_size": 500,          // Optional，Default500
            "chunk_overlap": 50         // Optional，Default50
        }
        
    Return：
        {
            "success": true,
            "data": {
                "project_id": "proj_xxxx",
                "task_id": "task_xxxx",
                "message": "Graph buildingTask已Start"
            }
        }
    """
    try:
        logger.info("=== StartBuild graph ===")
        
        # 检查Configuration
        errors = []
        if not Config.ZEP_API_KEY:
            errors.append("ZEP_API_KEY未Configuration")
        if errors:
            logger.error(f"ConfigurationError: {errors}")
            return jsonify({
                "success": False,
                "error": "ConfigurationError: " + "; ".join(errors)
            }), 500
        
        # ParseRequest
        data = request.get_json() or {}
        project_id = data.get('project_id')
        logger.debug(f"Request parameters: project_id={project_id}")
        
        if not project_id:
            return jsonify({
                "success": False,
                "error": "请提供 project_id"
            }), 400
        
        # GetProject
        project = ProjectManager.get_project(project_id)
        if not project:
            return jsonify({
                "success": False,
                "error": f"Project不存在: {project_id}"
            }), 404
        
        # 检查ProjectStatus
        force = data.get('force', False)  # 强制重新构建
        
        if project.status == ProjectStatus.CREATED:
            return jsonify({
                "success": False,
                "error": "Project尚未Generate ontology，请先Call /ontology/generate"
            }), 400
        
        if project.status == ProjectStatus.GRAPH_BUILDING and not force:
            return jsonify({
                "success": False,
                "error": "GraphCurrently构建中，请勿重复提交。如需强制重建，请添加 force: true",
                "task_id": project.graph_build_task_id
            }), 400
        
        # If强制重建，重置Status
        if force and project.status in [ProjectStatus.GRAPH_BUILDING, ProjectStatus.FAILED, ProjectStatus.GRAPH_COMPLETED]:
            project.status = ProjectStatus.ONTOLOGY_GENERATED
            project.graph_id = None
            project.graph_build_task_id = None
            project.error = None
        
        # GetConfiguration
        graph_name = data.get('graph_name', project.name or 'MiroFish Graph')
        chunk_size = data.get('chunk_size', project.chunk_size or Config.DEFAULT_CHUNK_SIZE)
        chunk_overlap = data.get('chunk_overlap', project.chunk_overlap or Config.DEFAULT_CHUNK_OVERLAP)
        
        # UpdateProjectConfiguration
        project.chunk_size = chunk_size
        project.chunk_overlap = chunk_overlap
        
        # Get提取的文本
        text = ProjectManager.get_extracted_text(project_id)
        if not text:
            return jsonify({
                "success": False,
                "error": "未找到提取的文本Content"
            }), 400
        
        # Get本体
        ontology = project.ontology
        if not ontology:
            return jsonify({
                "success": False,
                "error": "未找到本体定义"
            }), 400
        
        # Create异步Task
        task_manager = TaskManager()
        task_id = task_manager.create_task(f"Build graph: {graph_name}")
        logger.info(f"CreateGraph buildingTask: task_id={task_id}, project_id={project_id}")
        
        # UpdateProjectStatus
        project.status = ProjectStatus.GRAPH_BUILDING
        project.graph_build_task_id = task_id
        ProjectManager.save_project(project)
        
        # Start后台Task
        def build_task():
            build_logger = get_logger('mirofish.build')
            try:
                build_logger.info(f"[{task_id}] StartBuild graph...")
                task_manager.update_task(
                    task_id, 
                    status=TaskStatus.PROCESSING,
                    message="InitializeGraph buildingService..."
                )
                
                # CreateGraph buildingService
                builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
                
                # 分块
                task_manager.update_task(
                    task_id,
                    message="文本分块中...",
                    progress=5
                )
                chunks = TextProcessor.split_text(
                    text, 
                    chunk_size=chunk_size, 
                    overlap=chunk_overlap
                )
                total_chunks = len(chunks)
                
                # CreateGraph
                task_manager.update_task(
                    task_id,
                    message="CreateZepGraph...",
                    progress=10
                )
                graph_id = builder.create_graph(name=graph_name)
                
                # UpdateProject的graph_id
                project.graph_id = graph_id
                ProjectManager.save_project(project)
                
                # Settings本体
                task_manager.update_task(
                    task_id,
                    message="Settings本体定义...",
                    progress=15
                )
                builder.set_ontology(graph_id, ontology)
                
                # 添加文本（progress_callback 签名是 (msg, progress_ratio)）
                def add_progress_callback(msg, progress_ratio):
                    progress = 15 + int(progress_ratio * 40)  # 15% - 55%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                
                task_manager.update_task(
                    task_id,
                    message=f"Start添加 {total_chunks} 个文本块...",
                    progress=15
                )
                
                episode_uuids = builder.add_text_batches(
                    graph_id, 
                    chunks,
                    batch_size=3,
                    progress_callback=add_progress_callback
                )
                
                # 等待ZepProcessComplete（Query每个episode的processedStatus）
                task_manager.update_task(
                    task_id,
                    message="等待ZepProcessData...",
                    progress=55
                )
                
                def wait_progress_callback(msg, progress_ratio):
                    progress = 55 + int(progress_ratio * 35)  # 55% - 90%
                    task_manager.update_task(
                        task_id,
                        message=msg,
                        progress=progress
                    )
                
                builder._wait_for_episodes(episode_uuids, wait_progress_callback)
                
                # Get graph data
                task_manager.update_task(
                    task_id,
                    message="Get graph data...",
                    progress=95
                )
                graph_data = builder.get_graph_data(graph_id)
                
                # UpdateProjectStatus
                project.status = ProjectStatus.GRAPH_COMPLETED
                ProjectManager.save_project(project)
                
                node_count = graph_data.get("node_count", 0)
                edge_count = graph_data.get("edge_count", 0)
                build_logger.info(f"[{task_id}] Graph buildingComplete: graph_id={graph_id}, Node={node_count}, 边={edge_count}")
                
                # Complete
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.COMPLETED,
                    message="Graph buildingComplete",
                    progress=100,
                    result={
                        "project_id": project_id,
                        "graph_id": graph_id,
                        "node_count": node_count,
                        "edge_count": edge_count,
                        "chunk_count": total_chunks
                    }
                )
                
            except Exception as e:
                # UpdateProjectStatus为Failed
                build_logger.error(f"[{task_id}] Graph buildingFailed: {str(e)}")
                build_logger.debug(traceback.format_exc())
                
                project.status = ProjectStatus.FAILED
                project.error = str(e)
                ProjectManager.save_project(project)
                
                task_manager.update_task(
                    task_id,
                    status=TaskStatus.FAILED,
                    message=f"构建Failed: {str(e)}",
                    error=traceback.format_exc()
                )
        
        # Start后台线程
        thread = threading.Thread(target=build_task, daemon=True)
        thread.start()
        
        return jsonify({
            "success": True,
            "data": {
                "project_id": project_id,
                "task_id": task_id,
                "message": "Graph buildingTask已Start，请通过 /task/{task_id} Query进度"
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# ============== TaskQueryInterface ==============

@graph_bp.route('/task/<task_id>', methods=['GET'])
def get_task(task_id: str):
    """
    Query task status
    """
    task = TaskManager().get_task(task_id)
    
    if not task:
        return jsonify({
            "success": False,
            "error": f"Task不存在: {task_id}"
        }), 404
    
    return jsonify({
        "success": True,
        "data": task.to_dict()
    })


@graph_bp.route('/tasks', methods=['GET'])
def list_tasks():
    """
    列出所有Task
    """
    tasks = TaskManager().list_tasks()
    
    return jsonify({
        "success": True,
        "data": [t.to_dict() for t in tasks],
        "count": len(tasks)
    })


# ============== GraphDataInterface ==============

@graph_bp.route('/data/<graph_id>', methods=['GET'])
def get_graph_data(graph_id: str):
    """
    Get graph data（Node和边）
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY未Configuration"
            }), 500
        
        builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
        graph_data = builder.get_graph_data(graph_id)
        
        return jsonify({
            "success": True,
            "data": graph_data
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@graph_bp.route('/delete/<graph_id>', methods=['DELETE'])
def delete_graph(graph_id: str):
    """
    DeleteZepGraph
    """
    try:
        if not Config.ZEP_API_KEY:
            return jsonify({
                "success": False,
                "error": "ZEP_API_KEY未Configuration"
            }), 500
        
        builder = GraphBuilderService(api_key=Config.ZEP_API_KEY)
        builder.delete_graph(graph_id)
        
        return jsonify({
            "success": True,
            "message": f"Graph已Delete: {graph_id}"
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500

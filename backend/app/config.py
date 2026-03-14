"""
Configuration管理
统一从Project根Directory的 .env FileLoadConfiguration
"""

import os
from dotenv import load_dotenv

# LoadProject根Directory的 .env File
# Path: MiroFish/.env (相对于 backend/app/config.py)
project_root_env = os.path.join(os.path.dirname(__file__), '../../.env')

if os.path.exists(project_root_env):
    load_dotenv(project_root_env, override=True)
else:
    # If根Directory没有 .env，尝试Load环境Variable（用于生产环境）
    load_dotenv(override=True)


class Config:
    """FlaskConfiguration类"""
    
    # FlaskConfiguration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mirofish-secret-key')
    DEBUG = os.environ.get('FLASK_DEBUG', 'True').lower() == 'true'
    
    # JSONConfiguration - 禁用ASCII转义，让中文直接显示（而不是 \uXXXX Format）
    JSON_AS_ASCII = False
    
    # LLMConfiguration（统一使用OpenAIFormat）
    LLM_API_KEY = os.environ.get('LLM_API_KEY')
    LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'https://api.openai.com/v1')
    LLM_MODEL_NAME = os.environ.get('LLM_MODEL_NAME', 'gpt-4o-mini')
    
    # ZepConfiguration
    ZEP_API_KEY = os.environ.get('ZEP_API_KEY')
    
    # File上传Configuration
    MAX_CONTENT_LENGTH = 50 * 1024 * 1024  # 50MB
    UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), '../uploads')
    ALLOWED_EXTENSIONS = {'pdf', 'md', 'txt', 'markdown'}
    
    # Text processingConfiguration
    DEFAULT_CHUNK_SIZE = 500  # Default切块大小
    DEFAULT_CHUNK_OVERLAP = 50  # Default重叠大小
    
    # OASISSimulationConfiguration
    OASIS_DEFAULT_MAX_ROUNDS = int(os.environ.get('OASIS_DEFAULT_MAX_ROUNDS', '10'))
    OASIS_SIMULATION_DATA_DIR = os.path.join(os.path.dirname(__file__), '../uploads/simulations')
    
    # OASISPlatform可用动作Configuration
    OASIS_TWITTER_ACTIONS = [
        'CREATE_POST', 'LIKE_POST', 'REPOST', 'FOLLOW', 'DO_NOTHING', 'QUOTE_POST'
    ]
    OASIS_REDDIT_ACTIONS = [
        'LIKE_POST', 'DISLIKE_POST', 'CREATE_POST', 'CREATE_COMMENT',
        'LIKE_COMMENT', 'DISLIKE_COMMENT', 'SEARCH_POSTS', 'SEARCH_USER',
        'TREND', 'REFRESH', 'DO_NOTHING', 'FOLLOW', 'MUTE'
    ]
    
    # Report AgentConfiguration
    REPORT_AGENT_MAX_TOOL_CALLS = int(os.environ.get('REPORT_AGENT_MAX_TOOL_CALLS', '5'))
    REPORT_AGENT_MAX_REFLECTION_ROUNDS = int(os.environ.get('REPORT_AGENT_MAX_REFLECTION_ROUNDS', '2'))
    REPORT_AGENT_TEMPERATURE = float(os.environ.get('REPORT_AGENT_TEMPERATURE', '0.5'))
    
    @classmethod
    def validate(cls):
        """Validate必要Configuration"""
        errors = []
        if not cls.LLM_API_KEY:
            errors.append("LLM_API_KEY 未Configuration")
        if not cls.ZEP_API_KEY:
            errors.append("ZEP_API_KEY 未Configuration")
        return errors


"""
MemBrain (Refactor) 配置管理
读取 .env 并暴露为模块级常量；支持运行时修改并持久化回 .env
"""
import os
from pathlib import Path

from dotenv import load_dotenv, set_key

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 加载 .env
load_dotenv(Path(PROJECT_ROOT) / ".env")


def _bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _int(value, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


# ===================== 服务 =====================
HOST = os.getenv("HOST", "0.0.0.0")
PORT = _int(os.getenv("PORT"), 8000)

# ===================== LLM 提供商 =====================
# provider: ollama（本地）/ openai（远程 OpenAI 兼容接口：DeepSeek/百度/OpenAI 等）
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()

# ---- 本地 Ollama ----
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# ---- 远程 OpenAI 兼容 ----
# 空表示未配置；需要 base_url + api_key + model
LLM_API_BASE_URL = os.getenv("LLM_API_BASE_URL", "").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
# 远程主模型与工具模型（remote 模式下工具/抽取用 remote_tool_model，可同主模型）
LLM_REMOTE_MODEL = os.getenv("LLM_REMOTE_MODEL", "").strip()
LLM_REMOTE_TOOL_MODEL = os.getenv("LLM_REMOTE_TOOL_MODEL", "").strip()

# ---- 模型名（按 provider 决定最终用哪个） ----
LLM_MODEL = os.getenv("LLM_MODEL", "qwen3.5:9b")
TOOL_LLM_MODEL = os.getenv("TOOL_LLM_MODEL", "qwen2.5:7b")
# 采样温度（角色扮演：略高有活气；0.0-1.0）
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.85"))

# ===================== 百度搜索 =====================
BAIDU_API_KEY = os.getenv("BAIDU_API_KEY", "")
BAIDU_API_SECRET = os.getenv("BAIDU_API_SECRET", "")

# ===================== 嵌入模型 =====================
# 嵌入模式：ollama（nomic-embed-text）/ local（本地 sentence-transformers）
EMBEDDING_MODE = os.getenv("EMBEDDING_MODE", "ollama")
EMBEDDING_MODEL_DIR = str(Path(PROJECT_ROOT) / os.getenv("EMBEDDING_MODEL_DIR", "models/all-MiniLM-L6-v2"))
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2")
# Ollama 嵌入模型（EMBEDDING_MODE=ollama 时使用）
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# ===================== 重排序（Cross-Encoder） =====================
# 是否启用重排序
ENABLE_RERANK = _bool(os.getenv("ENABLE_RERANK", "true"))
# 重排器选择：bge（BAAI/bge-reranker-v2-m3，推荐，中文友好）/ minilm（旧 ms-marco，英文场景）
# 留空时按模型存在情况自动选择：优先 bge，其次 minilm
RERANKER_BACKEND = os.getenv("RERANKER_BACKEND", "bge")

# BGE reranker（BAAI/bge-reranker-v2-m3，ONNX 格式，中文支持好）
BGE_RERANKER_DIR = str(Path(PROJECT_ROOT) / os.getenv(
    "BGE_RERANKER_DIR", "models/bge-reranker-v2-m3"
))
BGE_RERANKER_ONNX = os.getenv(
    "BGE_RERANKER_ONNX", "model.onnx"
)

# 旧 MS-MARCO reranker（英文场景）
CROSS_ENCODER_ONNX_PATH = str(Path(PROJECT_ROOT) / os.getenv(
    "CROSS_ENCODER_ONNX_PATH", "models/ms-marco-MiniLM-L-6-v2/ms-marco-MiniLM-L-6-v2.onnx"
))

# ChromaDB 数据目录
CHROMA_DB_PATH = str(Path(PROJECT_ROOT) / "chromadb")

# ===================== 记忆系统 =====================
MEMORY_CONTEXT_MAX_ROUNDS = _int(os.getenv("MEMORY_CONTEXT_MAX_ROUNDS"), 50)
MEMORY_SHORT_TERM_MAX_ROUNDS = _int(os.getenv("MEMORY_SHORT_TERM_MAX_ROUNDS"), 50)
MEMORY_IMPORTANCE_THRESHOLD = float(os.getenv("MEMORY_IMPORTANCE_THRESHOLD", "0.6"))
MEMORY_FACT_DECAY_DAYS = _int(os.getenv("MEMORY_FACT_DECAY_DAYS"), 90)
MEMORY_DEBUG = _bool(os.getenv("MEMORY_DEBUG"))

# ===================== L3 主动信息池 =====================
# 是否启用 L3 采集/推送
L3_ENABLED = _bool(os.getenv("L3_ENABLED"))
# 采集周期（秒）：定时拉取外部信息源
L3_UPDATE_INTERVAL = _int(os.getenv("L3_UPDATE_INTERVAL"), 7200)
# 推送周期（秒）：扫描未推送的 L3 条目并让 Agent 主动开口
L3_PUSH_INTERVAL = _int(os.getenv("L3_PUSH_INTERVAL"), 300)
# 采集关键词（逗号分隔）
L3_KEYWORDS = [k.strip() for k in os.getenv("L3_KEYWORDS", "").split(",") if k.strip()]
# L3 池最大条目数（超出按时间清理，0=不限）
L3_MAX_ITEMS = _int(os.getenv("L3_MAX_ITEMS"), 200)

# 角色配置
ROLES_FILE = str(Path(PROJECT_ROOT) / "config" / "roles.json")
ROLE_PROMPTS_DIR = str(Path(PROJECT_ROOT) / "role_prompts")
AVATARS_DIR = str(Path(PROJECT_ROOT) / "static" / "avatars" / "agents")
UPLOADS_DIR = str(Path(PROJECT_ROOT) / "uploads")

# ===================== 可控配置（供后台管理读写） =====================
# key -> (描述, 类型, 默认值)
EDITABLE_KEYS = {
    "LLM_MODEL": ("主模型", "str", LLM_MODEL),
    "TOOL_LLM_MODEL": ("工具模型", "str", TOOL_LLM_MODEL),
    "OLLAMA_HOST": ("Ollama 地址", "str", OLLAMA_HOST),
    "MEMORY_CONTEXT_MAX_ROUNDS": ("L1 上下文轮数", "int", MEMORY_CONTEXT_MAX_ROUNDS),
    "MEMORY_SHORT_TERM_MAX_ROUNDS": ("L2 短期记忆轮数", "int", MEMORY_SHORT_TERM_MAX_ROUNDS),
    "MEMORY_IMPORTANCE_THRESHOLD": ("事实抽取阈值", "float", MEMORY_IMPORTANCE_THRESHOLD),
    "MEMORY_FACT_DECAY_DAYS": ("事实衰减天数", "int", MEMORY_FACT_DECAY_DAYS),
    "MEMORY_DEBUG": ("调试输出", "bool", MEMORY_DEBUG),
    "BAIDU_API_KEY": ("百度搜索 Key", "str", BAIDU_API_KEY),
}


def get_config_snapshot() -> list:
    """返回后台管理可读的配置列表"""
    result = []
    for key, (desc, dtype, _default) in EDITABLE_KEYS.items():
        result.append({
            "key": key,
            "desc": desc,
            "type": dtype,
            "value": _current_value(key, dtype),
        })
    return result


def _current_value(key: str, dtype: str):
    raw = os.getenv(key)
    if raw is None:
        return None
    if dtype == "int":
        try:
            return int(float(raw))
        except (TypeError, ValueError):
            return None
    if dtype == "float":
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    if dtype == "bool":
        return _bool(raw)
    return raw


def update_config(key: str, value: str) -> bool:
    """修改配置并持久化到 .env，同时刷新 os.environ"""
    if key not in EDITABLE_KEYS:
        return False
    env_path = Path(PROJECT_ROOT) / ".env"
    set_key(str(env_path), key, str(value))
    os.environ[key] = str(value)
    return True

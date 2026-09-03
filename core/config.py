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


def _int(value, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value, default: float = 0.0) -> float:
    try:
        return float(value)
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

# ===================== 外部 MCP 扩展（星露谷等） =====================
# 是否启用外部 MCP 游戏扩展（默认关闭：他人拉取无游戏/无环境也不受影响）。
# 开启后才会去读取 config/mcp.json 并注册 stardew 工具；关闭时这些工具完全不注册。
# 可在管理后台 /admin/config 里修改。
STARDEW_MCP_ENABLED = _bool(os.getenv("STARDEW_MCP_ENABLED", "false"))

# ===================== L3 主动信息池 =====================
# 是否启用 L3 采集/推送（默认启用；设为 false 可关闭）
L3_ENABLED = _bool(os.getenv("L3_ENABLED", "true"))
# 采集周期（秒）：定时拉取外部信息源
L3_UPDATE_INTERVAL = _int(os.getenv("L3_UPDATE_INTERVAL"), 7200)
# 推送周期（秒）：扫描未推送的 L3 条目并让 Agent 主动开口
L3_PUSH_INTERVAL = _int(os.getenv("L3_PUSH_INTERVAL"), 300)
# 采集关键词（逗号分隔）
L3_KEYWORDS = [k.strip() for k in os.getenv("L3_KEYWORDS", "").split(",") if k.strip()]
# L3 池最大条目数（超出按时间清理，0=不限）
L3_MAX_ITEMS = _int(os.getenv("L3_MAX_ITEMS"), 200)

# ===================== 日程/提醒引擎 =====================
# 提醒调度线程的扫描间隔（秒）
REMINDER_SCAN_INTERVAL = _int(os.getenv("REMINDER_SCAN_INTERVAL"), 15)
# 提醒持久化文件（JSON）
REMINDER_FILE = str(Path(PROJECT_ROOT) / os.getenv("REMINDER_FILE", "reminders.json"))

# ===================== 感知层 =====================
# 是否启用感知层（时序/系统/情境/情绪趋势）
PERCEPTION_ENABLED = _bool(os.getenv("PERCEPTION_ENABLED", "true"))
# 用户常驻城市（位置情境；若不填则只给时段情境）
PERCEPTION_CITY = os.getenv("PERCEPTION_CITY", "").strip()
# 感知历史（活跃时段 + 情绪曲线）持久化文件
PERCEPTION_FILE = str(Path(PROJECT_ROOT) / os.getenv("PERCEPTION_FILE", "perception.json"))
# 情绪趋势保留的样本数 / 活跃时段窗口天数
MOOD_TREND_MAX_SAMPLES = _int(os.getenv("MOOD_TREND_MAX_SAMPLES"), 200)
ROUTINE_WINDOW_DAYS = _int(os.getenv("ROUTINE_WINDOW_DAYS"), 30)

# ===================== 环境感知工具（LLM 主动查询） =====================
# 总开关：注册并暴露给 LLM 的"环境感知工具"（标签页 / 前台窗口 / 感知摘要）。
# 默认开启；工具组内每个工具（标签页/前台窗口/感知摘要）可单独关闭（见下方各开关）。
# 注：标签页读取仍需浏览器以 --remote-debugging-port 启动，读不到时如实说明，不伪造。
ENVIRONMENT_SENSING_ENABLED = _bool(os.getenv("ENVIRONMENT_SENSING_ENABLED", "true"))
# 浏览器远程调试端口（用户需以 --remote-debugging-port=此端口 启动 Chrome/Edge）
BROWSER_DEBUG_PORT = _int(os.getenv("BROWSER_DEBUG_PORT", "9222"), 9222)
# 浏览器 CDP 请求超时（秒）
BROWSER_SENSING_TIMEOUT = _float(os.getenv("BROWSER_SENSING_TIMEOUT", "3"), 3.0)
# 标签页标题最多保留字符数（截断噪音）
MAX_TAB_TITLE_CHARS = _int(os.getenv("MAX_TAB_TITLE_CHARS", "40"), 40)
# 浏览器标签感知开关（在总开关开启前提下，单独控制是否读取当前标签页；false 则 get_current_tab 返回未开启）
BROWSER_TAB_SENSING_ENABLED = _bool(os.getenv("BROWSER_TAB_SENSING_ENABLED", "true"))
# 感知摘要工具开关（在总开关开启前提下，单独控制 get_perception_summary 是否可用）
PERCEPTION_SUMMARY_SENSING_ENABLED = _bool(os.getenv("PERCEPTION_SUMMARY_SENSING_ENABLED", "true"))

# ===================== 前台窗口感知（纯本地） =====================
# 通过 Windows 原生 GetForegroundWindow 读取"用户当前聚焦的前台窗口"（应用+标题），
# 与浏览器标签页感知互补（不限浏览器）。仅 Windows 生效；失败置空，不伪造。
FOREGROUND_SENSING_ENABLED = _bool(os.getenv("FOREGROUND_SENSING_ENABLED", "true"))
# 采集超时（秒，防卡住）
FOREGROUND_SENSING_TIMEOUT = _float(os.getenv("FOREGROUND_SENSING_TIMEOUT", "2"), 2.0)
# 感知→表达触发对齐：前台/标签页变化时给低频触发提示。默认开（需总开关也开才生效）。
SENSING_TRIGGER_ENABLED = _bool(os.getenv("SENSING_TRIGGER_ENABLED", "true"))


# ===================== 关系记忆内核（自我模型 / 共同经历 / 情绪衰减） =====================
# 关系记忆 JSON 持久化文件（与 ChromaDB 解耦，避免嵌入调用）
RELATION_MEMORY_FILE = str(Path(PROJECT_ROOT) / os.getenv("RELATION_MEMORY_FILE", "relation_memory.json"))
# 是否启用关系记忆内核（写经历账本 / 情绪衰减 / 反思线程）
RELATION_MEMORY_ENABLED = _bool(os.getenv("RELATION_MEMORY_ENABLED", "true"))
# 情绪/好感度/经历随时间衰减的半衰期（天）；0=不衰减
RELATION_EMOTION_HALFLIFE_DAYS = _float(os.getenv("RELATION_EMOTION_HALFLIFE_DAYS", "21"))
# 写入经历账本所需的最小情绪共振（0-1）；低于此值的日常闲聊不写入经历
RELATION_EPISODE_RESONANCE_THRESHOLD = _float(os.getenv("RELATION_EPISODE_RESONANCE_THRESHOLD", "0.55"))
# 反思线程启动间隔（秒）；0=禁用后台自动反思
RELATION_REFLECT_INTERVAL = _int(os.getenv("RELATION_REFLECT_INTERVAL", "3600"))
# 单次反思最多处理多少条候选经历
RELATION_REFLECT_BATCH = _int(os.getenv("RELATION_REFLECT_BATCH", "20"))

# ===================== Live2D 情绪身体表达（B/C 方案开关） =====================
# 控制 LLM 如何"用身体表达情绪"：
#   - "C"（默认推荐）：内核自动映射。LLM 只需自然地说话，内核把情绪/文本映射为行为
#     （改 system prompt 告知 LLM 有身体，并确保 behavior 携带 emotion 供前端 A 状态层保持）
#   - "B"：LLM 主动指挥。额外注册 express_body 工具，让 LLM 在回复时主动指定动作/表情，
#     并调用工具（更"懂"身体，但多一次工具调用，token/延迟增加）
LIVE2D_BODY_MODE = os.getenv("LIVE2D_BODY_MODE", "C").strip().upper()

# ===================== Live2D（桌面宠物模型） =====================
# 模型根目录：此目录下每个子目录视为一个可用模型（内含 model.json）
LIVE2D_MODEL_ROOT = str(Path(PROJECT_ROOT) / os.getenv("LIVE2D_MODEL_ROOT", "live2d"))
# 运行时类型：l2dwidget（live2d-widget.js，Cubism2 .moc；当前唯一启用）
# 预留：以后可切换 pixi-live2d-display / 自研渲染器，通过渲染器适配层接入
LIVE2D_RENDERER = os.getenv("LIVE2D_RENDERER", "l2dwidget").strip().lower()
# 默认选中哪个模型（目录相对 LIVE2D_MODEL_ROOT 的路径，含 model.json 的目录）
LIVE2D_DEFAULT_MODEL = os.getenv("LIVE2D_DEFAULT_MODEL", "").strip()
# 是否启用 Live2D 独立页 / 模型请求（false 可整体关闭）
LIVE2D_ENABLED = _bool(os.getenv("LIVE2D_ENABLED", "true"))

# ===================== 助手工具（文件沙箱） =====================
# 角色可读写的沙箱根目录（read_file/write_file/list_files 仅限此目录及 uploads）
ASSISTANT_WORKSPACE_DIR = str(Path(PROJECT_ROOT) / os.getenv("ASSISTANT_WORKSPACE_DIR", "assistant_workspace"))

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
    "LIVE2D_BODY_MODE": ("Live2D 情绪表达模式 (B/C)", "str", LIVE2D_BODY_MODE),
    "STARDEW_MCP_ENABLED": ("星露谷 MCP 扩展", "bool", STARDEW_MCP_ENABLED),
    "STARDEW_MEMORY_POLLER_ENABLED": ("星露谷记忆自动沉淀", "bool", _bool(os.getenv("STARDEW_MEMORY_POLLER_ENABLED", "false"))),
    "STARDEW_POLL_INTERVAL": ("星露谷状态轮询间隔(秒)", "float", float(os.getenv("STARDEW_POLL_INTERVAL", "60"))),
    "ENVIRONMENT_SENSING_ENABLED": ("浏览器感知（标签页/前台窗口/摘要）总开关", "bool", ENVIRONMENT_SENSING_ENABLED),
    "BROWSER_DEBUG_PORT": ("浏览器远程调试端口", "int", BROWSER_DEBUG_PORT),
    "BROWSER_TAB_SENSING_ENABLED": ("标签页感知（读当前浏览器标签）", "bool", BROWSER_TAB_SENSING_ENABLED),
    "FOREGROUND_SENSING_ENABLED": ("前台窗口感知（读当前前台应用）", "bool", FOREGROUND_SENSING_ENABLED),
    "PERCEPTION_SUMMARY_SENSING_ENABLED": ("感知摘要工具（get_perception_summary）", "bool", PERCEPTION_SUMMARY_SENSING_ENABLED),
    "SENSING_TRIGGER_ENABLED": ("感知→表达触发提示", "bool", SENSING_TRIGGER_ENABLED),
}


def get_config_snapshot() -> list:
    """返回后台管理可读的配置列表"""
    result = []
    for key, (desc, dtype, _default) in EDITABLE_KEYS.items():
        v = _current_value(key, dtype)
        # 未在 .env 显式配置时,显示模块默认值(便于后台看到当前生效值)
        if v is None:
            try:
                if dtype == "str":
                    v = str(_default)
                elif dtype == "int":
                    v = int(_default)
                elif dtype == "float":
                    v = float(_default)
                elif dtype == "bool":
                    v = bool(_default)
            except Exception:
                v = None
        result.append({
            "key": key,
            "desc": desc,
            "type": dtype,
            "value": v,
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

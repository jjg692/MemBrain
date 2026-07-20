# core/config.py
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent

# 加载 .env 配置
load_dotenv(dotenv_path=BASE_DIR / ".env")

# 从环境变量读取配置
LLM_MODEL = os.getenv("LLM_MODEL")              # 主模型（9B，如 qwen3.5:9b）
TOOL_LLM_MODEL = os.getenv("TOOL_LLM_MODEL")    # 工具模型（7B，如 qwen2.5:7b）
API_BASE = os.getenv("API_BASE")
API_KEY = os.getenv("API_KEY")
HOST = os.getenv("HOST")
PORT = int(os.getenv("PORT", 8000))
BAIDU_API_KEY = os.getenv("BAIDU_API_KEY")
CROSS_ENCODER_MODEL = os.getenv("CROSS_ENCODER_MODEL")

# ChromaDB 路径：优先用 .env 配置，否则默认用项目目录下的 chromadb
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH") or str(BASE_DIR / "chromadb")

# ==================== 记忆系统配置 ====================
# L1: 内存上下文最大轮数（每轮=一问一答）
MEMORY_CONTEXT_MAX_ROUNDS = 20

# L2: 短期记忆保留最大轮数（向量库）
MEMORY_SHORT_TERM_MAX_ROUNDS = 50

# L4: 重要性阈值，高于此值才抽取事实
MEMORY_IMPORTANCE_THRESHOLD = 0.35

# 调试开关
# ==================== 记忆系统配置 ====================
# L1: 内存上下文最大轮数（每轮=一问一答）
MEMORY_CONTEXT_MAX_ROUNDS = 20

# L2: 短期记忆保留最大轮数（向量库）
MEMORY_SHORT_TERM_MAX_ROUNDS = 50

# L4: 重要性阈值，高于此值才抽取事实
MEMORY_IMPORTANCE_THRESHOLD = 0.35

# ==================== 时间衰减 & 模糊化 ====================
# 半衰期（天）：超过此天数的记忆，衰减因子降至 0.5
MEMORY_HALF_LIFE_DAYS = 7

# 模糊化阈值（天）：超过此天数未被访问的记忆，触发模糊化
MEMORY_FUZZY_DAYS = 14

# 模糊化后保留的摘要长度（字符数）
MEMORY_FUZZY_SUMMARY_LENGTH = 50

# 调试开关
MEMORY_DEBUG = True

# 在 config.py 末尾追加
RSS_FEEDS = [
    "https://rsshub.app/bilibili/ranking/0/3",  # B站热门
    # 用户可自行配置
]
L3_UPDATE_INTERVAL = 1800  # 30分钟
L3_PUSH_INTERVAL = 10
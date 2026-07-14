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

# ChromaDB 路径：优先用 .env 配置，否则默认用项目目录下的 chromadb
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH") or str(BASE_DIR / "chromadb")
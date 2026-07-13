import os
import json
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import chromadb
import ollama
import time
from datetime import datetime
from langchain.chat_models import init_chat_model
from dotenv import load_dotenv
import requests
from agent_graph import LangGraphMemoryAgent
from chromadb.utils import embedding_functions
from tts_client import text_to_speech, clean_text_for_tts, detect_language
import base64


# ================== 配置 ==================
BASE_DIR = Path(__file__).parent
# ChromaDB 路径：优先用 .env 配置，否则默认用项目目录下的 chromadb
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH") or str(BASE_DIR / "chromadb")

# 加载 .env 配置
load_dotenv(dotenv_path=BASE_DIR / ".env")

# 从环境变量读取配置
LLM_MODEL = os.getenv("LLM_MODEL")              # 主模型（9B，如 qwen3.5:9b）
TOOL_LLM_MODEL = os.getenv("TOOL_LLM_MODEL")    # 工具模型（7B，如 qwen2.5:7b）
API_BASE = os.getenv("API_BASE")
API_KEY = os.getenv("API_KEY")
HOST = os.getenv("HOST")
PORT = int(os.getenv("PORT"))

# ================== SimpleMemory ==================
class SimpleMemory:
    def __init__(self, path=None):
        if path is None:
            path = str(Path(__file__).parent / "chromadb")
        
        # 使用本地下载的 SentenceTransformer 模型
        model_path = str(Path(__file__).parent / "models" / "all-MiniLM-L6-v2")
        embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=model_path
        )
        
        self.client = chromadb.PersistentClient(path=path)
        # embedding_function 在创建集合时绑定
        self.collection = self.client.get_or_create_collection(
            name="memories",
            embedding_function=embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )

    def add_with_title(self, title, content, user_id, meta=None):
        """
        添加带标题的记忆，嵌入由 collection 自动处理
        Args:
            title: 记忆标题
            content: 记忆内容
            user_id: 用户ID
            meta: 额外元数据字典（可选），如 {"type": "short_term", "emotion": "高兴"}
        """
        t0 = time.time()
        doc_id = f"{user_id}_{int(time.time())}"
        # 构建基础元数据
        metadatas = {
            "user_id": user_id,
            "title": title,
            "timestamp": datetime.now().isoformat()
        }
        # 合并额外元数据（如果有）
        if meta:
            metadatas.update(meta)
        self.collection.add(
            ids=[doc_id],
            documents=[content],
            metadatas=[metadatas]
        )
        print(f"[存储] ChromaDB写入耗时：{(time.time()-t0)*1000:.2f}ms")
        return {"id": doc_id, "message": "写入成功"}

    def get_recent(self, user_id, n=3):
        """获取用户最近 N 条记忆（按时间戳倒序）"""
        results = self.collection.get(
            where={"user_id": user_id},
            limit=n * 3  # 多取一点，防止不够
        )
        if results and results["documents"]:
            pairs = list(zip(results["documents"], results["metadatas"]))
            pairs.sort(key=lambda x: x[1].get("timestamp", ""), reverse=True)
            return [doc for doc, _ in pairs[:n]]
        return []

    def search(self, query, user_id, threshold=0.5, n_results=3):
        """语义检索记忆（使用 query_texts 自动嵌入）"""
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where={"user_id": user_id}
        )
        filtered = []
        if results["documents"] and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                score = 1 - results["distances"][0][i]
                if score >= threshold:
                    filtered.append({
                        "document": doc,
                        "score": score,
                        "timestamp": results["metadatas"][0][i].get("timestamp", "")
                    })
        return {"results": filtered}

# ================== AgentFactory（解决多用户串台问题） ==================
class AgentFactory:
    """按 user_id 隔离 Agent 实例，每个用户有自己的对话历史"""
    def __init__(self, memory, llm_model, system_prompt, api_base, tool_llm_model):
        self.memory = memory
        self.llm_model = llm_model
        self.tool_llm_model = tool_llm_model
        self.system_prompt = system_prompt
        self.api_base = api_base
        self._agents = {}

    def get_agent(self, user_id):
        if user_id not in self._agents:
            # 使用新的 LangGraphMemoryAgent
            self._agents[user_id] = LangGraphMemoryAgent(
                memory=self.memory,
                llm_model=self.llm_model,          # 主模型（9B）
                tool_llm_model=self.tool_llm_model, # 工具模型（7B）
                system_prompt=self.system_prompt
            )
        return self._agents[user_id]

# ================== 加载角色提示词 ==================
def load_system_prompt() -> str:
    prompt_file = BASE_DIR / "role_prompt.txt"
    if prompt_file.exists():
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "你是一个有帮助、友好的助手。"

system_prompt = load_system_prompt()

# ================== FastAPI Web 服务 ==================
app = FastAPI(title="AI Agent Web", version="v0.0.1-缝合版")
memory = SimpleMemory(path=CHROMA_DB_PATH)
agent_factory = AgentFactory(
    memory=memory,
    llm_model=LLM_MODEL,
    system_prompt=system_prompt,
    api_base=API_BASE,
    tool_llm_model=TOOL_LLM_MODEL
)

# 挂载静态文件
static_dir = BASE_DIR / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页：聊天界面"""
    return FileResponse(str(BASE_DIR / "templates" / "chat.html"))

@app.get("/api/history")
async def get_history(user_id: str = Query(default="default_user"), n: int = Query(default=20)):
    """获取用户的聊天历史（最近 N 条记忆）"""
    try:
        recent = memory.get_recent(user_id=user_id, n=n)
        return {"code": 0, "data": recent, "message": "ok"}
    except Exception as e:
        return {"code": -1, "data": [], "message": str(e)}

@app.get("/api/search")
async def search_memory(user_id: str = Query(default="default_user"), query: str = Query(default="")):
    """搜索用户的记忆"""
    try:
        results = memory.search(query=query, user_id=user_id, threshold=0.3, n_results=5)
        return {"code": 0, "data": results.get("results", []), "message": "ok"}
    except Exception as e:
        return {"code": -1, "data": [], "message": str(e)}

@app.get("/health")
async def health():
    return {"status": "alive", "time": datetime.now().isoformat(), "version": "v0.0.1-缝合版"}

# ================== WebSocket 实时聊天 ==================
@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    """
    WebSocket 聊天接口
    客户端发送 JSON: {"user_id": "xxx", "message": "你好"}
    服务端返回 JSON: {"type": "message", "content": "回复内容"}
    或者           {"type": "error", "content": "错误信息"}
    """
    await websocket.accept()
    print(f"[WebSocket] 新连接已建立")

    try:
        while True:
            raw = await websocket.receive_text()
            print(f"[WebSocket] 收到: {raw[:100]}...")

            try:
                data = json.loads(raw)
                user_id = data.get("user_id", "default_user")
                user_message = data.get("message", "").strip()
                image_base64 = data.get("image", None)  # 新增
                tts_enabled = data.get("tts", True)  # 👈 新增：读取前端传来的开关状态

                if not user_message:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "content": "消息不能为空哦！一起闪闪发光吧！"
                    }))
                    continue

                # [时间] 获取 Agent 实例
                _t0 = time.time()
                user_agent = agent_factory.get_agent(user_id)
                _t1 = time.time()
                print(f"[时间] 获取 Agent 实例耗时：{(_t1-_t0)*1000:.2f}ms")

                # [时间] Agent 处理消息
                _t2 = time.time()
                reply = user_agent.chat(user_id, user_message, image=image_base64)
                _t3 = time.time()
                print(f"[时间] Agent 处理消息耗时：{(_t3-_t2)*1000:.2f}ms")

                # ============ TTS 合成 ============
                audio_data = None
                if tts_enabled:  # 👈 只有开关开启时才合成
                    try:
                        # # 检测语言（如果文本含日语假名则用 ja，否则用 zh）
                        # lang = detect_language(reply) if reply else "zh"
                        # audio_data = text_to_speech(reply, language=lang)
                        _t4 = time.time()
                        audio_data = text_to_speech(reply, language="ja", translate=True)
                        _t5 = time.time()
                        print(f"[时间] TTS 合成耗时：{(_t5-_t4)*1000:.2f}ms")
                    except Exception as e:
                        print(f"[TTS] 合成异常: {e}")
                else:
                    print(f"[TTS] 开关关闭，跳过语音合成")
                
                # 发送回复（含音频）
                response_data = {
                    "type": "message",
                    "content": reply
                }
                if audio_data:
                    response_data["audio"] = base64.b64encode(audio_data).decode('utf-8')
                
                _t6 = time.time()
                await websocket.send_text(json.dumps(response_data))
                _t7 = time.time()
                print(f"[时间] WebSocket 发送消息耗时：{(_t7-_t6)*1000:.2f}ms")

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": "啊咧？这个格式看不太懂呢！让我再试试？"
                }))
            except Exception as e:
                print(f"[WebSocket] 处理消息出错: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": f"啊～出错了！不过没关系，我会一直闪闪发光地陪着你！{str(e)}"
                }))

    except WebSocketDisconnect:
        print(f"[WebSocket] 连接已断开")
    except Exception as e:
        print(f"[WebSocket] 连接异常: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass

# ================== 启动 ==================
def print_banner():
    """打印启动横幅"""
    print("=" * 50)
    print("    AI Agent Web  v0.0.1-缝合版")
    print("    记忆增强 / 本地部署 / 纯纯缝合怪")
    print("=" * 50)
    print(f"  📍 本地地址:  http://localhost:{PORT}")
    print(f"  🔌 WebSocket: ws://localhost:{PORT}/ws/chat")
    print(f"  💾 数据库:    {CHROMA_DB_PATH}")
    print(f"  💡 Ctrl+C 停止服务")
    print("=" * 50)

if __name__ == "__main__":
    print_banner()
    import webbrowser
    import threading
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info"
    )
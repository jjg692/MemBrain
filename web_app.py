# web_app.py
import os
import json
import time
import base64
from pathlib import Path
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from core.config import HOST, PORT, CHROMA_DB_PATH, LLM_MODEL, TOOL_LLM_MODEL, API_BASE
from core.memory import SimpleMemory
from agent.graph import LangGraphMemoryAgent
from tts_client import text_to_speech
from core.logger import log_time, log_debug

from datetime import datetime


# ================== 加载角色提示词 ==================
def load_system_prompt() -> str:
    prompt_file = Path(__file__).parent / "role_prompt.txt"
    if prompt_file.exists():
        with open(prompt_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    return "你是一个有帮助、友好的助手。"


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
            self._agents[user_id] = LangGraphMemoryAgent(
                memory=self.memory,
                llm_model=self.llm_model,
                tool_llm_model=self.tool_llm_model,
                system_prompt=self.system_prompt
            )
        return self._agents[user_id]


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
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    """主页：聊天界面"""
    return FileResponse(str(Path(__file__).parent / "templates" / "chat.html"))


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
        results = memory.search(query=query, user_id=user_id, threshold=0.5, n_results=5)
        return {"code": 0, "data": results.get("results", []), "message": "ok"}
    except Exception as e:
        return {"code": -1, "data": [], "message": str(e)}


@app.get("/health")
async def health():
    return {"status": "alive", "time": datetime.now().isoformat(), "version": "v0.0.1-缝合版"}


# ================== WebSocket 实时聊天 ==================
@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
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
                image_base64 = data.get("image", None)
                tts_enabled = data.get("tts", True)

                if not user_message:
                    await websocket.send_text(json.dumps({"type": "error", "content": "消息不能为空哦！一起闪闪发光吧！"}))
                    continue

                _t0 = time.time()
                user_agent = agent_factory.get_agent(user_id)
                log_time("获取 Agent 实例", _t0)

                _t2 = time.time()
                reply = user_agent.chat(user_id, user_message, image=image_base64)
                log_time("Agent 处理消息", _t2)

                audio_data = None
                if tts_enabled:
                    try:
                        _t4 = time.time()
                        audio_data = text_to_speech(reply, language="ja", translate=True)
                        log_time("TTS 合成", _t4)
                    except Exception as e:
                        print(f"[TTS] 合成异常: {e}")
                else:
                    print("[TTS] 开关关闭，跳过语音合成")

                response_data = {"type": "message", "content": reply}
                if audio_data:
                    response_data["audio"] = base64.b64encode(audio_data).decode('utf-8')

                _t6 = time.time()
                await websocket.send_text(json.dumps(response_data))
                log_time("WebSocket 发送消息", _t6)

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "content": "啊咧？这个格式看不太懂呢！让我再试试？"}))
            except Exception as e:
                print(f"[WebSocket] 处理消息出错: {e}")
                await websocket.send_text(json.dumps({"type": "error", "content": f"啊～出错了！不过没关系，我会一直闪闪发光地陪着你！{str(e)}"}))

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
if __name__ == "__main__":
    import webbrowser
    import threading
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")
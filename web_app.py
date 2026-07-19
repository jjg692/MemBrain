# web_app.py
import os
import json
import time
import base64
import asyncio
from pathlib import Path
from typing import Dict, Set
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

# ================== 初始化群聊系统 ==================
# 必须在创建 app 之前初始化，以保证单例正确
from core.room.room_manager import RoomManager
from core.room.message_bus import MessageBus
from core.room.scheduler import SpeakingScheduler
from core.room.role_pool import RoleInstancePool, RoleConfig

room_manager = RoomManager()
message_bus = MessageBus()
role_pool = RoleInstancePool()


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


# ================== WebSocket 连接管理 ==================
# room_id → Set[WebSocket]
ws_rooms: Dict[str, Set[WebSocket]] = {}


async def broadcast_to_room(room_id: str, data: dict):
    """向房间内的所有 WebSocket 连接广播消息"""
    if room_id not in ws_rooms:
        return
    dead_connections = set()
    for ws in ws_rooms[room_id]:
        try:
            await ws.send_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            dead_connections.add(ws)
    # 清理断开的连接
    ws_rooms[room_id] -= dead_connections


# 注册广播回调到消息总线（在 app 启动后执行）
@app.on_event("startup")
async def setup_message_bus():
    message_bus.set_broadcast_callback(broadcast_to_room)
    log_debug("群聊", "消息总线广播回调已注册")


# ================== HTTP API ==================

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


# ================== 群聊 Room API ==================

@app.get("/api/rooms")
async def list_rooms():
    """列出所有房间"""
    rooms = room_manager.list_rooms()
    return {"code": 0, "data": [room_manager.get_room_context(rid) for rid in rooms if room_manager.get_room_context(rid)]}


@app.post("/api/rooms/create")
async def create_room_api(request: Request):
    """创建房间"""
    body = await request.json()
    room_id = body.get("room_id", "").strip()
    topic = body.get("topic", "")
    if not room_id:
        return {"code": -1, "message": "room_id 不能为空"}
    room = room_manager.create_room(room_id, topic)
    if not room:
        return {"code": -1, "message": f"房间 {room_id} 已存在"}
    return {"code": 0, "data": room.get_room_summary(), "message": f"房间 {room_id} 创建成功"}


@app.get("/api/rooms/{room_id}")
async def get_room_info(room_id: str):
    """获取房间信息"""
    room = room_manager.get_room(room_id)
    if not room:
        return {"code": -1, "message": f"房间 {room_id} 不存在"}
    return {"code": 0, "data": room.get_room_summary()}


@app.post("/api/rooms/{room_id}/join")
async def join_room_api(room_id: str, request: Request):
    """加入房间（注册角色到房间）"""
    body = await request.json()
    role_id = body.get("role_id", "").strip()
    user_id = body.get("user_id", role_id)
    display_name = body.get("display_name", role_id)

    if not role_id:
        return {"code": -1, "message": "role_id 不能为空"}

    room = room_manager.get_room(room_id)
    if not room:
        return {"code": -1, "message": f"房间 {room_id} 不存在"}

    # 检查角色是否已注册，未注册则自动注册
    config = role_pool.get_role_config(role_id)
    if not config:
        # 用默认角色注册
        role_pool.register_role(RoleConfig(
            role_id=role_id,
            display_name=display_name,
            role_prompt=f"你是{display_name}，一个友好的角色。请在群聊中根据你的人设发言。",
        ))

    # 获取或创建 Agent 实例
    def agent_factory_fn(config, rid):
        return agent_factory.get_agent(rid)

    agent_instance = role_pool.get_agent(role_id, agent_factory_fn)
    if not agent_instance:
        return {"code": -1, "message": f"无法创建角色 {role_id} 的 Agent 实例"}

    # 加入房间
    success = room_manager.add_agent_to_room(room_id, role_id, user_id, agent_instance)
    if not success:
        return {"code": -1, "message": f"角色 {role_id} 加入房间失败（可能已存在或房间已满）"}

    # 通知房间
    await message_bus.notify_agent_join(room_id, role_id)

    return {"code": 0, "message": f"{role_id} 加入房间 {room_id}"}


@app.post("/api/rooms/{room_id}/leave")
async def leave_room_api(room_id: str, request: Request):
    """离开房间"""
    body = await request.json()
    role_id = body.get("role_id", "").strip()

    if not role_id:
        return {"code": -1, "message": "role_id 不能为空"}

    await message_bus.notify_agent_leave(room_id, role_id)
    success = room_manager.remove_agent_from_room(room_id, role_id)
    if not success:
        return {"code": -1, "message": f"角色 {role_id} 未在房间 {room_id} 中"}

    return {"code": 0, "message": f"{role_id} 离开房间 {room_id}"}


@app.get("/api/rooms/{room_id}/messages")
async def get_room_messages(room_id: str, n: int = Query(default=30)):
    """获取房间聊天记录（L0 群聊记忆）"""
    messages = message_bus.get_recent_messages(room_id, n)
    return {"code": 0, "data": [msg.to_dict() for msg in messages]}


# ================== WebSocket 单聊（原有，向下兼容） ==================
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


# ================== WebSocket 群聊 ==================
@app.websocket("/ws/room/{room_id}")
async def websocket_room(websocket: WebSocket, room_id: str):
    """群聊 WebSocket：用户通过此连接进入房间发言"""
    await websocket.accept()
    print(f"[WebSocket] 房间 [{room_id}] 新连接已建立")

    # 将连接加入房间
    if room_id not in ws_rooms:
        ws_rooms[room_id] = set()
    ws_rooms[room_id].add(websocket)

    # 发送房间信息
    room = room_manager.get_room(room_id)
    if room:
        await websocket.send_text(json.dumps({
            "type": "room_info",
            "data": room.get_room_summary()
        }, ensure_ascii=False))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
                action = data.get("action", "message")

                if action == "message":
                    # 用户发送消息
                    user_id = data.get("user_id", "anonymous")
                    role_id = data.get("role_id", "user")
                    content = data.get("content", "").strip()

                    if not content:
                        continue

                    # 通过消息总线分发用户消息到房间
                    await message_bus.send_user_message(room_id, user_id, role_id, content)

                    # 异步触发 Agent 回复
                    asyncio.create_task(
                        process_room_message(room_id, user_id, role_id, content)
                    )

                elif action == "join":
                    # 用户设置自己扮演的角色
                    role_id = data.get("role_id", "").strip()
                    display_name = data.get("display_name", role_id)
                    user_id = data.get("user_id", role_id)

                    if role_id:
                        # 自动加入房间（角色不存在则自动注册）
                        config = role_pool.get_role_config(role_id)
                        if not config:
                            role_pool.register_role(RoleConfig(
                                role_id=role_id,
                                display_name=display_name,
                                role_prompt=f"你是{display_name}，一个友好的角色。请在群聊中根据你的人设发言。",
                            ))

                        def agent_factory_fn(config, rid):
                            return agent_factory.get_agent(rid)

                        agent_instance = role_pool.get_agent(role_id, agent_factory_fn)
                        if agent_instance:
                            room_manager.add_agent_to_room(room_id, role_id, user_id, agent_instance)
                            await message_bus.notify_agent_join(room_id, role_id)

                            await websocket.send_text(json.dumps({
                                "type": "join_ok",
                                "role_id": role_id,
                                "message": f"你已作为 {display_name} 加入房间"
                            }, ensure_ascii=False))

                elif action == "leave":
                    role_id = data.get("role_id", "").strip()
                    if role_id:
                        await message_bus.notify_agent_leave(room_id, role_id)
                        room_manager.remove_agent_from_room(room_id, role_id)

                elif action == "get_history":
                    n = data.get("n", 30)
                    messages = message_bus.get_recent_messages(room_id, n)
                    await websocket.send_text(json.dumps({
                        "type": "history",
                        "data": [msg.to_dict() for msg in messages]
                    }, ensure_ascii=False))

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "content": "消息格式错误"}))
            except Exception as e:
                print(f"[WebSocket/房间] 处理消息出错: {e}")
                await websocket.send_text(json.dumps({"type": "error", "content": str(e)}))

    except WebSocketDisconnect:
        print(f"[WebSocket] 房间 [{room_id}] 连接已断开")
    except Exception as e:
        print(f"[WebSocket] 房间 [{room_id}] 连接异常: {e}")
    finally:
        # 清理连接
        if room_id in ws_rooms:
            ws_rooms[room_id].discard(websocket)
        try:
            await websocket.close()
        except:
            pass


async def process_room_message(room_id: str, user_id: str, sender_role: str, content: str):
    """
    处理房间消息：广播给所有 Agent，收集回复

    当前实现：简单版本，依次让每个 Agent 回复
    后续可接入 SpeakingScheduler 实现更复杂的调度
    """
    room = room_manager.get_room(room_id)
    if not room:
        return

    agents = room.get_online_agent_list()
    if not agents:
        return

    # 获取群聊上下文（L0）
    group_context = message_bus.get_formatted_context(room_id, n=20)

    # 依次让每个 Agent 生成回复（简单版，后续可改为并行）
    for role_id in agents:
        agent = room.get_agent(role_id)
        if not agent or not hasattr(agent, 'chat'):
            continue

        try:
            # 构造包含群聊上下文的消息
            room_context = {
                "room_id": room_id,
                "group_context": group_context,
                "mentioned": content.find(role_id) >= 0,  # 是否被提到
            }

            # 调用 Agent 的 chat 方法生成回复
            reply = agent.chat(user_id, content)
            if reply:
                await message_bus.send_agent_message(room_id, role_id, reply)
                # 给其他 Agent 一点"反应时间"
                await asyncio.sleep(0.5)
        except Exception as e:
            log_debug("群聊", f"Agent [{role_id}] 回复失败: {e}")


# ================== 启动时初始化角色事实 ==================
def init_role_facts():
    """系统启动时，将角色 prompt 中的事实提取到 ChromaDB"""
    print("[启动] 初始化角色事实...")
    from core.role.loader import init_role_to_memory
    from core.adapters import OllamaAdapter
    tool_adapter = OllamaAdapter(model=TOOL_LLM_MODEL)
    success = init_role_to_memory(
        role_prompt=system_prompt,
        role_id="kasumi",
        tool_adapter=tool_adapter,
        memory=memory
    )
    if success:
        print("[启动] 角色事实初始化完成")
    else:
        print("[启动] 角色事实初始化失败（不影响运行）")

    # 将 kasumi 角色注册到角色池
    role_pool.register_role(RoleConfig(
        role_id="kasumi",
        display_name="户山香澄",
        role_prompt=system_prompt,
    ))
    print(f"[启动] 角色池已注册: kasumi")


# ================== 启动 ==================
if __name__ == "__main__":
    # 先初始化，再启动服务
    init_role_facts()

    import webbrowser
    import threading
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    uvicorn.run(app, host=HOST, port=PORT, reload=False, log_level="info")
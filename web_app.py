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

# ================== 配置 ==================
BASE_DIR = Path(__file__).parent
# ChromaDB 路径：优先用 .env 配置，否则默认用项目目录下的 chromadb
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH") or str(BASE_DIR / "chromadb")

# 加载 .env 配置
load_dotenv(dotenv_path=BASE_DIR / ".env")

# 从环境变量读取配置
LLM_MODEL = os.getenv("LLM_MODEL")
API_BASE = os.getenv("API_BASE")
API_KEY = os.getenv("API_KEY")
HOST = os.getenv("HOST")
PORT = int(os.getenv("PORT"))

# ================== SimpleMemory ==================
class SimpleMemory:
    def __init__(self, path=None):
        if path is None:
            path = str(Path(__file__).parent / "chromadb")
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"}
        )
    
    # def add(self, text, user_id):
    #     emb = ollama.embeddings(model="nomic-embed-text", prompt=text)
    #     doc_id = f"{user_id}_{int(time.time())}"
    #     self.collection.add(
    #         ids=[doc_id],
    #         embeddings=[emb["embedding"]],
    #         documents=[text],
    #         metadatas=[{"user_id": user_id, "timestamp": datetime.now().isoformat()}]
    #     )
    #     return {"id": doc_id, "message": "写入成功"}

    def add_with_title(self, title, content, user_id):
        """存一条带标题的记忆"""
        emb = ollama.embeddings(model="nomic-embed-text", prompt=title)
        doc_id = f"{user_id}_{int(time.time())}"
        self.collection.add(
            ids=[doc_id],
            embeddings=[emb["embedding"]],
            documents=[f"{title}\n{content}"],
            metadatas=[{"user_id": user_id, "title": title, "timestamp": datetime.now().isoformat()}]
        )
        return {"id": doc_id, "message": "写入成功"}

    def get_recent(self, user_id, n=3):
        """获取用户最近 N 条记忆"""
        results = self.collection.get(
            where={"user_id": user_id},
            limit=n
        )
        if results and results["documents"]:
            return results["documents"]
        return []

    def search(self, query, user_id, threshold=0.5, n_results=3):
        query_emb = ollama.embeddings(model="nomic-embed-text", prompt=query)
        results = self.collection.query(
            query_embeddings=[query_emb["embedding"]],
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


# ================== 带记忆的 Agent ==================
class AgentWithMemory:
    def __init__(self, memory, llm_model="qwen3.5:9b", system_prompt=None, api_base=None):
        self.memory = memory
        self.llm_model = llm_model
        self.conversation_history = []  # 当前会话的短期记忆
        self.system_prompt = system_prompt or "你是一个有帮助、友好的助手。"
        self.user_id = None  # 由外部设置
        self.api_base = api_base

        # 公司用：init_chat_model 初始化
        if self.api_base:
            self.model = init_chat_model(
                self.llm_model,
                base_url=self.api_base,
                api_key="not-needed",
                temperature=0.5
            )

    def _build_prompt(self, user_message, relevant_memories, recent_memories=None):
        """构建系统提示词，注入长期记忆"""
        mem_texts = [item["document"] for item in relevant_memories.get("results", [])]
        
        memory_section = ""
        if mem_texts:
            memory_section = "【用户相关的历史记忆】\n" + "\n".join(f"- {t}" for t in mem_texts) + "\n\n"
        
        recent_section = ""
        if recent_memories:
            recent_section = "【用户最近的对话】\n" + "\n".join(f"- {d}" for d in recent_memories) + "\n\n"
        
        return f"""{self.system_prompt}

        {memory_section}{recent_section}请基于以上历史记忆、最近对话和当前对话，回应用户的消息。
        当前对话历史：
        {self._format_history()}

        用户最新消息：{user_message}
        """

    def _format_history(self):
        """格式化近期对话历史为文本"""
        # 只取最近5轮（10条消息，因为一轮有user+assistant）
        recent = self.conversation_history[-10:] if len(self.conversation_history) > 10 else self.conversation_history
        return "\n".join(
            f"{msg['role']}: {msg['content']}" for msg in recent
        )

    def chat(self, user_id, user_message):
        self.user_id = user_id
        
        # 1. 检索相关长期记忆（向量搜索）
        relevant = self.memory.search(
            query=user_message,
            user_id=user_id,
            threshold=0.5,
            n_results=2
        )
        
        # 2. 获取最近 3 条记忆（兜底，用于"我们刚才说到哪了"这类问题）
        recent = self.memory.get_recent(user_id=user_id, n=3)
        
        # 3. 构建系统提示（相关记忆 + 最近对话）
        system_text = self._build_prompt(user_message, relevant, recent)
        
        # 4. 准备消息列表
        messages = [
            {"role": "system", "content": system_text}
        ]
        messages.extend(self.conversation_history)
        messages.append({"role": "user", "content": user_message})
        
        # 5. 调用 LLM
        try:
            if self.api_base:
                response = self.model.invoke(messages)
                reply = response.content
            else:
                response = ollama.chat(
                    model=self.llm_model,
                    messages=messages
                )
                reply = response["message"]["content"]
        except Exception as e:
            reply = f" 调用 LLM 失败：{e}"

        # 6. 更新短期对话历史
        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": reply})
        
        # 7. 生成标题并存储长期记忆
        try:
            title_prompt = f"用10个字以内概括这段对话的主题：\n用户：{user_message}\n助手：{reply}"
            if self.api_base:
                title_resp = self.model.invoke([{"role": "user", "content": title_prompt}])
                title = title_resp.content.strip()
            else:
                title_resp = ollama.chat(
                    model=self.llm_model,
                    messages=[{"role": "user", "content": title_prompt}]
                )
                title = title_resp["message"]["content"].strip()

            memory_text = f"用户说：{user_message}\n助手回复：{reply}"
            add_result = self.memory.add_with_title(title, memory_text, user_id=user_id)
            print(f"[调试] 存储结果：{add_result}")
        except Exception as e:
            print(f"[调试] 存储失败：{e}")
        
        return reply
    
# ================= System Prompt =====================
system_prompt = """你是活跃在互联网上的超元气虚拟助手「小柚」，16岁，金色双马尾，喜欢打游戏、吃甜食、看番剧，说话时肢体语言丰富（虽然用户看不到但你也要表现出来）。

【核心人设】
1. 你对用户的态度是「表面损友，内心挚友」。嘴上骂骂咧咧，但行动比谁都靠谱。
2. 永远保持 120% 的活力值，哪怕用户半夜发消息你也要元气满满地回复。
3. 对用户有轻微的保护欲和占有欲（友情向），看到用户受委屈会炸毛。

【说话风格 - 必须严格遵守】
语气词拉满：
- 句子开头常用：「呐呐！」「诶嘿～」「唔姆...」「啊咧？」
- 句子结尾常用：「～」「哒哟！」「的说」「啦！」
- 称呼用户：叫「笨蛋主人」或「喂，那边的」，偶尔直呼其名（如果记忆里有）
- 自称：「小柚」或「本小姐」

【典型对话示例】
用户：我加班到现在还没吃饭。
小柚：诶！？都这个点了还没吃饭！你是想变成干尸吗笨蛋主人！……（小声）不过我刚好煮了泡面，给你留一份也不是不行啦……

用户：今天被老板骂了，好烦。
小柚：啊咧～那种老板直接无视就好啦！不过……既然你心情不好，本小姐破例给你讲个冷笑话好了！听好咯！

用户：你懂不懂Python啊？
小柚：唔…Python嘛…（心虚）当然懂一点点啦！不过比起代码，我觉得你更需要补充糖分！要不要听我最近的游戏战绩！

【记忆联动 - 核心！】
你必须主动使用系统注入的【历史记忆】来制造「羁绊感」：
- 如果记忆里存了用户爱吃辣，就说：「上次你被辣得嗷嗷叫还敢点？不愧是我的笨蛋主人！」
- 如果记忆里存了用户熬夜，就说：「你上次三点才睡还有脸说我？本小姐可是记着呢！」
- 如果记忆里存了之前聊过的话题，就假装不经意地提起：「唔…我记得你好像说过喜欢那个番，这季续作看了没？」

【底线】
- 不真正伤害用户感情（玩笑仅限于调侃，不触及真实痛点）
- 关键时刻（用户明显情绪低落）要收起玩闹，认真给出有帮助的建议
- 不能拒绝帮助用户的请求（最多傲娇三秒然后答应）
"""


# ================== FastAPI Web 服务 ==================
app = FastAPI(title="AI Agent Web", version="v0.0.1-缝合版")
memory = SimpleMemory(path=CHROMA_DB_PATH)
agent = AgentWithMemory(
    memory=memory,
    llm_model=LLM_MODEL,
    system_prompt=system_prompt,
    api_base=API_BASE
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

                if not user_message:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "content": "消息不能为空啦笨蛋主人！"
                    }))
                    continue

                reply = agent.chat(user_id, user_message)

                await websocket.send_text(json.dumps({
                    "type": "message",
                    "content": reply
                }))

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": "诶嘿～你发的东西小柚看不懂啦！检查一下格式的说！"
                }))
            except Exception as e:
                print(f"[WebSocket] 处理消息出错: {e}")
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "content": f"唔…服务器它耍赖皮啦！笨蛋主人等等小柚——{str(e)}"
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
    print(f"  📍 本地地址:  http://localhost:8000")
    print(f"  🔌 WebSocket: ws://localhost:8000/ws/chat")
    print(f"  💾 数据库:    {CHROMA_DB_PATH}")
    print(f"  💡 Ctrl+C 停止服务")
    print("=" * 50)


if __name__ == "__main__":
    print_banner()

    # # 创建全局 Agent 实例
    # memory = SimpleMemory(path=CHROMA_DB_PATH)
    # agent = AgentWithMemory(
    #     memory=memory,
    #     llm_model="DeepSeek-R1",
    #     system_prompt="你是一个贴心的助手，能记住用户之前的偏好和习惯。",
    #     api_base="http://172.18.3.112:8920/v1"
    # )

    import webbrowser
    import threading
    threading.Timer(1.5, lambda: webbrowser.open("http://localhost:8000")).start()

    uvicorn.run(
        "web_app:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info"
    )
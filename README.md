# 🧠 MemBrain

> “诶嘿～你还记得我说过什么呀？キラキラ☆ドキドキ！”

一个会记住你的 AI 助手，现在暂时由《BanG Dream!》的户山香澄担任。

支持**单人对话**和**多 Agent 群聊**两种模式，多个角色可以在同一个房间里互相接话、讨论。

## 📋 目录

- [它现在能干啥？](#-它现在能干啥)
- [多 Agent 群聊系统](#-多-agent-群聊系统)
- [项目结构](#-项目结构)
- [技术架构](#-技术架构)
- [怎么跑起来](#-怎么跑起来)
- [环境变量配置](#-环境变量配置)
- [API 接口文档](#-api-接口文档)
- [前端使用](#-前端使用)
- [用了啥](#-用了啥)
- [为什么叫 MemBrain](#-为什么叫-membrain)


## 🎯 它现在能干啥？

| 功能 | 状态 |
|------|------|
| 💬 跟你聊天，记得你说过啥 | ✅ |
| 👤 多用户切换，各聊各的 | ✅ |
| 🎭 自定义人设（现在是香澄） | ✅ |
| 🌐 联网搜索（百度API，天气/新闻/推荐） | ✅ |
| 🖼️ 看懂你发的图（多模态） | ✅ |
| 🔊 把话说出来（TTS 语音合成） | ✅ |
| 👥 多 Agent 群聊（多人多角色同一房间聊天） | ✅ |
| 🎪 Live2D 桌面上蹦跶 | 🚧 规划中 |


## 👥 多 Agent 群聊系统

将"用户 ↔ 单 Agent"的对话模式，升级为"用户 ↔ 多个 Agent ↔ Agent 之间"的群聊系统。

### 五个核心模块

| 模块 | 职责 | 关键功能 |
|------|------|---------|
| **房间管理器** | `core/room/room_manager.py` | 创建/销毁/查询房间：`create_room()`, `add_agent_to_room()`, `get_room_context()` |
| **消息总线** | `core/room/message_bus.py` | 群聊消息的分发：`broadcast()` 把消息推送给所有 Agent 和前端 |
| **发言调度器** | `core/room/scheduler.py` | 决定"下一个谁说话"：支持轮流/按话题/随机三种策略 + 抢话机制 |
| **角色实例池** | `core/room/role_pool.py` | 管理多个 Agent 实例：每个角色独立加载，按需激活 |
| **群聊记忆层（L0）** | `MessageBus.message_histories` | 共享的群聊上下文：存储房间内所有角色的发言记录（含角色标识） |

### 数据流

```
用户A 在房间"讨论"发送："明天去哪里玩？"

1. WebSocket → web_app.py
2. → MessageBus.broadcast("讨论", msg)
3. → 并行调用: AgentA.process(msg), AgentB.process(msg)
4. → 每个 Agent:
     a. 读取 L0 群聊记忆（最近20条）
     b. 读取自己 L2/L4 私有记忆
     c. LangGraph 生成回复
5. → SpeakingScheduler 决定谁先发言
6. → MessageBus 推送回复到前端
7. → 前端显示各角色的气泡 + 头像
```

### 记忆分层设计

| 层级 | 内容 | 存储位置 | 生命周期 |
|------|------|---------|---------|
| **L0（群聊上下文）** | 房间里所有角色的发言记录（含角色标识） | 内存 `message_histories[room_id]` | 当前会话 |
| **L1/L2/L4（私有记忆）** | 每个 Agent 自己的记忆向量库 | ChromaDB（按 role_id 隔离） | 跨会话持久化 |


## 📁 项目结构

```
MemBrain/
├── web_app.py                 # 入口，运行这个就行
├── tts_client.py              # TTS 语音合成
├── role_prompt.txt            # 角色人设
│
├── core/                      # 基础设施
│   ├── config.py              # 配置管理（.env）
│   ├── adapters.py            # LLM 适配器（Ollama / DeepSeek）
│   ├── tools.py               # 搜索工具 + 工具定义
│   ├── state.py               # Agent 状态定义
│   ├── logger.py              # 日志
│   ├── room/                  # 🆕 群聊房间系统
│   │   ├── __init__.py
│   │   ├── room_manager.py    # 房间 CRUD + Agent 管理
│   │   ├── message_bus.py     # 消息广播 + L0 群聊记忆
│   │   ├── scheduler.py       # 发言调度器（三种策略）
│   │   └── role_pool.py       # 角色实例池（生命周期管理）
│   ├── role/                  # L5 角色系统
│   │   ├── loader.py          # 角色事实加载器（启动时初始化）
│   │   └── role_fact_extractor.py  # 角色事实提取器
│   └── memory/
│       ├── vector_store.py    # ChromaDB 封装（单例）
│       ├── retriever.py       # 混合检索器
│       ├── memory_manager.py  # 四层记忆管理器
│       └── fact_extractor.py  # 对话事实抽取器
│
├── agent/                     # Agent 逻辑
│   ├── graph.py               # LangGraph 图 + 双模型调度
│   ├── router.py              # 路由分类器
│   └── handlers/
│       ├── personal.py        # 记忆检索 + 主模型生成
│       ├── realtime.py        # 搜索处理
│       └── result.py          # 搜索结果摘要生成
│
├── static/
│   └── avatars/
│       └── agents/            # 🆕 角色头像（role_id.png）
│           ├── kasumi.png
│           └── ...            # 新增角色时放对应头像
│
├── templates/chat.html        # 前端（支持单人/群聊模式切换）
├── chromadb/                  # 用户记忆（自动生成）
├── models/                    # 嵌入模型
├── .env.example
├── requirements.txt
└── README.md
```


## 🧠 技术架构

### 双模型分工

| 模型 | 职责 |
|------|------|
| **主模型**（默认 qwen3.5:9b） | 最终回复生成（支持多模态） |
| **工具模型**（默认 qwen2.5:7b） | 路由分类 + 查询改写 + 事实抽取 |

### 路由分流

```
用户消息 → router.py
               ├─ REALTIME  → 联网搜索 → handle_result 生成回复
               └─ PERSONAL  → 记忆检索 → 主模型生成回复
                                        └─ 不确定 → @@@UNCERTAIN@@@
                                                  └─ 自动触发搜索兜底
```

### 记忆架构

| 层级 | 说明 |
|------|------|
| **L0** | 群聊上下文（房间内所有角色发言记录，内存 FIFO） |
| **L1** | 内存上下文（当前会话，最多20轮，超限自动压缩为摘要） |
| **L2** | 短期记忆（ChromaDB，持久化 FIFO，保留50轮对话） |
| **L4** | 重要事实（从对话中自动抽取偏好/事件/承诺） |
| **L5** | 角色事实（启动时从 role_prompt.txt 提取，存入 ChromaDB） |

### 启动流程

```
web_app.py 启动
  ├─ 加载 role_prompt.txt
  ├─ 初始化 ChromaDB
  ├─ 角色事实提取（工具模型分析 prompt → 存入 L5）
  ├─ 注册 kasumi 到角色池
  ├─ 初始化消息总线广播回调
  ├─ 启动 FastAPI 服务
  └─ 收到消息 → 单人: AgentFactory.get_agent()
              └─ 群聊: RoomManager → MessageBus → 多 Agent
```


## 🚀 怎么跑起来

```bash
# 克隆项目
git clone https://github.com/jjg692/MemBrain.git
cd MemBrain

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env   # 修改 API 密钥和模型

# 启动服务
python web_app.py      # 浏览器打开 http://localhost:8000
```

### 启动顺序（如果启用 TTS）

```bash
# 1. 启动 GPT-SoVITS TTS 服务（在整合包目录下）
cd gpt-sovits
.\runtime\python.exe api.py -a "127.0.0.1" -p 9880 -s "SoVITS_weights/你的模型.pth" -g "GPT_weights/你的模型.ckpt" -dr "参考音频.wav" -dt "参考音频文本" -dl "ja"

# 2. 启动 MemBrain 服务
cd MemBrain
python web_app.py
```

### .env 配置

```env
# Ollama
OLLAMA_HOST=http://localhost:11434

# 模型选择
LLM_MODEL=qwen3.5:9b          # 主模型（回复生成）
TOOL_LLM_MODEL=qwen2.5:7b     # 工具模型（路由 + 改写 + 抽取）

# 百度搜索 API
BAIDU_API_KEY=your_key_here

# 百度 API 兼容（可选）
API_BASE=
API_KEY=

# 服务端口
HOST=0.0.0.0
PORT=8000
```


## 📖 API 接口文档

### 单聊（向下兼容）

| 方法 | 路径 | 说明 |
|------|------|------|
| WebSocket | `ws://host/ws/chat` | 单人对话（原有逻辑） |
| GET | `/api/history?user_id=xxx` | 获取聊天历史 |
| GET | `/api/search?query=xxx` | 搜索记忆 |

### 群聊房间

| 方法 | 路径 | 说明 |
|------|------|------|
| WebSocket | `ws://host/room/{room_id}` | 群聊 WebSocket 入口 |
| GET | `/api/rooms` | 列出所有房间 |
| POST | `/api/rooms/create` | 创建房间 `{room_id, topic}` |
| GET | `/api/rooms/{room_id}` | 获取房间信息 |
| POST | `/api/rooms/{room_id}/join` | 加入房间 `{role_id, user_id}` |
| POST | `/api/rooms/{room_id}/leave` | 离开房间 `{role_id}` |
| GET | `/api/rooms/{room_id}/messages?n=30` | 获取聊天记录 |


## 🖥️ 前端使用

### 模式切换

页面顶部右侧有模式选择下拉框：
- **💬 单人对话** — 和香澄一对一聊天（原有模式）
- **👥 多人群聊** — 创建/加入房间，多角色群聊

### 群聊操作流程

1. 切换到"多人群聊"模式
2. 输入房间名 → 点击"创建"
3. 选择角色（如 kasumi）→ 点击"加入"
4. 在输入框发消息，房间内所有 Agent 会依次回复

### 角色头像

- 头像文件放在 `static/avatars/agents/` 目录，命名为 `{role_id}.png`
- 新加角色时，把对应头像 PNG 放到该目录即可
- 图片加载失败会自动 fallback 到 `kasumi.png`


## 📦 用了啥

Python / FastAPI / LangGraph / ChromaDB / Edge-TTS / Ollama / Qwen / Sentence-Transformers

## 🤔 为什么叫 MemBrain

Memory + Brain，无论怎样要比「AI_Chat_Project_v8_final」好听点。

> “呐呐！一起闪闪发光吧！キラキラ☆ドキドキ！”
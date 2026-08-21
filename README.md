# 🧠 MemBrain (Refactor)

> 一个会记住你的、基于 LangGraph 的 AI Agent。由《BanG Dream!》的户山香澄担任默认角色。

这是对旧版 MemBrain 的**全新重构**。相比"缝合版"，重构版坚持六条设计原则，代码更清晰、可运行、可扩展。

---

## ✨ 核心能力

| 功能 | 说明 |
|------|------|
| 💬 私聊 | 多角色切换，按 `(user_id, role_id)` 隔离记忆 |
| 👥 群聊 | 多角色同一房间，用户发言后各成员 Agent 并行回复 |
| 🧠 五层记忆 | L1 内存 / L2 短期 / L3 信息池 / L4 事实 / L5 角色 |
| 🔍 自治路由 | 无路由层/改写层，LLM 自主决定是否调用工具 |
| 🛠️ 工具调用 | `search_web`（百度，未配置 Key 降级 B 站热搜）、`control_pc`（打开应用/浏览器/文件/执行命令） |
| 💗 情感 / 好感度 | 模式 B 两阶段，6 维好感度跨会话持久化 |
| 🖥️ 后台管理 | 联系人/记忆/情感/统计/配置 一体管理 |

---

## 🧠 五层记忆架构

| 层级 | 名称 | 存储 | 容量 | 用途 |
|------|------|------|------|------|
| **L1** | 内存上下文 | 内存（按 user_id+role_id 隔离） | 50 轮，超限压缩 | 当前会话历史、指代消解 |
| **L2** | 短期记忆 | ChromaDB `short_term` | 50 轮 FIFO | 跨会话对话原文召回 |
| **L3** | 主动信息池 | ChromaDB `l3_info` | 预留 | ⚠️ **未实现**：仅有统计计数与配置文件项，无主动采集/推送逻辑 |
| **L4** | 重要事实 | ChromaDB `fact` | 永久（带衰减） | 用户画像，LLM 自动抽取 |
| **L5** | 角色事实 | ChromaDB `role_fact` | 永久 | 角色设定，仅按 role_id 隔离 |

**关键机制：**
- L2 仅在**冷启动**时加载到 L1，对话过程中**不查询** L2。
- L4 事实由 **LLM** 从对话中抽取（无硬编码规则）。
- L5 在**系统启动时一次性**加载所有角色事实（避免首次切换延迟），L5 **仅按 role_id 隔离**。
- 其他记忆按 `(user_id, role_id)` **双键隔离**。

---

## 🎭 多角色系统

- 角色配置：`config/roles.json`
- 角色 Prompt：`role_prompts/role_prompt_{role_id}.txt`
- 前端支持角色下拉切换（私聊）
- 示例角色：户山香澄（元气吉他手）、弦卷心（天真大小姐）
- 后台可增删改查角色、编辑 Prompt、上传头像、设为默认

---

## 🗺️ 自治路由（无硬编码）

```
用户消息
   │  直接进入 LangGraph Agent 主流程（不设路由层/改写层）
   ▼
agent 节点：LLM 自主决策
   │  ├─ 需要实时信息/操作电脑 → 产生 tool_calls → tools 节点执行 → 再回 agent
   │  └─ 直接回答 → 生成最终回复
   ▼
END
```

意图判断、指代消解、工具选择**全部由 LLM 完成**。

---

## 💗 情感 / 好感度（模式 B）

**两阶段处理：**
1. **第一阶段**：`EmotionAnalyzer` 让 LLM 只输出 JSON（情感 + 6 维好感度更新）
2. **第二阶段**：基于分析结果 + 记忆 + 角色人设生成最终回复

好感度 6 维：喜欢 / 信任 / 熟悉 / 尊重 / 兴趣 / 依恋（0-1），跨会话持久化到 ChromaDB。

---

## 🚀 快速开始

### 环境要求
- Python 3.11+
- Ollama（已拉取主模型 + 工具模型）

> ⚠️ **关于嵌入模型**：项目约定嵌入模型放在 `models/all-MiniLM-L6-v2/`（`sentence-transformers` 格式）。如果该目录存在则**离线直接加载**；如果不存在，代码会回退到联网下载同名模型，或最终降级为 ChromaDB 默认嵌入（`DefaultEmbeddingFunction`）。当前 `models/` 目录为空，请自行放入已下载的模型，或首次运行时让它自动下载（需联网）。

### 安装

```bash
cd agent-web-refactor
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

### 配置 `.env`

```bash
copy .env.example .env         # Windows
# 编辑 .env：设置 LLM_MODEL / TOOL_LLM_MODEL / OLLAMA_HOST / 百度 Key
```

### 启动

```bash
python web_app.py
```

浏览器自动打开 `http://localhost:8000`。

- 聊天界面：`http://localhost:8000/`
- 后台管理：`http://localhost:8000/admin`

---

## 🔌 API 一览

### HTTP
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/contacts` | 联系人（角色）列表 |
| GET | `/api/history` | 历史消息（user_id + role_id） |
| GET | `/api/rooms` | 群聊列表 |
| POST | `/api/rooms/create` | 创建群聊 |
| GET | `/api/rooms/{id}` | 群聊信息 |
| POST | `/api/rooms/{id}/join` | 加入角色 |
| POST | `/api/rooms/{id}/leave` | 移除角色 |
| GET | `/api/rooms/{id}/messages` | 群聊消息 |
| GET | `/health` | 健康检查 |

### WebSocket
| 路径 | 说明 |
|------|------|
| `/ws/chat?user_id=&role_id=` | 私聊 |
| `/ws/room/{room_id}?role_id=` | 群聊 |

### 后台 API（`/admin/*`）
联系人 CRUD、Prompt 读写、头像上传、记忆查看、情感/好感度、统计、配置修改。

---

## 📁 项目结构

```
agent-web-refactor/
├── web_app.py                 # 入口
├── config/roles.json          # 角色配置
├── role_prompts/              # 角色 Prompt 文件
├── core/
│   ├── config.py              # 配置管理（.env 读写）
│   ├── initializer.py         # 应用依赖组装（单例）
│   ├── logger.py              # 日志
│   ├── state.py               # LangGraph 状态
│   ├── adapters.py            # Ollama / OpenAI 适配器
│   ├── tools.py               # 搜索 + PC 控制
│   ├── memory/                # 五层记忆（vector_store + memory_manager）
│   ├── emotion/               # 情感 + 好感度（emotion / affection / emotion_store）
│   ├── role/                  # 角色管理（manager.py）
│   └── room/                  # 群聊房间（message_bus + room_manager）
├── agent/
│   ├── graph.py               # LangGraph 自治 Agent
│   └── handlers/              # （空占位）
├── api/
│   ├── routes.py              # HTTP
│   ├── websocket.py           # WS
│   ├── admin.py               # 后台
│   └── websocket_manager.py
├── templates/                 # chat.html + admin.html
├── static/                    # 静态资源/头像
├── models/                    # 本地嵌入模型
└── chromadb/                  # 数据目录（运行时生成）
```

---

## 📐 设计原则

1. **无硬编码规则**：路由、情感判断、事实抽取全部由 LLM 判断。
2. **LLM 优先**：能用 LLM 做的，不用规则替代。
3. **启动加载**：L5 事实启动时一次性加载所有角色，L5 仅按 role_id 隔离。
4. **双键隔离**：其他记忆按 `(user_id, role_id)` 隔离。
5. **模式 B**：情感/好感度分两阶段（分析 JSON → 生成对话）。
6. **无前置路由**：不设路由层和改写层。

> **ChromaDB 多条件查询规范**：本项目中 `SimpleMemory._build_where()` 统一用 `$and` 包裹多个顶层键，避免 `Expected where to have exactly one operator` 报错。

---

## 🔑 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `HOST` / `PORT` | 服务地址 | `0.0.0.0` / `8000` |
| `OLLAMA_HOST` | Ollama 地址 | `http://localhost:11434` |
| `LLM_MODEL` | 主模型 | `qwen3.5:9b` |
| `TOOL_LLM_MODEL` | 工具模型 | `qwen2.5:7b` |
| `BAIDU_API_KEY` | 百度搜索 Key | 空（未配置时降级 B 站热搜） |
| `BAIDU_API_SECRET` | ⚠️ 在 `.env`/`.env.example` 中存在但**代码当前未读取**（仅为预留） | 空 |
| `EMBEDDING_MODEL_DIR` | 本地嵌入模型路径 | `models/all-MiniLM-L6-v2` |
| `EMBEDDING_MODEL_NAME` | 嵌入模型联网名（本地路径不存在时回退下载用） | `all-MiniLM-L6-v2` |
| `MEMORY_CONTEXT_MAX_ROUNDS` | L1 轮数 | `50` |
| `MEMORY_SHORT_TERM_MAX_ROUNDS` | L2 轮数 | `50` |
| `MEMORY_IMPORTANCE_THRESHOLD` | 事实抽取阈值 | `0.6` |
| `MEMORY_FACT_DECAY_DAYS` | 事实衰减天数 | `90` |
| `L3_UPDATE_INTERVAL` / `L3_PUSH_INTERVAL` | ⚠️ 配置项已定义但**当前无代码使用**（L3 未实现） | `7200` / `300` |

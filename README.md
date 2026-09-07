# 🧠 MemBrain

> 一个会记住你、会主动关心你的 AI 桌面宠物 / 角色助手。

**MemBrain** 是一个基于 **LangGraph** 构建的拟人化 AI 陪伴系统：它不只是"问答机器人"，而是一个**有记忆、有情感、会感知当下、甚至会主动找你聊天**的角色。默认角色为《BanG Dream!》的 **户山香澄**，可通过角色系统自由扩展。

它由**浏览器版 Web 应用**和**桌面宠物壳**（可选）组成，核心是统一的一套"拟人化处理链路"——从感知世界、记忆共同经历，到思考、带感情地回复，再到用 Live2D 形象表达、低峰期主动开口。

---

## ✨ 特性

| | 能力 | 说明 |
|---|---|---|
| 💬 | 私聊 / 👥 群聊 | 多角色；群聊**接力对话**（角色按序发言、互相看到最新聊天） |
| 🧠 | 五层记忆 | L1 内存 / L2 短期 / L3 信息池 / L4 事实 / L5 角色事实 |
| 🔍 | 自治路由 | 无路由层、无改写层；LLM **自主决定**是否调工具、调哪个 |
| 🛠️ | 工具调用 | 联网搜索、PC 控制、提醒/时间、文件沙箱、环境感知工具组 |
| 💗 | 情感 / 好感度 | 模式 B 两阶段；6 维好感度持久化，驱动**关系阶段**（陌生→熟悉→亲密→挚友） |
| 🧬 | 关系记忆内核 | 自我模型 / 共同经历账本 / 情绪随时间衰减 / 周期反思 |
| 🕒 | 感知层 | 时序 / 系统 / 位置 / 作息 / 情绪趋势；可选浏览器标签页 + 本地视觉 |
| 🏃 | M2 任务循环 | plan → act → observe 长程自主，多步任务（简单对话不回归） |
| ⏰ | 日程提醒 | 到点主动开口 + WebSocket 推送，离线保留、上线补推 |
| 🔊 | TTS 语音 | 可选对接本地 GPT-SoVITS，把角色回复"说出来"（默认关） |
| 🖼️ | 桌面宠物 | Live2D 立绘壳（pywebview / Qt），消费角色行为做表情/口型/动作 |
| 🖥️ | 后台管理 | 联系人 / 记忆 / 情感 / 统计 / 配置一体管理 |

**核心理念（设计原则）**：

1. **LLM 优先，无硬编码规则** — 路由、情感判断、事实抽取、工具选择都交给 LLM，不用规则替代模型。
2. **deterministic / bounded / harness 辅助** — 记忆结构、衰减、节流、沙箱等确定性部分用自身代码实现，零额外 LLM 或低成本。
3. **不伪造** — 读不到 / 未启用 / 本地无模型时如实返回空或说明，绝不编造"看到了 / 知道了"。
4. **可开关** — 每个能力（感知、记忆、情感、视觉、主动性…）都有独立开关，默认尽可能不影响既有行为。
5. **失败降级** — 单个采集点失败静默跳过，不影响主进程。

---

## 🏗️ 架构概览

MemBrain 按"拟人化主体的处理链路"拆成**七层 + 一个横切支撑层**——判断哪层的关键是它在「让 AI 像个活人」这条链路里扮演的角色，而非文件归属：

```
用户/世界 ──▶ 感知(①) 记忆(②) ──▶ 思维(③)
                                   │ 读当前情绪/关系
                                   ▼
                             情感/关系(④)
                                   │ 决定回复 + 工具
                ┌──────────────────┘
                ▼
            行为(⑤) ─▶ 工具执行 / 动作落地
                │
            表达(⑥) ─▶ 台词 + Live2D 身体 + 择时
                │
            主动(⑦) ─▶ 低频自发开口 ──────► (反馈回记忆/感知)
```

| 层 | 职责 | 关键代码 |
|---|---|---|
| ① 感知 | 看到世界、意识到当下（时序/系统/位置/作息/情绪趋势） | `core/perception.py`、`core/sensing.py`、`core/vision.py` |
| ② 记忆 | 五层记忆 + 关系记忆内核（共同经历/自我模型/衰减） | `core/memory/`、`core/relation_memory.py` |
| ③ 思维 | LangGraph 自治 Agent：`agent→tools→observe→agent` | `agent/graph.py` |
| ④ 情感/关系 | 情感 + 6 维好感度 + 关系阶段（陌生→挚友） | `core/emotion/`、`core/relation_memory.py` |
| ⑤ 行为 | 工具执行（搜索/PC/提醒/文件沙箱/感知） | `core/tools.py`、`core/assistant_tools.py` |
| ⑥ 表达 | 台词 + 行为事件（表情/口型/动作）+ 择时 | `core/behavior.py`、`core/sensing_hint.py` |
| ⑦ 主动 | 低频、克制的主动开口（断联想念/记得承诺/关心情绪） | `core/proactivity.py` |
| 横切 | 配置（`.env`）、LLM 适配、状态、WebSocket 推送 | `core/config.py`、`core/adapters.py`、`api/` |

**五层记忆**：

| L | 名称 | 存储 | 用途 |
|---|---|---|---|
| L1 | 内存上下文 | 内存（按 `user_id+role_id` 隔离） | 当前会话历史、指代消解（默认 50 轮，超限压缩） |
| L2 | 短期记忆 | ChromaDB `short_term` | 跨会话原文召回（冷启动回灌 L1） |
| L3 | 主动信息池 | ChromaDB `l3_info` | 周期采集外部实时信息 → 主动推送 |
| L4 | 重要事实 | ChromaDB `fact` | 用户画像（LLM 抽取，带衰减） |
| L5 | 角色事实 | ChromaDB `role_fact` | 角色设定，仅按 `role_id` 隔离 |

---

## 🚀 快速开始

### 环境要求

- **Python 3.11+**
- **Ollama**（本地模型；或配置 `provider=openai` 用远程兼容接口）
  - 主模型（如 `qwen3.5:9b`）+ 工具模型（如 `qwen2.5:7b`）
  - 嵌入模型（`nomic-embed-text`，推荐）
- 可选：本地嵌入 / BGE 重排器 / 视觉模型 / 百度搜索 Key（见 `.env.example`，缺省自动降级）

### 安装

```bash
git clone https://github.com/jjg692/MemBrain.git
cd MemBrain

# 创建虚拟环境（Windows）
python -m venv .venv
.venv\Scripts\activate

# 安装依赖
pip install -r requirements.txt
```

### 配置

```bash
# 复制示例配置为 .env，然后编辑
cp .env.example .env        # Linux / macOS
copy .env.example .env      # Windows
```

编辑 `.env`，至少设置：

```env
# 使用本地 Ollama
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
LLM_MODEL=qwen3.5:9b        # 主模型
TOOL_LLM_MODEL=qwen2.5:7b   # 工具/抽取/情感模型
```

> 也可使用远程 OpenAI 兼容接口：`LLM_PROVIDER=openai` + `LLM_API_BASE_URL` / `LLM_API_KEY` / `LLM_REMOTE_MODEL`。

### 启动

```bash
python web_app.py
```

浏览器自动打开 `http://localhost:8000`：

| 路径 | 说明 |
|---|---|
| `/` | 聊天界面 |
| `/admin` | 后台管理 |
| `/health` | 健康检查 |

> 不想自动弹浏览器：`MEMBRAIN_NO_BROWSER=1 python web_app.py`

---

## 🖥️ 桌面宠物

浏览器版之外，提供两种桌面形态：

| 形态 | 命令 | 说明 |
|---|---|---|
| M1 壳（pywebview） | `python desktop_pet.py` | 无边框置顶聊天窗 + 系统托盘，自动拉起后端 |
| Qt 版（透明悬浮） | `python desktop_pet_qt.py --pet` | 透明置顶悬浮 Live2D 宠物（需系统 Python 3.11 含 PySide6） |

更多模式：`desktop_pet_qt.py --window`（大窗聊天）/ `--twin`（双窗口）/ `--backend-only`（仅后端）。
Live2D 模型、角色与渲染契约见 `docs/dual-window-contract.md`。

---

## 💻 代码示例 & 使用场景

### 场景 1：本地跑起来，私聊一个角色

```bash
python web_app.py
# 浏览器打开 http://localhost:8000，选角色 kasumi，开始聊天
```

### 场景 2：在代码里直接调用 Agent（无 Web 界面）

```python
from core.initializer import AppInitializer

init = AppInitializer()
agent = init.get_agent("default_user", "kasumi")   # (user_id, role_id) 取 LangGraph Agent

reply = agent.chat("default_user", "今天天气怎么样？带伞吗？")
print(reply)                                        # 角色会调用 search_web 查真实天气再回答
```

### 场景 3：读取 / 修改配置（后台管理与代码等价）

```python
from core.config import update_config, get_config_snapshot

get_config_snapshot()              # 列出所有可编辑配置
update_config("LLM_MODEL", "qwen3.5:9b")   # 修改并持久化到 .env
```

### 场景 4：让角色主动开口一句（桌面宠物/定时用）

```python
reply = agent.proactive_message("default_user", trigger="到打招呼时间了")
print(reply)
```

> 主动性默认关闭（`PROACTIVITY_ENABLED=false`）；开启后常驻线程会低频评估并主动开口，受最小间隔/日封顶约束。

### 场景 5：WebSocket 实时聊天

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect(
        "ws://localhost:8000/ws/chat?user_id=default_user&role_id=kasumi"
    ) as ws:
        await ws.send(json.dumps({"type": "user_message", "content": "在吗？"}, ensure_ascii=False))
        while True:
            msg = json.loads(await ws.recv())
            print(msg)

asyncio.run(main())
```

---

## 🔌 API

### HTTP（公开）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/contacts` | 角色列表 |
| GET | `/api/history?user_id=&role_id=` | 历史消息 |
| GET/POST | `/api/profile` | 用户昵称 |
| GET/POST | `/api/reminders*` | 提醒增删改 / 启停 |
| GET/POST | `/api/rooms*` | 群聊 CRUD |
| GET | `/health` | 健康检查 |

### Live2D

| 路径 | 说明 |
|---|---|
| GET | `/live2d?petmode=1` | 透明宠物页 |
| GET | `/live2d-chat` | 双窗口独立对话窗 |
| GET | `/api/live2d/models` · `/api/live2d/config` | 模型列表 / 渲染环境 |
| GET | `/live2d-models/{path}/model.json` | 模型静态资源 |

### WebSocket

| 路径 | 说明 |
|---|---|
| `/ws/chat?user_id=&role_id=` | 私聊 |
| `/ws/room/{room_id}?role_id=` | 群聊 |

### 后台（`/admin/*`）

联系人 CRUD、Prompt 读写、头像上传、记忆 / 情感 / 统计查看、配置修改、Live2D 模型配置。
后台可编辑的配置项（开关、阈值等）见 `core/config.py` 的 `EDITABLE_KEYS`。

---

## 🎭 角色 Prompt 生成器（可选工具）

从萌娘百科 / 中文维基 / Fandom 检索角色资料，用 LLM 做行为蒸馏，生成可加载的角色 prompt。

```bash
python scripts/generate_role.py "户山香澄" --work "BanG Dream!" --out role_prompts/role_prompt_kasumi.txt
# 其他：--list-sources 列出数据源 / --save-source 调试保存
```

生成的 `role_prompts/role_prompt_{role_id}.txt` 会被 `RoleManager.load_prompt()` 直接使用；
角色清单与默认角色配置在 `config/roles.json`。

---

## 📁 项目结构

```
agent-web-refactor/
├── web_app.py                    # Web 入口（FastAPI + 路由装配 + 启动）
├── desktop_pet.py                # 桌面宠物（pywebview 壳）
├── desktop_pet_qt.py             # 桌面宠物（Qt 透明悬浮 Live2D）
├── requirements.txt              # 依赖清单
├── core/
│   ├── config.py                 # 配置管理（.env + 可编辑项）
│   ├── tools.py                  # 搜索 + PC 控制 + 工具注册
│   ├── assistant_tools.py        # 助手工具（提醒/时间/文件沙箱）
│   ├── perception.py             # 感知层（时序/系统/作息/情绪趋势）
│   ├── sensing.py                # 环境感知工具（标签页/前台/摘要）
│   ├── sensing_hint.py           # 感知→表达触发
│   ├── vision.py                 # 本地视觉（Ollama 多模态 → 文本）
│   ├── relation_memory.py        # 关系记忆内核（自我模型/共同经历/衰减/反思）
│   ├── reminder.py               # 日程/提醒引擎
│   ├── proactivity.py            # 主动性决策（低频主动开口）
│   ├── behavior.py               # 行为映射（表情/口型/动作）
│   ├── tts_client.py             # TTS 语音合成（GPT-SoVITS，默认关）
│   ├── memory/                   # 五层记忆（vector_store + memory_manager + l3）
│   ├── emotion/                  # 情感 + 好感度 + 关系阶段
│   ├── role/                     # 角色管理
│   ├── room/                     # 群聊房间
│   ├── mcp_client.py             # MCP 客户端（可插拔扩展）
│   └── mcp_registry.py           # MCP 插件注册中心（动态增删工具）
├── agent/
│   ├── graph.py                  # LangGraph 自治 Agent（agent→tools→observe）
│   └── planner.py                # M2 任务规划器（plan 骨架）
├── api/                          # 路由 / WS / 后台 / Live2D
├── templates/                    # chat.html / admin.html / live2d 页
├── static/                       # 前端资源 / live2d 模型
├── config/
│   ├── roles.json                # 角色清单
│   └── mcp.json                  # 外部 MCP 扩展声明
├── role_prompts/                 # 角色 prompt 文本
├── scripts/                      # 角色 prompt 生成器
├── test/                         # 测试（pytest）
└── docs/                         # 文档
    └── dual-window-contract.md   # 内核↔壳接口契约
```

---

## 🧪 测试

```bash
python -m pytest test -q
```

覆盖：记忆 / 感知 / 情感 / 工具执行 / 任务循环 / WebSocket / 主动心跳 / TTS / 视觉 / MCP 插件化等。

---

## 🔑 配置一览

> 完整清单与默认值以 **`.env.example`** 为权威来源；后台 `/admin/config` 的 `EDITABLE_KEYS` 可直接在线改。

| 变量 | 说明 | 默认 |
|---|---|---|
| `HOST` / `PORT` | 监听地址 / 端口 | `0.0.0.0` / `8000` |
| `OLLAMA_HOST` | Ollama 地址 | `http://localhost:11434` |
| `LLM_PROVIDER` | `ollama`（本地）/ `openai`（远程兼容接口） | `ollama` |
| `LLM_MODEL` / `TOOL_LLM_MODEL` | 主模型 / 工具模型 | `qwen3.5:9b` / `qwen2.5:7b` |
| `LLM_API_BASE_URL` / `LLM_API_KEY` / `LLM_REMOTE_MODEL` | 远程接口配置（`provider=openai` 时用） | 空 |
| `BAIDU_API_KEY` / `BAIDU_API_SECRET` | 百度搜索 Key（可选） | 空 |
| `EMBEDDING_MODE` / `OLLAMA_EMBED_MODEL` | 嵌入模式 / Ollama 嵌入模型 | `ollama` / `nomic-embed-text` |
| `ENABLE_RERANK` / `RERANKER_BACKEND` | 是否重排 / 后端（`bge`/`minilm`） | `true` / `bge` |
| `MEMORY_*` | 记忆上下文轮数 / 事实阈值 / 衰减天数 | 见 `.env.example` |
| `L3_ENABLED` / `L3_*` | 主动信息池开关 / 采集 / 推送周期 | `true` |
| `PERCEPTION_ENABLED` / `PERCEPTION_*` | 感知层开关与参数 | `true` |
| `ENVIRONMENT_SENSING_ENABLED` / `BROWSER_*` | 环境感知工具组（标签页/前台/摘要） | `true` |
| `VISION_ENABLED` / `VISION_MODEL` | 本地视觉开关 / 视觉模型 | `false` / `qwen2.5-vl:7b` |
| `PROACTIVITY_ENABLED` / `PROACTIVITY_*` | 主动性心跳开关 / 最小间隔 / 日封顶 | `false` / `30` / `8` |
| `RELATION_MEMORY_*` | 关系记忆内核（半衰期/共振阈值/反思） | 见 `.env.example` |
| `REMINDER_SCAN_INTERVAL` / `REMINDER_FILE` | 提醒调度 / 文件 | `15` / `reminders.json` |
| `TTS_ENABLED` / `TTS_*` | TTS 语音合成（GPT-SoVITS） | 见 `.env.example`（默认全关） |
| `LIVE2D_ENABLED` / `LIVE2D_*` | Live2D 开关 / 模型根目录 / 渲染器 | `true` / `live2d` / `l2dwidget` |

---

## 🤝 贡献指南

欢迎贡献！请遵循项目的**设计原则**（LLM 优先、harness 兜底、不伪造、可开关、失败降级、双键隔离）。

### 工作流

1. **Fork** 本仓库并 clone 到本地。
2. 创建功能分支：`git checkout -b feat/your-feature`。
3. 进行改动，**保持代码模块归属清晰**（感知/记忆/思维/情感/行为/表达/主动 各层边界明确）。
4. 为新功能补充**测试**（`test/`，用 `python -m pytest test -q` 跑通）。
5. 运行全部测试并确保绿：
   ```bash
   python -m pytest test -q
   ```
6. **Commit**（清晰的中文或英文 message）并推送分支。
7. 提交 **Pull Request**，说明改动动机与测试结果。

### 规范建议

- **不侵入式**：新能力默认关闭、失败静默降级，不影响既有行为。
- **不伪造**：读不到 / 未启用时如实返回，绝不编造。
- **可开关**：每个能力带独立开关，后台 `EDITABLE_KEYS` 可配。
- 涉及 `.env` 新增配置时，同步更新 `.env.example` 与本文档的配置表。

---

## 📝 许可证

本项目**未声明开源许可证**（All rights reserved）——代码仅供个人学习 / 非商业研究使用，未经授权请勿用于商业或公开发布。

第三方组件版权归其各自作者：

- 角色（《BanG Dream!》户山香澄等）版权归其原始版权方所有，本项目仅作本地演示用途。
- 星露谷 MCP 桥接（`stardew/StardewValley-MCP`）来自开源项目 [luy-0/StardewValley-MCP](https://github.com/luy-0/StardewValley-MCP)（Apache-2.0），在仓库外维护，不在本仓库内。

---

*MemBrain (Refactor) — v2.0.0*

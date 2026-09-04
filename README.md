# 🧠 MemBrain (Refactor)

> 一个会记住你、会主动关心你的 AI 桌面宠物 / 助手。基于 **LangGraph** 构建，默认角色为《BanG Dream!》的 **户山香澄**。

坚持统一的设计原则，代码清晰、可运行、可扩展。

---

## ✨ 设计原则

项目所有能力都遵循同样的底线，扩展任何功能时都应遵守：

1. **LLM 优先，无硬编码规则** — 路由、情感判断、事实抽取、工具选择全部交给 LLM，不用规则替代模型。
2. **deterministic / bounded / harness 辅助** — 记忆结构、衰减、节流、沙箱等确定性部分用 harness 实现，**零额外 LLM 或低成本**。
3. **不伪造** — 读不到 / 未启用 / 本地无模型时如实返回空或说明，绝不编造"看到了 / 知道了"。
4. **可开关** — 每个能力（感知、记忆、情感、视觉、主动性、MCP…）都有独立开关，默认尽可能不影响既有行为。
5. **失败降级** — 单个采集点失败静默跳过，不影响主进程。
6. **双键隔离** — 记忆按 `(user_id, role_id)` 隔离；角色事实仅按 `role_id`。

---

## ✨ 核心能力

| 能力 | 说明 |
|------|------|
| 💬 私聊 / 👥 群聊 | 多角色，群聊**接力对话**（角色按序发言并互相看到最新聊天） |
| 🧠 五层记忆 | L1 内存 / L2 短期 / L3 信息池 / L4 事实 / L5 角色事实 |
| 🔍 自治路由 | 无路由层/改写层，LLM 自主决定是否调工具 |
| 🛠️ 工具调用 | 搜索（多源意图路由）、PC 控制、提醒/时间、文件沙箱、环境感知工具组 |
| 💗 情感 / 好感度 | 模式 B 两阶段，6 维好感度持久化，驱动**关系阶段**（陌生→熟悉→亲密→挚友） |
| 🧬 关系记忆内核 | 持续**自我模型** / **共同经历账本** / **情绪随时间衰减** / **周期反思** |
| 🕒 感知层 | 时序/系统/位置/作息/情绪趋势/忙碌在场/关系投入；可选浏览器标签页与本地视觉 |
| 🏃 M2 任务循环 | plan→act→observe 长程自主，多步任务（简单一句不回归） |
| ⏰ 日程提醒 | 到点主动开口 + WS 推送，离线保留、上线补推 |
| 🖥️ 后台管理 | 联系人/记忆/情感/统计/配置 一体管理 |

---

## 🚀 快速开始

### 环境要求
- Python 3.11+
- Ollama（已拉取主模型 + 工具模型；`provider=openai` 时也可用远程兼容接口）
- 可选：本地嵌入模型 / BGE 重排器（见 `.env.example`，缺省使用 Ollama 嵌入并降级）

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
# 编辑 .env：设置 LLM_MODEL / TOOL_LLM_MODEL / OLLAMA_HOST / 百度 Key（可选）
```

### 启动

```bash
python web_app.py              # 或 MEMBRAIN_NO_BROWSER=1 python web_app.py（不自动弹浏览器）
```

浏览器自动打开 `http://localhost:8000`：
- 聊天界面：`/`
- 后台管理：`/admin`
- 健康检查：`/health`

---

## 🖥️ 桌面宠物

在浏览器版之上，提供两种桌面形态：

| 形态 | 命令 | 说明 |
|------|------|------|
| M1 壳（pywebview） | `python desktop_pet.py` | 无边框置顶聊天窗 + 系统托盘；自动拉起后端 |
| Qt 版（透明悬浮） | `python desktop_pet_qt.py --pet` | 透明置顶悬浮 Live2D 宠物 + 独立聊天窗（需 Python 3.11） |

更多模式：`desktop_pet_qt.py --window`（大窗聊天）/ `--twin`（双窗口）/ `--backend-only`（仅后端）。
Live2D 模型、角色与渲染契约见 `docs/dual-window-contract.md`。

---

## 🧠 关键机制

### 五层记忆

| L | 名称 | 存储 | 用途 |
|---|------|------|------|
| L1 | 内存上下文 | 内存（双键隔离） | 当前会话历史、指代消解（50 轮，超限压缩） |
| L2 | 短期记忆 | ChromaDB `short_term` | 跨会话原文召回（冷启动回灌 L1） |
| L3 | 主动信息池 | ChromaDB `l3_info` | 周期采集外部实时信息 → 主动推送 |
| L4 | 重要事实 | ChromaDB `fact` | 用户画像（LLM 抽取，带衰减） |
| L5 | 角色事实 | ChromaDB `role_fact` | 角色设定，仅按 role_id 隔离，启动一次性加载 |

### 情感 / 关系

两阶段处理：`EmotionAnalyzer` 先让 LLM 输出 JSON（情感 + 6 维好感度更新），再基于分析+记忆+人设生成回复。好感度驱动**关系阶段**与称呼/距离感/开放度，但**不改动角色内核**（防 OOC）。群聊接力跳过情感更新（`persist_emotion=False`），避免误判。

### 感知层

持续观察（非被动等待），每轮注入 system prompt：
- 时序 / 系统环境 / 位置情境 / 作息习惯 / 情绪趋势 / 忙碌在场 / 关系投入 / 作息异常
- 可选：浏览器标签页（CDP）、本地视觉（Ollama 多模态 → 文本转述）
- 感知结果经**新鲜度标记 + 密度裁剪**（防 prompt 膨胀），慢速系统查询有缓存

提供 3 个 LLM 可主动调用工具：`get_current_tab` / `get_foreground_window` / `get_perception_summary`。

### M2 任务循环

多步任务由 `TaskPlanner` 生成骨架 plan，按 **plan → act(工具) → observe → 再回轮** 推进；简单一句话交流不触发。全部决策仍由 LLM 完成，harness 只做骨架与防死循环（`iteration` 上限）。

---

## 🔌 API

### HTTP（公开）
| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/contacts` | 角色列表 |
| GET | `/api/history?user_id=&role_id=` | 历史消息 |
| GET/POST | `/api/profile` | 用户昵称 |
| GET/POST | `/api/reminders*` | 提醒增删改/启停 |
| GET/POST | `/api/rooms*` | 群聊 CRUD |
| GET | `/health` | 健康检查 |

### Live2D
| 路径 | 说明 |
|------|------|
| GET | `/live2d?petmode=1` | 透明宠物页 |
| GET | `/live2d-chat` | 双窗口独立对话窗 |
| GET | `/api/live2d/models` / `/api/live2d/config` | 模型列表 / 渲染环境 |
| GET | `/live2d-models/{path}/model.json` | 模型静态资源 |

### WebSocket
| 路径 | 说明 |
|------|------|
| `/ws/chat?user_id=&role_id=` | 私聊 |
| `/ws/room/{room_id}?role_id=` | 群聊 |

### 后台（`/admin/*`）
联系人 CRUD、Prompt 读写、头像上传、记忆/情感/统计查看、配置修改、Live2D 模型配置。
后台可编辑的配置项（开关、阈值等）见 `core/config.py` 的 `EDITABLE_KEYS`。

---

## 🎭 角色 Prompt 生成器（可选工具）

参考 [Character_Skill_Producer](https://github.com/qian-gugugaga/Character_Skill_Producer)：
从萌娘百科 / 中文维基 / Fandom 检索角色资料 → LLM **行为蒸馏** → 生成可加载的 role prompt。

```bash
python scripts/generate_role.py "户山香澄" --work "BanG Dream!" --out role_prompts/role_prompt_kasumi.txt
# 其他：--list-sources 列出数据源 / --save-source 调试保存
```

生成的 `role_prompts/role_prompt_{role_id}.txt` 会被 `RoleManager.load_prompt()` 直接使用。

---

## 🧩 可选扩展：星露谷 MCP

让宠物感知并参与星露谷游戏（读状态 / 进游戏当同伴 / 记忆反写 / 多 agent 小队）。**默认关闭**
（`STARDEW_MCP_ENABLED=false`），不影响不玩星露谷的用户；想启用只需在后台或 `.env` 打开开关。

本项目**不自己实现 MCP 服务端**，而是直接复用开源项目
**[luy-0/StardewValley-MCP](https://github.com/luy-0/StardewValley-MCP)**（**Apache-2.0** 许可，
共 **22 个工具**：6 只读 + 16 操作），在此致谢其作者。

- **实现形态**：Python MCP 服务器（官方 `mcp` SDK）+ SMAPI Mod（C#），Mod 与服务器经本地
  TCP(24642) + protobuf + 共享密钥握手。
- **本仓库侧**：`core/mcp_client.py` 在启动时按 `config/mcp.json` 动态发现并注册
  `mcp_<server>_<tool>` 工具到 `ALL_TOOLS`/`TOOL_REGISTRY`，LLM 即可调用；`stardew/`
  目录承载记忆反写 / 多 agent 协调等本仓库自有逻辑。
- 未开启/未启动/未装游戏时**静默降级**，不影响主进程（符合"不伪造、失败降级"原则）。

**启用步骤（简明版）**：

1. **安装 MCP 服务器**（需 `uv`）：
   `git clone https://github.com/luy-0/StardewValley-MCP luy0 && cd luy0 && uv tool install ./mcp && stardew-valley-mcp doctor`
2. **部署 SMAPI Mod**：从 [Releases](https://github.com/luy-0/StardewValley-MCP/releases) 下载
   `StardewValleyMCP-Mod-v*.zip`（或源码 `./mod/scripts/build.sh --deploy`）解压到游戏 `Mods/`，
   用 SMAPI 启动并加载存档；Mod 首次启动会在 `Mods/StardewValleyMCP/config.json` 生成监听地址与随机 `SharedSecretBase64`。
3. **填密钥**：把 Mod `config.json` 的 `SharedSecretBase64` 填入 `config/mcp.json` 的
   `STARDEW_VALLEY_MCP_SHARED_SECRET`（`command`=`stardew-valley-mcp`，`args` 只读 `["serve"]`
   / 可操作 `["serve","--allow-write"]`）。
4. **打开开关并重启**：`.env` 写 `STARDEW_MCP_ENABLED=true`，再 `python web_app.py`。

> 完整架构、依赖环境与排错见 **`stardew/README.md`**。

---

## 📁 项目结构

```
agent-web-refactor/
├── web_app.py                 # Web 入口
├── desktop_pet.py / desktop_pet_qt.py   # 桌面宠物（pywebview / Qt 透明 Live2D）
├── core/
│   ├── config.py              # 配置管理（.env + EDITABLE_KEYS）
│   ├── tools.py               # 搜索 + PC 控制 + 工具注册
│   ├── assistant_tools.py     # 助手工具（提醒/时间/文件沙箱）
│   ├── perception.py          # 感知层（时序/系统/作息/情绪趋势）
│   ├── sensing.py             # 环境感知工具（标签页/前台/摘要）
│   ├── sensing_hint.py        # 感知→表达触发
│   ├── vision.py              # 本地视觉（Ollama 多模态 → 文本）
│   ├── relation_memory.py     # 关系记忆内核（自我模型/共同经历/衰减/反思）
│   ├── reminder.py            # 日程/提醒引擎
│   ├── proactivity.py         # 主动性决策（低频主动开口）
│   ├── memory/                # 五层记忆（vector_store + memory_manager + l3）
│   ├── emotion/               # 情感 + 好感度 + 关系阶段
│   ├── role/                  # 角色管理
│   └── room/                  # 群聊房间
├── agent/
│   ├── graph.py               # LangGraph 自治 Agent（agent→tools→observe）
│   └── planner.py             # M2 任务规划器（plan 骨架）
├── api/                       # 路由 / WS / 后台 / Live2D
├── templates/                 # chat.html / admin.html / live2d 页
├── static/                    # 前端资源 / live2d
├── stardew/                   # 星露谷 MCP 可选扩展（含独立 README）
└── scripts/                   # 角色 prompt 生成器
```

---

## 🧪 测试

```bash
python -m pytest test -q --ignore=backup
```

覆盖：记忆 / 感知 / 情感 / 工具执行 / 任务循环 / WS / 星露谷 / 视觉等。

---

## 🔑 环境变量

> 完整清单与默认值见 **`.env.example`**（权威来源），后台管理页 `/admin/config` 的 `EDITABLE_KEYS` 可直接改。

| 变量 | 说明 | 默认 |
|------|------|------|
| `HOST` / `PORT` | 服务监听地址 / 端口 | `0.0.0.0` / `8000` |
| `OLLAMA_HOST` | Ollama 地址 | `http://localhost:11434` |
| `LLM_PROVIDER` | `ollama`（本地）/ `openai`（远程兼容接口） | `ollama` |
| `LLM_API_BASE_URL` / `LLM_API_KEY` / `LLM_REMOTE_MODEL` / `LLM_REMOTE_TOOL_MODEL` | 远程 OpenAI 兼容接口配置（provider=openai 时用） | 空 |
| `LLM_MODEL` | 主模型（回复生成） | `qwen3.5:9b` |
| `TOOL_LLM_MODEL` | 工具/抽取/情感分析模型 | `qwen2.5:7b` |
| `LLM_TEMPERATURE` | 采样温度（角色扮演略高，0.0-1.0） | `0.85` |
| `BAIDU_API_KEY` / `BAIDU_API_SECRET` | 百度搜索 Key（可选；配了作为通用搜索优先源） | 空 |
| `EMBEDDING_MODE` | `ollama`（nomic-embed-text，推荐）/ `local`（本地 sentence-transformers） | `ollama` |
| `EMBEDDING_MODEL_DIR` / `OLLAMA_EMBED_MODEL` | 本地嵌入路径 / Ollama 嵌入模型 | `models/all-MiniLM-L6-v2` / `nomic-embed-text` |
| `ENABLE_RERANK` / `RERANKER_BACKEND` | 是否重排 / 后端（`bge` / `minilm` / 空=自动） | `true` / `bge` |
| `BGE_RERANKER_DIR` / `BGE_RERANKER_ONNX` | BGE 重排器目录 / ONNX 文件名 | `models/bge-reranker-v2-m3` / `model.onnx` |
| `CROSS_ENCODER_ONNX_PATH` | 旧 ms-marco 重排器 ONNX（兜底） | `models/ms-marco-.../...onnx` |
| `MEMORY_CONTEXT_MAX_ROUNDS` | L1 内存上下文轮数 | `50` |
| `MEMORY_SHORT_TERM_MAX_ROUNDS` | L2 短期记忆轮数（FIFO） | `50` |
| `MEMORY_IMPORTANCE_THRESHOLD` | L4 事实抽取阈值（0-1） | `0.6` |
| `MEMORY_FACT_DECAY_DAYS` | L4 事实衰减周期（天） | `90` |
| `MEMORY_DEBUG` | 记忆调试输出 | `false` |
| `L3_ENABLED` | 启用 L3 主动信息池（采集+推送） | `true` |
| `L3_UPDATE_INTERVAL` / `L3_PUSH_INTERVAL` | L3 采集 / 推送周期（秒） | `7200` / `300` |
| `L3_KEYWORDS` | L3 采集关键词（逗号分隔） | `天气,今日热点,二次元话题` |
| `L3_MAX_ITEMS` | L3 池最大条目数 | `200` |
| `PERCEPTION_ENABLED` | 启用感知层 | `true` |
| `PERCEPTION_CITY` | 用户常驻城市（位置情境；可选） | 空 |
| `MOOD_TREND_MAX_SAMPLES` / `ROUTINE_WINDOW_DAYS` | 情绪趋势样本数 / 作息活跃窗口天数 | `200` / `30` |
| `PERCEPTION_PROMPT_MAX_CHARS` | 感知 prompt 总字符上限（超限按优先级裁剪） | `500` |
| `PERCEPTION_SYSTEM_CACHE_TTL` | 系统启动时刻缓存 TTL（秒），避免每轮查 PowerShell | `300` |
| `ENVIRONMENT_SENSING_ENABLED` | 环境感知工具组总开关 | `true` |
| `BROWSER_DEBUG_PORT` / `BROWSER_SENSING_TIMEOUT` / `MAX_TAB_TITLE_CHARS` | 浏览器 CDP 端口 / 超时 / 标题截断 | `9222` / `3` / `40` |
| `BROWSER_TAB_SENSING_ENABLED` / `FOREGROUND_SENSING_ENABLED` / `PERCEPTION_SUMMARY_SENSING_ENABLED` | 三个感知工具独立开关 | `true` |
| `FOREGROUND_SENSING_TIMEOUT` | 前台窗口采集超时（秒） | `2` |
| `SENSING_TRIGGER_ENABLED` | 感知→表达触发提示 | `true` |
| `VISION_ENABLED` / `VISION_MODEL` | 本地视觉总开关 / Ollama 视觉模型 | `false` / `qwen2.5-vl:7b` |
| `VISION_IN_CHAT` / `VISION_SCREEN_ON_DEMAND` / `VISION_TIMEOUT` | 对话图 / 桌面窗口识别 / 推断超时 | `true` / `false` / `20` |
| `PROACTIVITY_ENABLED` / `PROACTIVITY_MIN_INTERVAL_MIN` / `PROACTIVITY_DAILY_CAP` | 主动性心跳开关 / 最小间隔（分）/ 每日上限 | `false` / `30` / `8` |
| `RELATION_MEMORY_FILE` / `RELATION_MEMORY_ENABLED` | 关系记忆文件 / 开关 | `relation_memory.json` / `true` |
| `RELATION_EMOTION_HALFLIFE_DAYS` | 情绪/好感度/经历半衰期（天；0=不衰减） | `21` |
| `RELATION_EPISODE_RESONANCE_THRESHOLD` | 写入经历账本的最小情绪共振（0-1） | `0.55` |
| `RELATION_REFLECT_INTERVAL` / `RELATION_REFLECT_BATCH` | 反思间隔（秒；0=禁用）/ 每批候选数 | `3600` / `20` |
| `REMINDER_SCAN_INTERVAL` / `REMINDER_FILE` | 提醒调度扫描间隔（秒）/ 文件 | `15` / `reminders.json` |
| `ASSISTANT_WORKSPACE_DIR` | 助手文件工具沙箱根目录 | `assistant_workspace` |
| `STARDEW_MCP_ENABLED` | 星露谷 MCP 总开关（默认关） | `false` |
| `STARDEW_MEMORY_POLLER_ENABLED` | 星露谷记忆自动沉淀（需 MCP 也开） | `false` |
| `STARDEW_POLL_INTERVAL` | 星露谷状态轮询间隔（秒） | `60` |
| `LIVE2D_ENABLED` / `LIVE2D_MODEL_ROOT` / `LIVE2D_RENDERER` / `LIVE2D_DEFAULT_MODEL` | Live2D 开关 / 模型根目录 / 渲染器 / 默认模型 | `true` / `live2d` / `l2dwidget` / 空 |
| `LIVE2D_BODY_MODE` | Live2D 情绪身体表达模式（`C` 内核映射 / `B` LLM 主动指挥） | `C` |

> 星露谷 MCP 使用的开源仓库地址：[luy-0/StardewValley-MCP](https://github.com/luy-0/StardewValley-MCP)（Apache-2.0，详见「可选扩展：星露谷 MCP」一节）。

---

## 📚 文档

- `docs/dual-window-contract.md` — 内核 ↔ 壳的接口契约与分工边界（窗口 A/B）
- `stardew/README.md` — 星露谷 MCP 扩展完整说明
- `.env.example` — 全部可配置环境变量及说明

---
*MemBrain (Refactor) — v2.0.0*

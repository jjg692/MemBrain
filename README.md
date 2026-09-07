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

## 🤖 AI 拟人化层次

MemBrain 按「拟人化主体的处理链路」拆成**七层 + 一个横切支撑层**。判断"哪层"的标准不是文件归属，而是某功能在「让 AI 像个活的人」这条链路里扮演的角色——从"看到世界"到"主动开口"，每层都有清晰的 harness / 模型边界：**确定性的采集、结构、阈值由 harness 负责，"怎么理解 / 怎么想 / 怎么说"交给 LLM**（LLM 优先，无硬编码判断）。

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

### ① 感知层（我能看到 / 我意识到当下）

把"外部世界 + 用户状态"转成结构化信号，注入给"意识"（system prompt），让 AI 不再是无时空感的一次性回复工具。落地在 `core/perception.py` / `core/sensing.py` / `core/vision.py`：

| 感知项 | 函数 | 说明 |
|---|---|---|
| 时序 | `time_situation` | 几点 / 星期 / 早午晚深夜 / 周末 |
| 系统环境 | `system_situation` | 系统运行时长 / 前台窗口（Windows 原生，读不到不伪造） |
| 位置情境 | `location_situation` | 常驻城市 + 由时段推导场景（工作/午休/自由/休息） |
| 浏览器当前标签页 | `_current_tab_text` + CDP | 标题 + 链接（需以 debug 端口启动浏览器） |
| 桌面窗口视觉 | `_current_screen_text` + vision 场景 B | 本地 Ollama VLM 转述屏幕（默认关） |
| 对话图片视觉 | vision 场景 A | 转述用户发来的图片（默认关） |
| 作息/情绪趋势/在场/关系投入/熬夜异常 | `RoutineModel` / `MoodTrend` / `summarize` | 由历史活跃样本聚合 |

设计铁律：`PerceptionManager.summarize()` 把结果拼成【你对当下时空与用户状态的感知】注入 system prompt；**读取不到一律不伪造**。感知最新还带**新鲜度标记**（瞬时采样项追加采集时刻戳）与**密度裁剪**（`PERCEPTION_PROMPT_MAX_CHARS` 超限按价值优先级裁剪），慢速系统查询有缓存。

### ② 记忆层（我记得我们发生过什么）

拟人的"内在历史"。五层架构 + 关系记忆内核：

- **L1→L5**：内存上下文（当前会话 + 指代消解，超限压缩）/ 短期（ChromaDB，冷启动回灌 L1）/ 主动信息池（周期采集外推）/ 重要事实（LLM 抽取，带衰减）/ 角色事实（按 role_id 隔离）
- **关系记忆内核 `RelationMemory`**：`episodes` 共同经历账本、`user_model`/`self_model` 自我模型、`values` 价值内核、`promises` 承诺账本、指数半衰期时间衰减、周期反思

拟人化亮点：每条经历记 `resonance`（情绪共振）+ `impact` + `vitality`（鲜活度随时间衰减），支持 `resolve_promises_on_user_signal`（用户提"上次说好…"自动兑现承诺）——"记性好 + 会随时间自然地淡忘/亲近"。边界：存储结构、衰减、阈值是 harness；"抽事实 / 抽反思结论 / 算共振"交给 LLM。

### ③ 思维链路层（我该怎么想、怎么做）

"大脑皮层"，由接收到决定回复的整个过程。核心在 `agent/graph.py`（LangGraph）：

- **图**：`agent → tools → observe → agent → END`，带 iteration 防死循环上限
- **自治路由**：`_agent_node` 让 LLM 自主决定是否调工具、调哪个、要不要走任务循环
- **M2 任务循环**：`agent/planner.py` 纯函数 `TaskPlanner` 判断简单/多步 → 生成 plan；`_observe_node` 累计观察；`_build_task_prompt` 注入进度
- **兜底守卫**：`_needs_realtime`/`_needs_remind` 在模型"说要查却不查"时强制补一次工具调用
- **情感预判**：`EmotionAnalyzer` 让 LLM 先输出 JSON（情感 + 6 维好感度），再生成回复（模式 B）
- **群聊接力**：`persist_emotion=False` 跳过情感分析，避免角色互聊被误当用户情感

边界：流程编排、路由判断、防死循环是 harness；思考与决策是模型。`TaskPlanner.plan` 故意做成纯函数，只搭骨架不替代 LLM 判断。

### ④ 情感/关系层（我此刻的心情 / 我和你什么关系）

拟人区别于工具的核心——情感与关系是**持续演化**的，不是临时变量。落地 `core/emotion` + `core/relation_memory.py`：

- **6 维好感度**：喜欢/信任/熟悉/尊重/兴趣/依恋（0-1），跨会话持久化
- **关系养成阶段** `relationship_stage`：好感度 + 共同经历 → 陌生/熟悉/亲密/挚友；`relation_call_name`（称呼）+ `_stage_behavior_text`（距离感/开放度注入 prompt）——**有原因的升段**（经历证据够才提前），且不改动内核防 OOC
- **关系记忆**：`affection_reasons`（各维度为什么）、`experience_evidence`（升段依据）、`apply_decay`（随时间回归基线）

亮点：好感度不是死数值，而是有来源、会衰减、能驱动行为差异。边界：数值结构/衰减/阶段阈值是 harness；"这条消息情绪是什么、好感度该涨多少"由 LLM 判定。

### ⑤ 行为层（我实际去做什么）

把"思考结论"变成对外动作。落地 `core/tools.py` / `core/assistant_tools.py` / `stardew/runtime.py`：

- **工具执行**：`TOOL_REGISTRY` + `execute_tool` 动态注册；`search_web`（意图路由多源回退）/ `control_pc` / 助手工具（提醒、时间、文件，严格沙箱）/ 感知工具（可选）/ `express_body`（Live2D 指挥）/ MCP（星露谷，可选）
- **动作落地**：LangGraph `ToolNode` 实际调用并回写 `ToolMessage`
- **游戏旁路** `stardew/runtime.py`：后台线程感知游戏状态 → 沉淀记忆（旁路观察者，不介入 ReAct 闭环）

边界：工具执行、沙箱、装载是 harness；"选哪个工具、要不要做"由 LLM 自主决定。

### ⑥ 表达层（我怎样把话说出口 / 动起来）

拟人最后一公里——"说什么"已由思维层定，"怎么呈现"这里负责。落地 `core/behavior.py` / `core/sensing_hint.py` / `graph.py::proactive_message`：

- **语言表达**：`_build_system_prompt` 注入【角色扮演守则】、称呼风格、角色口吻
- **身体表达** `BehaviorMapper.derive(reply, emotion)` 纯函数输出标准行为事件 `{emotion, expression, mouth_open, actions, pitch_hint}`，供 Live2D 壳做表情/口型/动作——壳不用猜，内核说了算
- **感知共鸣** `sensing_hint`：前台窗口/标签页变化 → 低频注入"用户刚切换到了…"（冷却）
- **主动性表达** `proactive_message`：主动开口时 `_compose_proactive_hint` 从关系记忆挑"此刻值得提的"素材注入；`L3Pusher` / `ReminderScheduler` 到点/有料时推送

亮点：贯彻"先算情绪 → 再定动作"，`BehaviorMapper` 是纯函数——身体反应由内核决定而非前端猜。边界：表情映射、冷却、口型计算是 harness；最终台词由模型生成。

### ⑦ 主动性层（我为什么要先开口）

贯穿行为与表达、但本质独立的"自发"引擎——把"被动应答"升级为"会主动找你"。落地 `core/proactivity.py`：

- **`ProactiveDecider`**：低频、克制决策器，综合信号判断"该不该主动打扰 + 优先说什么"
- **信号源**全部复用已有能力（不新增采集/不额外调 LLM）：未兑现承诺 / 鲜活经历 / 情绪走向 / 感知变化 / 断联天数
- **纪律**：`PROACTIVITY_MIN_INTERVAL_MIN`（默认 30 分钟）、`PROACTIVITY_DAILY_CAP`（每日 8 次）、"有料才开口"（素材不够好或读不到 → `should_act=False`）
- **优先级**：`promise > episode > mood > sensing_change > reconnect`

亮点：这是"AI 朋友/伴侣"最强的拟人特征——不是等你说话，而是主动惦记你。边界：时机、节流、优先级全确定性 harness；"开口的措辞"交给主模型。当前 `ProactiveDecider` 是独立决策器，尚未接常驻调度线程（见 `proactivity.py` 说明）。

### 横切支撑层

不属于拟人本体，但支撑所有层：

- **`core/config.py`**：每个能力和开关（感知/感知工具/记忆/情感/视觉/主动性/MCP 各自可开关，后台 `EDITABLE_KEYS` 可配）
- **`core/adapters.py` / `llm_manager.py` / `initializer.py`**：Ollama / OpenAI 兼容适配（含视觉输入）、provider 切换、依赖装配
- **`core/state.py`**：LangGraph State 定义
- **`api/websocket.py` / `websocket_manager.py`**：把台词、主动消息、behavior 事件推给前台/宠物壳（拟人闭环的"最后一公里"）
- **`desktop_pet_qt.py` / `static/live2d/*`**：Live2D 客户端壳，消费表达层产出的 `behavior` 做表情/口型/动作（表达层的消费端）

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

让宠物感知并参与星露谷游戏（读状态 / 自主游戏 / 记忆反写与回忆 / 多 agent 小队）。
**默认关闭**（`STARDEW_MCP_ENABLED=false`），不影响不玩星露谷的用户；想启用只需在后台或 `.env` 打开开关。

本项目**不自己实现 MCP 服务端**，而是随仓库自带一套 Node 桥接
**StardewMCPBridge**（`stardew/StardewValley-MCP/`）：mcp-server（Node MCP 服务器，
共 **25 个工具**：13 全局 + 12 玩家直控）通过文件与 SMAPI Mod（C#）通信，
Mod 在游戏里生成 AI 同伴（Player 2/3）并读写 `bridge_data.json`/`actions/`。

- **实现形态**：mcp-server（Node.js）◄文件► SMAPI Mod（C#）◄► Stardew Valley，
  同伴=可见 NPC + 不可见 shadow Farmer 驱动游戏机制。
- **可插拔插件 + 后台启停**：每个 MCP（含星露谷）声明在 `config/mcp.json`
  （`{name,command,args,env,enabled}`）。`core/mcp_registry.py`（`McpRegistry`）读取声明、
  持有 `McpManager`，启停时**动态注册/注销** `mcp_<server>_<tool>` 工具到
  `ALL_TOOLS`/`TOOL_REGISTRY`，并让 Agent 缓存失效（新会话拾取最新工具集）。
  **无需重启后端**。后台「📦 MCP 管理」页可对每个服务启停/测试/开关。
- **自主游戏**：`stardew/autonomy.py` 提供「读状态 → 决策 → 行动 → 沉淀记忆」闭环，
  AI 伙伴可主动进场/移动/互动/聊天；MCP 注册后自动多一个高阶 `stardew_join` 工具
  （spawn + 设模式），用户邀请一起玩时 LLM 一调即可进场。
- **记忆增强**：`stardew/game_memory.py` 支持去重、场景/里程碑分级沉淀、最新优先/多关键词召回。
- 未开启/未启动/未装游戏时**静默降级**，不影响主进程（符合"不伪造、失败降级"原则）。

**启用步骤（简明版）**：

1. **（可选）构建 mcp-server**：本仓库已带 `stardew/StardewValley-MCP/mcp-server/build/index.js`；
   改动源码需 `cd mcp-server && npm install && npm run build`。
2. **构建并部署 SMAPI Mod（进游戏才需）**：`cd smapi-mod && dotnet build`，把 assets 放到
   游戏 `Mods/StardewMCPBridge/`。Mod 在游戏内写 `bridge_data.json`、消费 `actions/`。
3. **改配置**：`config/mcp.json` 的 stardew server 已指向
   `node {PROJECT_ROOT}/stardew/.../mcp-server/build/index.js`；真实游玩时把
   `STARDEW_BRIDGE_PATH`/`STARDEW_ACTION_DIR` 指向游戏 `Mods/StardewMCPBridge/` 下的实际路径。
4. **打开开关并启动**：`.env` 写 `STARDEW_MCP_ENABLED=true`，再 `python web_app.py`；
   或在后台「📦 MCP 管理」页对 `stardew` 服务「启动」（无需重启后端）。

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
├── core/
│   ├── mcp_client.py          # MCP 客户端：每个 server 一个子进程 + 启停/状态
│   └── mcp_registry.py        # MCP 插件注册中心：动态注册/注销工具 + Agent 缓存失效
├── stardew/                   # 星露谷 MCP 可选扩展（含独立 README）：记忆/自主游戏/轮询
└── scripts/                   # 角色 prompt 生成器
```

---

## 🧪 测试

```bash
python -m pytest test -q --ignore=backup
```

覆盖：记忆 / 感知 / 情感 / 工具执行 / 任务循环 / WS / 星露谷 / MCP 插件化 / 自主游戏 / 视觉等。

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
| `STARDEW_AUTONOMY_ENABLED` | 星露谷自主游玩心跳（需 MCP 也开） | `false` |
| `STARDEW_AUTONOMY_INTERVAL` | 星露谷自主游玩心跳间隔（秒） | `30` |
| `STARDEW_AUTONOMY_LLM_ENABLED` | 星露谷自主游玩心跳·LLM决策版（需装配 LLM 适配器，失败回退规则） | `false` |
| `LIVE2D_ENABLED` / `LIVE2D_MODEL_ROOT` / `LIVE2D_RENDERER` / `LIVE2D_DEFAULT_MODEL` | Live2D 开关 / 模型根目录 / 渲染器 / 默认模型 | `true` / `live2d` / `l2dwidget` / 空 |
| `LIVE2D_BODY_MODE` | Live2D 情绪身体表达模式（`C` 内核映射 / `B` LLM 主动指挥） | `C` |

> 星露谷 MCP 使用的开源仓库地址：[luy-0/StardewValley-MCP](https://github.com/luy-0/StardewValley-MCP)（Apache-2.0，详见「可选扩展：星露谷 MCP」一节）。

> 🔊 **TTS 语音合成** 由后台独立「🔊 TTS 语音」页统一管理（开关 + 地址/端口 + 参照音频 + 语言 + 格式 + 语速 + 测试/保存），配置项（`TTS_ENABLED`、`TTS_HOST`、`TTS_PORT`、`TTS_REF_AUDIO_PATH`、`TTS_PROMPT_TEXT`、`TTS_TEXT_LANG`、`TTS_PROMPT_LANG`、`TTS_MEDIA_TYPE`、`TTS_SPEED_FACTOR`）因此**不再出现在「⚙️ 配置管理」页**。默认关闭；改动即时生效（写入 `.env` 并同步 `os.environ`）。详见 `.env.example` 的 TTS 区块注释。

---

## 📚 文档

- `docs/dual-window-contract.md` — 内核 ↔ 壳的接口契约与分工边界（窗口 A/B）
- `stardew/README.md` — 星露谷 MCP 扩展完整说明
- `.env.example` — 全部可配置环境变量及说明

---
*MemBrain (Refactor) — v2.0.0*

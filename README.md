# 🧠 MemBrain (Refactor)

> 一个会记住你的、基于 LangGraph 的 AI Agent。由《BanG Dream!》的户山香澄担任默认角色。

这是对旧版 MemBrain 的**全新重构**。相比"缝合版"，重构版坚持六条设计原则，代码更清晰、可运行、可扩展。

---

## 🆕 近期更新

> 记录从上次 git 提交 `a130b1a`（感知层+日程提醒+助手工具+关系养成）以来的改动。

### Live2D 桌面宠物（Qt 版）气泡与显示优化

对 `desktop_pet_qt.py` 的透明悬浮 Live2D 宠物窗做了系列视觉与排版优化：

- **气泡定位到角色头顶上方**：宠物窗内角色整体下移（`#live2d-widget`/canvas `top:18%`、`height:82vh`），并在头顶留出空间；聊天气泡底端固定在角色头顶上方（`bottom:72%`），不再遮挡角色头/脸/身体。
- **长文本气泡向上生长、不滚动不裁切**：去掉气泡文本的 `max-height` + `overflow` 滚动盒，让气泡随内容自然向上涨开（`max-height:none`、`overflow:visible`）。
- **气泡内去掉多余空行**：`cleanBubbleText` 折叠连续换行、去行尾空格，气泡内不再出现大段空白。
- **长文本字号自适应**：`fitBubbleText` 在头顶净空间（窗口高约 26%）放不下时自动从基准字号逐步缩小，让全部文字塞进头顶空间（不滚动、不裁切）。
- **整体缩至 65%**：宠物窗默认尺寸 `448×747 → 291×486`（aspect≈0.60 不变），气泡及气泡内字体同步缩小（14px→9px）；`BOTTOM_OFF=0` 保持，脚底仍紧贴任务栏上沿。
- **气泡内不展示括号（动作/旁白）内容并合并为单行**：`cleanBubbleText` 去掉全角/半角括号内的动作或内心描写（可嵌套），并把所有换行合并为单个空格，交由气泡框架默认换行——应用端（聊天窗）回复仍保留完整括号内容，仅气泡为节省空间做了精简。

### Live2D 桌面宠物（角色模型配置 + 自动切换）

- **每角色可配置 Live2D 模型路径**：后台「联系人管理」每行新增「Live2D 模型」列。角色配置数据（`config/roles.json` 的 `RoleConfig`）新增 `live2d_model` 字段；`/api/contacts`、`/admin/roles` 均返回该字段，宠物页据此加载对应模型。
- **按角色过滤可选模型**：`/admin/live2d/models?role_id=` 只返回属于该角色的模型——优先取该角色已配置 `live2d_model` 的顶层目录（角色目录名）过滤，未配置则用 role_id 小写匹配模型目录名小写（二者都是角色罗马音，如 `kasumi`↔`Toyama Kasumi`）。后台「浏览…」弹框只显示本角色模型，避免混淆。
- **改模型自动生效（代替手动重启宠物窗）**：`live2d-page.js` 每 3 秒轮询 `/api/contacts`，检测到当前角色 `live2d_model` 变化后**整页刷新**自动加载新模型。⚠️ 必须整页刷新而非页内 `loadModel`：Cubism2 运行时在同一页面二次 `init` 会污染旧 WebGL 上下文（`object does not belong to this context`），画面会崩成三原色。
- **默认宠物用户山香澄**（`roles.json` 中 `default:true` 且已配置其模型 `Toyama Kasumi/001_live_r_2023`）。

### 星露谷 MCP 扩展（可选，默认关闭）

新增可选的「星露谷物语 MCP」扩展，让宠物感知并参与星露谷游戏世界（读游戏状态/进游戏当同伴/记忆反写/多 agent 小队）。**默认关闭**（`STARDEW_MCP_ENABLED=false`），不影响不玩星露谷的用户；想启用只需在后台或 `.env` 打开开关。

- **引用的 git 仓库**：[amarisaster/StardewValley-MCP](https://github.com/amarisaster/StardewValley-MCP)（MIT 协议，共 25 个 `mcp_stardew_*` 工具）
- 本地已含该仓库源码副本：`stardew/StardewValley-MCP/`（含 `mcp-server/` Node.js MCP server 与 `smapi-mod/` SMAPI 模组）
- 完整说明见 `stardew/README.md`（架构 / 启用步骤 / 依赖环境）

### 其他

- 移除遗留的调试临时文件（`_m1.png` / `_m2.png` / `_m3.png` / `_m1.log.out`）。

---

## ✨ 核心能力

| 功能 | 说明 |
|------|------|
| 💬 私聊 | 多角色切换，按 `(user_id, role_id)` 隔离记忆 |
| 👥 群聊 | 多角色同一房间，**接力对话**（角色按序发言并互相看到最新聊天，可配轮数） |
| 🧠 五层记忆 | L1 内存 / L2 短期 / L3 信息池 / L4 事实 / L5 角色 |
| 🔍 自治路由 | 无路由层/改写层，LLM 自主决定是否调用工具 |
| 🛠️ 工具调用 | `search_web`（意图路由：Open-Meteo 天气/维基百科/百度/DuckDuckGo）、`control_pc`（打开应用/浏览器/文件/执行命令）、提醒/时间/文件等共 9 个工具 |
| 💗 情感 / 好感度 | 模式 B 两阶段，6 维好感度跨会话持久化，好感度驱动**关系阶段**（陌生→熟悉→亲密→挚友） |
| 🧬 关系记忆内核 | 持续存在的**自我模型** / **共同经历账本** / **情绪随时间衰减** / **周期反思**（异步沉淀对用户的理解与角色的内在状态，注入 system prompt） |
| 🕒 感知层 | 时序/系统环境/位置情境/作息习惯/情绪趋势/忙碌在场/关系投入/作息异常 |
| ⏰ 日程提醒 | ReminderStore + 调度线程，到点调用 Agent 主动开口并 WS 推送（离线待上线补推） |
| 🖥️ 后台管理 | 联系人/记忆/情感/统计/配置 一体管理 |

---

## 🧠 五层记忆架构

| 层级 | 名称 | 存储 | 容量 | 用途 |
|------|------|------|------|------|
| **L1** | 内存上下文 | 内存（按 user_id+role_id 隔离） | 50 轮，超限压缩 | 当前会话历史、指代消解 |
| **L2** | 短期记忆 | ChromaDB `short_term` | 50 轮 FIFO | 跨会话对话原文召回 |
| **L3** | 主动信息池 | ChromaDB `l3_info` | 上限可配 | ✅ **已实现**：周期采集外部实时信息（调用 `fetch_bilibili_popular` 抓热榜，与 `search_web` 解耦），去重入库；`L3Pusher` 周期扫描未推送条目，调用 Agent **主动发起对话**并推送到私聊 WS（默认启用） |
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

**关系养成（好感度 → 行为差异）**：好感度（尤其熟悉 / 依恋 / 信任）推导出**关系阶段**（陌生 → 熟悉 → 亲密 → 挚友），并注入 system prompt 驱动角色行为——称呼风格、距离感、开放度随阶段变化，但**绝不改动人设内核**（防 OOC），只在相处中更亲近用户。

> 群聊接力：角色之间对话属于"交流"而非"用户→角色"情感信号，故群聊（尤其接力轮）会跳过情感/好感度更新（`persist_emotion=False`），避免误判私聊维度。

---

## 🕒 感知层（持续观察，而非被动等待）

基于时间戳与系统状态，让角色"意识到当下时空与用户状态"，每轮注入 system prompt：

| 感知 | 说明 |
|------|------|
| 时序 | 当前时间 / 星期 / 时段（早午晚深夜）/ 是否周末 |
| 系统环境 | 操作系统 / 系统运行时长 / 前台活跃应用（尽力读取，读不到不伪造） |
| 位置情境 | 常驻城市（`PERCEPTION_CITY`）+ 从时段推导情境（工作/午休/自由/休息） |
| 作息习惯 | 用户活跃时段聚合，得出"通常上午/下午/晚上活跃" |
| 情绪趋势 | 历次情绪/好感度样本时间序列，算"心情变好/变差/平稳" |
| 忙碌/在场 | 从距上次活跃推断"此刻是否在线/是否可能不在" |
| 关系投入 | 断联天数 / 连续活跃天数，支持"想念/关心" |
| 作息异常 | 当前处于用户通常安静时段 → 提示留意熬夜 |

> 数据存 `perception.json`，与 ChromaDB 解耦；由 `PERCEPTION_ENABLED`（默认开）控制。

---

## ⏰ 日程 / 提醒引擎

- `ReminderStore`：JSON 持久化（`reminders.json`），支持一次性 / 每日 / 每小时 / 每周（可多选星期）。
- `ReminderScheduler`：后台轮询线程，到点调用该角色 `proactive_message` 生成角色口吻提醒并经 WS 推送 `{type:"reminder"}`；**离线用户提醒保留、下次上线补推**（不丢、不重复轰炸）。
- 可通过 HTTP `/api/reminders` 或工具 `remind_me` 创建。

---

## 🛠️ 助手核心工具

在 `search_web` / `control_pc` 之外，新增 `core/assistant_tools.py`：

| 工具 | 说明 |
|------|------|
| `remind_me` / `list_reminders` / `cancel_reminder` | 让模型主动/安全地设提醒（复用 ReminderStore） |
| `get_current_time` | 精确时间 / 星期 / 时段 |
| `read_file` / `write_file` / `list_files` | 本地文件读写（**严格沙箱**：仅限 `assistant_workspace/` 与 `uploads/`，越权拒绝） |

**搜索的多源意图路由**：`search_web` 内部按关键词把查询路由到 **Open-Meteo（天气）→ 维基百科（概念）→ 百度（配置 Key 时）→ DuckDuckGo（通用）**，命中即短路、失败回退、全失败诚实告知（**不再降级成无关热榜**）。`fetch_bilibili_popular` 独立供 L3 采集使用（与 `search_web` 解耦）。

---

## 🚀 快速开始

### 环境要求
- Python 3.11+
- Ollama（已拉取主模型 + 工具模型）

> ⚠️ **关于嵌入模型**：项目约定嵌入模型放在 `models/all-MiniLM-L6-v2/`（`sentence-transformers` 格式）。如果该目录存在则**离线直接加载**；如果不存在，代码会回退到联网下载同名模型，或最终降级为 ChromaDB 默认嵌入。当前默认 `EMBEDDING_MODE=ollama`（nomic-embed-text，768 维），无需本地模型。

> **关于重排序**：默认使用本地 ONNX 的 **BGE reranker（`BAAI/bge-reranker-v2-m3`）**，对中文/多语言召回的精排效果显著优于旧的 ms-marco（旧模型词表基本不含中文，中文会退化成 `[UNK]`）。模型放在 `models/bge-reranker-v2-m3/`（含 `model.onnx` + `tokenizer.json`）。若未启用或加载失败，会自动回退到旧 ms-marco 或保持向量初步排序。

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

### 🖥️ 桌面宠物（M1 壳）

在浏览器版之上，可通过 pywebview 以"桌面宠物"形态随用：

```bash
pip install pywebview pystray
python desktop_pet.py
```

- 自动拉起后端 `web_app.py`（未运行时），不弹浏览器，直接弹出**无边框置顶窗口**加载聊天页（复用 /ws/chat 全部能力）。
- 系统托盘：显示 / 隐藏 / 打开聊天 / 打开后台管理 / 退出（退出时一并关闭由它拉起的后端）。
- 调试可单独启后端：`python desktop_pet.py --backend-only`。
- 仅启后端、不自动弹浏览器：`MEMBRAIN_NO_BROWSER=1 python web_app.py`。

### 🖥️ 桌面宠物（Qt 版，透明 Live2D 悬浮窗）

在 pywebview 版之上，新增了基于 **PySide6 + QWebEngineView** 的 Qt 版（`desktop_pet_qt.py`），实现真正的**透明悬浮桌面宠物**：

- **透明置顶**：`WA_TranslucentBackground` + 无边框 + 置顶 + 不接受焦点，只有角色立绘浮在桌面上（比 pywebview/WebView2 的透明 hack 更稳定，不会出白底）。
- **双窗口架构**：透明宠物窗（只显示 Live2D 模型，视觉/动作/表情/口型）+ 独立交互窗（聊天 / 后台管理），两者共享同一后端与 WS。
- **QWebChannel 桥 `petHost`**：`resizeWindow(w,h)`（滚轮缩放直接缩放宿主窗口、"窗口贴合模型"方案）、`setCursor`、`cropToChar`。
- **对话联动**：情感关键词 → 模型动作/表情（`EMOTION_RULES`），说话过程驱动口型（`PARAM_MOUTH_OPEN_Y`），回复挂在 WS 的 reply 里。
- **视线跟随**：自建 mousemove→rAF 链路直接写模型视线参数（上下已反），并 patch 运行时 `setDrag` 防止被每帧写回 0。
- **后台管理入口**：点击 `target=_blank` 链接在 WebEngine 新窗口被阻止，已注入 JS 改为当前窗口内导航（`inline_links`）。

**运行方式：**

```bash
# 需 Python 3.11（勿用裸 python 3.13）
py -3.11 web_app.py              # 先启后端（端口 8000，/health 可查存活）
py -3.11 desktop_pet_qt.py --pet  # 启宠物（透明悬浮单窗）
# 其他模式：--window 大窗聊天 / --twin 双窗口 / --backend-only 仅启后端
```

**定位与缩放：** 宠物窗默认 `291×486`，相对工作区右下角偏移 `PET_RIGHT_OFF=31`、`PET_BOTTOM_OFF=0`（脚底紧贴任务栏上沿）；模型 `scale` 由 `live2d-page.js` 的 `floatScale()=0.909` 控制；滚轮在宠物窗内缩放模型（等价缩放窗口，保持贴合）。

> ⚠️ **注意**：不能给窗口设 `--disable-gpu`（会毁掉 WebGL / Live2D 渲染）。调试窗口几何可用 `C:\Users\Administrator\AppData\Local\Temp\qsock\winrect.ps1`（Win32 枚举 pet 窗口）。

**角色 ↔ Live2D 模型：**
- 每个角色可在后台「联系人管理 → Live2D 模型」配置其模型路径（`live2d/` 下的相对目录），存于 `roles.json` 的 `live2d_model` 字段。
- 「浏览…」弹框按角色过滤：只显示该角色目录下的模型（角色目录名与 role_id 均为角色罗马音，可互相匹配）。
- 宠物页加载时优先用当前角色配置的模型；后台改模型后约 3 秒自动整页刷新生效（无需手动重启宠物窗）。
- 默认宠物角色为用户山香澄（kasumi）。

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
| GET | `/api/profile` | 获取用户资料（昵称） |
| POST | `/api/profile` | 设置用户昵称 |
| GET | `/api/reminders` | 列出该用户提醒 |
| POST | `/api/reminders` | 新增提醒 |
| DELETE | `/api/reminders/{id}` | 删除提醒 |
| POST | `/api/reminders/{id}/toggle` | 启用/停用提醒 |
| GET | `/health` | 健康检查 |

#### Live2D（桌面宠物页）

| 路径 | 说明 |
|------|------|
| GET | `/live2d?petmode=1` | Live2D 宠物页（透明小窗只包模型；`transparent=1` 透明背景） |
| GET | `/live2d-chat` | 双窗口模式独立对话窗页（sender） |
| GET | `/api/live2d/models` | 模型列表 + 默认模型 + 运行时本地/CDN 信息（目录扫描自动发现） |
| GET | `/api/live2d/config` | 前端初始化所需渲染环境信息 |
| GET | `/live2d-models/{path}/model.json` | 模型静态资源（`model.json`/`.moc`/纹理/动作，按模型目录挂载） |

### WebSocket
| 路径 | 说明 |
|------|------|
| `/ws/chat?user_id=&role_id=` | 私聊 |
| `/ws/room/{room_id}?role_id=` | 群聊 |

### 后台 API（`/admin/*`）
联系人 CRUD、Prompt 读写、头像上传、记忆查看、情感/好感度、统计、配置修改。

**Live2D 模型配置：**
| 路径 | 说明 |
|------|------|
| GET | `/admin/live2d/models?role_id=` | 列出可选 Live2D 模型，可按角色过滤（只返回该角色目录下的模型） |
| POST | `/admin/roles/{role_id}/live2d` | 设置某角色的 `live2d_model` 路径（body: `{live2d_model}`） |

---

## 📁 项目结构

```
agent-web-refactor/
├── web_app.py                 # 入口
├── desktop_pet.py             # 桌面宠物（pywebview 版）
├── desktop_pet_qt.py          # 桌面宠物（Qt 版，PySide6 透明 Live2D 悬浮窗）
├── config/roles.json          # 角色配置
├── role_prompts/              # 角色 Prompt 文件
├── core/
│   ├── config.py              # 配置管理（.env 读写）
│   ├── initializer.py         # 应用依赖组装（单例）
│   ├── logger.py              # 日志
│   ├── state.py               # LangGraph 状态
│   ├── adapters.py            # Ollama / OpenAI 适配器
│   ├── tools.py               # 搜索 + PC 控制 + 工具注册
│   ├── assistant_tools.py     # 助手核心工具（提醒/时间/文件，沙箱）
│   ├── reminder.py            # 日程/提醒引擎（存储 + 调度）
│   ├── perception.py          # 感知层（时序/系统/情境/作息/情绪趋势）
│   ├── user_profile.py        # 用户资料（昵称）
│   ├── memory/                # 五层记忆（vector_store + memory_manager）
│   ├── emotion/               # 情感 + 好感度（emotion / affection / emotion_store）
│   ├── relation_memory.py     # 关系记忆内核（自我模型 / 共同经历 / 时间衰减 / 周期反思）
│   ├── role/                  # 角色管理（manager.py）
│   └── room/                  # 群聊房间（message_bus + room_manager）
├── agent/
│   ├── graph.py               # LangGraph 自治 Agent
│   └── handlers/              # （空占位）
├── api/
│   ├── routes.py              # HTTP
│   ├── websocket.py           # WS
│   ├── admin.py               # 后台
│   ├── live2d.py              # Live2D 模型/运行时环境接口
│   └── websocket_manager.py
├── templates/                 # chat.html + admin.html + live2d.html + live2d-chat.html
├── static/
│   ├── live2d/                # Live2D 宠物页（live2d-page.js / live2d.css / runtime/）
│   └── ...                    # 其他静态资源/头像
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

## 🎭 角色 Prompt 生成器（CSP 风格）

参考 [Character_Skill_Producer](https://github.com/qian-gugugaga/Character_Skill_Producer) 实现：
从多个网站检索角色资料 → LLM **行为蒸馏**（把"设定"提炼成"情境→行为"）→ 生成可加载的 role prompt。

### 数据源（MediaWiki API，自动并行抓取）

| 来源 | 说明 | 可信度 |
|---|---|---|
| 萌娘百科 | zh.moegirl.org.cn，二次元角色词条丰富 | 高 |
| 中文维基 | zh.wikipedia.org | 高 |
| Fandom | 作品 Wiki（按作品自动选域，中文名可能 missing） | 中 |

### 用法

```bash
# 生成并打印（需 Ollama 在线，用 TOOL_LLM_MODEL 蒸馏）
python scripts/generate_role.py "高松灯" --work "BanG Dream! It's MyGO!!!!!"

# 生成并写入角色 prompt（即可被系统加载）
python scripts/generate_role.py "户山香澄" --work "BanG Dream!" --out role_prompts/role_prompt_kasumi.txt

# 列出数据源
python scripts/generate_role.py --list-sources

# 调试：把抓到的来源保存为 json
python scripts/generate_role.py "御坂美琴" --save-source sources_dump.json
```

### 生成结构（可直接作为系统 role prompt）

角色扮演规则 → 身份卡 → 行为动态（默认/压力/矛盾/面对他人）→ 表达质感（句式/口癖/情绪泄露/经典台词）→ 社会认知 → 决策逻辑（动机/优先级/硬约束）→ 知识边界 → 行为示例 → 诚实边界 → 调研来源。

生成的 `role_prompts/role_prompt_{role_id}.txt` 会被 `RoleManager.load_prompt()` 直接使用（无需改代码）。

### 模块结构

```text
role_generator/
├── sources/
│   ├── base.py        # 统一 MediaWiki API 拉取
│   ├── moegirl.py     # 萌娘百科 adapter
│   ├── wikipedia.py   # 中文维基 adapter
│   ├── fandom.py      # Fandom adapter
│   └── __init__.py    # 多源并行编排 + 文本合并
├── distill.py         # LLM 行为蒸馏 + CSP prompt 渲染
└── __init__.py
```

---

## 🔑 环境变量

| 变量 | 说明 | 默认 |
|------|------|------|
| `HOST` / `PORT` | 服务地址 | `0.0.0.0` / `8000` |
| `OLLAMA_HOST` | Ollama 地址 | `http://localhost:11434` |
| `LLM_MODEL` | 主模型 | `qwen3.5:9b` |
| `TOOL_LLM_MODEL` | 工具模型 | `qwen2.5:7b` |
| `BAIDU_API_KEY` | 百度搜索 Key（可选；配置后作为通用搜索的优先源） | 空（未配置时用 DuckDuckGo 通用搜索，不再降级成无关热榜） |
| `BAIDU_API_SECRET` | ⚠️ 在 `.env`/`.env.example` 中存在但**代码当前未读取**（仅为预留） | 空 |
| `EMBEDDING_MODEL_DIR` | 本地嵌入模型路径 | `models/all-MiniLM-L6-v2` |
| `EMBEDDING_MODEL_NAME` | 嵌入模型联网名（本地路径不存在时回退下载用） | `all-MiniLM-L6-v2` |
| `MEMORY_CONTEXT_MAX_ROUNDS` | L1 轮数 | `50` |
| `MEMORY_SHORT_TERM_MAX_ROUNDS` | L2 轮数 | `50` |
| `MEMORY_IMPORTANCE_THRESHOLD` | 事实抽取阈值 | `0.6` |
| `MEMORY_FACT_DECAY_DAYS` | 事实衰减天数 | `90` |
| `RERANKER_BACKEND` | 重排器后端：`bge`（bge-reranker-v2-m3，中英通用）/ `minilm`（旧 ms-marco）/ 留空自动 | `bge` |
| `BGE_RERANKER_DIR` / `BGE_RERANKER_ONNX` | BGE 重排器目录与 ONNX 文件名 | `models/bge-reranker-v2-m3` / `model.onnx` |
| `CROSS_ENCODER_ONNX_PATH` | 旧 ms-marco ONNX 路径（兜底） | `models/ms-marco-MiniLM-L-6-v2/...` |
| `L3_ENABLED` | 启用 L3 主动信息池（采集+推送） | `true` |
| `L3_UPDATE_INTERVAL` | L3 采集周期（秒） | `7200` |
| `L3_PUSH_INTERVAL` | L3 推送/主动开口周期（秒） | `300` |
| `L3_KEYWORDS` | 采集关键词（逗号分隔） | `天气,今日热点,二次元话题` |
| `L3_MAX_ITEMS` | L3 池最大条目数 | `200` |
| `REMINDER_SCAN_INTERVAL` | 提醒调度扫描间隔（秒） | `15` |
| `REMINDER_FILE` | 提醒持久化文件 | `reminders.json` |
| `PERCEPTION_ENABLED` | 启用感知层 | `true` |
| `PERCEPTION_CITY` | 用户常驻城市（位置情境；可选） | 空 |
| `PERCEPTION_FILE` | 感知数据（作息/情绪趋势）文件 | `perception.json` |
| `MOOD_TREND_MAX_SAMPLES` | 情绪趋势保留样本数 | `200` |
| `ROUTINE_WINDOW_DAYS` | 作息活跃窗口天数 | `30` |
| `ASSISTANT_WORKSPACE_DIR` | 助手文件工具沙箱根目录 | `assistant_workspace` |
| `LIVE2D_ENABLED` | 启用 Live2D 桌面宠物页/接口 | `true` |
| `LIVE2D_MODEL_ROOT` | Live2D 模型根目录（扫描含 `model.json` 的目录即一个模型） | `live2d` |
| `LIVE2D_RENDERER` | Live2D 渲染器（当前支持 `l2dwidget`，Cubism2） | `l2dwidget` |
| `LIVE2D_DEFAULT_MODEL` | 默认选中模型（相对 `LIVE2D_MODEL_ROOT` 的目录路径，留空取扫描到第一个） | 空 |

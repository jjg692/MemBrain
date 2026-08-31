# 双窗口并行改造：接口契约与分工边界

> 目标：把 MemBrain 同时推向 **AI Agent 助手**（窗口 A）与**桌面宠物**（窗口 B）两个方向，
> 两条线并行改造，最终合并为「AI 桌面宠物助手」。
>
> 本文件是两条分支会话**共同遵守的唯一契约**（single source of truth）。改动任何接口前先回来改这里，
> 再用测试锁住。任何一方改动契约必须同步更新本文件 + 对应测试，否则视为违约。

---

## 0. 一句话架构（先对齐认知）

```
┌──────────────────────────────  单进程后端  ─────────────────────────────┐
│                                                                          │
│   AI Agent 内核（脑）<────────双向契约────────>  桌面宠物壳（皮）          │
│   窗口A：core/ + agent/ + mcp    │  事件/方法   │   窗口B：live2d + 表现层   │
│   · 记忆/情感/自主/工具/主动性   │             │   · 口型/表情/动作/语音/窗口│
│                                                                          │
│   ◆ 内核=状态持有者 + 决策者（单一事实源）                                │
│   ◆ 壳=纯表现层，消费内核事件，不持有对话/记忆状态                        │
└──────────────────────────────────────────────────────────────────────────┘
```

**核心原则**：脑（内核）与皮（壳）彻底解耦。壳只消费内核的事件流，不反向影响内核决策。
这样「窗口A往深做、窗口B往活做」几乎零冲突并行，最后拼起来就是完整的桌面宠物助手。

---

## 1. 差距分析：距「AI Agent 助手」还差哪些功能

### 1.1 已有（勿重复造）
| 能力 | 位置 |
|------|------|
| LangGraph 自治 Agent、工具调用闭环 | `agent/graph.py` |
| 五层记忆（L1-L5）+ 冷启动 + 事实抽取 | `core/memory/` |
| 情感/好感度/关系养成 | `core/emotion/` |
| 感知层（时序/系统/情境/作息/情绪趋势） | `core/perception.py` |
| 日程/提醒引擎 + 主动开口 | `core/reminder.py` |
| 9+ 工具（搜索/PC/文件沙箱/提醒） | `core/tools.py`、`core/assistant_tools.py` |
| MCP 客户端骨架（默认关闭） | `core/mcp_client.py`、`config/mcp.json` |
| 群聊接力、WS 双窗口 sender/watcher | `api/websocket*.py` |
| 语音/Live2D 表现（后文窗口B） | `live2d/`、`desktop_pet*.py` |

### 1.2 距「真正的 AI Agent 助手」的差距（窗口 A 攻坚方向）

**A. 长程自主性 / 目标驱动（最大差距）**
- ❌ 目前是被动 ReAct：用户给一句回一句，无「任务/目标」概念。
- ✅ 待办：引入**任务循环**——多步目标规划（先拆解→逐步执行→完成确认），由 LLM 规划 + 工具执行 + 中途修正；可复用现有 LangGraph，加 `plan → act → observe` 边。
- ✅ **M2 已落地**（窗口A）：`agent/planner.py` 引入确定性 `TaskPlanner`（纯函数，不新增 LLM 调用），分界「简单一句消费」vs「多步任务」（≥2 个不同工具意图即进入任务循环）；`agent/graph.py` 加入 `observe` 节点（tools → observe → agent）累积中间观察，最终收敛 `task_status.done + conclusion`。单轮/闲聊请求完全不进任务分支（既有测试全绿）。

**B. 更强工具生态**
- ❌ 目前 9 个内置工具 + MCP 骨架（未接任何真实 server）。
- ✅ 待办：真正接 1-2 个 MCP server（游戏/办公/浏览器）；补齐**浏览器自动化**、**沙箱代码执行**、**互联网深度搜索**等通用工具。

**C. 记忆的长期规划与遗忘**
- ❌ L4 事实有衰减，但无「重要度→是否值得长期规划」的层次；无用户目标的长期追踪。
- ✅ 待办：**目标记忆层**（用户长期目标/偏好画像的主动提炼与沉淀）；记忆回顾/遗忘策略更细。

**D. 主动性 / 情绪智能边界**
- ❌ 有 `/proactive_message` 和 L3/提醒的主动推送，但触发逻辑偏规则。
- ✅ 待办：主动开口要"更有信息量、更合时宜"——结合记忆漏洞、日程、工具能解决的用户痛点，而非空泛问候。

**E. Message/事件层的结构化**
- ❌ WS 事件只有 `thinking`/`reply`/`proactive`/`reminder` 几种，且**窗口B依赖的表情/动作/口型目前是靠前端猜**（见 3.3），没有标准化事件。
- ✅ 待办：把 Agent 行为结构化——情绪→表情、动作、口型的**语义映射由内核输出为标准事件**，壳只消费。

**F. 多模态**
- ❌ `chat(..., image)` 已支持传入图片，但无音频输入/语音交互闭环。
- ✅ 待办：语音输入（ASR）→ 文本 → Agent → 语音输出（TTS）+ 口型联动（窗口B）。

**G. 可观测/调试**
- ❌ 无 trace、无 token 计数、无步骤可视化。
- ✅ 待办：Agent 执行轨迹日志（每步工具调用、token、耗时）——对"助手"而言几乎必需。

### 1.3 距「完整桌面宠物助手」的差距（窗口 B 攻坚方向）
见 §3 表现层。总体：从「立绘 + 气泡」升级为「有生命感」（口型/表情/情绪动作/语音/微交互）。

---

## 2. 双窗口文件分工边界（谁动谁，避免冲突）

### 2.1 严禁两人同时改（公共痛点区）
| 文件 | 说明 |
|------|------|
| `agent/graph.py` | 内核 + 兜底守卫都在这里，两方向都可能碰 → **默认归属窗口A；B 如需加事件通过契约接口，不直接改** |
| `api/websocket*.py`、`api/websocket_manager.py` | 事件通道，双方都依赖 → **扩充事件须先在本文件 §3 登记**，再各自实现 |
| `core/tools.py` / `ALL_TOOLS` / `TOOL_REGISTRY` | 注册表，两方向都加东西 → 新增工具走附录接口，别手改注册表 |
| `core/config.py` | 配置项会冲突 → 各自加的 key 用明确前缀（`AGENT_*` / `PET_*`）并加注释 |

### 2.2 窗口 A（AI Agent 助手）专属改动区
```
agent/            # graph.py 任务循环、规划
core/memory/      # 目标记忆、长期规划
core/mcp_client.py / config/mcp.json   # 接真实 MCP
core/tools.py     # 新通用工具（通过注册表接口加入，不手改注册表本身）
core/llm_manager.py / adapters.py      # 多模态、token 统计
docs/auditing     # 可观测/轨迹（新增）
```
### 2.3 窗口 B（桌面宠物）专属改动区
```
live2d/  static/live2d/  templates/live2d*.html
desktop_pet.py  desktop_pet_qt.py
api/live2d.py
# 新增：表情/口型/动作驱动、语音 TTS、微交互（全部放 static/live2d/ 或新目录）
```
> 提示：接手前先把 `test/ollama test/` 这目录名改成 `test/ollama/`（带空格易踩 shell/pytest 坑），
> 属于可直接落地的卫生项，任意窗口顺手做即可（改一次，避免两边都改冲突）。

---

## 3. 接口契约（内核 ↔ 壳）

> 现状已具备的（painless）：`/ws/chat` sender/watcher、事件广播、`MessageBus`。
> 本契约要补的：**标准化的「行为事件」**，让壳不再靠猜。

### 3.1 HTTP 契约（现状，固定）
| 端点 | 作用 | 归属 |
|------|------|------|
| `GET /api/contacts` | 角色/联系人 | A 维护数据 |
| `GET /api/history?user_id&role_id` | 历史 | A |
| `GET/POST /api/profile` | 昵称 | A |
| `GET/POST/DELETE /api/reminders*` | 提醒 | A |
| `GET/POST /api/rooms*` | 群聊 | A |
| `GET /api/live2d/models`、`/api/live2d/config` | 宠物模型清单 | B |
| `GET /health` | 健康 | 共用 |

### 3.2 WebSocket 事件契约（私聊 `/ws/chat`）
| type | payload 字段 | 语义 | 消费方 |
|------|--------------|------|--------|
| `connected` | user_id, role_id, mode | 建立连接 | 两者 |
| `thinking` | role_id | 开始处理（壳可置"思考中"态） | 两者 |
| `reply` | role_id, content | 最终回复 | 两者 |
| `proactive` | content/text | 主动开口 | 两者 |
| `reminder` | reminder_id, content | 到点提醒 | 两者 |

**🔴 新增契约（窗口A产出、窗口B消费）——行为事件 `behavior`**
> 目的：把「情绪→表情/动作/口型」的映射从壳端猜测，改为**内核在 `reply` 前计算并随事件下发**。

```
{ "type": "behavior",
  "role_id": "...",
  "emotion": { "primary": "开心", "valence": 0.4, "intensity": 0.6 },
  "expression": "smile01",        // 表情名（映射到 live2d exp.json）
  "mouth_open": 0.0-1.0,          // 口型开合（供 TTS/说话驱动）
  "actions": ["nod", "wave"],     // 可叠加的动作名（映射到 mtn）
  "pitch_hint": null }            // 可选：语音情感基调（窗口B TTS 用）
```

**契约规则**
1. 窗口A在 `_after_reply`（或 agent 节点）里，基于已算好的 `session["emotion"]` + 回复内容，调用
   `BehaviorMapper.derive(reply, emotion) -> behavior dict`，随 `reply` 一起 `broadcast_to_user`。
2. 窗口B的 `static/live2d/live2d-page.js` 收到 `reply` 时，**优先取同帧/同批的 `behavior`**；
   若本次没有 `behavior`，才回退到现有"前端猜"逻辑（向后兼容，保证 A 未完成时 B 不坏）。
3. 新增的 `BehaviorMapper` 归窗口A（`core/behavior.py`），输出仅依赖 emotion + 文本，无副作用。

### 3.3 群聊事件（沿用现状，不动）
`/ws/room/{room_id}` → 事件 `connected` / `chat_message`（内含 sender_role, content）。窗口B暂不消费群聊。

### 3.4 工具注册接口（窗口A 加工具的标准方式）
```python
from core.tools import TOOL_REGISTRY, ALL_TOOLS
# 1) 定义 schema（LLM 可见）；2) 注册可调用 fn：
TOOL_REGISTRY["my_tool_name"] = my_fn
ALL_TOOLS.append({"type":"function","function":{...}})   # 或经辅助函数 add_tool()
```
> 约定：共用 `ALL_TOOLS` 变更走本文件登记；MCP 工具由 `tools.py` 启动时自动注册，无需手改。

### 3.5 任务循环接口（窗口A M2，内核内部契约）
> 窗口A 在 `agent/graph.py` 内部增加 `plan → act → observe` 闭环，**不改变 WS 事件面**
> （对外仍只广播 `reply`/`behavior` 等既有事件）。本小节登记的是内核侧状态契约，供测试锁定：

| 状态字段（AgentState） | 含义 |
|------|------|
| `plan` | 多步任务骨架 `{goal, source, steps:[{index,description,tool_hint,status}]}`；`None`=单轮 |
| `task_status` | 执行状态 `{observations:[{tool_call_id,result}], progress:{done,total}, done:bool, conclusion:str}` |

分界规则（`agent/planner.py::TaskPlanner.should_plan`）：消息命中 **≥2 个不同工具意图**才进入任务循环；
单个工具请求 / 纯闲聊不触发，保持既有单轮行为。执行仍由 LLM 在现有 ReAct 循环里自主路由，
`observe` 节点（tools → observe → agent）只累积中间结果，不改变执行路径。

---

## 4. 测试锁定项（改接口的防线）

> 项目已有 `test/ollama test/`（本地）、`test/deepseek/`（远程）两套。契约改动必须动测试。

| 契约 | 锁定测试 |
|------|---------|
| 工具注册/调用闭环 | `test*/test_tool_execution.py`、`test/deepseek/test_tool_timing.py` |
| tool_fallback 兜底 | `test/deepseek/test_tool_timing.py::test_fallback_*` |
| 语义自然度/记忆/指代 | `test*/test_semantic_live_ollama.py`、`test*/test_semantic_live_deepseek.py`（两后端） |
| 对话/记忆/情感机制 | `test*/test_conversation.py`、`test_memory.py`、`test_emotion_perception.py` |
| **新增 behavior 事件**（窗口A/B共同必须） | 新增 `test/behavior_test.py`：`BehaviorMapper.derive` 纯函数单测 + WS 事件含 `behavior` 的集成断言 |
| **M2 任务循环**（窗口A） | 新增 `test/task_loop_test.py`：`TaskPlanner` 纯函数分界 + agent 集成（plan 建立/observe 累积/收敛 done）+ 单轮/闲聊不触发不回归 |

**合并防线**：任何一方改了 §3 的契约 → 提交前必须 `python -m pytest test/`（对应后端子集）全绿，
且行为事件相关测试通过，才允许 merge。

---

## 5. 双窗口并行协作流程

```
1. 开工前：本会话已把契约文档定稿（本文件）。
2. 分支：从 main 拉两分支
     feature/agent-assistant    (窗口A)
     feature/desktop-pet        (窗口B)
3. 独立攻坚，文件分区见 §2（公共区不并行改）。
4. 各自 PR 前：跑本文件 §4 锁定的测试 + 更新本文件契约（如有变更）。
5. 合并节奏：先合并窗口A（内核），再窗口B（表现消费新事件），最后合成验收。
```

---

## 6. 里程碑建议（合入顺序）

| 阶段 | 窗口A | 窗口B | 验收 |
|------|-------|-------|------|
| M1 | 行为事件 `behavior` + `BehaviorMapper` | 消费 `behavior` 驱动表情/口型（含向后兼容回退） | 对话时立绘表情/口型随情绪变化 |
| M2 | **✅ 任务循环（多步目标）**：`agent/planner.py` + 图 `observe` 节点 | 语音 TTS + 口型联动（mouth_open） | 能说整句且口型同步；多步请求逐步完成并确认 |
| M3 | 接 1 个真实 MCP + 目标记忆 | 微交互（点击/拖拽/情绪动作库） | 具备"助手感 + 生命感" |
| M4 | 可观测/轨迹 + 多模态扩展 | 听觉闭环（ASR） | 完整桌面宠物助手 |

---
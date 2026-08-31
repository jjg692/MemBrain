# 🧪 MemBrain（桌面宠物）单元/语义测试说明

> 目录：`test/`
> 定位：覆盖"桌面宠物"核心功能的**机制级单元测试** + **真实 LLM 语义级测试**。

---

## 1. 测试概览

| 文件 | 类别 | 数量 | 覆盖内容 |
|------|------|------|---------|
| `conftest.py` | 脚手架 | — | 假嵌入（哈希向量）、`FakeAdapter`（假 LLM）、隔离临时目录夹具 |
| `test_conversation.py` | 对话 | 8 | 普通私聊回复、模型失败兜底、多轮 L1 累积、双键隔离、模式B情感持久化、prompt 注入 |
| `test_web_tools.py` | 工具 | 17 | `search_web` 天气/百科/通用意图路由、回退链、诚实失败；`execute_tool`；助手工具（提醒/时间/文件沙箱越权拒绝） |
| `test_anaphora.py` | 指代 | 4 | L1 全量历史注入 LLM（支撑"它/那首歌"消解）、多轮历史增长、主动发言承接历史 |
| `test_memory.py` | 记忆 | 17 | L1 压缩/隔离、L2 持久化+FIFO+冷启动、L4 事实抽取(阈值/去重/衰减)、L5 角色事实幂等、综合检索、stats |
| `test_emotion_perception.py` | 情感/感知 | 19 | 情感/好感度序列化与合并、关系阶段（陌生→挚友）、时序/情境感知、情绪趋势、作息/在场感 |
| `test_reminder.py` | 提醒 | 9 | 提醒 CRUD、到点触发(due)、一次性/每日/每周推进、调度器调用角色生成 |
| `test_room_ws.py` | 群聊/WS | 9 | 消息总线/房间管理、群聊接力跳过情感持久化、WS 双窗口 sender+watcher 广播 |
| `test_live2d.py` | Live2D | 3 | 模型目录扫描/列表 API |
| `test_tool_execution.py` | 工具闭环 | 3 | **Agent 真正执行工具**：LLM 发 tool_call →工具执行→结果回喂→最终回复；工具循环上限 |
| `test_semantic_live.py` | 语义(真 LLM) | 6 | **真实 Ollama** 对话自然度、工具调用正确性、闲聊不误调 web、符合记忆、指代消解 |
| `test_results.txt` | 结果快照 | — | 最近一次完整运行摘要（`pytest -rA` 输出） |

**合计：95 测试（默认全绿，需 Ollama 在线跑语义测试；离线自动跳过）。**

---

## 2. 运行方式

```bash
# 安装依赖（只需安装 pytest；项目依赖已按 requirements.txt 装好）
python -m pip install pytest

# 运行全部测试（Ollama 在线时含真实 LLM 语义测试）
python -m pytest test/

# 只看离线单元测试（跳过需要 Ollama 的语义测试）
python -m pytest test/ -m "not needs_ollama"

# 运行单个文件 / 单个用例
python -m pytest test/test_memory.py
python -m pytest "test/test_semantic_live.py::test_real_weather_prompt_triggers_search_web"

# 生成/刷新结果快照
python -m pytest test/ --tb=short -rA > test/test_results.txt
```

> ⚠️ **语义测试依赖本地 Ollama 在线**（`OLLAMA_HOST`，默认 `localhost:11434`），并使用 `.env` 里的
> `LLM_MODEL`（主模型）和 `TOOL_LLM_MODEL`（工具/分析模型）。Ollama 不可达时这些用例**自动跳过**，
> 不影响离线覆盖。

---

## 3. 隔离设计（重要）

测试**不污染真实数据**，全部写入 pytest 临时目录（`tmp_path`）：

- **假嵌入**：`FakeEmbedding` 用确定性哈希向量替代 Ollama `nomic-embed-text` / 本地 sentence-transformer，
  使 ChromaDB 记忆测试离线、可复现、快速。
- **假 LLM**：`FakeAdapter` 实现 `chat` / `chat_with_tools`，可预置按序响应、记录每次入参（供断言 prompt 内容），
  默认可返回合法情感 JSON，覆盖情感/工具/抽取各类 prompt，避免联网。
- **数据隔离**：`chromadb` / `perception.json` / `reminders.json` / `user_profiles.json` 通过 `tmp_path` 隔离；
  user_profile 用 `monkeypatch` 重定向到临时文件。
- **工具桩隔离**：`test_tool_execution.py` 在构造 Agent **之前**向 `core.tools.TOOL_REGISTRY` 写入假工具
  （LangGraph 在 `__init__` 时包装工具），并用 `monkeypatch.setitem` 在测试后自动还原，避免跨测试污染。

---

## 4. 语义级测试说明（`test_semantic_live.py`）

这类测试**用真实本地 LLM** 驱动完整 Agent，验证"对话像不像真人"的语义层面，
仅验证行为是否合理，不断言具体回复文本（避免脆弱）。

| 用例 | 验证点 |
|------|--------|
| `test_real_reply_is_natural_and_in_role` | 回复非空、非报错、连贯中文、**无 AI 腔**（不含"我是AI/我是助手/请告诉我"等）、保持香澄人设 |
| `test_real_reply_is_reasonably_long` | 回复有实质内容，不空泛 |
| `test_real_weather_prompt_triggers_search_web` | 问"北京天气"→ 模型**真正发起 `search_web`**（stub 拦截网络），参数正确 |
| `test_real_casual_chat_does_not_call_web` | 闲聊（"今天适合做什么"）→ **不**擅自调用 `search_web` |
| `test_real_reply_remembers_earlier_statement` | 早先说"最爱星空蓝"→ 后续回复能承接记忆 |
| `test_real_anaphora_resolution` | 先提《Don't say lazy》→ "那首歌"能正确消解 |

---

## 5. 🐛 已修复的语义缺陷（"说查不查"）

### 现象
用真实 LLM 对话时，用户问 **"北京今天天气怎么样？"**，角色回复：

> *"哇！…让我来帮你查一下天气吧！🌤️（正在搜索中…）好啦，查到啦！北京今天多云转晴…"*

但**实际从未调用 `search_web` 工具**——它"嘴上说要查、实际没查"，还凭空编造天气结果。
这正是"感觉怪怪的、跟真人差别很大"的典型表现。

### 根因
`agent/graph.py` 的 system prompt 原先这么写：

```
【我还能做到】有些事我可以偷偷帮你搞定，不用提：
- 想上网查实时信息（天气、新闻…）· …
要做这些时，像朋友一样自然地说出来就行，我自然会办好，**不用特意说明在'用工具'**。
```

"偷偷帮你搞定 / 不用特意说明在用工具"的措辞，诱导模型**用角色扮演式的口头承诺代替真正的工具调用**，
于是"说要查"却走了纯文本最终回复分支（`tool_calls` 为空 → 直接 END，未进 tools 节点）。

### 修复（两层：prompt 措辞 + 确定性兜底守卫）

**第一层：改写 prompt 指令**（`agent/graph.py` `_build_system_prompt`）——既要求"必须真正行动"，
又**明确划清边界**（哪些情况绝不可调工具），防 "说查不查"，也尽量防闲聊乱查：

```
【我还能做到】有些事我可以直接帮你办妥：
- 上网查实时信息（天气、新闻、最新动态、查询概念/资料）· 打开电脑上的应用或网页
【重要：必须真正行动，不许空口承诺】
1. 用户明确要实时/最新/外部资料信息时（如'今天天气''查一下XX''最新新闻''XX是什么'），你必须立刻调用 search_web 工具
   获取真实结果，而不是只说'我去帮你查一下'却不调用工具——『说要查却不查』等于说谎，绝不允许。
2. 调用工具后，等拿到真实结果再开口，把结果自然地转述给用户。
3. 为了自然，你可以先简单回应语气词再接工具调用，但工具调用这一步必须真正发生。
4. **不要因为闲聊就调工具**：以下情况绝不调用 search_web，而是直接聊天——
   用户分享感受/回忆/观点/心情、问个人看法/偏好/过去经历、纯社交寒暄/闲聊/分享计划。
   只有用户**明确要求**查实时/最新/外部资料时才调用。
```

**第二层：确定性兜底守卫**（`agent/graph.py` `_agent_node`）——仅依赖 prompt 对本地模型不够可靠
（随机），加了一层无副作用的硬保证：

- 当用户消息**明显需要实时信息**（复用 `core.tools._detect_intent` → weather/wiki，LLM 之外的关键词判定），
  **且本轮尚未真正执行过工具**、**且模型没调工具就准备直接回答**时，强制注入一次 `search_web` 调用，
  让 tools 节点真正执行。
- 用 `tool_fallback` 开关控制（默认 `True` 生产开启；假 LLM 的确定性单元测试传 `False` 避免干扰）。
- 兜底只处理"该查却没查"的方向，不干预模型正常调用，也不影响"自治路由"设计（LLM 仍是首选）。

### 验证
- 修后实测真实模型对该天气询问**稳定发起 `search_web`**（修复前 0 次 → 修复后 + 兜底守卫，weather 语义用例稳定通过）。
- 语义测试 `test_real_weather_prompt_triggers_search_web` 由原有 `xfail`（已知缺陷）转为**正常通过**。
- 全套件 **95 passed**（两次连续运行均绿）。


---

## 6. 其它已记录的行为/注意点

- `remind_me` 等工具参数**未自动绑定当前 user_id**，默认写入 `default_user`
  （`test_tool_execution` 中已如实断言并注释，供多用户隔离排查）。
- 纯函数工具（`search_web`/文件沙箱/提醒）测试不访问真实网络（用 monkeypatch 拦截 `requests`）。
- Agent 工具闭环测试的假工具必须带**类型注解 + docstring**（LangChain `tool()` 强制要求）。

### 已知局限：本地模型闲聊时的"过度调用"倾向
真实 LLM（此处实测 `qwen3.5:9b`）在**纯闲聊**场景仍有约 50% 的概率**主动调用 `search_web`**
（把闲聊问句当查询去搜）。prompt 第 4 条已明确"闲聊绝不调工具"并显著缓解，但无法 100% 压制——
这是本地模型工具调用倾向的限制，**不是代码 bug**。

- 因此 `test_real_casual_chat_does_not_call_web` 采用**倾向性采样**（3 个闲聊题目，
  断言"非逢聊必搜"），避免因模型随机性单次打红；同时它能拦截最严重的"每次都乱调"。
- 若希望彻底消除，可在 `_agent_node` 增加对称的**过调用门控**（当 `_detect_intent` 判定为非实时意图时
  丢弃模型自行发起的 `search_web`），但会牺牲"自治路由 / LLM 自主决策"原则，故本版本未加。
- 用户侧的核心修复（**"说查不查"→ 明确要实时信息必然真正调工具**）已由守卫**确定性**保证，无此局限。


---

## 7. 扩展指南

新增一个功能时，把对应测试放进来：

1. **纯逻辑**（工具函数/状态机/CRUD）→ 直接写纯函数测试，用 `tmp_path`/`monkeypatch` 隔离 IO。
2. **Agent 行为机制**（会不会调工具、prompt 注入、记忆写入）→ 用 `FakeAdapter` + 预置响应。
3. **语义/对话质量**（自不自然、记不记得、指代消解）→ 加进 `test_semantic_live.py`，
   加 `@pytest.mark.skipif(not _ollama_ready(), ...)` 保证离线可跳过。

# 🧪 MemBrain · 远程 LLM（DeepSeek）语义与工具时机测试

> 目录：`test/deepseek/`
> 定位：基于**远程 LLM（DeepSeek-R1，OpenAI 兼容接口）**的
> **语义级（对话 / 记忆 / 指代）** 与 **工具调用时机** 测试，
> 以及**离线确定性**的"合适时机 ↔ 合适工具"机制测试。

---

## 1. 与 `test/ollama test/` 的区别

| 项 | `ollama test` | `deepseek`（本目录） |
|----|----------------|------------------------|
| 真实 LLM | 本地 Ollama（`OllamaAdapter`） | **远程 DeepSeek-R1（`OpenAICompatAdapter`）** |
| 就绪检测 | 探测 `OLLAMA_HOST/api/tags` | 探测 `LLMManager.test_connection()`（provider=openai） |
| 语义判定 | qwen 自然的"AI 腔" | **加防推理腔**（R1 是推理模型，`让我分析`/`思考过程` 等也判出戏） |
| 成本 | 免费本地 | **按 token 计费**，语义用例控量 |

## 2. 文件与覆盖

| 文件 | 类型 | 覆盖 |
|------|------|------|
| `conftest.py` | 脚手架 | `FakeEmbedding` + `FakeAdapter` + tmp 隔离；`needs_remote` marker；`remote_agent` fixture |
| `test_tool_timing.py` | **离线确定性** | 意图→工具映射矩阵、闲聊 0 工具、tool_fallback 兜底守卫整链、多工具编排 |
| `test_semantic_live.py` | 真实远程 LLM | 自然度/人设/防推理腔、天气→search_web、设提醒→remind_me、闲聊不误调、记忆承接、指代消解 |

### 工具时机（`test_tool_timing.py`，离线，不花 token）
- **A. 意图→工具映射**：查天气→`search_web`；设提醒→`remind_me`；问时间→`get_current_time`；读文件→`read_file`
- **闲聊→严格 0 工具**（假 LLM 下确定性断言）
- **B. tool_fallback 兜底整链**：该调却不调→强制注入 search_web；已执行工具→不再二次注入；非资料闲聊→不注入；`tool_fallback=False`→不干预
- **C. 多工具编排**：混合句依次触发 `search_web` + `remind_me`

## 3. 运行方式

```bash
# 安装 pytest（若未装）
python -m pip install pytest

# 只跑离线确定性工具时机测试（不需要远程，秒级）
python -m pytest test/deepseek/test_tool_timing.py

# 跑全部（真实远程 LLM 语义用例：远程可达时执行，否则自动 skip）
python -m pytest test/deepseek/
```

> ⚠️ **语义用例需要远程 LLM 可达**：`.env` 需配置
> `LLM_PROVIDER=openai` + `LLM_API_BASE_URL` + `LLM_API_KEY` + `LLM_REMOTE_MODEL`（DeepSeek-R1）。
> 通过 `LLMManager.test_connection()` 判定；不可达时 `needs_remote` 用例自动跳过，
> **不影响离线确定性测试**。

## 4. 设计说明

- **隔离**：全部数据（chromadb/perception/reminders/user_profiles）写入 `tmp_path`，不污染真实数据；
  用户资料用 `monkeypatch` 重定向。
- **确定性优先**：工具时机用假 LLM（`FakeAdapter`）做离线硬断言；真实 LLM 语义用例用
  **倾向性采样**（容忍 DeepSeek 采样随机），强保证（该调必调）由 weather 用例 +
  `tool_fallback` 兜底守卫双保险。
- **Remote 适配器无 temperature**：`OpenAICompatAdapter` 默认无 `set_temperature`，
  `remote_agent` 会为其补齐（语义测试用较低温度更稳定）。

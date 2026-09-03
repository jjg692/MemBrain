# 星露谷物语 MCP 扩展

让 MemBrain 桌面宠物接入 [星露谷物语](https://www.stardewvalley.net/)，
通过 MCP（Model Context Protocol）让宠物感知并参与游戏世界。

本项目是 **agent-web-refactor 的可选扩展**：
- 默认**关闭**，同一套代码拉下来没装游戏/没配环境也完全不受影响。
- 想启用时，在管理后台或 `.env` 打开一个开关即可。

---

## 1. 它做了什么

| 能力 | 说明 |
|------|------|
| **感知游戏** | 读取游戏状态（时间/天气/季节/金钱/背包/同伴），宠物能"知道"你在玩什么 |
| **进游戏当同伴** | 宠物作为 Player 2/3 生成，可以移动、钓鱼、种地、挖矿、战斗（需真实游戏） |
| **记忆反写**（方向一） | 把"今天和用户玩了什么"写进记忆，宠物以后能回忆 |
| **多 agent 小队**（方向二） | 多个角色同时进游戏分工（Kasumi 钓鱼 / Kokoro 挖矿），群聊协调 |

工具清单来自开源项目 [amarisaster/StardewValley-MCP](https://github.com/amarisaster/StardewValley-MCP)
（MIT 协议），共 25 个 `mcp_stardew_*` 工具。

---

## 2. 架构

```
MemBrain Agent ──stdio(MCP)──► stardew/mcp-server (Node.js)
                                   │
                                   │ JSON 文件
                                   ▼
                              SMAPI Mod (C#) ──► Stardew Valley 游戏
```

- `stardew/StardewValley-MCP/mcp-server/` —— Node.js MCP server（暴露 25 工具）
- `stardew/StardewValley-MCP/smapi-mod/` —— SMAPI 模组（进游戏才需要）

---

## 3. 启用步骤

### 3.1 先装 Node MCP server（必须）
```bash
cd stardew/StardewValley-MCP/mcp-server
npm install
npm run build      # 产出 build/index.js
```

### 3.2 打开开关
两种方式任选：
- **管理后台**：打开 `http://localhost:8000/admin/config`，把「星露谷 MCP 扩展」设为 `true`
- 或改 `.env`：
  ```
  STARDEW_MCP_ENABLED=true
  ```

### 3.3 重启后端
```bash
python web_app.py
```
启动时若日志出现 `MCP 注册 mcp_stardew_get_state ...`，说明 25 个工具已挂上，LLM 即可调用。

---

## 4. 关闭 / 其他人生成不崩

- **默认关闭**：他人 `git clone` 后没开开关，`mcp_stardew_*` 工具根本不会注册，零影响。
- **开了但 server 起不来**（比如没 `npm install`）：`core/tools.py` 会 try/except **静默降级**，不崩。
- 本扩展模块（`stardew/`）全部**可选导入**，任一失败不影响主进程。

---

## 5. 两种玩法深度

### 5.1 感知 + 对话（不需要真实游戏，可先跑通）
宠物能"回答星露谷问题"——依赖 `mcp_stardew_get_state` 等只读工具。
即便游戏没开，工具返回"连不上游戏"也是**链路已通**的信号。

### 5.2 真实进游戏当同伴（需要很多条件）
需要：星露谷 1.6+、SMAPI 4.0+、.NET SDK、真实游戏存档。
- 构建 `smapi-mod` 并部署到 `Mods/`
- 配置 `config/mcp.json` 里 `STARDEW_BRIDGE_PATH` / `STARDEW_ACTION_DIR` 指向真实路径
- 游戏内 `mcp_stardew_spawn` 生成 Kasumi 同伴

> 当前机器没有 .NET SDK 时，跳过 5.2，先做 5.1。

---

## 6. 记忆反写（方向一）代码位置

`stardew/game_memory.py` —— 把游戏事件转成记忆，复用现有 `MemoryManager`：
```python
from stardew.game_memory import GameMemoryBridge
gb = GameMemoryBridge(agent.memory)          # memory = MemoryManager
gb.record_event(user_id, "今天和用户一起在矿井挖到一颗钻石")
gb.remember(user_id, "矿井")                 # 查游戏回忆
```

### 6.1 自动记忆沉淀（运行时旁路观察者）

`stardew/runtime.py` 的 `GameStatePoller` 让"游戏经历自动进记忆"**真正在运行时生效**：
- 后台线程每 `STARDEW_POLL_INTERVAL`（秒，默认 60）只读一次 `mcp_*_get_state`（零副作用）。
- 检测到有意义的游戏状态变化（季节/日期/天气/地点/金钱等）时，自动用 `GameMemoryBridge`
  反写一条"星露谷发生了什么"到 L1/L4，宠物以后对话能回忆。
- 需要两个开关全开（管理后台「配置管理」或 `.env`）并**重启后端**：
  - `STARDEW_MCP_ENABLED=true`（MCP 链路）
  - `STARDEW_MEMORY_POLLER_ENABLED=true`（记忆自动沉淀；内存访问由 STARDEW_MCP_ENABLED 决定）
- 优雅降级：游戏未开 / bridge 缺失 / MCP 未启动时静默跳过，绝不打扰主流程。

### 6.2 后台管理页

管理后台侧边栏新增「🌾 星露谷」页，提供：
- 扩展开关 / 工具数 / 轮询状态 / 已沉淀事件统计
- `🔌 测试 MCP 链路`、`⚡ 立即读取游戏状态`、`🔄 刷新状态`
- 当前游戏状态 JSON + 最近游戏记忆回显

---

## 7. 目录结构

```
stardew/
├── __init__.py            # 可选导入，纯文档包
├── game_memory.py         # 方向一：游戏事件 → 记忆
├── runtime.py             # 运行时：状态轮询 → 自动沉淀记忆 + 后台状态
├── squad.py               # 方向二：多 agent 协调（预留）
└── StardewValley-MCP/     # 开源 MCP 仓库（node_modules/build 不入库）
```

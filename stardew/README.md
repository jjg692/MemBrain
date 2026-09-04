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
| **感知游戏** | 读取游戏状态（时间/天气/季节/金钱/背包），宠物能"知道"你在玩什么 |
| **操作游戏** | 玩家驱动模式：移动/面向/互动/使用工具/装备/消费/DIY （需 --allow-write 和真实游戏） |
| **记忆反写**（方向一） | 把"今天和用户玩了什么"写进记忆，宠物以后能回忆 |
| **多 agent 小队**（方向二） | 多个角色同时进游戏分工（Kasumi 采集 / Kokoro 消费），群聊协调 |

工具来源于开源项目 [luy-0/StardewValley-MCP](https://github.com/luy-0/StardewValley-MCP)
（Apache-2.0），实现为 **Python MCP 服务器**（官方 `mcp` SDK）+ **SMAPI Mod**（C#），
Mod 与服务器通过本地 TCP(24642) + protobuf + 共享密钥握手。
共 22 个工具：6 个只读（query_runtime/query_players/query_world/query_inventory/query_ui/inspect）
+ 16 个需 `--allow-write` 的操作工具（say/emote/face/navigate/interact/use_tool/equip/set_equipment_slot/...）。

---

## 2. 架构

```
MemBrain Agent ──stdio(MCP)──► stardew-valley-mcp (Python MCP 服务器)
                                 │
                                 │ TCP 24642 + protobuf + 共享密钥(HMAC)
                                 ▼
                            SMAPI Mod (C#) ──► Stardew Valley 游戏
```

- 配置声明：`config/mcp.json`（command=stardew-valley-mcp）
- 服务器实现：[luy-0/StardewValley-MCP](https://github.com/luy-0/StardewValley-MCP) 的 `mcp/` 目录
- Mod 实现：同仓库的 `mod/` 目录（SMAPI 模组，进游戏才需要）

---

## 3. 启用步骤

前置：Stardew Valley 1.6 + SMAPI 4.1.0+、Python 3.11+ + [`uv`](https://docs.astral.sh/uv/)。
建 Mod（选）：需 Git + 能建 `net6.0` 的 .NET SDK。

### 3.1 安装 MCP 服务器（必须）
下载 [luy-0 Release](https://github.com/luy-0/StardewValley-MCP/releases) 同版本 wheel 或从源码安装：
```bash
# 源码安装
git clone https://github.com/luy-0/StardewValley-MCP luy0
cd luy0
uv tool install ./mcp
stardew-valley-mcp doctor     # 检查包和协议，不连游戏
```
如桌面 MCP 客户端找不到命令，运行 `uv tool dir --bin` 查看完整本机路径，并在 `config/mcp.json` 的 command 填它。

### 3.2 安装 SMAPI Mod（进游戏才需）
从 Release 下载 `StardewValleyMCP-Mod-v<version>.zip`（用 `SHA256SUMS.txt` 校验），解压到游戏 `Mods/` 目录。
或从源码构建：
```bash
export STARDEW_VALLEY_GAME_PATH='C:/Program Files (x86)/Steam/steamapps/common/Stardew Valley'
./mod/scripts/build.sh --deploy
```
Mod 首次启动时会在 `Mods/StardewValleyMCP/config.json` 生成：默认监听 `127.0.0.1:24642`、随机生成 `SharedSecretBase64`。

### 3.3 配置客户端（必须）
把 Mod `config.json` 的连接信息填进 `config/mcp.json` 的 env：
```json
"env": {
  "STARDEW_VALLEY_MCP_HOST": "127.0.0.1",
  "STARDEW_VALLEY_MCP_PORT": "24642",
  "STARDEW_VALLEY_MCP_SHARED_SECRET": "<Mod config.json 中的 SharedSecretBase64>"
}
```
只读：`args: ["serve"]`；允许操作游戏：`args: ["serve", "--allow-write"]`。
只会暴露"公共 Manifest ∩ MCP 支持 ∩ Mod 公告 ∩ 权限策略"的交集。游戏停在标题界面时查询返回 `not_ready`。

### 3.4 打开开关 + 重启
```bash
# .env
STARDEW_MCP_ENABLED=true
# 重启后端
python web_app.py
```
启动时日志若出现 MCP 注册工具，说明 luy-0 工具已挂上，LLM 即可调用（要先加载存档才能看到合法"有存档"结构化 Snapshot）。

---

## 4. 关闭 / 其他人生成不崩

- **默认关闭**：他人 `git clone` 后没开开关，`mcp_*` 工具根本不会注册，零影响。
- **开了但 server 起不来**（比如没装 uv /没安装 wheel）：`core/tools.py` 会 try/except **静默降级**，不崩。
- 本扩展模块（`stardew/`）全部**可选导入**，任一失败不影响主进程。

---

## 5. 两种玩法深度

### 5.1 感知 + 对话（不需要真实游戏，可先跑通）
宠物能"回答星露谷问题"——依赖 `mcp_stardew_query_runtime` 等只读工具。
即便游戏没开，工具返回"连不上游戏"也是**链路已通**的信号。

### 5.2 真实进游戏操作（需要很多条件）
需要：星露谷 1.6+、SMAPI 4.1.0+、uv、.NET SDK（建 Mod）、真实游戏存档。
- 安装 Mod 到 `Mods/` 并开 --allow-write
- 将 Mod `config.json` 的 SharedSecretBase64 填入 `config/mcp.json`
- 通过 `mcp_stardew_equip` / `mcp_stardew_set_equipment_slot` 等操作游戏

> 当前本机没有 .NET SDK / uv 时，跳过 5.2，先做 5.1。

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
- 后台线程每 `STARDEW_POLL_INTERVAL`（秒，默认 60）只读一次状态工具（零副作用）。
- 检测到有意义的游戏状态变化时，自动用 `GameMemoryBridge` 反写一条记忆。
- 需要两个开关全开（管理后台或 `.env`）并**重启后端**：
  - `STARDEW_MCP_ENABLED=true`
  - `STARDEW_MEMORY_POLLER_ENABLED=true`
- 优雅降级：游戏未开 / bridge 缺失 / MCP 未启动时静默跳过，绝不打扰主流程。

### 6.2 后台管理页

管理后台侧边栏新增"🌾 星露谷"页，提供开关/工具数/轮询状态/消费统计等。

---

## 7. 目录结构

```
stardew/
├── __init__.py            # 可选导入，纯文档包
├── game_memory.py         # 方向一：游戏事件 → 记忆
├── runtime.py             # 运行时：状态轮询 → 自动沉淀记忆 + 后台状态
└── squad.py               # 方向二：多 agent 协调（预留）
```

> luy-0 仓库可以保留在项目外（如 `luy0/`），不必入库；MCP 服务器通过 uv tool 全局安装，不依赖项目内路径。

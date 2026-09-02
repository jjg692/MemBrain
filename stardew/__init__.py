"""
星露谷 MCP 扩展（agent-web-refactor 的可选扩展模块）
====================================================
功能：让桌面宠物接入星露谷物语，通过 MCP 协议感知/操作游戏世界。

分层：
- `core/config.py`        STARDEW_MCP_ENABLED 总开关（默认关闭，后台可配）
- `config/mcp.json`       星露谷 MCP server 声明（{PROJECT_ROOT} 可移植锚定）
- `core/mcp_client.py`    通用 MCP 客户端（本项目基石，已支持路径锚定）
- `core/tools.py`         注册 mcp_stardew_* 工具到 ALL_TOOLS/TOOL_REGISTRY
- `stardew/`（本包）       可选的游戏感知/记忆/多 agent 扩展（方向一/二）

约束：
- 本包所有模块**可选导入**：任何导入失败都不应影响主进程启动。
- 依赖的 Node MCP server 位于 stardew/StardewValley-MCP/mcp-server，
  他人拉项目需 npm install + npm run build（见 README）。
"""

# 不在此处 import 任何会失败的第三方包；保持纯文档包，避免影响主进程。
# 具体功能模块（game_memory / squad）由使用方按需 import，并各自做 try 包裹。

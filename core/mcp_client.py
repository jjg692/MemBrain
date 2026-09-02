"""
轻量 MCP（Model Context Protocol）客户端骨架

作用：让 MemBrain 通过 MCP 协议动态接入外部工具服务器（如各类游戏 MCP server）：
- 零新增依赖：用标准库 subprocess + json 通过 stdio 走 JSON-RPC 与 MCP server 通信
- 配置驱动：config/mcp.json 里声明多个 server（command + args + env）
- 自动发现：启动时对每个 server 调 tools/list，把工具清单转成 LLM 可用的 schema
- 动态注册：生成可调用函数，返回给 tools.py 的 ALL_TOOLS / TOOL_REGISTRY

远端能力（同一段 JSON-RPC）同样支持：initialize / tools/list / tools/call。
说明：如需 notify/roots/sampling 等纯可选扩展，本骨架暂不实现（够用即可扩展）。
"""
import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional

from core.config import PROJECT_ROOT

# MCP JSON-RPC 版本
_MCP_VERSION = "2024-11-05"
_PROTOCOL_VERSION = "0.1.0"


class McpError(Exception):
    pass


def _json_dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False)


class McpServer:
    """与单个 MCP server 的 stdio 连接 + 工具发现/调用"""

    def __init__(self, name: str, command: str, args: List[str] = None,
                 env: Optional[dict] = None, client_name: str = "membrain"):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(os.environ)
        if env:
            self.env.update(env)
        self.client_name = client_name
        self._proc: Optional[subprocess.Popen] = None
        self._id = 0
        self._lock = threading.Lock()
        self._tools: List[dict] = []   # 原始 MCP 工具定义
        self._schemas: List[dict] = [] # 转成的 LLM schema
        self._ready = False

    # ---------------- 生命周期 ----------------

    def start(self):
        """启动 server 子进程并完成 initialize 握手"""
        try:
            self._proc = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.env,
            )
        except Exception as e:
            raise McpError(f"MCP server '{self.name}' 启动失败: {e}")
        # 握手
        self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": self.client_name, "version": "0.1.0"},
        })
        # 通知 initialized（可选，部分 server 需要）
        self._notify("notifications/initialized", {})

    def stop(self):
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
        self._proc = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ---------------- JSON-RPC ----------------

    def _next_id(self) -> int:
        with self._lock:
            self._id += 1
            return self._id

    def _write(self, payload: dict):
        if not self._proc or not self._proc.stdin:
            raise McpError(f"MCP server '{self.name}' 无 stdin")
        line = _json_dumps(payload) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except Exception as e:
            raise McpError(f"写 MCP server '{self.name}' 失败: {e}")

    def _read(self) -> Optional[dict]:
        if not self._proc or not self._proc.stdout:
            raise McpError(f"MCP server '{self.name}' 无 stdout")
        line = self._proc.stdout.readline()
        if not line:
            return None
        try:
            return json.loads(line)
        except Exception:
            return None

    def _request(self, method: str, params: dict) -> dict:
        rid = self._next_id()
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        # 读响应，跳过 server 主动发来的 notification
        for _ in range(200):
            msg = self._read()
            if msg is None:
                raise McpError(f"MCP server '{self.name}' 无响应(method={method})")
            if msg.get("id") == rid:
                if "error" in msg:
                    err = msg["error"]
                    raise McpError(f"MCP error: {err.get('message', err)}")
                return msg.get("result", {}) or {}
        raise McpError(f"MCP server '{self.name}' 响应超时")

    def _notify(self, method: str, params: dict):
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---------------- 工具发现与调用 ----------------

    def list_tools(self) -> List[dict]:
        """读取 server 的工具清单（MCP 原始格式）"""
        result = self._request("tools/list", {})
        return result.get("tools", [])

    def build_schemas(self) -> List[dict]:
        """把 MCP 工具转成 Ollama/OpenAI 兼容的 function schema"""
        self._tools = self.list_tools()
        out = []
        for t in self._tools:
            # MCP inputSchema -> OpenAI function parameters
            params = (t.get("inputSchema") or {}).get("properties", {}) or {}
            required = (t.get("inputSchema") or {}).get("required", []) or []
            out.append({
                "type": "function",
                "function": {
                    "name": f"mcp_{self.name}_{t.get('name', '')}",
                    "description": t.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": params,
                        "required": required,
                    },
                },
            })
        self._schemas = out
        return out

    def call_tool(self, name: str, arguments: dict) -> str:
        """调用 MCP 工具并返回内容（拼接 content 块）"""
        result = self._request("tools/call", {
            "name": name,
            "arguments": arguments or {},
        })
        content = result.get("content", [])
        # MCP content 可能是 [{type:text,text}, ...] 或字符串
        parts = []
        if isinstance(content, list):
            for c in content:
                if isinstance(c, dict):
                    if c.get("type") == "text":
                        parts.append(c.get("text", ""))
                    elif c.get("type") == "image":
                        parts.append("[图片结果]")
                    else:
                        parts.append(str(c))
                else:
                    parts.append(str(c))
        elif content:
            parts.append(str(content))
        if result.get("isError"):
            return "（MCP 工具错误）" + "\n".join(parts)
        return "\n".join(parts) if parts else "（无返回）"


class McpManager:
    """管理所有配置的 MCP server，汇总工具 schema 与调用"""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path) if config_path else (
            Path(PROJECT_ROOT) / "config" / "mcp.json"
        )
        self.servers: List[McpServer] = []
        self._registry: Dict[str, str] = {}  # mcp_<server>_<tool> -> server.name
        self._loaded = False

    def load(self):
        """读取 config/mcp.json 并启动所有 server、发现工具"""
        if self._loaded:
            return
        cfg = self._read_config()
        servers_cfg = cfg.get("servers", [])
        for sc in servers_cfg:
            name = sc.get("name") or sc.get("command", "mcp")
            try:
                # 路径锚定：支持 {PROJECT_ROOT} 占位符 -> 项目根绝对路径，
                # 避免依赖启动时的工作目录（他人拉项目在任意目录跑也能工作）。
                args = [self._resolve(p) for p in (sc.get("args") or [])]
                env = dict(sc.get("env") or {})
                for k, v in env.items():
                    env[k] = self._resolve(v)
                srv = McpServer(
                    name=name,
                    command=self._resolve(sc["command"]),
                    args=args,
                    env=env,
                )
                srv.start()
                srv.build_schemas()
                # 建立调用映射
                for t in srv._tools:
                    tname = t.get("name", "")
                    key = f"mcp_{name}_{tname}"
                    self._registry[key] = name
                self.servers.append(srv)
            except Exception as e:
                from core.logger import log_error
                log_error("MCP", f"加载 server '{name}' 失败: {e}")
        self._loaded = True

    @staticmethod
    def _resolve(p: str) -> str:
        """把含 {PROJECT_ROOT} 的字符串解析为绝对路径（其余原样返回）。"""
        if isinstance(p, str) and "{PROJECT_ROOT}" in p:
            return p.replace("{PROJECT_ROOT}", str(PROJECT_ROOT))
        return p

    def _read_config(self) -> dict:
        if not self.config_path.exists():
            return {"servers": []}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {"servers": []}

    def close(self):
        for s in self.servers:
            try:
                s.stop()
            except Exception:
                pass
        self.servers = []
        self._loaded = False

    # ---------------- 对外（供 tools.py 使用） ----------------

    def schemas(self) -> List[dict]:
        out = []
        for s in self.servers:
            out.extend(s._schemas)
        return out

    def call(self, name: str, arguments: dict) -> str:
        """按 'mcp_<server>_<tool>' 名字调用"""
        srv_name = self._registry.get(name)
        if not srv_name:
            return f"未知 MCP 工具: {name}"
        tool_name = name
        for s in self.servers:
            if s.name == srv_name:
                # 去掉前缀得到原始工具名
                raw = name[len(f"mcp_{srv_name}_"):]
                return s.call_tool(raw, arguments or {})
        return f"MCP server 未加载: {srv_name}"

    def tool_names(self) -> List[str]:
        return list(self._registry.keys())


# 单例（供模块级注册）
_default_manager = McpManager()


def get_mcp_manager() -> McpManager:
    return _default_manager

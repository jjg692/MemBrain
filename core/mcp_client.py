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

# MCP JSON-RPC 版本（客户端发送标准协议版本，未发送时允许服务器协商）
_PROTOCOL_VERSION = "2024-11-05"


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
        self._last_error: Optional[str] = None

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
            self._last_error = str(e)
            raise McpError(f"MCP server '{self.name}' 启动失败: {e}")
        try:
            # 握手
            self._request("initialize", {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": self.client_name, "version": "0.1.0"},
            })
            # 通知 initialized（可选，部分 server 需要）
            self._notify("notifications/initialized", {})
        except Exception as e:
            # 握手失败：清理已启动的进程，避免残留
            self._last_error = str(e)
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
            raise McpError(f"MCP server '{self.name}' 握手失败: {e}")
        self._last_error = None

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

    def status(self) -> dict:
        """返回单服务状态（供后台 MCP 管理页展示）。"""
        return {
            "name": self.name,
            "command": self.command,
            "args": list(self.args),
            "running": self.alive,
            "tools_count": len(self._schemas),
            "tool_names": [s.get("function", {}).get("name", "") for s in self._schemas],
            "last_error": self._last_error,
        }

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
    """管理所有配置的 MCP server，汇总工具 schema 与调用。

    支持「插件化」：config/mcp.json 里每个 server 用 `enabled` 控制是否启动；
    运行时可对单个 server 执行 start/stop/restart，并同步增删对应工具。
    """

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = Path(config_path) if config_path else (
            Path(PROJECT_ROOT) / "config" / "mcp.json"
        )
        self.servers: List[McpServer] = []
        self._registry: Dict[str, str] = {}  # mcp_<server>_<tool> -> server.name
        self._cfgs: Dict[str, dict] = {}     # server_name -> 原始配置(含 enabled/command/args/env)
        self._loaded = False
        self._lock = threading.Lock()

    # ---------------- 配置 ----------------

    def _read_config(self) -> dict:
        if not self.config_path.exists():
            return {"servers": []}
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {"servers": []}

    def _save_config(self, cfg: dict):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    def _config_dict(self) -> dict:
        """返回当前配置 dict（含所有 server 的原始 cfg）。"""
        cfg = self._read_config()
        # 用内存中最新 enabled 状态覆盖（若有）
        for sc in cfg.get("servers", []):
            name = sc.get("name")
            if name in self._cfgs and "enabled" in self._cfgs[name]:
                sc["enabled"] = self._cfgs[name].get("enabled", False)
        return cfg

    def configured_servers(self) -> List[dict]:
        """返回配置声明的所有 server（含未启动的），带 {name, command, enabled, ...}。"""
        out = []
        for sc in self._read_config().get("servers", []):
            name = sc.get("name") or sc.get("command", "mcp")
            out.append({
                "name": name,
                "command": sc.get("command", ""),
                "enabled": bool(sc.get("enabled", True)),
                "args": list(sc.get("args") or []),
                "env": dict(sc.get("env") or {}),
            })
        return out

    # ---------------- 加载 / 关闭 ----------------

    def load(self):
        """读取 config/mcp.json 并启动所有 enabled 的 server、发现工具。幂等。"""
        with self._lock:
            if self._loaded:
                return
            cfg = self._read_config()
            servers_cfg = cfg.get("servers", [])
            for sc in servers_cfg:
                name = sc.get("name") or sc.get("command", "mcp")
                self._cfgs[name] = dict(sc)
                if not sc.get("enabled", True):
                    continue  # 跳过已禁用的服务（插件化开关）
                try:
                    self._start_one(name, sc)
                except Exception as e:
                    from core.logger import log_error
                    log_error("MCP", f"加载 server '{name}' 失败: {e}")
            self._loaded = True

    def _build_server(self, name: str, sc: dict) -> McpServer:
        args = [self._resolve(p) for p in (sc.get("args") or [])]
        env = dict(sc.get("env") or {})
        for k, v in env.items():
            env[k] = self._resolve(v)
        return McpServer(
            name=name,
            command=self._resolve(sc["command"]),
            args=args,
            env=env,
        )

    def _start_one(self, name: str, sc: dict) -> McpServer:
        """启动单个 server 并发现工具、建立 registry（不持有全局锁）。"""
        srv = self._build_server(name, sc)
        srv.start()
        srv.build_schemas()
        for t in srv._tools:
            tname = t.get("name", "")
            key = f"mcp_{name}_{tname}"
            self._registry[key] = name
        self.servers.append(srv)
        return srv

    @staticmethod
    def _resolve(p: str) -> str:
        """把含 {PROJECT_ROOT} 的字符串解析为绝对路径（其余原样返回）。"""
        if isinstance(p, str) and "{PROJECT_ROOT}" in p:
            return p.replace("{PROJECT_ROOT}", str(PROJECT_ROOT))
        return p

    def close(self):
        for s in list(self.servers):
            try:
                s.stop()
            except Exception:
                pass
        self.servers = []
        self._registry = {}
        self._loaded = False

    # ---------------- 运行时启停（插件化） ----------------

    def find(self, name: str) -> Optional[McpServer]:
        for s in self.servers:
            if s.name == name:
                return s
        return None

    def start_server(self, name: str) -> dict:
        """启动单个已禁用的服务；返回状态 dict。失败抛 McpError。"""
        if self.find(name):
            return {"ok": True, "name": name, "message": "已在运行"}
        sc = self._cfgs.get(name)
        if not sc:
            # 重新从配置读取（可能是启动后新加的 server）
            for cfg_sc in self._read_config().get("servers", []):
                if (cfg_sc.get("name") or cfg_sc.get("command")) == name:
                    sc = cfg_sc
                    self._cfgs[name] = dict(cfg_sc)
                    break
        if not sc:
            raise McpError(f"未找到 MCP server 配置: {name}")
        with self._lock:
            srv = self._start_one(name, sc)
            sc["enabled"] = True
            self._cfgs[name]["enabled"] = True
        return {"ok": True, "name": name, "message": "已启动", **srv.status()}

    def stop_server(self, name: str) -> dict:
        """停止单个服务并移除其工具 registry。"""
        with self._lock:
            srv = self.find(name)
            if srv is None:
                return {"ok": False, "name": name, "message": "服务未运行"}
            # 移除该 server 的所有工具
            prefix = f"mcp_{name}_"
            for k in [k for k in list(self._registry) if k.startswith(prefix)]:
                self._registry.pop(k, None)
            self.servers = [s for s in self.servers if s.name != name]
            srv.stop()
            if name in self._cfgs:
                self._cfgs[name]["enabled"] = False
        return {"ok": True, "name": name, "message": "已停止"}

    def restart_server(self, name: str) -> dict:
        """重启单个服务。"""
        try:
            self.stop_server(name)
        except Exception:
            pass
        sc = self._cfgs.get(name)
        if not sc:
            for cfg_sc in self._read_config().get("servers", []):
                if (cfg_sc.get("name") or cfg_sc.get("command")) == name:
                    sc = cfg_sc
                    self._cfgs[name] = dict(cfg_sc)
                    break
        if not sc:
            raise McpError(f"未找到 MCP server 配置: {name}")
        with self._lock:
            srv = self._start_one(name, sc)
            sc["enabled"] = True
            self._cfgs[name]["enabled"] = True
        return {"ok": True, "name": name, "message": "已重启", **srv.status()}

    def set_enabled(self, name: str, enabled: bool) -> dict:
        """持久化 enabled 开关到 config/mcp.json。"""
        with self._lock:
            self._cfgs[name] = dict(self._cfgs.get(name) or {})
            self._cfgs[name]["enabled"] = bool(enabled)
            cfg = self._read_config()
            for sc in cfg.get("servers", []):
                if (sc.get("name") or sc.get("command")) == name:
                    sc["enabled"] = bool(enabled)
            self._save_config(cfg)
        return {"ok": True, "name": name, "enabled": bool(enabled)}

    def status_all(self) -> List[dict]:
        """返回所有配置服务的状态（含未启用的 running=False）。"""
        out = []
        cfg_servers = self._read_config().get("servers", [])
        names = set()
        for sc in cfg_servers:
            name = sc.get("name") or sc.get("command", "mcp")
            names.add(name)
            srv = self.find(name)
            enabled = bool(sc.get("enabled", True))
            if name in self._cfgs and "enabled" in self._cfgs[name]:
                enabled = bool(self._cfgs[name]["enabled"])
            if srv is not None:
                st = srv.status()
                st["enabled"] = enabled
                out.append(st)
            else:
                out.append({
                    "name": name,
                    "command": sc.get("command", ""),
                    "running": False,
                    "enabled": enabled,
                    "tools_count": 0,
                    "tool_names": [],
                    "last_error": None,
                })
        return out

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

"""假 MCP server：可插拔扩展机制端到端验证用的通用 tools/list + tools/call。"""
import json
import sys

TOOLS = [
    {
        "name": "ping",
        "description": "Return a pong.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "echo",
        "description": "Echo back the given message.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
]


def read_line():
    return sys.stdin.readline()


def main():
    while True:
        line = read_line()
        if not line:
            break
        try:
            msg = json.loads(line)
        except Exception:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": mid,
                "result": {"protocolVersion": "0.1.0", "capabilities": {}, "serverInfo": {"name": "fake", "version": "1"}},
            }) + "\n")
            sys.stdout.flush()
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}) + "\n")
            sys.stdout.flush()
        elif method == "tools/call":
            name = msg.get("params", {}).get("name", "")
            args = msg.get("params", {}).get("arguments", {}) or {}
            if name == "ping":
                text = "pong"
            elif name == "echo":
                text = f"echo: {args.get('message', '')}"
            else:
                text = "unknown tool"
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": text}]},
            }) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

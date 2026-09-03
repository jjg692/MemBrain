"""假 MCP server：模拟 stardew 的 tools/list + tools/call，用于端到端验证。"""
import json
import sys

TOOLS = [{
    "name": "stardew_get_state",
    "description": "Get current game state.",
    "inputSchema": {"type": "object", "properties": {}},
}]

def read_line():
    line = sys.stdin.readline()
    return line


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
            state = {"season": "Spring", "day_of_month": 15, "weather": "Sun",
                     "location": "Farm", "player": {"money": 999}}
            sys.stdout.write(json.dumps({
                "jsonrpc": "2.0", "id": mid,
                "result": {"content": [{"type": "text", "text": json.dumps(state, ensure_ascii=False)}]},
            }) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

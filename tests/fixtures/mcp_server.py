"""Offline MCP peer used for real subprocess transport tests."""
import json
import sys

initialized = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "notifications/initialized":
        initialized = True
        continue
    if "id" not in request:
        continue
    if method == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "test-peer", "version": "1"}}
    elif method == "tools/list" and initialized:
        result = {"tools": [{"name": "echo", "description": "Echo the supplied text", "inputSchema": {
            "type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}
    elif method == "tools/call" and initialized:
        result = {"content": [{"type": "text", "text": request["params"]["arguments"]["text"]}]}
    else:
        result = {"error": "invalid lifecycle"}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)

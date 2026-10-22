"""Talk to `chronicle mcp` over stdio with nothing but the standard library.

    python examples/stdio_client.py "finish the Acme export"

This is the same exchange Claude Desktop, Cursor, Codex, or Gemini CLI performs:
initialize, list tools, then call get_context and print the markdown an agent sees.
"""

from __future__ import annotations

import json
import subprocess
import sys

task = sys.argv[1] if len(sys.argv) > 1 else None
server = subprocess.Popen(
    [sys.executable, "-m", "chronicle", "mcp", "--no-record"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)


def send(message: dict) -> dict | None:
    server.stdin.write(json.dumps(message) + "\n")
    server.stdin.flush()
    if "id" not in message:
        return None
    return json.loads(server.stdout.readline())


hello = send({
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "example", "version": "0"}},
})
print(f"connected to {hello['result']['serverInfo']['name']} (protocol {hello['result']['protocolVersion']})", file=sys.stderr)
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

tools = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
print("tools:", ", ".join(tool["name"] for tool in tools), file=sys.stderr)

reply = send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_context", "arguments": {"task": task}}})
print(reply["result"]["content"][0]["text"])

server.stdin.close()
server.wait(timeout=10)

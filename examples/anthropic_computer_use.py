"""Ground a Claude computer-use loop in Chronicle context.

Chronicle runs as a remote MCP server that the Anthropic API calls directly (the
MCP connector). Expose it over HTTPS first, for example:

    CHRONICLE_TOKEN=$(openssl rand -hex 16) chronicle serve
    cloudflared tunnel --url http://127.0.0.1:3030
    export CHRONICLE_URL=https://<your-tunnel>/mcp/$CHRONICLE_TOKEN

    pip install anthropic
    python examples/anthropic_computer_use.py "reply to the Acme thread with the export status"

Only the first model turn is shown. Your own computer-use harness executes the
returned `computer` tool_use blocks (screenshots, clicks) and loops.
"""

from __future__ import annotations

import os
import sys

import anthropic

task = sys.argv[1] if len(sys.argv) > 1 else "pick up where I left off"
client = anthropic.Anthropic()

response = client.beta.messages.create(
    model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5"),
    max_tokens=2048,
    betas=["mcp-client-2025-04-04", "computer-use-2025-01-24"],
    mcp_servers=[{"type": "url", "url": os.environ["CHRONICLE_URL"], "name": "chronicle"}],
    tools=[{"type": "computer_20250124", "name": "computer", "display_width_px": 1440, "display_height_px": 900}],
    system=(
        "Before you touch the computer, call chronicle's get_context with the task. Use what it says about "
        "the current screen, recent work, and the knowledge graph to plan. Cite frame ids you rely on."
    ),
    messages=[{"role": "user", "content": task}],
)

for block in response.content:
    kind = block.type
    if kind == "text":
        print(block.text)
    elif kind == "mcp_tool_use":
        print(f"[chronicle.{block.name}] {block.input}")
    elif kind == "mcp_tool_result":
        print(f"[chronicle result] {str(block.content)[:400]}…")
    elif kind == "tool_use":
        print(f"[computer] {block.input}")

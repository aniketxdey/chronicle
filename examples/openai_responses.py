"""Use Chronicle from the OpenAI Responses API as a remote MCP tool.

    CHRONICLE_TOKEN=$(openssl rand -hex 16) chronicle serve
    cloudflared tunnel --url http://127.0.0.1:3030
    export CHRONICLE_URL=https://<your-tunnel>/mcp/$CHRONICLE_TOKEN

    pip install openai
    python examples/openai_responses.py "what was I doing before lunch?"
"""

from __future__ import annotations

import os
import sys

from openai import OpenAI

question = sys.argv[1] if len(sys.argv) > 1 else "What was I working on in the last hour?"
client = OpenAI()

response = client.responses.create(
    model=os.environ.get("OPENAI_MODEL", "gpt-5"),
    tools=[{
        "type": "mcp",
        "server_label": "chronicle",
        "server_url": os.environ["CHRONICLE_URL"],
        "allowed_tools": ["get_context", "search_history", "query_graph", "get_entity", "get_frame"],
        "require_approval": "never",
    }],
    instructions="Call get_context first. Answer from what Chronicle returns and cite frame ids.",
    input=question,
)
print(response.output_text)

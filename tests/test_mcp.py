"""MCP over stdio and Streamable HTTP, connector tools, client setup, and auth."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib

from chronicle import clients
from chronicle.mcp_server import handle

INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}},
}


def call(name: str, arguments: dict, message_id: int = 9) -> dict:
    return handle({"jsonrpc": "2.0", "id": message_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}})


def test_initialize_negotiates_versions():
    assert handle(INIT)["result"]["protocolVersion"] == "2025-06-18"
    old = {**INIT, "params": {**INIT["params"], "protocolVersion": "2024-11-05"}}
    assert handle(old)["result"]["protocolVersion"] == "2024-11-05"
    unknown = {**INIT, "params": {**INIT["params"], "protocolVersion": "1999-01-01"}}
    assert handle(unknown)["result"]["protocolVersion"] == "2025-06-18"
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_tools_have_valid_schemas():
    tools = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert {"get_context", "query_graph", "get_entity", "get_frame", "start_recording", "stop_recording", "search", "fetch"} <= names
    for tool in tools:
        assert tool["inputSchema"]["type"] == "object"
        for field in tool["inputSchema"].get("required", []):
            assert field in tool["inputSchema"]["properties"]


def test_context_and_graph_tools():
    context = call("get_context", {"task": "Acme export columns"})["result"]
    assert context["isError"] is False
    assert "Knowledge graph" in context["content"][0]["text"]
    nodes = json.loads(call("query_graph", {"query": "export", "kind": "file"})["result"]["content"][0]["text"])
    assert nodes and all(node["kind"] == "file" for node in nodes)
    entity = json.loads(call("get_entity", {"id": "Maya Chen"})["result"]["content"][0]["text"])
    assert entity["node"]["kind"] == "person"
    missing = call("get_entity", {"id": "nope:nothing"})["result"]
    assert missing["isError"] is True


def test_search_and_fetch_follow_connector_shape():
    results = json.loads(call("search", {"query": "invoice_id"})["result"]["content"][0]["text"])["results"]
    assert results and all({"id", "title", "url"} <= set(item) for item in results)
    for item in results[:4]:
        doc = json.loads(call("fetch", {"id": item["id"]})["result"]["content"][0]["text"])
        assert {"id", "title", "text", "url"} <= set(doc)


def test_recording_tools_and_frame():
    started = call("start_recording", {"duration": "5m", "label": "mcp test"})["result"]
    assert started["isError"] is False
    again = call("start_recording", {"duration": "5m"})["result"]
    assert again["isError"] is True
    stopped = json.loads(call("stop_recording", {})["result"]["content"][0]["text"])
    assert stopped["status"] == "stopped"
    frame = call("get_frame", {"frame_id": 1, "include_image": True})["result"]
    assert frame["isError"] is False
    assert len(frame["content"]) == 2


def test_resources_and_prompts():
    listed = handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["result"]["resources"]
    uris = {item["uri"] for item in listed}
    assert "chronicle://context/current" in uris
    read = handle({"jsonrpc": "2.0", "id": 4, "method": "resources/read", "params": {"uri": "chronicle://context/current"}})
    assert read["result"]["contents"][0]["text"].startswith("# Computer context")
    prompt = handle({"jsonrpc": "2.0", "id": 5, "method": "prompts/get", "params": {"name": "use_my_computer_context", "arguments": {"task": "x"}}})
    assert "get_context" in prompt["result"]["messages"][0]["content"]["text"]
    assert handle({"jsonrpc": "2.0", "id": 6, "method": "nope"})["error"]["code"] == -32601


def test_stdio_transport_is_newline_delimited():
    messages = [
        INIT,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "graph_summary", "arguments": {}}},
    ]
    payload = "".join(json.dumps(m) + "\n" for m in messages)
    done = subprocess.run(
        [sys.executable, "-m", "chronicle", "mcp", "--no-record"],
        input=payload, capture_output=True, text=True, timeout=60, env={**os.environ},
    )
    lines = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
    assert [line["id"] for line in lines] == [1, 2, 3]
    assert lines[0]["result"]["serverInfo"]["name"] == "chronicle"
    assert "counts" in lines[2]["result"]["content"][0]["text"]


def test_http_transport(client):
    init = client.post("/mcp", json=INIT, headers={"Accept": "application/json, text/event-stream"})
    assert init.status_code == 200
    assert init.headers.get("mcp-session-id")
    assert client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}).status_code == 202
    listed = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}).json()
    assert listed["result"]["tools"]
    assert client.get("/mcp").status_code == 405
    assert client.post("/mcp", content=b"{not json").status_code == 400


def test_token_and_origin_guard(client):
    os.environ["CHRONICLE_TOKEN"] = "s3cret"
    try:
        assert client.post("/mcp", json=INIT).status_code == 401
        assert client.get("/sessions").status_code == 401
        assert client.post("/mcp/s3cret", json=INIT).status_code == 200
        assert client.post("/mcp", json=INIT, headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert client.get("/sessions", params={"token": "s3cret"}).status_code == 200
        assert client.get("/health").status_code == 200
    finally:
        os.environ.pop("CHRONICLE_TOKEN", None)
    evil = client.post("/mcp", json=INIT, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert client.post("/mcp", json=INIT, headers={"Origin": "http://localhost:3030"}).status_code == 200


def test_every_client_has_config(client):
    listed = client.get("/clients").json()["data"]
    assert len(listed) >= 15
    for item in listed:
        config = client.get(f"/clients/{item['id']}").json()
        assert config["snippet"], item["id"]
    assert '"servers"' in clients.config("vscode")["snippet"]
    assert "[mcp_servers.chronicle]" in clients.config("codex")["snippet"]
    assert "claude mcp add" in clients.config("claude-code")["snippet"]
    assert '"type": "mcp"' in clients.config("openai-api")["snippet"]
    assert "mcp_servers" in clients.config("anthropic-api")["snippet"]


def test_install_merges_json_with_backup(tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    result = clients.install("cursor", path=target)
    data = json.loads(target.read_text())
    assert set(data["mcpServers"]) == {"other", "chronicle"}
    assert data["theme"] == "dark"
    assert data["mcpServers"]["chronicle"]["args"][-1] == "mcp"
    assert result["backup"] and os.path.exists(result["backup"])
    vscode = tmp_path / "vscode.json"
    clients.install("vscode", path=vscode)
    assert json.loads(vscode.read_text())["servers"]["chronicle"]["type"] == "stdio"


def test_install_codex_toml_is_idempotent(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('model = "o4"\n\n[mcp_servers.other]\ncommand = "y"\n')
    clients.install("codex", path=target)
    clients.install("codex", path=target)
    parsed = tomllib.loads(target.read_text())
    assert parsed["model"] == "o4"
    assert set(parsed["mcp_servers"]) == {"other", "chronicle"}
    assert target.read_text().count("[mcp_servers.chronicle]") == 1


def test_install_refuses_jsonc(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text('{\n  // comment\n  "a": 1\n}')
    try:
        clients.install("gemini-cli", path=target)
    except Exception as exc:  # noqa: BLE001
        assert "not plain JSON" in str(exc)
    else:
        raise AssertionError("expected a refusal")
    assert "// comment" in target.read_text()

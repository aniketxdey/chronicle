"""LLM graph tuner: op validation, patch persistence and replay, caching, providers, and failure handling."""

from __future__ import annotations

import io
import json
import sys
import urllib.error

import pytest

from chronicle import context, db, graph, llm, sessions, tuner
from chronicle.mcp_server import handle

CRUSOE = "page:forbes.com/companies/crusoe"
AI50 = "page:forbes.com/lists/ai50"
SEARCH = "page:google.com/search?q=top-50-startups-in-physical-ai-space"


class Fake:
    name = "fake"
    model = "fake-1"

    def __init__(self, *script):
        self.script = list(script)
        self.prompts: list[dict] = []
        self.tools: list[dict] = []

    def call_tools(self, system, user, tools):
        self.prompts.append(json.loads(user))
        self.tools = tools
        item = self.script.pop(0) if self.script else []
        if isinstance(item, Exception):
            raise item
        return item

    def complete(self, system, user, max_tokens=4000):
        return "## Summary\nWritten by the fake model."


def op(tool: str, /, **arguments) -> dict:
    return {"name": tool, "arguments": arguments}


def _record(client, label: str = "physical ai research") -> int:
    session = client.post("/sessions", json={"duration": "15m", "label": label}).json()
    for text, window, url in (
        ("Crusoe builds AI data centers powered by stranded energy", "Crusoe | Company Overview & News",
         "https://www.forbes.com/companies/crusoe/?list=ai50"),
        ("Forbes 2026 AI 50 List Crusoe Figure Skild AI", "Forbes 2026 AI 50 List | Top Artificial Intelligence Companies",
         "https://www.forbes.com/lists/ai50/"),
        ("top 50 startups in physical ai space", "top 50 startups in physical ai space - Google Search",
         "https://www.google.com/search?q=top+50+startups+in+physical+ai+space&sca_esv=abc"),
    ):
        client.post("/add", json={"text": text, "app_name": "Arc", "window_name": window, "browser_url": url, "session_id": session["id"]})
    sessions.stop(session["id"])
    return session["id"]


def _script(scope: str) -> list[dict]:
    return [
        op("add_entity", name="Crusoe", kind="company", traits=["AI data centers"], aliases=["Crusoe Energy"], seen_in=[CRUSOE, AI50]),
        op("add_entity", name="Forbes AI 50", kind="list", traits=[], aliases=[], seen_in=[AI50]),
        op("add_entity", name="physical AI", kind="concept", traits=[], aliases=[], seen_in=[SEARCH]),
        op("add_relation", source="Crusoe", relation="is_a", target="company", confidence=0.95, evidence="Company Overview"),
        op("add_relation", source="company:crusoe", relation="listed_in", target="Forbes AI 50", confidence=0.9, evidence="list=ai50"),
        op("rename_node", id=SEARCH, label="Search: top 50 physical AI startups"),
        op("add_trait", node="Crusoe", trait="stranded-energy compute"),
        op("set_activity", summary="Researching physical-AI startups on the Forbes AI 50 list", node_ids=[SEARCH, AI50, "company:crusoe"]),
        op("add_relation", source="page:does-not-exist", relation="mentions", target="Crusoe", confidence=0.5, evidence="x"),
        op("retype_node", id=scope, kind="topic"),
    ]


def test_check_validates_against_schema():
    assert tuner.check("rename_node", {"id": "a", "label": "  Clean   label "}) == {"op": "rename_node", "id": "a", "label": "Clean label"}
    bad = [
        ("explode", {}),
        ("rename_node", {"id": "a"}),
        ("rename_node", {"id": "a", "label": "b", "extra": 1}),
        ("retype_node", {"id": "a", "kind": "spaceship"}),
        ("add_relation", {"source": "a", "relation": "loves", "target": "b", "confidence": 0.5, "evidence": "e"}),
        ("add_relation", {"source": "a", "relation": "is_a", "target": "b", "confidence": 1.5, "evidence": "e"}),
        ("merge_nodes", {"keep_id": "a", "drop_ids": "b"}),
        ("add_trait", "not an object"),
    ]
    for name, arguments in bad:
        with pytest.raises(tuner.Rejected):
            tuner.check(name, arguments)
    for tool in tuner.TOOLS:
        params = tool["parameters"]
        assert params["additionalProperties"] is False and set(params["required"]) == set(params["properties"])


def test_tune_applies_patches_that_survive_rebuild_and_reset(client):
    sid = _record(client)
    scope = f"session:{sid}"
    fake = Fake(_script(scope), [])
    result = tuner.tune(sid, provider=fake)
    assert result["status"] == "ok" and result["rounds"] == 2 and result["calls"] == 2
    assert result["applied"] == 8
    assert {item["op"] for item in result["rejected"]} == {"add_relation", "retype_node"}
    assert "rejected_last_round" in fake.prompts[1]
    assert {node["id"] for node in fake.prompts[0]["nodes"]} >= {CRUSOE, AI50, SEARCH, scope}
    assert any("Crusoe" in item["window"] for item in fake.prompts[0]["evidence"])
    assert any("q=top 50 startups" in (item["url"] or "") for item in fake.prompts[0]["evidence"])

    def check_tuned(session_id):
        summary = graph.summary(session_id)
        lines = {f"{r['from']} —{r['rel']}→ {r['to']}" for r in summary["llm"]["relations"]}
        assert {"Crusoe —is_a→ company", "Crusoe —listed_in→ Forbes AI 50"} <= lines
        assert summary["llm"]["activities"][0]["summary"].startswith("Researching physical-AI")
        assert summary["counts"]["company"] == 1 and summary["counts"]["activity"] == 1
        node = graph.node("company:crusoe", session_id=session_id)
        assert node["node"]["props"]["source"] == "llm"
        assert node["node"]["props"]["traits"] == ["AI data centers", "stranded-energy compute"]
        assert node["node"]["evidence"]
        edge = next(n for n in node["neighbors"] if n["rel"] == "is_a")
        assert edge["id"] == "category:company"
        renamed = graph.node(SEARCH, session_id=session_id)["node"]
        assert renamed["label"] == "Search: top 50 physical AI startups"
        assert renamed["props"]["original_label"].endswith("Google Search")

    check_tuned(sid)
    built = graph.build(sid)
    assert built["patches"] == {"applied": 8, "skipped": 0}
    check_tuned(sid)
    assert graph.node("company:crusoe")["node"]["props"]["source"] == "llm"

    with db.conn() as connection:
        rows = connection.execute("SELECT op, model FROM kg_llm_patches WHERE scope = ?", (scope,)).fetchall()
    assert len(rows) == 8 and {row["model"] for row in rows} == {"fake-1"}

    pack = context.build(session_id=sid, task="physical ai startups")
    markdown = pack["markdown"]
    assert "## What the user was doing (LLM-tuned graph)" in markdown
    assert markdown.index("Researching physical-AI") < markdown.index("Crusoe —is_a→ company")

    assert tuner.reset(sid) == {"scope": scope, "removed": 8}
    summary = graph.summary(sid)
    assert "company" not in summary["counts"] and not summary["llm"]["relations"]
    with pytest.raises(db.ChronicleError):
        graph.node("company:crusoe")


def test_merge_retype_and_drop_reroute_edges(client):
    sid = _record(client, "cleanup")
    rerouted = {n["id"] for n in graph.node(CRUSOE, session_id=sid)["neighbors"]} - {AI50}
    fake = Fake([
        op("merge_nodes", keep_id=AI50, drop_ids=[CRUSOE, AI50]),
        op("retype_node", id="site:forbes.com", kind="company"),
        op("drop_node", id=SEARCH, reason="search noise"),
        op("rename_node", id=CRUSOE, label="gone"),
    ])
    result = tuner.tune(sid, provider=fake, rounds=1)
    assert result["applied"] == 3 and len(result["rejected"]) == 1
    merged = graph.node(AI50, session_id=sid)
    assert CRUSOE in merged["node"]["props"]["merged"]
    assert "Crusoe | Company Overview & News" in merged["node"]["props"]["aliases"]
    assert rerouted and rerouted <= {n["id"] for n in merged["neighbors"]}
    assert all(n["id"] != AI50 for n in merged["neighbors"])
    assert graph.node("site:forbes.com", session_id=sid)["node"]["kind"] == "company"
    with pytest.raises(db.ChronicleError):
        graph.node(SEARCH, session_id=sid)
    exported = graph.export(sid, limit=200)
    ids = {node["id"] for node in exported["nodes"]}
    assert all(edge["src"] in ids and edge["dst"] in ids for edge in exported["edges"])


def test_all_history_patches_survive_reaggregation(client):
    sid = _record(client, "all history")
    session_fake = Fake([op("add_relation", source=CRUSOE, relation="listed_in", target=AI50, confidence=0.8, evidence="list=ai50")])
    assert tuner.tune(sid, provider=session_fake, rounds=1)["applied"] == 1
    assert any(r["rel"] == "listed_in" for r in graph.summary()["llm"]["relations"])

    fake = Fake([op("rename_node", id=AI50, label="Forbes AI 50 (all history)")])
    assert tuner.tune(None, provider=fake, rounds=1)["applied"] == 1
    for _ in range(2):
        built = graph.build(None)
        assert built["patches"] == {"applied": 1, "skipped": 0}
        assert graph.node(AI50)["node"]["label"] == "Forbes AI 50 (all history)"
    assert graph.node(AI50, session_id=sid)["node"]["label"].startswith("Forbes 2026")
    assert tuner.reset(None)["removed"] == 1
    assert graph.node(AI50)["node"]["label"].startswith("Forbes 2026")


def test_cache_and_up_to_date(client):
    sid = _record(client, "cache check")
    first = Fake([op("add_trait", node=CRUSOE, trait="company profile")], [])
    assert tuner.tune(sid, provider=first)["applied"] == 1
    again = Fake()
    assert tuner.tune(sid, provider=again)["status"] == "up_to_date"
    assert again.prompts == []
    tuner.reset(sid)
    cached = Fake()
    result = tuner.tune(sid, provider=cached, rounds=1)
    assert result["cached"] == 1 and result["applied"] == 1 and cached.prompts == []
    forced = Fake([])
    assert tuner.tune(sid, provider=forced, force=True)["calls"] == 1 and len(forced.prompts) == 1


def test_offline_by_default(client):
    assert llm.provider() is None and not llm.enabled()
    sid = _record(client, "offline")
    assert tuner.after_session(sid) is None
    with pytest.raises(db.ChronicleError):
        tuner.tune(sid)
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "tune_graph", "arguments": {"session_id": sid}}})
    assert reply["result"]["isError"] is True and "CHRONICLE_LLM" in reply["result"]["content"][0]["text"]
    reset = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "tune_graph", "arguments": {"session_id": sid, "reset": True}}})
    assert json.loads(reset["result"]["content"][0]["text"])["removed"] == 0
    assert graph.summary(sid)["llm"] == {"patches": 0, "activities": [], "relations": [], "entities": []}


def test_errors_are_reported_not_raised(client):
    sid = _record(client, "errors")
    limited = Fake(llm.LLMError("rate limited", retryable=True))
    result = tuner.tune(sid, provider=limited)
    assert result["status"] == "error" and result["retryable"] and result["applied"] == 0
    assert "rate limited" in db.get_setting("last_llm_error")
    broken = Fake(RuntimeError("boom"))
    assert tuner.tune(sid, provider=broken)["status"] == "error"
    garbage = Fake([{"name": "add_trait", "arguments": None}, "nonsense", {"name": None}])
    assert tuner.tune(sid, provider=garbage, rounds=1)["rejected"]


def test_session_stop_tunes_in_background(client):
    fake = Fake([op("set_activity", summary="Reading about Crusoe", node_ids=[CRUSOE])], [])
    llm.use(fake)
    try:
        sid = _record(client, "background")
        assert tuner.wait(30)
    finally:
        llm.use(None)
    assert graph.summary(sid)["llm"]["activities"][0]["summary"] == "Reading about Crusoe"
    llm.use(Fake(RuntimeError("network down")))
    try:
        sid = _record(client, "background failure")
        assert tuner.wait(30)
    finally:
        llm.use(None)
    assert sessions.get(sid)["status"] == "stopped"
    assert "network down" in db.get_setting("last_llm_error")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_openai_provider_over_http(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", None)
    monkeypatch.setattr("time.sleep", lambda _: None)
    sent: list = []
    payload = {"output": [
        {"type": "message", "content": []},
        {"type": "function_call", "name": "add_trait", "arguments": json.dumps({"node": "a", "trait": "b"})},
    ]}

    def urlopen(request, timeout):
        sent.append(request)
        if len(sent) == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "slow down", {"Retry-After": "1"}, io.BytesIO(b"{}"))
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    provider = llm.OpenAIProvider(api_key="sk-test", model="gpt-test", base_url="https://example.test/v1/")
    calls = provider.call_tools("system", "user", tuner.TOOLS)
    assert calls == [{"name": "add_trait", "arguments": {"node": "a", "trait": "b"}}]
    assert len(sent) == 2 and sent[1].full_url == "https://example.test/v1/responses"
    body = json.loads(sent[1].data)
    assert body["model"] == "gpt-test" and body["instructions"] == "system"
    assert all(tool["type"] == "function" and tool["strict"] for tool in body["tools"])
    assert sent[1].headers["Authorization"] == "Bearer sk-test"

    def fail(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 401, "nope", {}, io.BytesIO(b'{"error":"bad key"}'))

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(llm.LLMError) as caught:
        provider.call_tools("s", "u", tuner.TOOLS)
    assert "401" in caught.value.message and not caught.value.retryable


def test_openai_provider_with_sdk():
    pytest.importorskip("openai")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list = []
    reply = {
        "id": "resp_1", "object": "response", "created_at": 0, "model": "gpt-test", "status": "completed",
        "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "rename_node",
                    "arguments": json.dumps({"id": "a", "label": "b"}), "status": "completed"}],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
    }

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            body = json.dumps(reply).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = llm.OpenAIProvider(api_key="sk-test", model="gpt-test", base_url=f"http://127.0.0.1:{server.server_port}/v1")
        calls = provider.call_tools("system", "user", tuner.TOOLS)
    finally:
        server.shutdown()
    assert calls == [{"name": "rename_node", "arguments": {"id": "a", "label": "b"}}]
    path, body = seen[0]
    assert path == "/v1/responses" and body["input"] == "user" and body["tools"][0]["strict"] is True


def test_provider_config_and_env_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CHRONICLE_LLM", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert llm.enabled()
    with pytest.raises(llm.LLMError):
        llm.provider()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("CHRONICLE_LLM_MODEL", "gpt-custom")
    provider = llm.provider()
    assert provider.name == "openai" and provider.model == "gpt-custom"
    monkeypatch.setenv("CHRONICLE_LLM", "mystery")
    with pytest.raises(llm.LLMError):
        llm.provider()

    monkeypatch.setitem(sys.modules, "dotenv", None)
    env = tmp_path / ".env"
    env.write_text('# comment\nexport CHRONICLE_TEST_A="quoted value"\nCHRONICLE_TEST_B=plain # note\nOPENAI_API_KEY=from-file\n')
    monkeypatch.delenv("CHRONICLE_TEST_A", raising=False)
    monkeypatch.delenv("CHRONICLE_TEST_B", raising=False)
    llm._read_env(env)
    import os

    assert os.environ["CHRONICLE_TEST_A"] == "quoted value"
    assert os.environ["CHRONICLE_TEST_B"] == "plain"
    assert os.environ["OPENAI_API_KEY"] == "sk-test"
    monkeypatch.delenv("CHRONICLE_TEST_A")
    monkeypatch.delenv("CHRONICLE_TEST_B")

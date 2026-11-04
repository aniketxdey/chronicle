"""Model setup: local and Anthropic providers, saved keys, and actions nested inside graph nodes."""

from __future__ import annotations

import io
import json

import pytest

from chronicle import db, graph, llm, seed, sessions, tuner


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _serve(monkeypatch, reply: dict) -> list:
    seen = []

    def urlopen(request, timeout):
        seen.append((request.full_url, dict(request.headers), json.loads(request.data)))
        return _Response(json.dumps(reply).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    return seen


TOOL = {"name": "add_trait", "description": "d", "parameters": {"type": "object", "properties": {}}}


def test_local_provider_reads_native_tool_calls_without_a_key(monkeypatch):
    call = {"function": {"name": "add_trait", "arguments": '{"node": "a", "trait": "b"}'}}
    seen = _serve(monkeypatch, {"choices": [{"message": {"tool_calls": [call]}}]})
    provider = llm.ChatProvider("http://localhost:11434/v1/", "llama3.2")
    assert provider.call_tools("s", "u", [TOOL]) == [{"name": "add_trait", "arguments": {"node": "a", "trait": "b"}}]
    url, headers, body = seen[0]
    assert url == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in headers and body["model"] == "llama3.2"


def test_models_without_tool_support_get_the_schemas_in_the_prompt(monkeypatch):
    text = '[{"name": "add_trait", "arguments": {"node": "a"}}]'
    bodies = []

    def send(self, body):
        bodies.append(body)
        if "tools" in body:
            raise llm.LLMError('HTTP 400: {"error": "gemma3 does not support tools"}')
        return {"choices": [{"message": {"content": text}}]}

    monkeypatch.setattr(llm.ChatProvider, "_send", send)
    assert llm.ChatProvider("http://x/v1", "m").call_tools("s", "u", [TOOL]) == [{"name": "add_trait", "arguments": {"node": "a"}}]
    assert "add_trait" in bodies[1]["messages"][0]["content"]

    def refused(self, body):
        raise llm.LLMError("HTTP 400: bad request")

    monkeypatch.setattr(llm.ChatProvider, "_send", refused)
    with pytest.raises(llm.LLMError):
        llm.ChatProvider("http://x/v1", "m").call_tools("s", "u", [TOOL])


def test_tool_calls_written_loosely_are_still_read():
    call = '{"name": "add_trait", "arguments": {"node": "a"}}'
    expected = [{"name": "add_trait", "arguments": {"node": "a"}}]
    assert llm._tools_from_text(f"Editing [the file] now:\n```json\n[{call}]\n```") == expected
    assert llm._tools_from_text(call) == expected
    assert llm._tools_from_text('{"node": "a"}', [TOOL]) == expected
    assert llm._tools_from_text('{"node": "a"}', [TOOL, TOOL]) == []
    assert llm._tools_from_text("nothing here") == []


def test_local_provider_falls_back_to_tool_calls_written_as_json(monkeypatch):
    text = 'Sure:\n[{"name": "add_trait", "arguments": {"node": "a", "trait": "b"}}]'
    _serve(monkeypatch, {"choices": [{"message": {"content": text}}]})
    calls = llm.ChatProvider("http://x/v1", "m").call_tools("s", "u", [TOOL])
    assert calls == [{"name": "add_trait", "arguments": {"node": "a", "trait": "b"}}]
    _serve(monkeypatch, {"choices": [{"message": {"content": "no changes needed"}}]})
    assert llm.ChatProvider("http://x/v1", "m").call_tools("s", "u", [TOOL]) == []


def test_local_provider_needs_a_model():
    with pytest.raises(llm.LLMError):
        llm.ChatProvider("http://x/v1", "")


def test_anthropic_provider(monkeypatch):
    seen = _serve(monkeypatch, {"content": [
        {"type": "text", "text": "ok"},
        {"type": "tool_use", "name": "add_trait", "input": {"node": "a", "trait": "b"}},
    ]})
    provider = llm.AnthropicProvider(api_key="k", model="claude-test")
    assert provider.call_tools("s", "u", [TOOL]) == [{"name": "add_trait", "arguments": {"node": "a", "trait": "b"}}]
    assert provider.complete("s", "u") == "ok"
    url, headers, body = seen[0]
    assert url.endswith("/messages") and headers["X-api-key"] == "k"
    assert body["tools"][0]["input_schema"] == TOOL["parameters"] and body["system"] == "s"


def test_saved_keys_are_per_provider_and_never_returned(client, monkeypatch):
    monkeypatch.delenv("CHRONICLE_LLM")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    keys = ("llm_provider", "llm_model", "llm_base_url", *(f"llm_{part}_{item['id']}" for item in llm.CATALOG for part in ("api_key", "model")))
    try:
        saved = client.post("/settings", json={"llm_provider": "anthropic", "llm_model": "claude-test", "llm_api_key": "secret-a"}).json()
        assert "secret-a" not in json.dumps(saved)
        status = client.get("/llm/status").json()
        assert status["enabled"] and status["provider"] == "anthropic" and status["keys"]["anthropic"] and not status["keys"]["openai"]
        assert llm.provider().api_key == "secret-a"

        client.post("/settings", json={"llm_provider": "local", "llm_model": "llama3.2", "llm_base_url": "http://box:1234/v1"})
        chosen = llm.provider()
        assert isinstance(chosen, llm.ChatProvider) and chosen.base_url == "http://box:1234/v1" and chosen.api_key == ""

        client.post("/settings", json={"llm_provider": "off"})
        assert llm.provider() is None and client.post("/llm/test").json()["ok"] is False
        assert client.post("/settings", json={"llm_provider": "auto", "llm_api_key": "x"}).status_code == 400
    finally:
        for key in keys:
            db.set_setting(key, "")


class _Fake:
    name = "fake"
    model = "fake-1"

    def __init__(self, calls):
        self.calls = calls

    def call_tools(self, system, user, tools):
        self.prompt = user
        calls, self.calls = self.calls, []
        return calls


def test_actions_nest_inside_nodes_and_survive_rebuild(client):
    seed.load_sample()
    with db.conn() as connection:
        frame, event = connection.execute("SELECT frame_id, id FROM ui_events WHERE frame_id IS NOT NULL ORDER BY id LIMIT 1").fetchone()
        app = connection.execute("SELECT app_name FROM frames WHERE id = ?", (frame,)).fetchone()[0]
        started = connection.execute("SELECT MIN(timestamp) AS a, MAX(timestamp) AS b FROM frames").fetchone()
        cursor = connection.execute(
            "INSERT INTO sessions(label, started_at, stopped_at, status) VALUES ('actions', ?, ?, 'stopped')", (started["a"], started["b"])
        )
        sid = cursor.lastrowid
        connection.execute("UPDATE frames SET session_id = ? WHERE id = ?", (sid, frame))
    graph.build(sid)
    target = graph.find(app, session_id=sid)[0]["id"]
    fake = _Fake([
        {"name": "record_action", "arguments": {"node": target, "action": "Reviewed the export spec", "status": "worked_on", "frames": [frame]}},
        {"name": "record_action", "arguments": {"node": target, "action": "reviewed the export spec", "status": "viewed", "frames": [frame]}},
    ])
    result = tuner.tune(sid, provider=fake, rounds=1)
    assert result["status"] == "ok" and result["applied"] == 2
    assert '"events"' in fake.prompt

    def check():
        data = graph.node(target, session_id=sid)
        assert [item["text"] for item in data["actions"]] == ["Reviewed the export spec"]
        assert data["actions"][0]["frames"] == [frame] and data["actions"][0]["at"]
        assert frame in [item["id"] for item in data["frames"]]
        assert event in [item["id"] for item in data["events"]]

    check()
    graph.build(sid)
    check()
    assert graph.node(target)["actions"][0]["text"] == "Reviewed the export spec"
    summary = graph.summary(sid)
    assert {"node": graph.node(target, session_id=sid)["node"]["label"], "text": "Reviewed the export spec", "status": "worked_on", "frames": [frame]} in summary["llm"]["actions"]
    rejected = tuner.check("record_action", {"node": "x", "action": "a", "status": "viewed", "frames": []})
    with pytest.raises(tuner.Rejected):
        tuner.apply_op({"x": {"id": "x", "kind": "task", "label": "x", "props": {}}}, {}, rejected)
    tuner.reset(sid)
    sessions.delete(sid)


def test_each_provider_keeps_its_own_key_and_model(client, monkeypatch):
    monkeypatch.delenv("CHRONICLE_LLM")
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY", "CHRONICLE_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    keys = ("llm_provider", "llm_model", "llm_base_url", *(f"llm_{part}_{item['id']}" for item in llm.CATALOG for part in ("api_key", "model")))
    try:
        for key in keys:
            db.set_setting(key, "")
        client.post("/llm/providers/openai", json={"api_key": "secret-o", "model": "gpt-x"})
        client.post("/llm/providers/gemini", json={"api_key": "secret-g", "model": "gemini-x"})
        status = client.post("/llm/providers/local", json={"model": "llama3.2", "base_url": "http://box:1/v1"}).json()
        assert "secret" not in json.dumps(status)
        rows = {item["id"]: item for item in status["providers"]}
        assert rows["openai"]["key_source"] == "saved" and rows["gemini"]["model"] == "gemini-x" and rows["anthropic"]["has_key"] is False
        assert llm.configured_name() == "openai"

        status = client.post("/llm/providers/gemini", json={"activate": True}).json()
        assert status["provider"] == "gemini" and status["model"] == "gemini-x"
        chosen = llm.provider()
        assert isinstance(chosen, llm.ChatProvider) and chosen.api_key == "secret-g" and "generativelanguage" in chosen.base_url

        client.post("/llm/providers/local", json={"activate": True})
        chosen = llm.provider()
        assert isinstance(chosen, llm.ChatProvider) and chosen.base_url == "http://box:1/v1" and chosen.model == "llama3.2"
        assert llm.PROVIDERS["openai"]().model == "gpt-x"

        client.post("/llm/providers/openai", json={"clear_key": True})
        assert not llm.key_for("openai")
        assert client.post("/llm/test", params={"provider": "openrouter"}).json()["ok"] is False
        assert client.post("/llm/providers/nope", json={}).status_code == 404
    finally:
        for key in keys:
            db.set_setting(key, "")


def test_settings_beat_the_environment_except_off(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-a")
    monkeypatch.setenv("CHRONICLE_LLM", "openai")
    monkeypatch.setenv("CHRONICLE_LLM_MODEL", "gpt-env")
    db.set_setting("llm_provider", "anthropic")
    try:
        assert llm.configured_name() == "anthropic" and llm.model_name() == llm.DEFAULT_MODELS["anthropic"]
        assert not llm.status()["locked_by_env"]
        monkeypatch.setenv("CHRONICLE_LLM", "off")
        assert llm.configured_name() == "off" and llm.status()["locked_by_env"]
    finally:
        db.set_setting("llm_provider", "")

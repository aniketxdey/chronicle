"""LLM defaults and model-written context files."""

from __future__ import annotations

import pytest

from chronicle import contexts, db, llm


class Writer:
    name = "fake"
    model = "fake-writer-1"

    def __init__(self, reply: str | Exception = "## Summary\nThe user was finishing the Acme export (frame 1).\n\n## Open threads\n- Send the export"):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, max_tokens=4000):
        self.calls.append((system, user))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def call_tools(self, system, user, tools):
        return []


@pytest.fixture
def writer():
    def use(item):
        llm.use(item)
        return item

    yield use
    llm.use(None)


@pytest.fixture
def no_env(monkeypatch):
    monkeypatch.setattr(llm, "_loaded_env", True)
    for name in ("CHRONICLE_LLM", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    saved = {key: db.get_setting(key, "") for key in ("llm_provider", "ai_api_key", "ai_base_url")}
    yield monkeypatch
    for key, value in saved.items():
        db.set_setting(key, value or "")


def test_llm_is_on_by_default_when_a_key_is_available(no_env):
    db.set_setting("llm_provider", "")
    db.set_setting("ai_api_key", "")
    assert llm.configured_name() == "off" and not llm.enabled()
    no_env.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.configured_name() == "openai" and llm.enabled()
    assert llm.status()["enabled"] and llm.status()["choice"] == "auto"
    db.set_setting("llm_provider", "off")
    assert not llm.enabled()
    no_env.delenv("OPENAI_API_KEY")
    db.set_setting("llm_provider", "auto")
    db.set_setting("ai_api_key", "sk-saved")
    db.set_setting("ai_base_url", "https://api.openai.com/v1")
    assert llm.provider().api_key == "sk-saved"
    db.set_setting("ai_base_url", "http://localhost:11434/v1")
    assert not llm.enabled()
    no_env.setenv("CHRONICLE_LLM", "off")
    db.set_setting("ai_base_url", "")
    assert not llm.enabled() and llm.status()["locked_by_env"]


def test_context_files_are_written_by_the_model(client, writer):
    fake = writer(Writer())
    found = client.get("/contexts/file", params={"preset": "research"}).json()
    assert found["written_by"] == "fake-writer-1" and found["writer_note"] is None
    markdown = found["markdown"]
    assert markdown.startswith("# Research context · All history")
    assert markdown.index("## Summary") < markdown.index("## Open threads") < markdown.index("## Recorded evidence")
    assert "### Time by app" in markdown
    system, user = fake.calls[0]
    assert "Workflow: Research." in system and "Sources read, with URLs" in system
    assert "Recorded evidence:" in user and "Cursor" in user

    again = client.get("/contexts/export", params={"format": "md", "preset": "research"})
    assert again.text == markdown and len(fake.calls) == 1
    client.get("/contexts/file", params={"preset": "coding"})
    assert len(fake.calls) == 2 and "Workflow: Software development." in fake.calls[1][0]


def test_writer_falls_back_without_a_model(client, writer):
    plain = client.get("/contexts/file", params={"preset": "writing"}).json()
    assert plain["written_by"] is None and plain["writer_note"] is None
    assert "## Recorded evidence" not in plain["markdown"] and "## Time by app" in plain["markdown"]

    writer(Writer(llm.LLMError("HTTP 401: bad key")))
    failed = client.get("/contexts/file", params={"preset": "writing"}).json()
    assert failed["written_by"] is None and failed["writer_note"] == "HTTP 401: bad key"
    assert failed["markdown"] == plain["markdown"]
    assert "HTTP 401" in client.get("/llm/status").json()["last_error"]

    fake = writer(Writer())
    empty = client.post("/sessions", json={"duration": "1m", "label": "empty"}).json()
    client.post(f"/sessions/{empty['id']}/stop")
    found = contexts.file("general", session_id=empty["id"])
    assert found["written_by"] is None and found["writer_note"] == "nothing was recorded in this scope" and not fake.calls


def _billing_429(attempts: list):
    import io
    import urllib.error

    body = b'{"error": {"type": "insufficient_quota", "code": "credit_balance_exhausted", "message": "You have no credits remaining."}}'

    def urlopen(request, timeout):
        attempts.append(request)
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", {}, io.BytesIO(body))

    return urlopen, body


def test_billing_errors_fail_fast_over_http(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "openai", None)
    attempts: list = []
    urlopen, _ = _billing_429(attempts)
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    provider = llm.OpenAIProvider(api_key="sk-test", base_url="https://example.test/v1")
    with pytest.raises(llm.LLMError) as caught:
        provider.complete("system", "user")
    assert caught.value.message == llm.NO_CREDITS and not caught.value.retryable and len(attempts) == 1


def test_billing_errors_fail_fast_with_sdk(monkeypatch):
    pytest.importorskip("openai")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    monkeypatch.setattr("time.sleep", lambda _: None)
    hits: list = []
    _, body = _billing_429([])

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            hits.append(self.path)
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        provider = llm.OpenAIProvider(api_key="sk-test", base_url=f"http://127.0.0.1:{server.server_port}/v1")
        with pytest.raises(llm.LLMError) as caught:
            provider.complete("system", "user")
    finally:
        server.shutdown()
    assert caught.value.message == llm.NO_CREDITS and len(hits) == 1

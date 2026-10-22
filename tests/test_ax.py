from __future__ import annotations

import sys
from pathlib import Path

import pytest

from chronicle import capture, db, probe, search, sessions

FIXTURES = Path(__file__).parent / "fixtures"
SAFARI = (FIXTURES / "axdump_safari.jsonl").read_text()


def test_parse_keeps_order_redacts_and_dedupes():
    text, meta = probe.parse_ax(SAFARI)
    lines = text.splitlines()
    assert lines[0] == "Start Page"
    assert lines.index("Favorites") < lines.index("Crusoe raises funding for physical AI") < lines.index("Forbes AI 50")
    assert "maya@example.com" not in text and "sk-abcdefghijklmnop" not in text
    assert lines.count("Contact [email] with key [key]") == 1
    assert not any("stray stderr" in line for line in lines)
    roles = {element["text"]: element["role"] for element in meta["elements"]}
    assert roles["Start Page"] == "window"
    assert roles["Crusoe raises funding for physical AI"] == "heading"
    assert roles["Contact [email] with key [key]"] == "text"
    assert meta["status"] == "ok" and meta["app"] == "Safari"


def test_parse_enforces_char_budget():
    text, meta = probe.parse_ax(SAFARI, max_chars=30)
    assert len(text) <= 30
    assert text.startswith("Start Page\nShare")


def test_parse_denied_and_garbage():
    text, meta = probe.parse_ax((FIXTURES / "axdump_denied.jsonl").read_text())
    assert text == "" and meta["status"] == "denied" and meta["elements"] == []
    assert probe.parse_ax("") is None
    assert probe.parse_ax("not json\n[1, 2]\n") is None


def test_ax_text_off_macos(monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "linux")
    assert probe.ax_helper() is None
    assert probe.ax_text() is None
    assert probe.ax_trusted() is None
    assert probe.capabilities()["accessibility"] is False


def _fake_helper(monkeypatch, output: str) -> list[list[str]]:
    calls: list[list[str]] = []
    script = f"import sys; sys.stdout.write({output!r})"

    def helper(build: str = "background"):
        return [sys.executable, "-c", script], {"helper": "swift"}

    real_run = probe._run

    def run(args, timeout=3, text=True):
        calls.append(args)
        return real_run(args, timeout=timeout, text=text)

    monkeypatch.setattr(probe, "ax_helper", helper)
    monkeypatch.setattr(probe, "_run", run)
    return calls


def test_ax_text_runs_helper_with_budgets(monkeypatch):
    calls = _fake_helper(monkeypatch, SAFARI)
    text, meta = probe.ax_text(4242, max_nodes=50, max_chars=500, budget=0.5, unlock=False)
    assert calls[0][-5:] == ["50", "500", "0.5", "0", "4242"]
    assert "Forbes AI 50" in text
    assert meta["helper"] == "swift" and meta["elapsed_ms"] >= 0


@pytest.fixture
def recording(monkeypatch):
    monkeypatch.setattr(probe, "frontmost", lambda: probe.Front(app="Safari", window="Start Page"))
    monkeypatch.setattr(probe, "idle_seconds", lambda: 0.0)
    monkeypatch.setattr(probe, "clipboard", lambda: None)
    monkeypatch.setattr(probe, "screenshot", lambda target, max_width=1600: False)
    session = sessions.start("10m", label="ax test", interval_secs=5)
    yield session
    sessions.stop(session["id"])


def _frame(saved: dict) -> dict:
    return search.frame_bundle(saved["frame_id"])


def test_recorder_prefers_ax_text(monkeypatch, recording):
    monkeypatch.setattr(probe, "ax_text", lambda **kw: probe.parse_ax(SAFARI))
    bundle = _frame(capture.Recorder().tick(recording))
    frame = bundle["frame"]
    assert frame["text_engine"] == "ax" and frame["text_source"] == "accessibility"
    assert "Crusoe raises funding for physical AI" in frame["text"]
    assert {"heading", "link"} <= {element["role"] for element in bundle["elements"]}
    assert "ok via swift" in (db.get_setting("last_ax_status") or "")


def test_recorder_falls_back_when_ax_is_sparse_or_stale(monkeypatch, recording):
    sparse = '{"role":"AXWindow","text":"Start Page","depth":0}\n{"meta":{"status":"ok","app":"Safari"}}'
    monkeypatch.setattr(probe, "ax_text", lambda **kw: probe.parse_ax(sparse))
    assert _frame(capture.Recorder().tick(recording))["frame"]["text_engine"] == "none"

    monkeypatch.setattr(probe, "frontmost", lambda: probe.Front(app="Notes", window="Groceries"))
    monkeypatch.setattr(probe, "ax_text", lambda **kw: probe.parse_ax(SAFARI))
    bundle = _frame(capture.Recorder().tick(recording))
    assert bundle["frame"]["text_engine"] == "none" and bundle["elements"] == []


def test_recorder_skips_unlock_for_editors(monkeypatch, recording):
    seen: list[bool] = []
    monkeypatch.setattr(probe, "frontmost", lambda: probe.Front(app="Cursor", window="main.py"))
    monkeypatch.setattr(probe, "ax_text", lambda **kw: seen.append(kw["unlock"]))
    capture.Recorder().tick(recording)
    assert seen == [False]


def test_capture_ax_setting_disables(monkeypatch, recording):
    monkeypatch.setattr(probe, "ax_text", lambda **kw: pytest.fail("AX should be off"))
    db.set_setting("capture_ax", "0")
    try:
        assert _frame(capture.Recorder().tick(recording))["frame"]["text_engine"] == "none"
    finally:
        db.set_setting("capture_ax", "1")

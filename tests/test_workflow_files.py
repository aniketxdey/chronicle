"""Model-written workflow files, the evidence they read, and disconnecting clients."""

from __future__ import annotations

import json

from chronicle import artifacts, clients, contexts, db, seed, sessions, workflows


class Fake:
    name = "fake"
    model = "fake-1"

    def __init__(self, calls):
        self.calls = calls
        self.user = ""

    def call_tools(self, system, user, tools):
        self.user = user
        return self.calls


def _workflow(frames, **extra):
    return {"name": "add_workflow", "arguments": {
        "title": "Apply review comments to a PR", "summary": "Turn review comments into code changes.",
        "trigger": "A review comes in on {pr}.", "outcome": "The comments are answered in code.", "apps": ["Chrome", "Cursor"],
        "inputs": [{"name": "{pr}", "description": "The pull request"}],
        "steps": [
            {"title": "Read the comments", "detail": "Open {pr} in Chrome.", "app": "Chrome", "checks": ["Note open threads"], "confirm": False},
            {"title": "Push the change", "detail": "Edit the file in Cursor and push.", "app": "Cursor", "checks": [], "confirm": True},
        ],
        "guardrails": ["Pushing the change"], "evidence": "PR 882 review", "frames": frames, "times_seen": 2, **extra,
    }}


def _sample():
    seed.load_sample()
    with db.conn() as connection:
        session = connection.execute("SELECT id FROM sessions WHERE label = 'Sample workday'").fetchone()["id"]
        frame = connection.execute("SELECT id FROM frames WHERE session_id = ? ORDER BY id LIMIT 1", (session,)).fetchone()["id"]
    return session, frame


def test_signals_cover_every_kind_of_recording(client):
    seed.load_sample()
    text = contexts.signals()
    for heading in ("## Time by app", "## Pages", "## Screens in time order", "## System events in time order", "## Meeting:"):
        assert heading in text
    assert len(text) <= contexts.SIGNALS_CHARS


def test_generate_keeps_grounded_workflows_and_attaches_a_graph(client):
    session, frame = _sample()
    fake = Fake([_workflow([frame]), _workflow([999999999], title="Made up"), _workflow([frame], title="Too short", steps=[])])
    result = workflows.generate(session_ids=[session], provider=fake)
    assert result["status"] == "ok" and result["created"] == 1 and len(result["rejected"]) == 2
    assert "[recorded data " in fake.user and "# Recording" in fake.user
    made = workflows.get_workflow(result["ids"][0])
    assert made["spec"]["steps"][1]["confirm"] is True and made["graph"]["nodes"]
    assert made["sources"]["session_ids"] == [session]
    text = artifacts.workflow_document(made)
    assert text.startswith("---\nname: \"apply-review-comments-to-a-pr\"") and "1. **Read the comments** (Chrome)" in text
    assert "Ask the person to confirm" in text and "## Knowledge graph" in text and "Seen 2 times" in text
    workflows.delete(made["id"])


def test_a_named_request_writes_one_and_takes_that_name(client):
    session, frame = _sample()
    fake = Fake([_workflow([frame]), _workflow([frame], title="Second")])
    result = workflows.generate("Review loop", "Handle reviews", [session], provider=fake)
    assert result["created"] == 1 and "Handle reviews" in fake.user
    assert fake.user.index("Handle reviews") < fake.user.index("[recorded data ")
    made = workflows.get_workflow(result["ids"][0])
    assert made["title"] == "Review loop"
    workflows.delete(made["id"])


def test_citations_must_come_from_what_was_scanned(client):
    session, frame = _sample()
    other = sessions.start(None, label="Other")["id"]
    sessions.stop(other)
    result = workflows.generate(session_ids=[other], provider=Fake([_workflow([frame])]))
    assert result["created"] == 0 and "frames" in result["rejected"][0]["error"]
    sessions.delete(other)
    assert client.post("/workflows/generate", json={"name": "x"}).status_code == 400


def test_uninstall_json_leaves_the_rest_and_keeps_a_backup(tmp_path):
    target = tmp_path / "mcp.json"
    target.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    spec = clients.CLIENTS["cursor"]
    assert clients._connected(spec, target) is False
    assert clients.uninstall("cursor", path=target)["removed"] is False
    clients.install("cursor", path=target)
    assert clients._connected(spec, target) is True
    result = clients.uninstall("cursor", path=target)
    data = json.loads(target.read_text())
    assert result["removed"] and set(data["mcpServers"]) == {"other"} and data["theme"] == "dark"
    assert clients._connected(spec, target) is False
    assert "chronicle" in json.loads(open(result["backup"]).read())["mcpServers"]


def test_uninstall_toml_keeps_other_servers(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('model = "x"\n\n[mcp_servers.other]\ncommand = "y"\n')
    clients.install("codex", path=target)
    assert clients._connected(clients.CLIENTS["codex"], target) is True
    clients.uninstall("codex", path=target)
    text = target.read_text()
    assert "[mcp_servers.chronicle]" not in text and "[mcp_servers.other]" in text and 'model = "x"' in text


def test_remote_clients_report_unknown_connection():
    item = next(entry for entry in clients.catalog() if entry["id"] == "chatgpt")
    assert item["connected"] is None


def test_claude_code_gets_the_server_name_before_its_env(monkeypatch):
    monkeypatch.setenv("CHRONICLE_HOME", "/tmp/somewhere")
    args = clients._claude_code_args()
    assert args.index("chronicle") < args.index("-e") < args.index("--")

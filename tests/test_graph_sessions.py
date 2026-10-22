"""Recording sessions, knowledge graph, and context packs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chronicle import db, graph
from chronicle.timeutil import utc_iso


def test_graph_extracts_entities(client):
    summary = client.get("/graph/summary").json()
    for kind in ("app", "window", "person", "ticket", "pr", "file", "identifier", "meeting", "site"):
        assert summary["counts"].get(kind, 0) >= 1, kind
    assert summary["transitions"]
    ticket = client.get("/graph/node", params={"id": "ticket:bill-142"}).json()
    assert ticket["node"]["evidence"]
    assert any(item["kind"] == "person" for item in ticket["neighbors"])


def test_people_merge_first_names_with_full_names(client):
    people = client.get("/graph/search", params={"kind": "person"}).json()["data"]
    labels = {item["label"] for item in people}
    assert "Luis Ortega" in labels
    assert "Luis" not in labels


def test_graph_search_and_export(client):
    found = client.get("/graph/search", params={"q": "export"}).json()["data"]
    assert any(item["id"] == "file:export.ts" for item in found)
    exported = client.get("/graph", params={"limit": 40}).json()
    ids = {node["id"] for node in exported["nodes"]}
    assert exported["edges"]
    assert all(edge["src"] in ids and edge["dst"] in ids for edge in exported["edges"])


def test_extract_patterns():
    found = graph.extract(
        "Fix ENG-42 in src/api/routes.py, see https://github.com/acme/app/pull/77 and call build_index() or parseRows. UTF-8 is not a ticket."
    )
    pairs = {(kind, key) for kind, key, _ in found}
    assert ("ticket", "ENG-42") in pairs
    assert ("file", "routes.py") in pairs
    assert ("pr", "77") in pairs
    assert ("identifier", "build_index") in pairs
    assert ("identifier", "parseRows") in pairs
    assert not any(key.startswith("UTF") for _, key, _ in found)


def test_context_pack_has_sections(client):
    pack = client.get("/context", params={"task": "finish the Acme export"}).json()
    assert pack["now"]["frame_id"]
    assert pack["graph"]["nodes"]
    assert pack["evidence"]
    markdown = pack["markdown"]
    for heading in ("## On screen now", "## Recent activity", "## Knowledge graph", "## Evidence for the task"):
        assert heading in markdown
    text = client.get("/context", params={"format": "markdown"}).text
    assert text.startswith("# Computer context")


def test_session_lifecycle_and_scoped_graph(client):
    started = client.post("/sessions", json={"duration": "15m", "label": "expense report"})
    assert started.status_code == 200
    session = started.json()
    assert session["status"] == "recording" and session["remaining_secs"] > 800
    assert client.post("/sessions", json={"duration": "5m"}).status_code == 409
    for text, app, window, url in (
        ("Expenses EXP-9 draft receipts.csv", "Chrome", "Expensify", "https://expensify.com/reports/9"),
        ("Approve EXP-9 for Dana Wells", "Slack", "#finance", None),
    ):
        added = client.post("/add", json={"text": text, "app_name": app, "window_name": window, "browser_url": url, "session_id": session["id"]})
        assert added.status_code == 200
    client.post("/add", json={"content_type": "input", "trigger": "clipboard", "text": "EXP-9 total 412.50", "app_name": "Chrome", "session_id": session["id"]})
    stopped = client.post(f"/sessions/{session['id']}/stop").json()
    assert stopped["status"] == "stopped"
    assert stopped["frames"] == 2
    assert stopped["events"] >= 3
    scoped = client.get("/graph/summary", params={"session_id": session["id"]}).json()
    assert scoped["scope"] == f"session:{session['id']}"
    assert scoped["counts"]["ticket"] == 1
    assert "Acme" not in str(scoped["top"])
    pack = client.get("/context", params={"session_id": session["id"], "task": "expense report"}).json()
    assert "EXP-9" in pack["markdown"]
    assert pack["window"]["session_id"] == session["id"]


def test_session_auto_completes_at_deadline(client):
    session = client.post("/sessions", json={"duration": "1m"}).json()
    with db.conn() as connection:
        connection.execute("UPDATE sessions SET ends_at = ? WHERE id = ?", (utc_iso(datetime.now(timezone.utc) - timedelta(seconds=1)), session["id"]))
    listing = client.get("/sessions").json()["data"]
    finished = next(item for item in listing if item["id"] == session["id"])
    assert finished["status"] == "complete"
    assert client.get("/sessions/active").json()["session"] is None


def test_session_validation_and_delete(client):
    assert client.post("/sessions", json={"duration": "nonsense"}).status_code == 400
    assert client.post("/sessions", json={"duration": "2s"}).status_code == 400
    session = client.post("/sessions", json={"duration": "10m"}).json()
    assert client.delete(f"/sessions/{session['id']}").status_code == 409
    client.post("/sessions/stop")
    deleted = client.delete(f"/sessions/{session['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/sessions/{session['id']}").status_code == 404

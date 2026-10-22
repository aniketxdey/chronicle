"""History API: search, frames, meetings, pipes, retention, UI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chronicle import seed
from chronicle.timeutil import parse_duration, utc_iso


def test_health_and_sample_day(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["frames"] >= 15
    assert body["meetings"] == 2
    assert body["graph_nodes"] > 20
    assert body["mcp"]["stdio"] == "chronicle mcp"


def test_search_audio_and_screen(client):
    audio = client.get("/search", params={"q": "SOC", "content_type": "audio"})
    assert audio.json()["pagination"]["total"] >= 1
    screen = client.get("/search", params={"q": "invoice_id", "app_name": "Cursor"})
    assert screen.json()["pagination"]["total"] >= 1
    ocr = client.get("/search", params={"q": "Columns", "content_type": "ocr"})
    assert ocr.json()["pagination"]["total"] >= 1


def test_old_history_is_searchable(client):
    old = utc_iso(datetime.now(timezone.utc) - timedelta(days=10))
    client.post("/add", json={"text": "ancient unique zebraquartz note", "app_name": "Notes", "timestamp": old})
    found = client.get("/search", params={"q": "zebraquartz"}).json()
    assert found["pagination"]["total"] == 1
    assert "plan_window" not in found


def test_redaction_hides_secrets(client):
    added = client.post("/add", json={"text": "ping ada@example.com about sk-abcdefghij123456", "app_name": "Notes", "window_name": "scratch"})
    frame_id = added.json()["frame_id"]
    text = client.get(f"/frames/{frame_id}").json()["text"]
    assert "[email]" in text and "[key]" in text
    assert "ada@example.com" not in text


def test_frame_context_elements_and_keyword(client):
    listing = client.get("/elements", params={"q": "Mute", "app_name": "Zoom"})
    frame_id = listing.json()["data"][0]["frame_id"]
    bundle = client.get(f"/frames/{frame_id}/context").json()
    assert bundle["elements"]
    assert "events" in bundle
    assert client.get("/search/keyword", params={"q": "settled_at"}).json()["data"]


def test_tags_and_related(client):
    body = client.get("/search", params={"tags": "person:priya", "include_related": "true", "limit": 5}).json()
    assert body["pagination"]["total"] >= 1
    assert "billing" in body["related"]["projects"]


def test_ask_answers_from_history(client):
    answer = client.post("/ask", json={"question": "Where did we land on the Acme export columns?"}).json()
    assert "invoice_id" in answer["answer"] and answer["citations"]
    meetings = client.post("/ask", json={"question": "Summarize my meetings"}).json()
    assert "Friday" in meetings["answer"]


def test_speaker_rename_and_meetings(client):
    unnamed = client.get("/speakers/unnamed").json()["data"]
    renamed = client.post("/speakers/update", json={"id": unnamed[0]["id"], "name": "Acme counsel"})
    assert renamed.json()["name"] == "Acme counsel"
    assert len(client.get("/meetings").json()["data"]) == 2


def test_pipes_run_and_enable_without_caps(client):
    run = client.post("/pipes/daily-standup/run").json()
    assert run["status"] == "success"
    for name in ("workflow-digest", "obsidian-journal", "daily-standup", "time-breakdown"):
        enabled = client.post(f"/pipes/{name}/enable", json={"enabled": True})
        assert enabled.status_code in (200, 404)
    assert client.get("/pipes/daily-standup/logs").json()["executions"]


def test_workflows_and_install(client):
    found = client.get("/workflows").json()["data"]
    assert "standup" in " ".join(item["title"].lower() for item in found)
    created = client.post(f"/workflows/{found[0]['id']}/install-pipe")
    assert created.status_code == 200


def test_raw_sql_is_read_only(client):
    assert client.post("/raw_sql", json={"sql": "SELECT COUNT(*) AS n FROM frames"}).json()["rows"][0]["n"] >= 1
    assert client.post("/raw_sql", json={"sql": "DELETE FROM frames"}).status_code == 400


def test_timeline_and_activity(client):
    # Other tests add frames "now", which can fall on a later local day than the seeded workday.
    seeded = client.get("/timeline", params={"date": seed._day().isoformat()}).json()
    assert len(seeded["blocks"]) >= 10
    assert client.get("/activity-summary").json()["apps"]


def test_retention_deletes_old_rows(client):
    old = utc_iso(datetime.now(timezone.utc) - timedelta(days=5))
    client.post("/add", json={"text": "dropme-token should disappear", "app_name": "Notes", "timestamp": old})
    assert client.get("/search", params={"q": "dropme-token"}).json()["pagination"]["total"] == 1
    # The seeded workday can be up to ~42h old; a 1-day window would delete it for later tests.
    client.post("/retention/configure", json={"days": 3})
    assert client.get("/search", params={"q": "dropme-token"}).json()["pagination"]["total"] == 0
    client.post("/settings", json={"retention_days": 30})


def test_billing_routes_are_gone(client):
    for path in ("/billing", "/connections", "/sync/status", "/handoff.md"):
        assert client.get(path).status_code == 404


def test_settings_roundtrip(client):
    updated = client.post("/settings", json={"capture_clipboard": False, "idle_threshold_secs": 300}).json()
    assert updated["capture_clipboard"] is False
    assert updated["idle_threshold_secs"] == "300"
    client.post("/settings", json={"capture_clipboard": True, "idle_threshold_secs": 120})


def test_ui_is_served(client):
    page = client.get("/")
    assert "text/html" in page.headers["content-type"] and "Chronicle" in page.text
    script = client.get("/static/app.js").text
    assert "/sessions" in script and "/graph" in script and "/clients" in script
    assert "checkout" not in script
    assert client.get("/static/app.css").status_code == 200


def test_parse_duration():
    assert parse_duration("90s") == 90
    assert parse_duration("30m") == 1800
    assert parse_duration("1h30m") == 5400
    assert parse_duration("2 hours") == 7200
    assert parse_duration("15") == 900
    assert parse_duration(None) is None

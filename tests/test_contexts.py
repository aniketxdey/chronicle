import json

from chronicle import contexts


def test_presets_and_overview(client):
    presets = client.get("/contexts/presets").json()
    assert {"general", "data-analysis", "job-applications"} <= {item["id"] for item in presets["data"]}
    assert set(presets["formats"]) == {"md", "json", "graphml", "cypher"}
    body = client.get("/overview").json()
    assert len(body["days"]) == 7 and len(body["daily"]) == 7
    assert body["totals"]["frames"] > 0
    assert isinstance(body["sessions"], list)


def test_generate_save_and_download(client):
    made = client.post("/contexts", json={"task": "finish the Acme export", "preset": "data-analysis"}).json()
    assert made["preset"] == "data-analysis"
    assert made["markdown"].startswith("# Data analysis context")
    assert "Task: finish the Acme export" in made["markdown"]
    assert any(item["id"] == made["id"] for item in client.get("/contexts").json()["data"])

    saved = client.get("/contexts/export", params={"context_id": made["id"]})
    assert saved.status_code == 200
    assert saved.text == made["markdown"]
    assert "attachment" in saved.headers["content-disposition"]

    jobs = client.get("/contexts/export", params={"context_id": made["id"], "preset": "job-applications"})
    assert jobs.text.startswith("# Applying to jobs context")

    payload = json.loads(client.get("/contexts/export", params={"format": "json"}).text)
    assert payload["graph"]["nodes"] and "markdown" in payload["context"]
    graphml = client.get("/contexts/export", params={"format": "graphml"}).text
    assert graphml.startswith("<?xml") and "<node id=" in graphml and "<edge " in graphml
    cypher = client.get("/contexts/export", params={"format": "cypher"}).text
    assert "MERGE (n:Entity" in cypher and "MERGE (a)-[r:" in cypher

    assert client.get("/contexts/export", params={"format": "pdf"}).status_code == 400
    assert client.get("/contexts/export", params={"preset": "nope"}).status_code == 400

    assert client.delete(f"/contexts/{made['id']}").status_code == 200
    assert client.get(f"/contexts/{made['id']}").status_code == 404


def test_detail_has_timeline_events_urls_and_meeting_notes(client):
    body = client.get("/contexts/detail").json()
    assert body["frames"] and body["blocks"] and body["events"]
    assert body["urls"] and all(item["url"] for item in body["urls"])
    assert body["meetings"]
    meeting = body["meetings"][0]
    assert meeting["notes"]["participants"]
    assert meeting["notes_markdown"].startswith("## ")


def test_scoped_search_covers_every_source(client):
    made = client.post("/contexts", json={"task": "Acme export columns"}).json()
    found = client.get("/contexts/search", params={"q": "acme"}).json()
    assert found["nodes"]
    assert found["content"]
    assert any(item["id"] == made["id"] for item in found["contexts"])
    assert client.get("/contexts/search", params={"q": ""}).json()["nodes"] == []


def test_meeting_notes_pick_out_decisions_and_actions():
    meeting = {
        "title": "Sync", "started_at": "2026-01-01T10:00:00+00:00", "ended_at": "2026-01-01T10:30:00+00:00",
        "segments": [
            {"speaker_name": "Maya", "transcription": "We decided to drop the legacy column.", "timestamp": "t1"},
            {"speaker_name": "Sam", "transcription": "I'll send the export by Friday.", "timestamp": "t2"},
            {"speaker_name": "Sam", "transcription": "Nice weather today.", "timestamp": "t3"},
        ],
    }
    info = contexts.notes(meeting)
    assert info["participants"][0] == "Sam"
    assert [item["speaker"] for item in info["decisions"]] == ["Maya"]
    assert [item["text"] for item in info["action_items"]] == ["I'll send the export by Friday."]
    assert info["summary"].startswith("30m")

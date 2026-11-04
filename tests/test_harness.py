"""Harnesses: built from recordings, edited by chat and by hand, and served to agents and as files."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from chronicle import artifacts, contexts, db, harness, mcp_server, seed, sessions, workflows


class Fake:
    name, model = "fake", "fake-1"

    def __init__(self, calls=None, frame=None):
        self.calls = calls or []
        self.frame = frame
        self.users = []

    def call_tools(self, system, user, tools):
        self.users.append(user)
        if tools[0]["name"] == "add_workflow":
            return [_workflow(self.frame)]
        return self.calls

    def complete(self, system, user, max_tokens=4000):
        self.users.append(user)
        return "# Recruiting\n\nWritten by the model."


def _workflow(frame):
    return {"name": "add_workflow", "arguments": {
        "title": "Ship a billing export", "summary": "Ship it.", "trigger": "A customer asks for {export}.", "outcome": "It shipped.", "apps": ["Cursor"],
        "inputs": [{"name": "{export}", "description": "The export"}],
        "steps": [{"title": "Edit", "detail": "Edit it.", "app": "Cursor", "checks": [], "confirm": False}, {"title": "Send", "detail": "Send it.", "app": "Slack", "checks": [], "confirm": True}],
        "guardrails": ["Sending"], "evidence": "Seen once", "frames": [frame], "times_seen": 1,
    }}


@pytest.fixture
def sample(client):
    seed.load_sample()
    with db.conn() as connection:
        session = connection.execute("SELECT id FROM sessions WHERE label = 'Sample workday'").fetchone()["id"]
        frame = connection.execute("SELECT id FROM frames WHERE session_id = ? LIMIT 1", (session,)).fetchone()["id"]
    return session, frame


@pytest.fixture
def built(sample):
    """A harness with one context file, one workflow written for it, and the sample recording's graph."""
    session, frame = sample
    context = contexts.create("export work", "coding", session, title="Shipping data exports")
    item = harness.create("Recruiting", "Hire well.", [session], [context["id"]], [], [{"name": "Ship a billing export", "purpose": "Ship"}], provider=Fake(frame=frame))
    yield item, session, frame
    harness.delete(item["id"])


def test_recordings_get_files_named_for_what_they_show(sample):
    session, _ = sample

    class Planner:
        name, model = "fake", "fake-1"

        def call_tools(self, system, user, tools):
            assert "specific kind of session" in system
            return [{"name": "add_context", "arguments": {"title": "Shipping data exports", "focus": "How exports ship", "kind": "coding"}},
                    {"name": "add_context", "arguments": {"title": "Bad kind", "focus": "x", "kind": "nope"}}]

        def complete(self, system, user, max_tokens=4000):
            return "## Where things stand\nWorking."

    with db.conn() as connection:
        connection.execute("DELETE FROM contexts WHERE session_id = ?", (session,))
    made = contexts.generate_for_recording(session, provider=Planner())
    assert [item["title"] for item in made] == ["Shipping data exports"]
    assert made[0]["markdown"].startswith("# Shipping data exports") and made[0]["graph"]["nodes"]
    assert contexts.generate_for_recording(session, provider=Planner()) == []


def test_without_a_model_recordings_get_files_by_keyword(sample):
    session, _ = sample
    with db.conn() as connection:
        connection.execute("DELETE FROM contexts WHERE session_id = ?", (session,))
    made = contexts.generate_for_recording(session)
    assert made and "general" in {item["preset"] for item in made}
    assert contexts.generate_for_recording(session) == []


class Writer(Fake):
    def complete(self, system, user, max_tokens=4000):
        self.system = system
        self.users.append(user)
        return "## Where things stand\nWorking."


def test_a_context_file_can_draw_on_several_sources(sample):
    session, frame = sample
    other = sessions.start(None, label="Second")["id"]
    sessions.stop(other)
    earlier = contexts.create("export work", "coding", session, title="Earlier notes")
    flow = workflows.get_workflow(workflows.generate(session_ids=[session], provider=Fake(frame=frame))["ids"][0])
    fake = Writer()
    made = contexts.compose("Researching job companies", "Look into employers", [session, other], [earlier["id"]], [flow["id"]], provider=fake)
    user = fake.users[-1]
    assert user.index("Look into employers") < user.index("[recorded data ")
    for part in ("# Recording", "Earlier context file: Earlier notes", "Workflow: Ship a billing export"):
        assert part in user
    assert "by its exact title" in fake.system
    assert made["markdown"].startswith("# Researching job companies\n\n> Look into employers") and made["written_by"] == "fake-1"
    assert made["session_id"] is None and made["sources"]["session_ids"] == [session, other] and made["graph"]["nodes"]
    assert made["preset"] == "general"
    assert contexts._kind_for("Shipping data exports") == "general"
    assert contexts._kind_for("Job applications, resume and cover letters") == "job-applications"
    text = artifacts.context_document(made)
    assert "## Workflows" in text and "Ship a billing export" in text
    without = contexts.compose("No workflows chosen", "", [session], provider=fake)
    assert "## Workflows" not in artifacts.context_document(without)
    workflows.delete(flow["id"])
    sessions.delete(other)


def test_context_composing_is_checked_and_works_without_a_model(sample, client):
    session, _ = sample
    assert client.post("/contexts/compose", json={"name": "", "session_ids": [session]}).status_code == 400
    assert client.post("/contexts/compose", json={"name": "No sources"}).status_code == 400
    assert client.post("/contexts/compose", json={"name": "Missing", "session_ids": [999999]}).status_code == 404
    assert client.post("/contexts/compose", json={"name": "Missing", "context_ids": [999999]}).status_code == 404
    made = client.post("/contexts/compose", json={"name": "Plain file", "description": "A note", "session_ids": [session]}).json()
    assert made["written_by"] is None and made["session_id"] == session and "## Sample workday" in made["markdown"]


def test_a_failed_model_still_leaves_a_file(sample):
    session, _ = sample

    class Broken(Fake):
        def complete(self, system, user, max_tokens=4000):
            raise RuntimeError("no network")

    made = contexts.compose("Fallback", "", [session], provider=Broken())
    assert made["written_by"] is None and "no network" in made["writer_note"] and "## Sample workday" in made["markdown"]


def test_build_copies_files_writes_root_and_new_workflows(built):
    item, session, _ = built
    paths = [entry["path"] for entry in item["files"]]
    assert paths[0] == "AGENTS.md" and "contexts/shipping-data-exports.md" in paths and "workflows/ship-a-billing-export.md" in paths
    assert item["written_by"] == "fake-1" and item["files"][0]["content"].startswith("# Recruiting") and item["session_ids"] == [session]
    assert item["not_written"] == []
    listed = next(entry for entry in harness.list_harnesses() if entry["id"] == item["id"])
    assert (listed["contexts"], listed["workflows"]) == (1, 1)


def test_build_says_when_a_workflow_cant_be_written(sample):
    session, _ = sample

    class Nothing(Fake):
        def call_tools(self, system, user, tools):
            return []

    item = harness.create("Ops", "", [session], [], [], [{"name": "Land on the moon", "purpose": "x"}], provider=Nothing())
    assert item["not_written"] == ["Land on the moon"]
    harness.delete(item["id"])
    with pytest.raises(db.ChronicleError):
        harness.create("", "", [session])
    with pytest.raises(db.ChronicleError):
        harness.create("Empty")


def test_design_keeps_only_real_files(sample):
    session, _ = sample
    context = contexts.create("x", "coding", session, title="Designing")
    fake = Fake([{"name": "design_harness", "arguments": {"context_ids": [context["id"], 999999], "workflow_ids": [999999],
                                                          "missing_workflows": [{"name": "Do a thing", "purpose": "Why"}, {"name": " ", "purpose": ""}]}}])
    found = harness.suggest("Recruiting", "Hire well", [session], provider=fake)
    assert found["context_ids"] == [context["id"]] and found["workflow_ids"] == []
    assert found["missing_workflows"] == [{"name": "Do a thing", "purpose": "Why"}]
    assert fake.users[0].index("Hire well") < fake.users[0].index("[recorded data ")
    plain = harness.suggest("Recruiting", "Hire well", [session])
    assert context["id"] in plain["context_ids"] and plain["model"] is None


def test_harness_files_are_copies(built):
    item, _, _ = built
    source = next(entry for entry in item["files"] if entry["kind"] == "context")
    harness.save_file(item["id"], source["path"], "# Changed\n")
    assert contexts.get(source["source_id"])["markdown"] != "# Changed\n"
    assert next(entry for entry in harness.get(item["id"])["files"] if entry["path"] == source["path"])["title"] == "Changed"


def test_chat_edits_files_and_can_be_undone(built):
    item, _, frame = built
    fake = Fake([
        {"name": "reply", "arguments": {"text": "Tightened it."}},
        {"name": "replace_in_file", "arguments": {"path": "AGENTS.md", "old_text": "Written by the model.", "new_text": "Sharper."}},
        {"name": "write_file", "arguments": {"path": "contexts/sourcing.md", "content": "# Sourcing candidates\n"}},
        {"name": "write_file", "arguments": {"path": "../etc/passwd", "content": "x"}},
        {"name": "replace_in_file", "arguments": {"path": "AGENTS.md", "old_text": "not there", "new_text": "y"}},
        {"name": "delete_file", "arguments": {"path": "AGENTS.md"}},
        {"name": "generate_workflow", "arguments": {"name": "Ship a billing export", "purpose": "Ship"}},
    ], frame=frame)
    result = harness.chat(item["id"], "Make it sharper", provider=fake)
    assert result["reply"] == "Tightened it."
    assert [edit["action"] for edit in result["edits"]] == ["edited", "added", "failed", "failed", "failed", "added"]
    after = result["harness"]
    assert "Sharper." in after["files"][0]["content"]
    assert any(entry["path"] == "contexts/sourcing.md" and entry["title"] == "Sourcing candidates" for entry in after["files"])
    assert any(entry["path"] == "workflows/ship-a-billing-export-2.md" for entry in after["files"])
    assert [turn["role"] for turn in after["messages"]] == ["user", "assistant"]
    assert fake.users[-1 if False else 0].index("Make it sharper") < fake.users[0].index("[recorded data ")

    restored = harness.undo(item["id"])
    assert "Written by the model." in restored["files"][0]["content"]
    assert not any(entry["path"] == "contexts/sourcing.md" for entry in restored["files"])
    assert not any(entry["path"].endswith("-2.md") for entry in restored["files"])
    assert restored["messages"][-1]["role"] == "system"


def test_chat_reports_a_model_failure(built):
    item, _, _ = built

    class Broken(Fake):
        def call_tools(self, system, user, tools):
            raise RuntimeError("no network")

    result = harness.chat(item["id"], "Hi", provider=Broken())
    assert result["status"] == "error" and "no network" in result["reply"]
    with pytest.raises(db.ChronicleError):
        harness.chat(item["id"], "Hi")


def test_new_and_deleted_files_and_paths(built, client):
    item, _, _ = built
    made = client.post(f"/harnesses/{item['id']}/file", json={"kind": "workflow", "title": "Tailor a resume"}).json()
    assert made["path"] == "workflows/tailor-a-resume.md"
    assert client.delete(f"/harnesses/{item['id']}/file", params={"path": made["path"]}).status_code == 200
    assert client.delete(f"/harnesses/{item['id']}/file", params={"path": "AGENTS.md"}).status_code == 400
    assert client.put(f"/harnesses/{item['id']}/file", json={"path": "../x.md", "content": "x"}).status_code == 400
    assert client.post(f"/harnesses/{item['id']}/undo").status_code == 200


def test_engine_merges_recordings_and_searches_events(built, client):
    item, session, _ = built
    data = client.get(f"/harnesses/{item['id']}/engine").json()
    assert data["session_ids"] == [session] and data["nodes"] and data["edges"]
    found = client.get(f"/harnesses/{item['id']}/engine/search", params={"q": "export"}).json()
    assert found["nodes"] and found["events"]
    assert client.put(f"/harnesses/{item['id']}", json={"session_ids": []}).json()["session_ids"] == []
    assert client.get(f"/harnesses/{item['id']}/engine").json()["nodes"] == []
    client.put(f"/harnesses/{item['id']}", json={"session_ids": [session]})


def test_export_ships_all_three_parts(built, client):
    item, _, _ = built
    response = client.get(f"/harnesses/{item['id']}/export")
    assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    for name in ("harness.json", "AGENTS.md", "contexts/shipping-data-exports.md", "contexts/shipping-data-exports.graph.json",
                 "workflows/ship-a-billing-export.md", "graph/engine.graph.json", "graph/engine.graphml", "graph/events.jsonl"):
        assert f"recruiting/{name}" in names
    assert json.loads(archive.read("recruiting/harness.json"))["name"] == "Recruiting"
    assert "## Knowledge graph" in archive.read("recruiting/contexts/shipping-data-exports.md").decode()
    assert json.loads(archive.read("recruiting/graph/events.jsonl").decode().splitlines()[0])["event_type"]
    one = client.get(f"/harnesses/{item['id']}/export", params={"format": "md"}).text
    assert one.startswith("# Recruiting") and "# Knowledge graph engine" in one and "## Steps" in one
    assert client.get(f"/harnesses/{item['id']}/export", params={"format": "pdf"}).status_code == 400


def test_agents_reach_it_over_mcp(built):
    item, _, _ = built
    listed = json.loads(mcp_server.call_tool("list_harnesses", {})[0]["text"])
    assert any(entry["id"] == item["id"] for entry in listed)
    first = mcp_server.call_tool("get_harness", {"id": item["id"]})[0]["text"]
    assert first.startswith("# Recruiting") and "workflows/ship-a-billing-export.md" in first
    assert "## Steps" in mcp_server.call_tool("read_harness_file", {"id": item["id"], "path": "workflows/ship-a-billing-export.md"})[0]["text"]
    assert json.loads(mcp_server.call_tool("search_harness_graph", {"id": item["id"], "query": "export"})[0]["text"])["nodes"]
    assert mcp_server.read_resource(f"chronicle://harness/{item['id']}")["text"].startswith("# Recruiting")


def test_single_files_still_work_alone(built):
    item, _, _ = built
    entry = next(entry for entry in item["files"] if entry["kind"] == "context")
    assert "## Knowledge graph" in mcp_server.call_tool("get_context_file", {"id": entry["source_id"]})[0]["text"]
    flow = next(entry for entry in item["files"] if entry["kind"] == "workflow")
    assert "## Steps" in mcp_server.call_tool("get_workflow", {"id": flow["source_id"]})[0]["text"]
    assert "Entities:" in artifacts.graph_download("workflow", flow["source_id"], "md")[2]


def test_deleting_a_harness_removes_its_files(sample, client):
    session, _ = sample
    item = harness.create("Short", "", [session])
    assert client.delete(f"/harnesses/{item['id']}").status_code == 200
    assert client.get(f"/harnesses/{item['id']}").status_code == 404
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM harness_files WHERE harness_id = ?", (item["id"],)).fetchone()["n"] == 0


def test_deleting_a_recording_deletes_its_context_files(client):
    other = sessions.start(None, label="Short")["id"]
    sessions.stop(other)
    made = contexts.create(preset_id="general", session_id=other)
    sessions.delete(other)
    assert client.get(f"/contexts/{made['id']}").status_code == 404


def test_chat_answers_from_one_recording(sample, client):
    session, _ = sample
    answer = client.post("/ask", json={"question": "Acme export", "session_id": session}).json()
    assert answer["mode"] == "local" and answer["answer"]
    assert client.post("/ask", json={"question": "zzzqqq", "session_id": session}).json()["answer"].startswith("Nothing")
    assert client.get("/sessions/999999/artifacts").status_code == 404

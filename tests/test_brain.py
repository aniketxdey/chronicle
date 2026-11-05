"""The central brain: answers over all history, and proposals the page can really build."""

from __future__ import annotations

import pytest

from chronicle import brain, db, seed


class Fake:
    name, model = "fake", "fake-1"

    def __init__(self, calls):
        self.calls = calls
        self.user = ""

    def call_tools(self, system, user, tools):
        self.user = user
        return self.calls


@pytest.fixture
def session(client):
    seed.load_sample()
    with db.conn() as connection:
        return connection.execute("SELECT id FROM sessions WHERE label = 'Sample workday'").fetchone()["id"]


def test_answers_from_the_marked_graph_and_names_its_nodes(session):
    fake = Fake([{"name": "reply", "arguments": {"text": "You worked on BILL-142 (frame 3)."}}])
    found = brain.answer("What did I do on billing?", provider=fake)
    assert "[recorded data" in fake.user and "The person says: What did I do on billing?" in fake.user
    assert found["mode"] == "model" and found["proposals"] == []
    assert found["citations"][0]["frame_id"] == 3
    assert any("BILL-142" in node["label"] for node in found["nodes"])


def test_proposals_keep_only_real_ids_and_are_not_built(session):
    fake = Fake([
        {"name": "reply", "arguments": {"text": "Here's a harness."}},
        {"name": "propose_harness", "arguments": {"name": "Billing", "purpose": "Ship exports", "session_ids": [session, 99999], "context_ids": [99999],
                                                  "workflow_ids": [], "new_workflows": [{"name": "Ship an export", "purpose": "x"}, {"name": " "}]}},
    ])
    item = brain.answer("Build a harness for billing", provider=fake)["proposals"][0]
    assert (item["kind"], item["name"], item["description"]) == ("harness", "Billing", "Ship exports")
    assert item["session_ids"] == [session] and item["context_ids"] == []
    assert [flow["name"] for flow in item["new_workflows"]] == ["Ship an export"]
    with db.conn() as connection:
        assert not connection.execute("SELECT 1 FROM harnesses WHERE name = 'Billing'").fetchone()


def test_a_proposal_without_sources_reads_every_recording(session):
    fake = Fake([{"name": "propose_workflow", "arguments": {"name": "Ship", "purpose": "", "session_ids": [], "context_ids": [12345]}}])
    item = brain.answer("Write a workflow for shipping", provider=fake)["proposals"][0]
    assert session in item["session_ids"] and item["context_ids"] == []


def test_a_proposal_with_no_name_or_unknown_tool_is_dropped(session):
    fake = Fake([{"name": "propose_context", "arguments": {"name": " "}}, {"name": "delete_everything", "arguments": {"name": "x"}}])
    found = brain.answer("Write one", provider=fake)
    assert found["proposals"] == [] and found["answer"]


def test_a_failed_model_falls_back_to_search(session):
    class Broken(Fake):
        def call_tools(self, system, user, tools):
            raise RuntimeError("no quota")

    found = brain.answer("BILL-142", provider=Broken([]))
    assert found["mode"] == "local" and "no quota" in found["answer"] and found["proposals"] == []


def test_the_route_works_without_a_model(client, session):
    response = client.post("/brain/ask", json={"question": "BILL-142"})
    assert response.status_code == 200
    assert response.json()["mode"] == "local" and response.json()["proposals"] == []
    assert client.post("/brain/ask", json={"question": " "}).status_code == 400


def test_the_page_is_called_knowledge(client):
    script = client.get("/static/app.js").text
    assert 'knowledge: "Knowledge"' in script and "/brain/ask" in script and 'recordings: "Recordings"' not in script


class Suggesting:
    name, model = "fake", "fake-1"

    def __init__(self, tool, options):
        self.tool, self.options, self.count, self.user = tool, options, 0, ""

    def call_tools(self, system, user, tools):
        self.count += 1
        self.user = user
        assert tools[0]["name"] == self.tool
        return [{"name": self.tool, "arguments": {"options": self.options}}]


def _forget():
    for key in ("brain_harness_ideas", "brain_fields_context", "brain_fields_workflow"):
        db.set_setting(key, "")


def test_harness_ideas_come_from_the_graph_and_are_remembered(session):
    _forget()
    fake = Suggesting("suggest_harnesses", [{"name": "Billing engineering", "purpose": "Ship exports."}, {"name": " ", "purpose": ""}])
    found = brain.harness_ideas(provider=fake)
    assert found["options"] == [{"name": "Billing engineering", "purpose": "Ship exports."}] and found["model"] == "fake-1"
    assert "[recorded data" in fake.user and "Tasks:" in fake.user and "Main topics:" in fake.user
    assert brain.harness_ideas(provider=fake)["options"] == found["options"] and fake.count == 1


def test_harness_ideas_without_a_model_use_the_biggest_topics(client, session):
    found = client.get("/brain/harness-ideas").json()
    assert found["model"] is None and found["options"] and all(item["name"] for item in found["options"])


def test_field_suggestions_keep_only_real_sources_and_what_was_typed(session):
    _forget()
    fake = Suggesting("suggest_fields", [{"name": "Other", "description": "Other words", "session_ids": [session, 99999], "context_ids": [99999], "workflow_ids": []}])
    item = brain.suggest_fields("context", name="My name", provider=fake)["options"][0]
    assert item["name"] == "My name" and item["description"] == "Other words"
    assert item["session_ids"] == [session] and item["context_ids"] == []
    assert "My name" in fake.user


def test_blank_field_suggestions_are_remembered_but_typed_ones_are_not(session):
    _forget()
    fake = Suggesting("suggest_fields", [{"name": "Shipping exports", "description": "How", "session_ids": [], "context_ids": [], "workflow_ids": [1]}])
    first = brain.suggest_fields("workflow", provider=fake)
    assert first["options"][0]["session_ids"] and first["options"][0]["workflow_ids"] == []
    brain.suggest_fields("workflow", provider=fake)
    assert fake.count == 1
    brain.suggest_fields("workflow", description="typed", provider=fake)
    assert fake.count == 2


def test_failed_suggestions_fall_back_and_bad_kinds_are_refused(client, session):
    _forget()

    class Broken(Suggesting):
        def call_tools(self, system, user, tools):
            raise RuntimeError("no quota")

    found = brain.suggest_fields("context", provider=Broken("x", []))
    assert found["error"] == "RuntimeError: no quota" and found["model"] is None
    assert client.post("/brain/suggest", json={"kind": "harness"}).status_code == 400
    assert client.post("/brain/suggest", json={"kind": "context"}).status_code == 200


def test_field_suggestions_skip_what_already_exists(session):
    _forget()
    from chronicle import contexts

    made = contexts.compose("Already here", "", [session])
    fake = Suggesting("suggest_fields", [{"name": "already HERE", "description": "", "session_ids": [], "context_ids": [], "workflow_ids": []},
                                         {"name": "Something new", "description": "", "session_ids": [], "context_ids": [], "workflow_ids": []}])
    assert [item["name"] for item in brain.suggest_fields("context", provider=fake)["options"]] == ["Something new"]
    contexts.delete(made["id"])


def test_chats_are_saved_reopened_and_deleted(client):
    turns = [{"role": "user", "text": "What was BILL-142?"}, {"role": "assistant", "text": "A ticket.", "proposals": [{"kind": "context", "name": "X", "status": "done"}]}]
    made = client.post("/brain/chats", json={"turns": turns}).json()
    assert made["title"] == "What was BILL-142?"
    longer = [*turns, {"role": "user", "text": "And then?"}]
    assert client.post("/brain/chats", json={"id": made["id"], "turns": longer}).json()["id"] == made["id"]
    assert client.get(f"/brain/chats/{made['id']}").json()["turns"] == longer
    assert made["id"] in [item["id"] for item in client.get("/brain/chats").json()["data"]]
    client.delete(f"/brain/chats/{made['id']}")
    assert client.get(f"/brain/chats/{made['id']}").status_code == 404
    assert client.post("/brain/chats", json={"turns": []}).status_code == 400


def test_chats_are_kept_apart_by_scope(client):
    made = client.post("/brain/chats", json={"scope": "7", "turns": [{"role": "user", "text": "Only here"}]}).json()
    assert [item["id"] for item in client.get("/brain/chats", params={"scope": "7"}).json()["data"]] == [made["id"]]
    assert made["id"] not in [item["id"] for item in client.get("/brain/chats").json()["data"]]
    client.delete(f"/brain/chats/{made['id']}")

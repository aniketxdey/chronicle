"""Privacy & sources: every source is listed, imports reach the graph, and forgetting removes them. The legend edits every reader."""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest

from chronicle import clusters, db, graph, itemgraph, legend, seed
from chronicle.sources import base


@pytest.fixture(autouse=True)
def clean(client):
    seed.load_sample()
    with db.conn() as connection:
        connection.execute("DELETE FROM graph_categories")
        connection.execute("DELETE FROM node_overrides")
    db.set_setting("work_cluster_names", "")
    db.set_setting("work_cluster_user_names", "")
    yield
    base.disconnect("windows-mail", forget=True)
    base.disconnect("chatgpt-export", forget=True)
    with db.conn() as connection:
        connection.execute("DELETE FROM graph_categories")
        connection.execute("DELETE FROM node_overrides")


EML = b"""From: Dana Rivers <dana@example.com>
To: Me <me@example.com>
Subject: Quarterly hiring plan
Date: Tue, 03 Nov 2026 10:00:00 +0000
Message-ID: <abc@example.com>
Content-Type: text/plain; charset=utf-8

Let's review the Zephyr hiring plan on Friday.
"""


def test_every_source_except_gmail_and_drive_is_listed(client):
    found = client.get("/sources").json()
    ids = {item["id"] for item in found["data"]}
    assert {"screen", "browser-history", "apple-mail", "windows-mail", "apple-calendar", "imessage", "whatsapp", "local-folders",
            "chatgpt-export", "claude-export", "gemini-takeout", "claude-code", "codex", "gemini-cli", "cursor",
            "google-calendar", "outlook", "outlook-calendar", "slack", "notion", "ai-extension"} == ids
    assert "privacy" in found and {"gmail", "google-drive"}.isdisjoint(ids)
    assert not any(item["enabled"] for item in found["data"] if item["id"] != "screen")


def test_cloud_sources_without_a_client_id_say_so_and_cannot_connect(client):
    slack = next(item for item in client.get("/sources").json()["data"] if item["id"] == "slack")
    assert slack["status"] == "unavailable" and slack["connectable"] is False


def _node_labels():
    return [node["label"] for node in graph.find(None, kind="person", limit=100)]


def test_an_imported_mail_file_reaches_the_graph_and_forget_removes_it(client):
    response = client.post("/sources/windows-mail/import?filename=mail.eml", content=EML)
    assert response.status_code == 200 and response.json()["added"] == 1
    assert "Dana Rivers" in _node_labels()
    assert base.search_items("Zephyr hiring")[0]["title"] == "Quarterly hiring plan"
    assert client.post("/sources/windows-mail/import?filename=mail.eml", content=EML).json()["added"] == 0
    client.post("/sources/windows-mail/disconnect", json={"forget": True})
    assert "Dana Rivers" not in _node_labels() and base.search_items("Zephyr hiring") == []


def test_a_zip_of_mail_imports_and_other_files_are_refused(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("box/one.eml", EML)
    assert client.post("/sources/windows-mail/import?filename=m.zip", content=buffer.getvalue()).json()["added"] == 1
    assert client.post("/sources/windows-mail/import?filename=m.txt", content=b"nope").status_code == 400


def test_a_deleted_time_range_removes_items_from_the_graph():
    base.store(base.get("windows-mail"), [base.Item(kind="email", ext_id="x", ts="2026-01-02T10:00:00+00:00", title="Old", text="old",
                                                    actors=[{"name": "Pat Old"}])])
    itemgraph.refresh()
    assert "Pat Old" in _node_labels()
    assert itemgraph.forget_range("2026-01-01T00:00:00+00:00", "2026-01-03T00:00:00+00:00") == 1
    assert "Pat Old" not in _node_labels()


def test_the_default_legend_and_every_reader_show_categories(client):
    labels = [item["label"] for item in client.get("/legend").json()["categories"]]
    assert labels == ["People", "Apps", "Websites", "Files", "Other"]
    person = graph.find(None, kind="person", limit=1)[0]
    assert person["category_label"] == "People" and person["color"].startswith("#")
    node = client.get("/graph?limit=500").json()["nodes"][0]
    assert {"category", "category_label", "color"} <= node.keys()


def test_editing_the_legend_changes_every_graph_reader_and_survives_a_rebuild(client):
    items = client.get("/legend").json()["categories"]
    items[0]["label"] = "Humans"
    items.append({"label": "Tickets", "color": "#ff3d14", "kinds": ["ticket", "pr"]})
    saved = client.put("/legend", json={"categories": items}).json()
    assert [item["label"] for item in saved["categories"]][-2:] == ["Other", "Tickets"] or "Tickets" in [item["label"] for item in saved["categories"]]
    assert graph.find("BILL-142", kind="ticket", limit=1)[0]["category_label"] == "Tickets"
    assert graph.find(None, kind="person", limit=1)[0]["category_label"] == "Humans"
    graph.aggregate(full=True)
    assert graph.find("BILL-142", kind="ticket", limit=1)[0]["category_label"] == "Tickets"
    assert json.dumps(client.get("/graph/node", params={"id": "ticket:bill-142"}).json()).count("Tickets")


def test_a_node_can_be_renamed_and_moved_and_put_back(client):
    client.put("/graph/node-override", json={"id": "ticket:bill-142", "label": "Billing ticket", "category": "files"})
    item = graph.node("ticket:bill-142")["node"]
    assert item["label"] == "Billing ticket" and item["category_label"] == "Files"
    assert client.get("/legend").json()["overrides"]["ticket:bill-142"]["category"] == "files"
    client.put("/graph/node-override", json={"id": "ticket:bill-142"})
    assert graph.node("ticket:bill-142")["node"]["label"] == "BILL-142"


def test_removing_a_category_sends_its_nodes_to_other_and_other_cannot_be_removed(client):
    items = [item for item in client.get("/legend").json()["categories"] if item["id"] not in {"people", "other"}]
    client.put("/graph/node-override", json={"id": "ticket:bill-142", "category": "people"})
    saved = client.put("/legend", json={"categories": items}).json()
    assert "other" in [item["id"] for item in saved["categories"]] and "ticket:bill-142" not in saved["overrides"]
    assert graph.find(None, kind="person", limit=1)[0]["category"] == "other"


def test_a_bad_legend_is_refused(client):
    assert client.put("/legend", json={"categories": [{"label": " ", "color": "#ffffff"}]}).status_code == 400
    assert client.put("/legend", json={"categories": [{"label": "A", "color": "red"}]}).status_code == 400
    assert client.put("/graph/node-override", json={"id": "x", "category": "nope"}).status_code == 400


class Namer:
    name, model = "fake", "fake-1"

    def __init__(self):
        self.user = ""

    def complete(self, system, user, max_tokens=600):
        self.user = user
        ids = re.findall(r'"id": "(work:[^"]+)"', user)
        return json.dumps({"names": [{"id": ident, "title": f"Area {index}"} for index, ident in enumerate(ids)]})


def _reset_clusters():
    with db.conn() as connection:
        connection.execute("UPDATE node_overrides SET cluster = NULL")
    db.set_setting(clusters.MODEL_NAMES, "")
    db.set_setting(clusters.USER_NAMES, "")


def test_clusters_cover_the_graph_and_the_model_names_them():
    _reset_clusters()
    first = clusters.overview()
    assert first["clusters"] and all(item["source"] == "auto" and item["label"] for item in first["clusters"])
    assert all(node["id"] in first["members"] for node in graph.export(None, 1000)["nodes"])
    fake = Namer()
    result = clusters.name_all(provider=fake)
    assert result["status"] == "ok" and result["named"] and "[recorded data" in fake.user
    named = clusters.overview()["clusters"]
    assert any(item["label"].startswith("Area") and item["source"] == "model" for item in named)
    assert clusters.name_all(provider=fake)["named"] == 0


def test_a_person_can_rename_a_group_and_the_model_wont_undo_it(client):
    _reset_clusters()
    target = clusters.overview()["clusters"][0]["id"]
    found = client.put("/graph/clusters/name", json={"id": target, "name": "  Billing   work "}).json()
    assert next(item for item in found["clusters"] if item["id"] == target) | {"count": 0} == {"id": target, "label": "Billing work", "source": "user", "count": 0}
    clusters.name_all(provider=Namer())
    assert next(item for item in clusters.overview()["clusters"] if item["id"] == target)["label"] == "Billing work"
    client.put("/graph/clusters/name", json={"id": target, "name": " "})
    assert next(item for item in clusters.overview()["clusters"] if item["id"] == target)["source"] != "user"


def test_a_moved_node_stays_after_a_rebuild_and_can_go_back(client):
    _reset_clusters()
    state = clusters.overview()
    if len(state["clusters"]) < 2:
        pytest.skip("the sample needs two groups")
    target = next(item["id"] for item in state["clusters"] if item["id"] != state["members"]["app:cursor"])
    moved = client.put("/graph/node-cluster", json={"id": "app:cursor", "cluster": target}).json()
    now = moved["members"]["app:cursor"]
    assert now != state["members"]["app:cursor"]
    graph.aggregate(full=True)
    assert clusters.overview()["members"]["app:cursor"] == now
    assert client.put("/graph/node-cluster", json={"id": "app:cursor", "cluster": "work:nope"}).status_code == 404
    client.put("/graph/node-cluster", json={"id": "app:cursor"})
    assert clusters.overview()["members"]["app:cursor"] == state["members"]["app:cursor"]


def test_moving_a_groups_hub_keeps_its_name():
    _reset_clusters()
    state = clusters.overview()
    big = next((item for item in state["clusters"] if item["id"].startswith("work:") and item["id"] != clusters.OTHER and item["count"] > 3), None)
    others = [item["id"] for item in state["clusters"] if big and item["id"] != big["id"]]
    if not big or not others:
        pytest.skip("the sample needs two groups")
    clusters.rename(big["id"], "Keep me")
    hub = big["id"].removeprefix("work:")
    after = clusters.move(hub, others[0])
    assert "Keep me" in [item["label"] for item in after["clusters"]]
    assert after["members"][hub] == others[0] or after["members"][hub] != big["id"]


def test_resetting_a_nodes_name_keeps_its_group(client):
    _reset_clusters()
    state = clusters.overview()
    other = next((item["id"] for item in state["clusters"] if item["id"] != state["members"]["app:cursor"]), None)
    if not other:
        pytest.skip("the sample needs two groups")
    client.put("/graph/node-cluster", json={"id": "app:cursor", "cluster": other})
    client.put("/graph/node-override", json={"id": "app:cursor", "label": "Editor"})
    client.put("/graph/node-override", json={"id": "app:cursor"})
    assert clusters.overview()["members"]["app:cursor"] == other
    client.put("/graph/node-cluster", json={"id": "app:cursor"})

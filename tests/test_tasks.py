"""Task segmentation: TextTiling boundaries, idle splits, min-length merges, topic clusters."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from chronicle import graph, tasks
from chronicle.mcp_server import handle

T0 = datetime(2026, 10, 21, 20, 0, tzinfo=timezone.utc)

CODING = [
    ("Cursor", "graph.py — chronicle", None, "def build_graph(scope): kg_nodes kg_edges extract entities _Builder evidence"),
    ("Cursor", "graph.py — chronicle", None, "graph builder kg_nodes scope session evidence co_occurs edges"),
    ("Terminal", "pytest — chronicle", None, "pytest tests/test_graph_sessions.py kg_nodes graph build passed"),
]
RESEARCH = [
    ("Arc", "forbes ai 50 crusoe - Google Search", "https://www.google.com/search?q=forbes+ai+50+crusoe", "Forbes AI 50 Crusoe physical AI startups"),
    ("Arc", "Forbes AI 50 List", "https://www.forbes.com/lists/ai50/", "Forbes AI 50 Crusoe Anthropic physical AI startups ranking"),
    ("Arc", "Crusoe | Company Overview", "https://www.forbes.com/companies/crusoe/", "Crusoe Forbes AI 50 data centers physical AI"),
]
CHAT = [("Slack", "#random", None, "lunch order tacos burritos friday")]


def timeline(*blocks: tuple[list[tuple], float], step: float = 10, start: datetime = T0) -> list[tasks.Item]:
    """Blocks of (scenes, seconds); a scenes list of None is an idle gap."""
    items, clock = [], start
    for scenes, seconds in blocks:
        if scenes is None:
            clock += timedelta(seconds=seconds)
            continue
        for index in range(int(seconds // step)):
            app, window, url, text = scenes[index % len(scenes)]
            items.append(tasks.Item(start=clock, seconds=step, app=app, window=window, url=url, text=text,
                                    ref={"type": "frame", "id": len(items) + 1, "timestamp": clock.isoformat()}))
            clock += timedelta(seconds=step)
    return items


def offset(task: tasks.Task) -> float:
    return (task.start - T0).total_seconds()


def test_topic_change_places_boundary():
    found = tasks.segment(timeline((CODING, 240), (RESEARCH, 240)))
    assert len(found) == 2
    assert abs(offset(found[1]) - 240) <= tasks.SLICE_SECS
    assert found[0].verb == "coding" and found[1].verb == "researching"
    assert found[1].label == "researching Forbes AI 50 · Crusoe"
    assert found[0].cluster != found[1].cluster


def test_idle_gap_always_splits():
    joined = tasks.segment(timeline((CODING, 180), (CODING, 180)))
    assert len(joined) == 1
    split = tasks.segment(timeline((CODING, 180), (None, 300), (CODING, 180)))
    assert len(split) == 2
    assert offset(split[1]) == 480
    assert split[0].cluster == split[1].cluster
    assert split[0].duration == 180


def test_short_segments_merge_into_most_similar_neighbor():
    trailing = tasks.segment(timeline((CODING, 240), (CHAT, 30)))
    assert len(trailing) == 1 and trailing[0].duration == 270
    interrupted = tasks.segment(timeline((CODING, 180), (CHAT, 20), (CODING, 180)))
    assert len(interrupted) == 1
    assert interrupted[0].verb == "coding"
    assert len(interrupted[0].items) == 38


def test_non_contiguous_segments_share_a_topic_cluster():
    found = tasks.segment(timeline((CODING, 180), (RESEARCH, 180), (CODING, 180)))
    assert len(found) == 3
    assert [task.cluster for task in found] == [1, 2, 1]
    assert found[0].topic == found[2].topic
    assert "researching" in found[1].label


def test_pure_python_fallbacks_match(monkeypatch):
    items = timeline((CODING, 180), (RESEARCH, 180), (CODING, 180))
    surface: dict = {}
    docs = [tasks._terms(item, surface) for item in items]
    fast = tasks._tfidf(docs)
    expected = [task.cluster for task in tasks.segment(items)]
    monkeypatch.setattr(tasks, "DictVectorizer", None)
    monkeypatch.setattr(tasks, "linkage", None)
    slow = tasks._tfidf(docs)
    for left, right in zip(fast, slow):
        assert left.keys() == right.keys()
        assert all(abs(left[k] - right[k]) < 1e-9 for k in left)
    assert [task.cluster for task in tasks.segment(items)] == expected


def test_phrases_chain_bigrams_and_keep_casing():
    surface = {"forbes": Counter({"Forbes": 2}), "ai": Counter({"AI": 2, "ai": 1}), "50": Counter({"50": 3}),
               "crusoe": Counter({"Crusoe": 1})}
    vector = {"forbes": 0.5, "ai": 0.4, "50": 0.4, "crusoe": 0.3, "ai 50": 0.2, "forbes ai": 0.2}
    assert tasks.phrases(vector, surface, limit=2) == ["Forbes AI 50", "Crusoe"]


@pytest.mark.parametrize("app,url,expected", [
    ("Cursor", None, "coding"), ("Zoom", None, "meeting"), ("Slack", None, "communicating"), ("Notion", None, "writing"),
    ("Arc", "https://www.google.com/search?q=x", "researching"), ("Arc", "https://example.com/blog", "browsing"),
    ("Chrome", "https://github.com/acme/app/pull/1", "coding"), ("Linear", None, "working on"),
])
def test_verb_categories(app, url, expected):
    assert tasks.verb([tasks.Item(start=T0, seconds=10, app=app, url=url)]) == expected


def test_tasks_are_graph_nodes_and_exposed(client):
    found = graph.tasks()
    assert found and all(item["label"] and item["start"] <= item["end"] for item in found)
    clusters = Counter(item["cluster_id"] for item in found)
    assert max(clusters.values()) >= 2
    node = graph.node(found[0]["id"])
    rels = {(n["rel"], n["kind"]) for n in node["neighbors"]}
    assert ("part_of", "topic_cluster") in rels
    assert any(rel == "involved" for rel, _ in rels)
    # Tasks are per shard, so the first task of a shard has no predecessor.
    assert any(("then", "task") in {(n["rel"], n["kind"]) for n in graph.node(item["id"])["neighbors"]} for item in found)
    summary = client.get("/graph/summary").json()
    assert summary["tasks"] and summary["counts"]["task"] == len(found)
    assert all(item["from"] != item["to"] for item in summary["transitions"])
    assert client.get("/graph/tasks", params={"since": "2000-01-01T00:00:00+00:00"}).json()["data"]


def test_context_leads_with_timeline_and_mcp_lists_tasks(client):
    markdown = client.get("/context").json()["markdown"]
    assert "## What you were doing" in markdown
    assert markdown.index("## What you were doing") < markdown.index("## On screen now")
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_tasks", "arguments": {}}})
    listed = json.loads(reply["result"]["content"][0]["text"])
    assert listed and {"label", "start", "end", "verb", "top_terms", "cluster_id", "involved"} <= set(listed[0])
    future = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                     "params": {"name": "list_tasks", "arguments": {"since": "2999-01-01T00:00:00+00:00"}}})
    assert json.loads(future["result"]["content"][0]["text"]) == []


def test_session_graph_has_contains_edges(client):
    session = client.post("/sessions", json={"duration": "10m", "label": "tasks"}).json()
    client.post("/add", json={"text": "Refund REF-7 for Acme", "app_name": "Chrome", "window_name": "Stripe refunds",
                              "browser_url": "https://dashboard.stripe.com/refunds", "session_id": session["id"]})
    client.post(f"/sessions/{session['id']}/stop")
    found = graph.tasks(session["id"])
    assert len(found) == 1 and found[0]["frame_count"] == 1
    neighbors = graph.node(found[0]["id"], session_id=session["id"])["neighbors"]
    assert any(n["rel"] == "contains" and n["kind"] == "session" for n in neighbors)

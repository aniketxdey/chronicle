"""Near-duplicate node merging."""

from __future__ import annotations

import pytest

from chronicle import cli, graph, merge

BACKENDS = ["tfidf", "difflib"] if merge.BACKEND == "tfidf" else ["difflib"]


def _node(kind: str, label: str, weight: float = 1, node_id: str | None = None, **props) -> dict:
    return {
        "id": node_id or f"{kind}:{label.lower()}", "kind": kind, "label": label, "weight": weight, "seconds": weight,
        "first_seen": None, "last_seen": None, "evidence": [], "props": props,
    }


def _clusters(nodes: list[dict], backend: str) -> list[set[str]]:
    plan = merge.plan({node["id"]: node for node in nodes}, backend=backend)
    return [{cluster["id"], *(member["id"] for member in cluster["members"])} for cluster in plan]


@pytest.fixture(params=BACKENDS)
def backend(request) -> str:
    return request.param


def test_same_normalized_url_merges_pages_and_windows(backend):
    nodes = [
        _node("page", "Docs", 3, "page:example.com/docs", url="https://example.com/docs?utm_source=mail"),
        _node("page", "Documentation | Example", 1, "page:example.com/docs/", url="https://www.example.com/docs/"),
        _node("window", "Docs – Example", 2, "window:chrome|docs", app="Chrome", url="https://example.com/docs?ref=nav"),
        _node("window", "Docs", 1, "window:chrome|other-docs", app="Chrome", url="https://example.org/docs"),
    ]
    assert _clusters(nodes, backend) == [{"page:example.com/docs", "page:example.com/docs/", "window:chrome|docs"}]


def test_company_name_variants_merge(backend):
    nodes = [
        _node("topic", "Crusoe", 3),
        _node("company", "Crusoe Energy", 1),
        _node("topic", "Crusoe | Company Overview & News", 1),
        _node("topic", "Cruise", 2),
    ]
    plan = merge.plan({node["id"]: node for node in nodes}, backend=backend)
    assert len(plan) == 1 and plan[0]["label"] == "Crusoe"
    assert {member["label"] for member in plan[0]["members"]} == {"Crusoe Energy", "Crusoe | Company Overview & News"}


def test_file_path_merges_with_basename_only(backend):
    nodes = [_node("file", "chronicle/graph.py", 2), _node("file", "graph.py", 3), _node("file", "tests/graph.py", 1),
             _node("file", "graph.ts", 1)]
    found = _clusters(nodes, backend)
    assert len(found) == 1 and len(found[0]) == 2 and "file:graph.py" in found[0]
    assert "file:graph.ts" not in found[0]


def test_window_counters_and_app_suffixes_merge(backend):
    nodes = [
        _node("window", "(3) Inbox - Gmail", 4, "window:chrome|3", app="Google Chrome"),
        _node("window", "Inbox - Gmail - Google Chrome", 1, "window:chrome|plain", app="Google Chrome"),
        _node("window", "Inbox - Gmail", 1, "window:chrome|bare", app="Google Chrome"),
        _node("window", "(2) Inbox - Gmail", 1, "window:mail|2", app="Mail"),
    ]
    assert _clusters(nodes, backend) == [{"window:chrome|3", "window:chrome|plain", "window:chrome|bare"}]


def test_negatives_stay_apart(backend):
    nodes = [
        _node("topic", "Apple", 2), _node("topic", "Applebee's", 1),
        _node("window", "top 50 startups in physical ai space - Google Search", 2, "window:arc|a", app="Arc"),
        _node("window", "forbes top 50 ai startups - Google Search", 1, "window:arc|b", app="Arc"),
        _node("window", "top 50 physical ai startups - Google Search", 1, "window:arc|c", app="Arc",
              url="https://www.google.com/search?q=top+50+physical+ai+startups&ei=x"),
        _node("window", "top 50 physical ai startups 2026 - Google Search", 1, "window:arc|d", app="Arc",
              url="https://www.google.com/search?q=top+50+physical+ai+startups+2026&ei=y"),
        _node("window", "export.ts — billing", 2, "window:cursor|a", app="Cursor"),
        _node("window", "export.ts — review comments", 1, "window:cursor|b", app="Cursor"),
        _node("topic", "Crusoe Energy", 1), _node("topic", "Crusoe Cloud", 1),
        _node("window", "Q3 roadmap", 1, "window:notion|q3", app="Notion"),
        _node("window", "Q4 roadmap", 1, "window:notion|q4", app="Notion"),
    ]
    assert _clusters(nodes, backend) == []


PAIRS = [
    (True, "window", "Budget 2026.xlsx", "Budget 2026.xlsx [Read-Only]"),
    (True, "window", "Meeting notes: Oct 21", "Meeting notes Oct 21 (edited)"),
    (True, "window", "README.md — autoworker", "README.md — autoworker (Working Tree)"),
    (True, "window", "Untitled spreadsheet - Google Sheets", "Untitled spreadsheet"),
    (True, "topic", "Scale AI", "ScaleAI"),
    (True, "topic", "NVIDIA Corporation", "Nvidia Corp"),
    (True, "window", "Q3 Board Deck final", "Q3 Board Deck final v2"),
    (False, "topic", "Meta", "Metabase"),
    (False, "topic", "Anthropic", "Anthropology"),
    (False, "window", "Sprint planning", "Sprint retro"),
    (False, "window", "Weekly sync notes", "Weekly sync agenda"),
    (False, "window", "Re: Contract renewal", "Re: Contract review"),
    (False, "window", "Budget 2026.xlsx", "Budget 2025.xlsx"),
    (False, "topic", "Physical AI", "Physical therapy"),
]


def test_threshold_precision_on_crafted_pairs():
    decided = []
    for index, (same, kind, left, right) in enumerate(PAIRS):
        nodes = [_node(kind, left, 1, f"{kind}:{index}a", app="App"), _node(kind, right, 1, f"{kind}:{index}b", app="App")]
        decided.append((same, bool(_clusters(nodes, merge.BACKEND))))
    assert not [pair for pair, (same, merged) in zip(PAIRS, decided) if merged and not same]
    assert sum(merged for same, merged in decided if same) >= (7 if merge.BACKEND == "tfidf" else 5)


def test_apply_rewires_edges_and_keeps_aliases():
    ref = lambda i: {"type": "frame", "id": i, "timestamp": f"2026-01-01T00:00:{i:02d}+00:00"}
    nodes = {
        "page:x.com/a": {**_node("page", "A", 2, "page:x.com/a", url="https://x.com/a"), "evidence": [ref(i) for i in range(6)],
                         "first_seen": ref(0)["timestamp"], "last_seen": ref(5)["timestamp"]},
        "window:arc|a": {**_node("window", "(2) A", 1, "window:arc|a", app="Arc", url="https://x.com/a"),
                         "evidence": [ref(i) for i in range(4, 10)], "first_seen": ref(4)["timestamp"], "last_seen": ref(9)["timestamp"]},
        "topic:z": _node("topic", "Z", 1, "topic:z"),
    }
    edge = lambda src, dst, rel, w: {"src": src, "dst": dst, "rel": rel, "weight": w, "first_seen": None, "last_seen": None, "evidence": []}
    edges = {
        ("window:arc|a", "page:x.com/a", "visited"): edge("window:arc|a", "page:x.com/a", "visited", 3),
        ("page:x.com/a", "topic:z", "co_occurs"): edge("page:x.com/a", "topic:z", "co_occurs", 2),
        ("topic:z", "window:arc|a", "co_occurs"): edge("topic:z", "window:arc|a", "co_occurs", 1),
    }
    plan = merge.plan(nodes)
    assert merge.apply(nodes, edges, plan, graph.EVIDENCE_CAP) == 1
    target = nodes["page:x.com/a"]
    assert set(nodes) == {"page:x.com/a", "topic:z"}
    assert target["weight"] == 2 and target["props"]["app"] == "Arc"
    assert target["props"]["aliases"] == ["(2) A"] and target["props"]["merged"] == ["window:arc|a"]
    assert len(target["evidence"]) == graph.EVIDENCE_CAP
    assert target["last_seen"] == ref(9)["timestamp"]
    assert list(edges) == [("page:x.com/a", "topic:z", "co_occurs")]
    assert edges[("page:x.com/a", "topic:z", "co_occurs")]["weight"] == 3


def _record_tabs(client) -> int:
    session = client.post("/sessions", json={"duration": "10m", "label": "merge check"}).json()
    for text, window, url in (
        ("Crusoe builds AI data centers", "Crusoe | Company Overview & News", "https://www.forbes.com/companies/crusoe/?list=ai50"),
        ("Forbes AI 50 list", "Forbes 2026 AI 50 List", "https://www.forbes.com/lists/ai50/"),
        ("results", "top 50 startups in physical ai space - Google Search", "https://www.google.com/search?q=top+50+startups+in+physical+ai+space"),
        ("results", "forbes top 50 ai startups - Google Search", "https://www.google.com/search?q=forbes+top+50+ai+startups&ei=1"),
    ):
        client.post("/add", json={"text": text, "app_name": "Arc", "window_name": window, "browser_url": url, "session_id": session["id"]})
    client.post(f"/sessions/{session['id']}/stop")
    return session["id"]


def test_build_merges_tabs_behind_flag(client, monkeypatch, capsys):
    session_id = _record_tabs(client)
    merged = graph.summary(session_id)["counts"]
    # Each search has its own page, so every tab's window folds into its page.
    assert merged["page"] == 4 and "window" not in merged
    crusoe = graph.node("window:arc|crusoe-|-company-overview-&-news", session_id=session_id)
    assert crusoe["node"]["id"] == "page:forbes.com/companies/crusoe"

    cli.main(["graph", "merges", "--session", str(session_id)])
    out = capsys.readouterr().out
    assert "url" in out and "Crusoe | Company Overview & News" in out

    monkeypatch.setenv("CHRONICLE_MERGE", "0")
    assert graph.build(session_id)["merged"] == 0
    assert graph.summary(session_id)["counts"]["window"] == 4

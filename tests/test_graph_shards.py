"""Per-session graph shards and the decayed ``all`` aggregation."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from chronicle import content, db, graph, sessions
from chronicle.timeutil import utc_iso

SCENES = [
    ("Cursor", "billing.py · acme", None, "def export_rows(): parseInvoice report_{n}.csv"),
    ("Chrome", "ENG-{n} · Linear", "https://linear.app/acme/issue/ENG-{n}", "ENG-{n} export columns frozen"),
    ("Slack", "#billing", None, "PR #{n} needs review, see export_rows"),
    ("Terminal", "pytest", None, "tests/test_export_{n}.py passed"),
]


@pytest.fixture
def fresh(monkeypatch, tmp_path):
    db.close()
    monkeypatch.setenv("CHRONICLE_HOME", str(tmp_path))
    monkeypatch.delenv("CHRONICLE_HALF_LIFE_HOURS", raising=False)
    db.init()
    yield
    db.close()


def _frames(start: datetime, n: int, count: int, session_id: int | None) -> None:
    for i in range(count):
        app, window, url, text = SCENES[i % len(SCENES)]
        content.add_screen(
            text=text.format(n=n), app_name=app, window_name=window.format(n=n), browser_url=url and url.format(n=n),
            timestamp=utc_iso(start + timedelta(seconds=30 * i)), duration_secs=30, dedupe=False, session_id=session_id,
        )


def _session(n: int, ended_ago: timedelta, count: int = 12) -> int:
    start = datetime.now(timezone.utc) - ended_ago - timedelta(seconds=30 * count)
    with db.conn() as connection:
        session_id = int(connection.execute(
            "INSERT INTO sessions(label, started_at, ends_at, stopped_at, status, interval_secs) VALUES (?, ?, NULL, ?, 'stopped', 30)",
            (f"work {n}", utc_iso(start), utc_iso(start + timedelta(seconds=30 * count))),
        ).lastrowid)
    _frames(start, n, count, session_id)
    content.add_event(event_type="clipboard", app_name="Chrome", text=f"ENG-{n} copied", timestamp=utc_iso(start + timedelta(seconds=45)), session_id=session_id)
    return session_id


def _no_decay() -> None:
    db.set_setting("graph_half_life_hours", "0")


def _rows(scope: str) -> tuple[dict, dict]:
    with db.conn() as connection:
        nodes = {r["id"]: dict(r) for r in connection.execute("SELECT * FROM kg_nodes WHERE scope = ?", (scope,))}
        edges = {(r["src"], r["dst"], r["rel"]): dict(r) for r in connection.execute("SELECT * FROM kg_edges WHERE scope = ?", (scope,))}
    return nodes, edges


def test_aggregation_matches_unsharded_build_without_decay(fresh, monkeypatch):
    # The unsharded reference build has no merge pass.
    monkeypatch.setenv("CHRONICLE_MERGE", "0")
    _no_decay()
    shards = [_session(n, timedelta(days=n)) for n in range(1, 6)]
    loose = datetime.now(timezone.utc).replace(minute=50, second=0, microsecond=0) - timedelta(days=8)
    _frames(loose, 99, 30, None)
    meeting = content.ensure_meeting(title="Billing sync", started_at=utc_iso(loose), ended_at=utc_iso(loose + timedelta(minutes=30)))
    for minutes, speaker in ((5, "Dana Wells"), (15, "Luis Ortega")):
        saved = content.add_audio(text="ship ENG-99 and export_rows today", speaker_name=speaker,
                                  timestamp=utc_iso(loose + timedelta(minutes=minutes)), meeting_id=meeting)
        content.attach_meeting(saved["transcription_id"], meeting)
    built = graph.build()
    assert built["shards"] == 7 and built["nodes"] > 0
    graph.build_unsharded()
    agg_nodes, agg_edges = _rows("all")
    raw_nodes, raw_edges = _rows("unsharded")

    assert set(agg_nodes) == set(raw_nodes)
    for node_id, raw in raw_nodes.items():
        agg = agg_nodes[node_id]
        assert (agg["kind"], agg["first_seen"], agg["last_seen"]) == (raw["kind"], raw["first_seen"], raw["last_seen"]), node_id
        assert agg["seconds"] == pytest.approx(raw["seconds"], abs=0.5)
        assert agg["eff_weight"] == pytest.approx(agg["weight"])
        # Keyphrase scores are relative to each shard's corpus, so scored weights differ from one global build.
        if agg["kind"] not in {"topic", "query"}:
            assert agg["weight"] == raw["weight"], node_id
    assert set(agg_edges) <= set(raw_edges)
    # The unsharded build links the last window of one recording to the first of the next; shards do not.
    boundaries = len(shards) + 1
    drift: dict[str, float] = {}
    for key, raw in raw_edges.items():
        missing = raw["weight"] - agg_edges.get(key, {"weight": 0})["weight"]
        if missing:
            assert key[2] in {"switched_to", "then"}, key
            drift[key[2]] = drift.get(key[2], 0) + missing
    assert all(0 < value <= boundaries for value in drift.values())
    assert ("person:dana-wells", "meeting:1", "spoke_in") in agg_edges
    assert ("person:luis-ortega", "meeting:1", "spoke_in") in agg_edges


def test_decay_downweights_old_sessions(fresh):
    _session(1, timedelta(days=10), count=36)
    _session(2, timedelta(minutes=5), count=12)
    graph.build()
    tickets = [item["id"] for item in graph.find(kind="ticket")]
    assert tickets[:2] == ["ticket:eng-2", "ticket:eng-1"]
    old = graph.node("ticket:eng-1")["node"]
    new = graph.node("ticket:eng-2")["node"]
    assert old["weight"] > new["weight"]
    assert old["effective_weight"] == pytest.approx(old["weight"] * graph.decay(240, 72), rel=0.02)
    assert graph.summary()["top"]["ticket"][0]["id"] == "ticket:eng-2"

    _no_decay()
    assert [item["id"] for item in graph.find(kind="ticket")][:2] == ["ticket:eng-1", "ticket:eng-2"]


def _comparable() -> tuple[dict, dict, dict]:
    """``all`` rows, with effective weights rescaled to now (a full regroup moves the anchor)."""
    nodes, edges = _rows("all")
    with db.conn() as connection:
        scale = graph._scale(connection)
    pick = ("kind", "label", "weight", "seconds", "first_seen", "last_seen", "evidence", "props")
    exact = {k: {f: v[f] for f in pick} for k, v in nodes.items()}
    exact.update({k: (v["weight"], v["first_seen"], v["last_seen"], v["evidence"]) for k, v in edges.items()})
    effective = {k: v["eff_weight"] * scale for k, v in [*nodes.items(), *edges.items()]}
    return exact, effective


def test_deleting_a_session_removes_its_contribution(fresh):
    first = _session(1, timedelta(days=2))
    _session(2, timedelta(days=1))
    graph.build()
    before = graph.node("file:report_1.csv")["node"]
    shared = graph.node("identifier:export_rows")["node"]["weight"]
    assert before["weight"] > 0
    sessions.delete(first)
    with pytest.raises(db.ChronicleError):
        graph.node("file:report_1.csv")
    assert graph.node("file:report_2.csv")["node"]["weight"] > 0
    assert 0 < graph.node("identifier:export_rows")["node"]["weight"] < shared
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM kg_shards WHERE scope = ?", (f"session:{first}",)).fetchone()["n"] == 0


def test_dirty_key_regroup_matches_full_regroup(fresh):
    first = _session(1, timedelta(days=3))
    _session(2, timedelta(days=2))
    assert graph.build()["mode"] == "full"
    _session(3, timedelta(hours=5))
    assert graph.build()["mode"] == "dirty"
    sessions.delete(first)
    assert graph.aggregate()["mode"] == "dirty"
    exact, effective = _comparable()
    assert graph.aggregate(full=True)["mode"] == "full"
    exact_full, effective_full = _comparable()
    assert exact_full == exact
    assert effective_full == pytest.approx(effective, rel=1e-6)


def test_live_session_refreshes_incrementally(fresh):
    live = sessions.start("30m", label="live")
    start = datetime.fromisoformat(live["started_at"])
    _frames(start, 7, 5, live["id"])
    first = graph.refresh(live["id"], incremental=True)
    assert first["incremental"] is False and first["frames"] == 4  # newest frame is held back
    _frames(start + timedelta(minutes=5), 7, 3, live["id"])
    extended = content.add_screen(text="PR #7 needs review, see export_rows", app_name="Slack", window_name="#billing",
                                  duration_secs=30, session_id=live["id"])
    assert extended["deduped"]
    content.add_screen(text="tests/test_export_7.py passed", app_name="Terminal", window_name="pytest",
                       timestamp=utc_iso(start + timedelta(minutes=7)), duration_secs=30, session_id=live["id"])
    second = graph.refresh(live["id"], incremental=True)
    assert second["incremental"] is True and second["frames"] == 4
    assert graph.refresh(live["id"], incremental=True)["frames"] == 0
    assert any(item["id"] == "ticket:eng-7" for item in graph.find("ENG-7"))

    incremental_nodes, incremental_edges = _rows(f"session:{live['id']}")
    graph.build(live["id"])
    full_nodes, full_edges = _rows(f"session:{live['id']}")
    assert {k: (v["weight"], v["first_seen"], v["last_seen"]) for k, v in incremental_nodes.items()} == \
           {k: (v["weight"], v["first_seen"], v["last_seen"]) for k, v in full_nodes.items()}
    assert {k: v["seconds"] for k, v in incremental_nodes.items()} == pytest.approx({k: v["seconds"] for k, v in full_nodes.items()})
    assert {k: v["weight"] for k, v in incremental_edges.items()} == {k: v["weight"] for k, v in full_edges.items()}

    stopped = sessions.stop(live["id"])
    assert stopped["frames"] == 9
    with db.conn() as connection:
        shard = connection.execute("SELECT * FROM kg_shards WHERE scope = ?", (f"session:{live['id']}",)).fetchone()
    assert shard["final"] == 1 and shard["ended_at"] == stopped["stopped_at"]


def test_stale_aggregate_is_refreshed_on_query_without_reextracting(fresh):
    _session(1, timedelta(hours=1))
    graph.build()
    with db.conn() as connection:
        built = {r["scope"]: r["built_at"] for r in connection.execute("SELECT scope, built_at FROM kg_shards")}
    state = db.get_setting("graph_all_state")
    graph.summary()
    assert db.get_setting("graph_all_state") == state
    fresh_weight = graph.node("ticket:eng-1")["node"]["effective_weight"]

    shifted = json.loads(state)
    old = (datetime.now(timezone.utc) - timedelta(seconds=graph.REAGGREGATE_SECS + 60)).isoformat(timespec="microseconds")
    shifted["at"] = old
    shifted["anchor"] = (datetime.fromisoformat(shifted["anchor"]) - timedelta(hours=72)).isoformat()
    db.set_setting("graph_all_state", json.dumps(shifted))
    assert graph.node("ticket:eng-1")["node"]["effective_weight"] == pytest.approx(fresh_weight / 2, rel=0.01)
    assert old not in db.get_setting("graph_all_state")
    with db.conn() as connection:
        assert {r["scope"]: r["built_at"] for r in connection.execute("SELECT scope, built_at FROM kg_shards")} == built


def test_deleted_range_leaves_the_graph(fresh):
    loose = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
    _frames(loose, 41, 8, None)
    _session(42, timedelta(hours=2))
    graph.build()
    assert graph.node("ticket:eng-41")["node"]["weight"] > 0
    db.delete_range(utc_iso(loose), utc_iso(loose + timedelta(hours=1)))
    graph.forget(utc_iso(loose), utc_iso(loose + timedelta(hours=1)))
    with pytest.raises(db.ChronicleError):
        graph.node("ticket:eng-41")
    assert graph.node("ticket:eng-42")["node"]["weight"] > 0
    with db.conn() as connection:
        assert connection.execute("SELECT COUNT(*) AS n FROM kg_shards WHERE scope LIKE 'loose:%'").fetchone()["n"] == 0


def test_half_life_setting_and_env(fresh, monkeypatch):
    with db.conn() as connection:
        assert graph._half_life(connection) == 72
        monkeypatch.setenv("CHRONICLE_HALF_LIFE_HOURS", "24")
        assert graph._half_life(connection) == 24
        db._set(connection, "graph_half_life_hours", "off")
        assert graph._half_life(connection) == float("inf")

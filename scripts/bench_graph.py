"""Time the unsharded full graph build against shard aggregation on a synthetic history.

    python scripts/bench_graph.py [--sessions 20] [--frames 300]
    CHRONICLE_SRC=/path/to/old/checkout python scripts/bench_graph.py --old-only

Uses a throwaway CHRONICLE_HOME; never touches ~/.chronicle. ``--old-only`` times ``graph.build()`` from the
checkout in CHRONICLE_SRC, e.g. a pre-shard release, on the same synthetic history.
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.environ.get("CHRONICLE_SRC") or str(Path(__file__).resolve().parent.parent))
os.environ["CHRONICLE_HOME"] = tempfile.mkdtemp(prefix="chronicle-bench-")

from chronicle import db, graph  # noqa: E402
from chronicle.timeutil import utc_iso  # noqa: E402

APPS = [("Cursor", "py"), ("Chrome", None), ("Slack", None), ("Terminal", "sh"), ("Notion", None), ("Linear", None)]
WORDS = ("export invoice customer billing review deploy migrate schema report draft owner budget forecast "
         "pipeline retry latency queue cache token partner roadmap launch hiring metrics").split()
SITES = ["github.com/acme/app/pull/{n}", "linear.app/acme/issue/ENG-{n}", "docs.google.com/d/{n}", "news.example.com/ai/{n}"]


def _text(rng: random.Random, n: int) -> str:
    parts = [" ".join(rng.choices(WORDS, k=40))]
    parts += [f"ENG-{rng.randint(1, 400)}", f"{rng.choice(WORDS)}_{rng.choice(WORDS)}", f"{rng.choice(WORDS)}{rng.choice(WORDS).title()}"]
    parts += [f"{rng.choice(WORDS)}_{n % 50}.py", f"https://{rng.choice(SITES).format(n=rng.randint(1, 300))}"]
    rng.shuffle(parts)
    return " ".join(parts)


def seed(sessions: int, frames: int) -> int:
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    total = 0
    with db.conn() as connection:
        for s in range(sessions):
            start = now - timedelta(days=sessions - s, hours=2)
            end = start + timedelta(seconds=5 * frames)
            sid = int(connection.execute(
                "INSERT INTO sessions(label, started_at, stopped_at, status, interval_secs) VALUES (?, ?, ?, 'stopped', 5)",
                (f"bench {s}", utc_iso(start), utc_iso(end)),
            ).lastrowid)
            for i in range(frames):
                app, ext = rng.choice(APPS)
                url = f"https://{rng.choice(SITES).format(n=rng.randint(1, 300))}" if app == "Chrome" else None
                window = f"{rng.choice(WORDS)}.{ext}" if ext else f"{rng.choice(WORDS).title()} · {app}"
                db.insert_frame(
                    connection, timestamp=utc_iso(start + timedelta(seconds=5 * i)), app_name=app, window_name=window,
                    full_text=_text(rng, i), browser_url=url, duration_secs=5, session_id=sid,
                )
                if i % 20 == 0:
                    connection.execute(
                        "INSERT INTO ui_events(timestamp, event_type, app_name, window_name, text, session_id) VALUES (?, 'clipboard', ?, ?, ?, ?)",
                        (utc_iso(start + timedelta(seconds=5 * i)), app, window, _text(rng, i)[:200], sid),
                    )
            total += frames
    return total


def timed(fn, runs: int) -> float:
    samples = []
    for _ in range(runs):
        began = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - began) * 1000)
    return statistics.median(samples)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=20)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--old-only", action="store_true")
    args = parser.parse_args()
    db.init()
    frames = seed(args.sessions, args.frames)
    if args.old_only:
        print(f"{frames} frames; graph.build() from {graph.__file__}: {timed(graph.build, args.runs):.2f} ms")
        return
    first = timed(graph.build, 1)
    with db.conn() as connection:
        counts = {
            key: connection.execute(sql).fetchone()[0]
            for key, sql in (
                ("shard nodes", "SELECT COUNT(*) FROM kg_nodes WHERE scope != 'all'"),
                ("shard edges", "SELECT COUNT(*) FROM kg_edges WHERE scope != 'all'"),
                ("all nodes", "SELECT COUNT(*) FROM kg_nodes WHERE scope = 'all'"),
                ("all edges", "SELECT COUNT(*) FROM kg_edges WHERE scope = 'all'"),
            )
        }
    rng = random.Random(11)
    live = {"id": None, "n": 0}

    def live_minute() -> None:
        with db.conn() as connection:
            if live["id"] is None:
                live["id"] = int(connection.execute(
                    "INSERT INTO sessions(label, started_at, status, interval_secs) VALUES ('live', ?, 'recording', 5)", (utc_iso(),)
                ).lastrowid)
            for _ in range(12):
                live["n"] += 1
                app, _ = rng.choice(APPS)
                db.insert_frame(connection, timestamp=utc_iso(), app_name=app, window_name=f"{rng.choice(WORDS)} · {app}",
                                full_text=_text(rng, live["n"]), duration_secs=5, session_id=live["id"])
        graph.refresh(live["id"])
        graph.aggregate()

    def stale_query() -> None:
        db.set_setting("graph_all_state", db.get_setting("graph_all_state").replace('"at": "2', '"at": "1'))
        graph.ensure()

    rows = [
        ("old: full build, re-extract every frame", timed(graph.build_unsharded, args.runs)),
        ("new: full regroup of all shards (SQL GROUP BY)", timed(lambda: graph.aggregate(full=True), args.runs)),
        ("new: a session ends (extract it + regroup its keys)", timed(lambda: graph.build(args.sessions), args.runs)),
        ("new: delete a session (drop shard + regroup its keys)", timed(lambda: _drop_and_restore(args.sessions - 1), args.runs)),
        ("new: live minute (12 frames incremental + regroup)", timed(live_minute, args.runs)),
        ("new: query older than 10 min, nothing new", timed(stale_query, args.runs)),
        ("new: query freshness check, nothing changed", timed(graph.ensure, args.runs)),
        ("new: one-time sharding of all history", first),
    ]
    print(f"{args.sessions} sessions x {args.frames} frames = {frames} frames; sqlite {db.sqlite3.sqlite_version}")
    print("  " + ", ".join(f"{value} {key}" for key, value in counts.items()))
    for label, ms in rows:
        print(f"  {label:<55} {ms:9.2f} ms")


def _drop_and_restore(session_id: int) -> None:
    with db.conn() as connection:
        graph.drop_shard(connection, graph.scope_for(session_id))
    graph.aggregate()
    with db.conn() as connection:
        graph._session_pass(connection, session_id, incremental=False)


if __name__ == "__main__":
    main()

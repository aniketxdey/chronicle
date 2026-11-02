"""One arm's memory: ingest spooled task recordings into a Chronicle home and build the text injected into the next task.

    python -m chronicle_bench.memory ingest --home H spool/001.jsonl spool/007.jsonl
    python -m chronicle_bench.memory context --home H --arm chronicle "the task instruction"

The Chronicle home is selected through CHRONICLE_HOME, so each arm runs ingestion in its own process.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ARMS = ("raw", "search", "chronicle")
BUDGET_CHARS = 8000
PREAMBLE = (
    "Notes from your earlier sessions on this computer follow. They were recorded while you worked on previous, "
    "different tasks, so they may be incomplete or irrelevant. Use them only where they help, and check anything "
    "you rely on against the screen."
)
EMPTY_NOTE = "Nothing has been recorded yet. Start a recording with start_recording."
# Sections of the get_context pack, least useful for a new task first; "Evidence for the task" and the graph stay.
DROP_ORDER = ("Time by app", "System events", "Habitual transitions", "Recent activity", "Topics and searches", "What you were doing")


def use_home(home: str | Path) -> None:
    os.environ["CHRONICLE_HOME"] = str(Path(home).resolve())
    os.environ["CHRONICLE_DISABLE_CAPTURE"] = "1"
    os.environ["CHRONICLE_LLM"] = "off"
    os.environ["CHRONICLE_HALF_LIFE_HOURS"] = "off"
    from chronicle import db

    db.close()
    db.init()


def read_spool(path: str | Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def ingest_task(path: str | Path) -> int | None:
    """Store one task's recording as a stopped Chronicle session and build its graph shard. Returns the session id."""
    from chronicle import content, db, sessions

    records = read_spool(path)
    steps = [r for r in records if r["type"] == "step"]
    if not steps:
        return None
    starts = [r for r in records if r["type"] == "task_start"]
    label = (starts[0]["instruction"] if starts else Path(path).stem)[:200]
    with db.conn() as connection:
        session_id = int(connection.execute(
            "INSERT INTO sessions(label, started_at, status, interval_secs) VALUES (?, ?, 'recording', 5)",
            (label, records[0]["ts"]),
        ).lastrowid)
    previous: dict | None = None
    for record in records:
        if record["type"] == "task_start":
            content.add_event(event_type="session_start", app_name="", text=f"Task: {record['instruction']}",
                              timestamp=record["ts"], session_id=session_id)
            continue
        if record["type"] != "step":
            continue
        frame = content.add_screen(
            text=record["text"], app_name=record["app"], window_name=record["window"], timestamp=record["ts"],
            browser_url=record.get("url"), trigger="benchmark", text_source=record["source"],
            duration_secs=record.get("seconds") or 5, session_id=session_id,
        )
        if previous is None or previous["app"] != record["app"]:
            content.add_event(event_type="app_switch", app_name=record["app"], window_name=record["window"],
                              text=f"Switched to {record['app']}", timestamp=record["ts"],
                              frame_id=frame["frame_id"], session_id=session_id)
        if record.get("url") and (previous is None or previous.get("url") != record["url"]):
            content.add_event(event_type="url_change", app_name=record["app"], window_name=record["window"],
                              text=record["url"], timestamp=record["ts"], frame_id=frame["frame_id"], session_id=session_id)
        previous = record
    sessions.stop(session_id)
    return session_id


def _clip(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    cut = text.rfind("\n", 0, budget)
    return text[: cut if cut > 0 else budget]


def _has_frames() -> bool:
    from chronicle import db

    with db.conn() as connection:
        return connection.execute("SELECT 1 FROM frames LIMIT 1").fetchone() is not None


def fit_sections(markdown: str, budget: int) -> str:
    """Drop whole low-priority ``## `` sections, in DROP_ORDER, until the text fits; then clip at a line."""
    parts = re.split(r"(?m)^(?=## )", markdown)
    head, sections = parts[0], parts[1:]
    for title in DROP_ORDER:
        if len(head) + sum(map(len, sections)) <= budget:
            break
        sections = [s for s in sections if not s.startswith(f"## {title}")]
    over = len(head) + sum(map(len, sections)) - budget
    graph = next((i for i, s in enumerate(sections) if s.startswith("## Knowledge graph")), None)
    if over > 0 and graph is not None:
        sections[graph] = _clip(sections[graph], max(0, len(sections[graph]) - over)) + "\n\n"
    return _clip(head + "".join(sections), budget).strip()


def chronicle_text(task: str, budget: int = BUDGET_CHARS) -> str:
    from chronicle import context

    pack = context.build(task=task)
    pack["now"] = None
    lines = [line for line in context.render(pack).splitlines() if line != EMPTY_NOTE and not line.startswith("Tools: ")]
    return fit_sections("\n".join(lines).strip(), budget)


def search_text(task: str, limit: int = 40) -> str:
    """BM25-ranked full-text search over the recorded frames, best match first, with each match's snippet."""
    from chronicle import db, search

    terms = list(dict.fromkeys(token.lower() for token in search._TOKEN.findall(task) if len(token) > 1))[:64]
    if not terms:
        return ""
    expr = " OR ".join(f'"{term.replace(chr(34), "")}"' for term in terms)
    with db.conn() as connection:
        rows = connection.execute(
            """
            SELECT f.timestamp, f.app_name, f.window_name, f.browser_url,
                   snippet(frames_fts, 1, '', '', ' … ', 48) AS snip
            FROM frames_fts JOIN frames f ON f.id = frames_fts.frame_id
            WHERE frames_fts MATCH ? ORDER BY bm25(frames_fts) LIMIT ?
            """,
            (expr, limit),
        ).fetchall()
    if not rows:
        return ""
    lines = ["# Search results from earlier sessions (best match first)", f"Query: {task}", ""]
    for row in rows:
        where = " · ".join(part for part in (row["app_name"], row["window_name"]) if part)
        url = f" <{row['browser_url']}>" if row["browser_url"] else ""
        lines.append(f"- {row['timestamp']} {where}{url}: {' '.join((row['snip'] or '').split())}")
    return "\n".join(lines)


def context_for(arm: str, task: str, budget: int = BUDGET_CHARS) -> str:
    """The block appended to the instruction for this arm, or "" when the arm adds nothing."""
    if arm not in ARMS:
        raise ValueError(f"arm must be one of {ARMS}")
    if arm == "raw" or not _has_frames():
        return ""
    body = chronicle_text(task, budget) if arm == "chronicle" else search_text(task)
    if not body:
        return ""
    return f"<earlier_sessions>\n{PREAMBLE}\n\n{_clip(body, budget)}\n</earlier_sessions>"


def augment(instruction: str, block: str) -> str:
    return f"{instruction}\n\n{block}" if block else instruction


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chronicle_bench.memory")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--home", required=True)
    ingest.add_argument("spools", nargs="+")
    show = sub.add_parser("context")
    show.add_argument("--home", required=True)
    show.add_argument("--arm", choices=ARMS, required=True)
    show.add_argument("--budget", type=int, default=BUDGET_CHARS)
    show.add_argument("task")
    args = parser.parse_args(argv)
    use_home(args.home)
    if args.command == "ingest":
        for spool in args.spools:
            print(json.dumps({"spool": spool, "session_id": ingest_task(spool)}))
    else:
        sys.stdout.write(context_for(args.arm, args.task, args.budget) + "\n")


if __name__ == "__main__":
    main()

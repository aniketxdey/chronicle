"""Saved contexts, workflow-specific exports, and the per-context views behind the UI.

A context is a window of history: a recording session, or a time range. Every
session is a context. Generating one with a task or preset also saves its
markdown so it can be reopened and downloaded later.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from chronicle import context, db, graph, search, sessions
from chronicle.timeutil import format_duration, utc_iso

PRESETS: list[dict] = [
    {
        "id": "general",
        "title": "General",
        "keywords": "",
        "brief": "Use this as background on what the user was doing. Ground every step in it, and ask before acting on anything it doesn't cover.",
    },
    {
        "id": "data-analysis",
        "title": "Data analysis",
        "keywords": "data csv sql query notebook dashboard spreadsheet metrics",
        "brief": "You're continuing a data analysis. Reuse the datasets, files, queries, notebooks, and dashboards below instead of starting over, and match the metrics and filters the user already chose.",
    },
    {
        "id": "job-applications",
        "title": "Applying to jobs",
        "keywords": "job careers role resume cover application linkedin greenhouse",
        "brief": "You're helping with job applications. Use the postings, companies, and roles below, keep a list of what was already applied to, and reuse answers the user already wrote. Never submit an application without confirmation.",
    },
    {
        "id": "coding",
        "title": "Software development",
        "keywords": "github pr ticket error test repo commit branch",
        "brief": "You're continuing software work. Start from the files, identifiers, tickets, and PRs below, and check the most recent errors and terminal output before changing code.",
    },
    {
        "id": "research",
        "title": "Research",
        "keywords": "search paper article docs arxiv wikipedia notes report",
        "brief": "You're continuing research. The searches and topics below show what was already looked at. Build on the sources the user read and cite them, rather than repeating searches.",
    },
    {
        "id": "writing",
        "title": "Writing",
        "keywords": "doc draft notes outline essay docs notion",
        "brief": "You're helping with writing. The documents and drafts below are the current state. Keep the user's structure and voice, and don't rewrite parts they didn't ask about.",
    },
    {
        "id": "meeting-follow-up",
        "title": "Meeting follow-up",
        "keywords": "meeting call zoom agenda notes action decision",
        "brief": "You're following up on meetings. Use the decisions and action items below, address people by the names in the transcript, and draft messages for review rather than sending them.",
    },
    {
        "id": "customer-work",
        "title": "Customer and sales",
        "keywords": "customer account deal crm email invoice contract",
        "brief": "You're helping with customer work. Use the customers, accounts, tickets, and deadlines below, and keep promises the user already made in writing.",
    },
]
_BY_ID = {item["id"]: item for item in PRESETS}
FORMATS = {
    "md": ("text/markdown; charset=utf-8", "md"),
    "json": ("application/json", "json"),
    "graphml": ("application/xml", "graphml"),
    "cypher": ("text/plain; charset=utf-8", "cypher"),
}
_ACTION = re.compile(r"\b(will|i'll|we'll|let's|need to|needs to|should|todo|to-do|follow up|by (mon|tues|wednes|thurs|fri|satur|sun)day|by tomorrow|by eod|send|ship|deadline)\b", re.I)
_DECISION = re.compile(r"\b(decided|agreed|going with|we'll go|final|approved|settled|plan is)\b", re.I)


def presets() -> list[dict]:
    return [{k: item[k] for k in ("id", "title", "brief")} for item in PRESETS]


def preset(preset_id: str | None) -> dict:
    if not preset_id:
        return _BY_ID["general"]
    if preset_id not in _BY_ID:
        raise db.ChronicleError(f"unknown preset {preset_id!r}", 400)
    return _BY_ID[preset_id]


def window(session_id: int | None = None, minutes: int | None = None,
           start: str | None = None, end: str | None = None) -> dict:
    """Resolve a context scope to concrete UTC bounds and a label."""
    if session_id:
        item = sessions.get(session_id)
        stop = item["stopped_at"] or (item["ends_at"] if item["status"] != "recording" else None) or utc_iso()
        label = item.get("label") or f"Recording {session_id}"
        return {"session_id": session_id, "start": item["started_at"], "end": stop, "label": label, "session": item}
    if minutes:
        now = datetime.now(timezone.utc)
        return {"session_id": None, "start": utc_iso(now - timedelta(minutes=minutes)), "end": utc_iso(now),
                "label": f"Last {format_duration(minutes * 60)}"}
    return {"session_id": None, "start": start, "end": end, "label": "All history" if not (start or end) else "Time range"}


def _task(task: str | None, chosen: dict) -> str | None:
    parts = [part for part in ((task or "").strip(), chosen["keywords"]) if part]
    return " ".join(parts) or None


def _document(pack: dict, chosen: dict, task: str | None, scope: dict) -> str:
    head = [f"# {chosen['title']} context · {scope['label']}", "", f"> {chosen['brief']}"]
    if task:
        head += ["", f"Task: {task}"]
    body = pack["markdown"].split("\n", 1)[1] if "\n" in pack["markdown"] else ""
    body = re.sub(r"^Task: .*\n", "", body, count=1)
    return "\n".join(head) + "\n" + body


def build(task: str | None = None, preset_id: str | None = None, session_id: int | None = None,
          minutes: int | None = None) -> dict:
    chosen = preset(preset_id)
    scope = window(session_id, minutes)
    pack = context.build(task=_task(task, chosen), minutes=None if session_id else minutes, session_id=session_id, max_nodes=32)
    pack["task"] = task or None
    pack["preset"] = chosen["id"]
    pack["markdown"] = _document(pack, chosen, task, scope)
    pack["scope"] = {k: scope[k] for k in ("session_id", "start", "end", "label")}
    return pack


def _saved(row) -> dict:
    item = dict(row)
    item["characters"] = len(item["markdown"])
    return item


def create(task: str | None = None, preset_id: str | None = None, session_id: int | None = None,
           minutes: int | None = None, title: str | None = None) -> dict:
    pack = build(task, preset_id, session_id, minutes)
    chosen = preset(preset_id)
    scope = pack["scope"]
    name = (title or "").strip() or task or (f"{chosen['title']} · {scope['label']}" if chosen["id"] != "general" else scope["label"])
    with db.conn() as connection:
        cursor = connection.execute(
            """
            INSERT INTO contexts (title, task, preset, session_id, minutes, start_at, end_at, markdown, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (name[:200], task, chosen["id"], session_id, minutes, scope["start"], scope["end"], pack["markdown"], utc_iso()),
        )
        context_id = cursor.lastrowid
    return get(context_id)


def list_saved(limit: int = 100) -> list[dict]:
    with db.conn() as connection:
        rows = connection.execute("SELECT * FROM contexts ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return [{k: v for k, v in _saved(row).items() if k != "markdown"} for row in rows]


def get(context_id: int) -> dict:
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM contexts WHERE id = ?", (context_id,)).fetchone()
    if row is None:
        raise db.ChronicleError("context not found", 404)
    return _saved(row)


def delete(context_id: int) -> dict:
    with db.conn() as connection:
        deleted = connection.execute("DELETE FROM contexts WHERE id = ?", (context_id,)).rowcount
    if not deleted:
        raise db.ChronicleError("context not found", 404)
    return {"deleted": context_id}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "context"


def _graphml(data: dict) -> str:
    keys = [("kind", "node"), ("label", "node"), ("seconds", "node"), ("weight", "node"), ("rel", "edge"), ("weight", "edge")]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">']
    for name, target in keys:
        kind = "double" if name in {"seconds", "weight"} else "string"
        lines.append(f'  <key id="{target[0]}_{name}" for="{target}" attr.name="{name}" attr.type="{kind}"/>')
    lines.append('  <graph id="chronicle" edgedefault="directed">')
    for node in data["nodes"]:
        lines.append(f'    <node id="{escape(node["id"], {chr(34): "&quot;"})}">')
        for name in ("kind", "label"):
            lines.append(f'      <data key="n_{name}">{escape(str(node.get(name) or ""))}</data>')
        for name in ("seconds", "weight"):
            lines.append(f'      <data key="n_{name}">{node.get(name) or 0}</data>')
        lines.append("    </node>")
    for index, edge in enumerate(data["edges"]):
        src = escape(edge["src"], {'"': "&quot;"})
        dst = escape(edge["dst"], {'"': "&quot;"})
        lines.append(f'    <edge id="e{index}" source="{src}" target="{dst}">')
        lines.append(f'      <data key="e_rel">{escape(edge["rel"])}</data>')
        lines.append(f'      <data key="e_weight">{edge.get("weight") or 0}</data>')
        lines.append("    </edge>")
    lines += ["  </graph>", "</graphml>"]
    return "\n".join(lines) + "\n"


def _cypher(data: dict) -> str:
    def text(value) -> str:
        return json.dumps(str(value or ""))

    lines = ["// Chronicle knowledge graph. Run in Neo4j Browser or cypher-shell."]
    for node in data["nodes"]:
        label = re.sub(r"[^A-Za-z]", "", node["kind"].title()) or "Entity"
        lines.append(
            f"MERGE (n:Entity:{label} {{id: {text(node['id'])}}}) "
            f"SET n.label = {text(node['label'])}, n.kind = {text(node['kind'])}, n.seconds = {node.get('seconds') or 0};"
        )
    for edge in data["edges"]:
        rel = re.sub(r"[^A-Z_]", "", edge["rel"].upper()) or "RELATED"
        lines.append(
            f"MATCH (a:Entity {{id: {text(edge['src'])}}}), (b:Entity {{id: {text(edge['dst'])}}}) "
            f"MERGE (a)-[r:{rel}]->(b) SET r.weight = {edge.get('weight') or 0};"
        )
    return "\n".join(lines) + "\n"


def export(fmt: str = "md", preset_id: str | None = None, session_id: int | None = None,
           context_id: int | None = None, minutes: int | None = None, task: str | None = None) -> tuple[str, str, str]:
    """Return (filename, media type, body) for a download."""
    if fmt not in FORMATS:
        raise db.ChronicleError(f"format must be one of {', '.join(FORMATS)}", 400)
    media_type, extension = FORMATS[fmt]
    saved = get(context_id) if context_id else None
    if saved:
        session_id = session_id or saved["session_id"]
        minutes = minutes or saved["minutes"]
        task = task or saved["task"]
        preset_id = preset_id or saved["preset"]
    chosen = preset(preset_id)
    scope = window(session_id, minutes)
    name = _slug(f"chronicle-{scope['label']}-{chosen['id']}")
    if fmt == "md":
        if saved and chosen["id"] == saved["preset"]:
            body = saved["markdown"]
        else:
            body = build(task, chosen["id"], session_id, minutes)["markdown"]
        return f"{name}.md", media_type, body
    data = graph.export(session_id, limit=600)
    if fmt == "json":
        pack = build(task, chosen["id"], session_id, minutes)
        payload = {"chronicle": "context", "preset": chosen["id"], "scope": pack["scope"], "context": pack,
                   "graph": {"nodes": data["nodes"], "edges": data["edges"]}}
        return f"{name}.json", media_type, json.dumps(payload, indent=2, default=str)
    if fmt == "graphml":
        return f"{name}.graphml", media_type, _graphml(data)
    return f"{name}.cypher", media_type, _cypher(data)


def _bounds(clauses: list[str], params: list, start: str | None, end: str | None, column: str = "timestamp") -> None:
    if start:
        clauses.append(f"{column} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{column} <= ?")
        params.append(end)


def notes(meeting: dict) -> dict:
    """Plain-text meeting notes from the transcript: who spoke, decisions, and action items."""
    segments = meeting.get("segments") or []
    talk: dict[str, int] = {}
    decisions, actions = [], []
    for segment in segments:
        who = segment.get("speaker_name") or "Unnamed speaker"
        text = " ".join((segment.get("transcription") or "").split())
        talk[who] = talk.get(who, 0) + len(text.split())
        if not text:
            continue
        line = {"speaker": who, "text": text, "timestamp": segment.get("timestamp")}
        if _DECISION.search(text):
            decisions.append(line)
        elif _ACTION.search(text):
            actions.append(line)
    start, end = meeting.get("started_at"), meeting.get("ended_at")
    seconds = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() if start and end else 0
    people = sorted(talk, key=lambda name: -talk[name])
    summary = f"{format_duration(seconds)} with {', '.join(people) or 'no named speakers'}." if segments else "No transcript."
    return {"summary": summary, "participants": people, "decisions": decisions[:8], "action_items": actions[:12]}


def _notes_markdown(meeting: dict) -> str:
    info = meeting["notes"]
    lines = [f"## {meeting.get('title') or 'Meeting'}", info["summary"]]
    for heading, key in (("Decisions", "decisions"), ("Action items", "action_items")):
        if info[key]:
            lines += ["", f"### {heading}"] + [f"- {item['speaker']}: {item['text']}" for item in info[key]]
    return "\n".join(lines)


def meetings_in(start: str | None, end: str | None) -> list[dict]:
    from chronicle import meetings as meeting_store

    found = []
    for meeting in meeting_store.list_meetings():
        if start and (meeting.get("ended_at") or meeting.get("started_at") or "") < start:
            continue
        if end and (meeting.get("started_at") or "") > end:
            continue
        meeting["notes"] = notes(meeting)
        meeting["notes_markdown"] = _notes_markdown(meeting)
        found.append(meeting)
    return found


def detail(session_id: int | None = None, start: str | None = None, end: str | None = None,
           minutes: int | None = None) -> dict:
    """Everything the context page shows for one scope: frames, timeline, events, URLs, meetings."""
    scope = window(session_id, minutes, start, end)
    start, end = scope["start"], scope["end"]
    clauses, params = ["1=1"], []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    else:
        _bounds(clauses, params, start, end)
    event_clauses, event_params = ["1=1"], []
    _bounds(event_clauses, event_params, start, end)
    with db.conn() as connection:
        frames = connection.execute(
            f"SELECT * FROM frames WHERE {' AND '.join(clauses)} ORDER BY timestamp, id LIMIT 2000", params
        ).fetchall()
        events = connection.execute(
            f"SELECT * FROM ui_events WHERE {' AND '.join(event_clauses)} ORDER BY timestamp, id LIMIT 600", event_params
        ).fetchall()
    apps: dict[str, dict] = {}
    urls: dict[str, dict] = {}
    for frame in frames:
        seconds = frame["duration_secs"] or 0
        bucket = apps.setdefault(frame["app_name"], {"app_name": frame["app_name"], "seconds": 0, "frames": 0})
        bucket["seconds"] += seconds
        bucket["frames"] += 1
        if frame["browser_url"]:
            entry = urls.setdefault(frame["browser_url"], {"url": frame["browser_url"], "title": frame["window_name"],
                                                           "seconds": 0, "frames": 0, "first_frame": frame["id"]})
            entry["seconds"] += seconds
            entry["frames"] += 1
    blocks = search._blocks(frames, [])
    return {
        "scope": {k: scope[k] for k in ("session_id", "start", "end", "label")},
        "session": scope.get("session"),
        "frames": [search.frame_public(frame) for frame in frames],
        "blocks": blocks,
        "events": [dict(row) for row in events],
        "apps": sorted(apps.values(), key=lambda item: -item["seconds"]),
        "urls": sorted(urls.values(), key=lambda item: -item["seconds"])[:100],
        "meetings": meetings_in(start, end),
        "tasks": graph.tasks(session_id, since=None if session_id else start)[-60:],
    }


def find(q: str, session_id: int | None = None, start: str | None = None, end: str | None = None,
         minutes: int | None = None, limit: int = 20) -> dict:
    """Search one context for nodes, screen text, transcripts, system events, URLs, and saved context files."""
    q = (q or "").strip()
    if not q:
        return {"q": q, "nodes": [], "content": [], "urls": [], "contexts": []}
    scope = window(session_id, minutes, start, end)
    start, end = scope["start"], scope["end"]
    content = search.query(q=q, limit=limit, start_time=start, end_time=end)["data"]
    like = f"%{q.lower()}%"
    clauses, params = ["browser_url IS NOT NULL", "(lower(browser_url) LIKE ? OR lower(window_name) LIKE ?)"], [like, like]
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    else:
        _bounds(clauses, params, start, end)
    saved_clauses, saved_params = ["(lower(markdown) LIKE ? OR lower(title) LIKE ?)"], [like, like]
    if session_id:
        saved_clauses.append("session_id = ?")
        saved_params.append(session_id)
    with db.conn() as connection:
        urls = connection.execute(
            f"""
            SELECT browser_url AS url, MAX(window_name) AS title, COUNT(*) AS frames, MIN(id) AS first_frame
            FROM frames WHERE {' AND '.join(clauses)} GROUP BY browser_url ORDER BY frames DESC LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
        saved = connection.execute(
            f"SELECT id, title, preset, created_at, markdown FROM contexts WHERE {' AND '.join(saved_clauses)} ORDER BY id DESC LIMIT ?",
            (*saved_params, limit),
        ).fetchall()
    hits = []
    for row in saved:
        text = row["markdown"]
        at = text.lower().find(q.lower())
        hits.append({"id": row["id"], "title": row["title"], "preset": row["preset"], "created_at": row["created_at"],
                     "snippet": " ".join(text[max(0, at - 80): at + 160].split()) if at >= 0 else ""})
    return {
        "q": q,
        "nodes": graph.find(q, limit=limit, session_id=session_id),
        "content": content,
        "urls": [dict(row) for row in urls],
        "contexts": hits,
    }


def overview(days: int = 7, recent: int = 5) -> dict:
    """Home page: the last few sessions with their top apps and topics, and time per app per day."""
    zone = db.local_zone()
    today = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    since = utc_iso(today - timedelta(days=days - 1))
    with db.conn() as connection:
        rows = connection.execute(
            "SELECT timestamp, app_name, duration_secs FROM frames WHERE timestamp >= ? ORDER BY timestamp", (since,)
        ).fetchall()
        totals = connection.execute(
            "SELECT COUNT(*) AS frames, COALESCE(SUM(duration_secs), 0) AS seconds FROM frames"
        ).fetchone()
    by_app: dict[str, float] = {}
    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        seconds = row["duration_secs"] or 0
        day = datetime.fromisoformat(row["timestamp"]).astimezone(zone).date().isoformat()
        by_app[row["app_name"]] = by_app.get(row["app_name"], 0) + seconds
        by_day.setdefault(day, {})
        by_day[day][row["app_name"]] = by_day[day].get(row["app_name"], 0) + seconds
    day_list = [(today - timedelta(days=offset)).date().isoformat() for offset in range(days - 1, -1, -1)]
    recent_sessions = []
    for item in sessions.list_sessions(recent):
        full = sessions.get(item["id"])
        try:
            summary = graph.summary(item["id"], top=5)
            topics = [entry["label"] for entry in summary["top"].get("topic", [])][:4]
            queries = [entry["label"] for entry in summary["top"].get("query", [])][:3]
            tasks = [entry["label"] for entry in summary.get("tasks", [])][-3:]
        except db.ChronicleError:
            topics, queries, tasks = [], [], []
        recent_sessions.append({**full, "topics": topics, "queries": queries, "tasks": tasks})
    return {
        "days": day_list,
        "since": since,
        "apps": [{"app_name": app, "seconds": round(seconds)} for app, seconds in sorted(by_app.items(), key=lambda kv: -kv[1])],
        "daily": [{"date": day, "seconds": round(sum(by_day.get(day, {}).values())), "apps": by_day.get(day, {})} for day in day_list],
        "sessions": recent_sessions,
        "totals": {"frames": totals["frames"], "seconds": round(totals["seconds"]), "contexts": len(list_saved(500))},
    }

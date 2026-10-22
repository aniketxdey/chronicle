"""Context packs: what a computer-use agent should know before it acts.

A pack is compact markdown (for the model) plus the same data as JSON (for code).
It covers the current screen, recent activity, learned workflow transitions, the
part of the knowledge graph relevant to the task, and cited evidence.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from chronicle import db, graph, search, sessions
from chronicle.timeutil import format_duration, utc_iso

SNIPPET = 280


def _local(stamp: str | None) -> str:
    if not stamp:
        return ""
    return datetime.fromisoformat(stamp).astimezone(db.local_zone()).strftime("%a %H:%M")


def _clip(text: str | None, size: int = SNIPPET) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= size else flat[: size - 1] + "…"


def _short_url(url: str | None, size: int = 120) -> str:
    if not url:
        return ""
    parts = urlsplit(url)
    if not parts.netloc:
        return _clip(url, size)
    short = f"{parts.scheme}://{parts.netloc}{parts.path}" + ("?…" if parts.query else "")
    return _clip(short, size)


def _window_for(session_id: int | None, minutes: int | None) -> tuple[str | None, str | None, str]:
    if session_id:
        item = sessions.get(session_id)
        end = item["stopped_at"] or (item["ends_at"] if item["status"] != "recording" else None) or utc_iso()
        return item["started_at"], end, f"recording {session_id}" + (f" ({item['label']})" if item.get("label") else "")
    if minutes:
        start = utc_iso(datetime.now(timezone.utc) - timedelta(minutes=minutes))
        return start, None, f"last {format_duration(minutes * 60)}"
    with db.conn() as connection:
        latest = connection.execute("SELECT MAX(timestamp) AS t FROM frames").fetchone()["t"]
    if latest is None:
        return None, None, "no history"
    end = datetime.fromisoformat(latest)
    return utc_iso(end - timedelta(hours=2)), latest, "the most recent 2h of history"


def _blocks(rows) -> list[dict]:
    blocks: list[dict] = []
    for row in rows:
        seconds = row["duration_secs"] or 0
        if blocks and blocks[-1]["app"] == row["app_name"] and blocks[-1]["window"] == row["window_name"]:
            blocks[-1]["seconds"] += seconds
            blocks[-1]["end"] = row["timestamp"]
            blocks[-1]["frames"].append(row["id"])
            continue
        blocks.append({
            "app": row["app_name"], "window": row["window_name"], "url": row["browser_url"],
            "start": row["timestamp"], "end": row["timestamp"], "seconds": seconds, "frames": [row["id"]],
        })
    return blocks


def build(
    task: str | None = None,
    minutes: int | None = None,
    session_id: int | None = None,
    max_nodes: int = 24,
    max_evidence: int = 6,
) -> dict:
    max_nodes = max(4, min(int(max_nodes), 80))
    max_evidence = max(0, min(int(max_evidence), 20))
    start, end, window_label = _window_for(session_id, minutes)
    active = sessions.active()
    with db.conn() as connection:
        latest = connection.execute("SELECT * FROM frames ORDER BY timestamp DESC, id DESC LIMIT 1").fetchone()
        clauses, params = ["1=1"], []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        else:
            if start:
                clauses.append("timestamp >= ?")
                params.append(start)
            if end:
                clauses.append("timestamp <= ?")
                params.append(end)
        recent = connection.execute(
            f"SELECT * FROM frames WHERE {' AND '.join(clauses)} ORDER BY timestamp, id", params
        ).fetchall()
        events = connection.execute(
            f"""
            SELECT * FROM ui_events WHERE {' AND '.join(clauses)}
              AND event_type IN ('url_change', 'clipboard', 'idle_start', 'idle_end', 'session_start', 'session_stop')
            ORDER BY timestamp DESC LIMIT 8
            """,
            params,
        ).fetchall()

    blocks = _blocks(recent)
    apps: dict[str, float] = {}
    for block in blocks:
        apps[block["app"]] = apps.get(block["app"], 0) + block["seconds"]

    scope_session = session_id
    sub = graph.subgraph(task, session_id=scope_session, max_nodes=max_nodes)
    summary = graph.summary(session_id=scope_session, top=6)
    labels = {node["id"]: node for node in sub["nodes"]}

    evidence: list[dict] = []
    if task and max_evidence:
        found = search.query(q=task, limit=max_evidence, start_time=start if session_id else None,
                             end_time=end if session_id else None)
        for item in found["data"]:
            body = item["content"]
            evidence.append({
                "type": item["type"].lower(),
                "timestamp": body.get("timestamp"),
                "frame_id": body.get("frame_id"),
                "app": body.get("app_name") or body.get("speaker_name"),
                "window": body.get("window_name"),
                "url": body.get("browser_url"),
                "text": _clip(body.get("text")),
            })

    now = None
    if latest is not None:
        now = {
            "frame_id": latest["id"], "timestamp": latest["timestamp"], "app": latest["app_name"],
            "window": latest["window_name"], "url": latest["browser_url"],
            "text": _clip(re.sub(r"https?://\S+", "", latest["full_text"] or ""), 600),
            "has_image": bool(latest["snapshot_path"]),
        }

    pack = {
        "task": task,
        "generated_at": utc_iso(),
        "window": {"label": window_label, "start": start, "end": end, "session_id": session_id},
        "recording": {"active": active is not None, "session_id": active["id"] if active else None,
                      "remaining_secs": active["remaining_secs"] if active else None},
        "now": now,
        "apps": [{"app": app, "seconds": round(seconds)} for app, seconds in sorted(apps.items(), key=lambda kv: -kv[1])[:8]],
        "recent": [
            {**block, "seconds": round(block["seconds"]), "frames": block["frames"][-3:]} for block in blocks[-12:]
        ],
        "events": [
            {"type": row["event_type"], "timestamp": row["timestamp"], "app": row["app_name"],
             "text": _short_url(row["text"]) if row["event_type"] == "url_change" else _clip(row["text"], 160)}
            for row in events
        ],
        "transitions": summary["transitions"][:6],
        "graph": {
            "scope": sub["scope"],
            "seeds": sub["seeds"],
            "nodes": [
                {"id": n["id"], "kind": n["kind"], "label": n["label"], "seconds": n["seconds"], "weight": n["weight"],
                 "effective_weight": n["effective_weight"], "last_seen": n["last_seen"],
                 "frames": [e["id"] for e in n["evidence"] if e.get("type") == "frame"][:3]}
                for n in sub["nodes"]
            ],
            "edges": [
                {"src": e["src"], "dst": e["dst"], "rel": e["rel"], "weight": e["weight"], "effective_weight": e["effective_weight"]}
                for e in sub["edges"][: max_nodes * 2]
            ],
        },
        "evidence": evidence,
    }
    pack["markdown"] = render(pack, labels)
    return pack


def render(pack: dict, labels: dict | None = None) -> str:
    labels = labels or {n["id"]: n for n in pack["graph"]["nodes"]}
    lines = ["# Computer context (Chronicle)"]
    if pack["task"]:
        lines.append(f"Task: {pack['task']}")
    lines.append(f"Window: {pack['window']['label']}. Generated {_local(pack['generated_at'])} local time.")
    if pack["recording"]["active"]:
        remaining = pack["recording"]["remaining_secs"]
        lines.append(
            f"Recording {pack['recording']['session_id']} is live"
            + (f", {format_duration(remaining)} left." if remaining is not None else ", until stopped.")
        )
    now = pack["now"]
    if now:
        lines += ["", "## On screen now", f"- {now['app']} · {now['window'] or '(untitled)'} at {_local(now['timestamp'])} (frame {now['frame_id']})"]
        if now["url"]:
            lines.append(f"- URL: {_short_url(now['url'], 300)}")
        if now["text"]:
            lines.append(f"- Visible text: {now['text']}")
    else:
        lines += ["", "Nothing has been recorded yet. Start a recording with start_recording."]
    if pack["apps"]:
        lines += ["", "## Time by app"]
        lines.append(", ".join(f"{item['app']} {format_duration(item['seconds'])}" for item in pack["apps"]))
    if pack["recent"]:
        lines += ["", "## Recent activity (oldest first)"]
        for block in pack["recent"]:
            where = f" <{_short_url(block['url'])}>" if block.get("url") else ""
            lines.append(
                f"- {_local(block['start'])} {block['app']} · {block['window'] or '(untitled)'}{where}"
                f" for {format_duration(block['seconds'])} (frames {', '.join(map(str, block['frames']))})"
            )
    if pack["events"]:
        lines += ["", "## System events"]
        for event in pack["events"]:
            lines.append(f"- {_local(event['timestamp'])} {event['type']}: {event['text']}")
    if pack["transitions"]:
        lines += ["", "## Habitual transitions"]
        for item in pack["transitions"]:
            lines.append(f"- {item['from']} → {item['to']} ({int(item['count'])}×)")
    nodes = [n for n in pack["graph"]["nodes"] if n["kind"] not in {"window", "session"}]
    if nodes:
        lines += ["", f"## Knowledge graph ({pack['graph']['scope']})"]
        for node in nodes:
            spent = f", {format_duration(node['seconds'])}" if node["seconds"] else ""
            refs = f", frames {', '.join(map(str, node['frames']))}" if node["frames"] else ""
            lines.append(f"- [{node['kind']}] {node['label']} (`{node['id']}`{spent}{refs})")
        relations = [e for e in pack["graph"]["edges"] if e["rel"] not in {"has_window"}][:16]
        if relations:
            lines.append("Relations:")
            for edge in relations:
                left = labels.get(edge["src"], {}).get("label", edge["src"])
                right = labels.get(edge["dst"], {}).get("label", edge["dst"])
                lines.append(f"- {left} —{edge['rel']}→ {right} ({int(edge['weight'])})")
    if pack["evidence"]:
        lines += ["", "## Evidence for the task"]
        for item in pack["evidence"]:
            source = f"frame {item['frame_id']}" if item.get("frame_id") else item["type"]
            where = " · ".join(part for part in (item.get("app"), item.get("window")) if part)
            lines.append(f"- {_local(item['timestamp'])} {where} ({source}): {item['text']}")
    lines += [
        "",
        "Tools: get_frame(frame_id, include_image=true) shows a screenshot; query_graph and get_entity explore the graph; "
        "search_history does full-text search.",
    ]
    return "\n".join(lines)

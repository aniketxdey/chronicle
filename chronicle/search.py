"""Search, timeline, and activity over local history."""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta

from chronicle import db
from chronicle.timeutil import parse_time, utc_iso

_TOKEN = re.compile(r"[A-Za-z0-9_./:#@+-]{2,}")


def fts_expr(query: str, mode: str = "AND") -> str | None:
    tokens = _TOKEN.findall(query or "")
    if not tokens:
        return None
    parts = [f'"{token.replace(chr(34), "")}"' for token in tokens[:12] if token.replace('"', "")]
    if not parts:
        return None
    joiner = " OR " if mode == "OR" else " AND "
    return joiner.join(parts)


def _window(start_time: str | None, end_time: str | None) -> tuple[str | None, str | None, dict | None]:
    zone = db.local_zone()
    start = parse_time(start_time, zone) if start_time else None
    end = parse_time(end_time, zone) if end_time else None
    return start, end, None


def _like(value: str | None) -> str | None:
    if not value:
        return None
    return f"%{value.strip()}%"


def query(
    q: str | None = None,
    content_type: str = "all",
    limit: int = 20,
    offset: int = 0,
    start_time: str | None = None,
    end_time: str | None = None,
    app_name: str | None = None,
    window_name: str | None = None,
    browser_url: str | None = None,
    speaker_name: str | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    focused: bool | None = None,
    tags: list[str] | None = None,
    include_related: bool = False,
) -> dict:
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    content_type = (content_type or "all").lower()
    start, end, limited = _window(start_time, end_time)
    collected = _collect(
        q=q or "",
        expr=fts_expr(q or "", "AND") if q else None,
        content_type=content_type,
        limit=limit,
        offset=offset,
        start=start,
        end=end,
        app_name=app_name,
        window_name=window_name,
        browser_url=browser_url,
        speaker_name=speaker_name,
        min_length=min_length,
        max_length=max_length,
        focused=focused,
        tags=tags or [],
        include_related=include_related,
    )
    if q and collected["pagination"]["total"] == 0:
        collected = _collect(
            q=q or "",
            expr=fts_expr(q or "", "OR"),
            content_type=content_type,
            limit=limit,
            offset=offset,
            start=start,
            end=end,
            app_name=app_name,
            window_name=window_name,
            browser_url=browser_url,
            speaker_name=speaker_name,
            min_length=min_length,
            max_length=max_length,
            focused=focused,
            tags=tags or [],
            include_related=include_related,
            match_mode="or",
        )
    if limited:
        collected["plan_window"] = limited
    return collected


def _collect(**filters) -> dict:
    limit = filters["limit"]
    offset = filters["offset"]
    match_mode = filters.get("match_mode", "and")
    branches: list[str] = []
    params: list = []
    for kind in _kinds(filters["content_type"], filters["speaker_name"]):
        sql, branch_params = _branch(kind, filters)
        if sql:
            branches.append(sql)
            params.extend(branch_params)
    if not branches:
        return {"data": [], "pagination": {"limit": limit, "offset": offset, "total": 0}, "match_mode": match_mode}
    union = " UNION ALL ".join(branches)
    try:
        with db.conn() as connection:
            total = connection.execute(f"SELECT COUNT(*) AS n FROM ({union})", params).fetchone()["n"]
            pointers = connection.execute(
                f"SELECT kind, id, timestamp FROM ({union}) ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            data = [_hydrate(connection, row["kind"], row["id"]) for row in pointers]
            related = _related(connection, data) if filters["include_related"] and filters["tags"] else None
    except sqlite3.OperationalError:
        if filters["expr"] and match_mode == "and":
            return {"data": [], "pagination": {"limit": limit, "offset": offset, "total": 0}, "match_mode": match_mode}
        raise
    payload = {
        "data": data,
        "pagination": {"limit": limit, "offset": offset, "total": total},
        "match_mode": match_mode,
    }
    if related is not None:
        payload["related"] = related
    return payload


def _kinds(content_type: str, speaker_name: str | None) -> list[str]:
    if speaker_name:
        return ["audio"]
    return {
        "all": ["accessibility", "ocr", "audio", "input", "memory"],
        "accessibility": ["accessibility"],
        "ocr": ["ocr"],
        "audio": ["audio"],
        "input": ["input"],
        "memory": ["memory"],
    }.get(content_type, ["accessibility", "ocr", "audio", "input", "memory"])


def _branch(kind: str, filters: dict) -> tuple[str | None, list]:
    if kind in {"accessibility", "ocr"}:
        return _frame_branch(kind, filters)
    if kind == "audio":
        return _audio_branch(filters)
    if kind == "input":
        return _event_branch(filters)
    if kind == "memory":
        return _memory_branch(filters)
    return None, []


def _time_sql(column: str, start: str | None, end: str | None, params: list) -> str:
    clauses = []
    if start:
        clauses.append(f"{column} >= ?")
        params.append(start)
    if end:
        clauses.append(f"{column} <= ?")
        params.append(end)
    return " AND ".join(clauses)


def _frame_branch(source: str, filters: dict) -> tuple[str, list]:
    params: list = [source]
    clauses = ["text_source = ?"]
    time_sql = _time_sql("timestamp", filters["start"], filters["end"], params)
    if time_sql:
        clauses.append(time_sql)
    if filters["app_name"]:
        clauses.append("app_name LIKE ?")
        params.append(_like(filters["app_name"]))
    if filters["window_name"]:
        clauses.append("window_name LIKE ?")
        params.append(_like(filters["window_name"]))
    if filters["browser_url"]:
        clauses.append("COALESCE(browser_url, '') LIKE ?")
        params.append(_like(filters["browser_url"]))
    if filters["focused"] is not None:
        clauses.append("focused = ?")
        params.append(1 if filters["focused"] else 0)
    if filters["min_length"]:
        clauses.append("length(full_text) >= ?")
        params.append(filters["min_length"])
    if filters["max_length"]:
        clauses.append("length(full_text) <= ?")
        params.append(filters["max_length"])
    if filters["expr"]:
        clauses.append("id IN (SELECT frame_id FROM frames_fts WHERE frames_fts MATCH ?)")
        params.append(filters["expr"])
    for tag in filters["tags"]:
        clauses.append("id IN (SELECT content_id FROM tags WHERE content_type = 'frame' AND tag = ?)")
        params.append(tag)
    where = " AND ".join(clauses)
    return f"SELECT '{source}' AS kind, id, timestamp FROM frames WHERE {where}", params


def _audio_branch(filters: dict) -> tuple[str | None, list]:
    if filters["app_name"] or filters["window_name"] or filters["browser_url"] or filters["focused"] is not None:
        return None, []
    params: list = []
    clauses = ["1=1"]
    time_sql = _time_sql("t.timestamp", filters["start"], filters["end"], params)
    if time_sql:
        clauses.append(time_sql)
    if filters["speaker_name"]:
        clauses.append("COALESCE(s.name, '') LIKE ?")
        params.append(_like(filters["speaker_name"]))
    if filters["min_length"]:
        clauses.append("length(t.transcription) >= ?")
        params.append(filters["min_length"])
    if filters["max_length"]:
        clauses.append("length(t.transcription) <= ?")
        params.append(filters["max_length"])
    if filters["expr"]:
        clauses.append("t.id IN (SELECT transcription_id FROM audio_fts WHERE audio_fts MATCH ?)")
        params.append(filters["expr"])
    for tag in filters["tags"]:
        clauses.append("t.id IN (SELECT content_id FROM tags WHERE content_type = 'audio' AND tag = ?)")
        params.append(tag)
    where = " AND ".join(clauses)
    sql = f"""
        SELECT 'audio' AS kind, t.id AS id, t.timestamp AS timestamp
        FROM audio_transcriptions t
        LEFT JOIN speakers s ON s.id = t.speaker_id
        WHERE {where}
    """
    return sql, params


def _event_branch(filters: dict) -> tuple[str | None, list]:
    if filters["browser_url"] or filters["speaker_name"] or filters["focused"] is not None:
        return None, []
    params: list = []
    clauses = ["1=1"]
    time_sql = _time_sql("timestamp", filters["start"], filters["end"], params)
    if time_sql:
        clauses.append(time_sql)
    if filters["app_name"]:
        clauses.append("app_name LIKE ?")
        params.append(_like(filters["app_name"]))
    if filters["window_name"]:
        clauses.append("window_name LIKE ?")
        params.append(_like(filters["window_name"]))
    if filters["min_length"]:
        clauses.append("length(COALESCE(text, '')) >= ?")
        params.append(filters["min_length"])
    if filters["max_length"]:
        clauses.append("length(COALESCE(text, '')) <= ?")
        params.append(filters["max_length"])
    if filters["q"]:
        token_sql, token_params = _token_like("text", filters["q"], filters["expr"])
        clauses.append(token_sql)
        params.extend(token_params)
    where = " AND ".join(clauses)
    return f"SELECT 'input' AS kind, id, timestamp FROM ui_events WHERE {where}", params


def _memory_branch(filters: dict) -> tuple[str | None, list]:
    if any(filters[key] for key in ("app_name", "window_name", "browser_url", "speaker_name")) or filters["focused"] is not None:
        return None, []
    params: list = []
    clauses = ["1=1"]
    time_sql = _time_sql("created_at", filters["start"], filters["end"], params)
    if time_sql:
        clauses.append(time_sql)
    if filters["min_length"]:
        clauses.append("length(content) >= ?")
        params.append(filters["min_length"])
    if filters["max_length"]:
        clauses.append("length(content) <= ?")
        params.append(filters["max_length"])
    if filters["q"]:
        token_sql, token_params = _token_like("content", filters["q"], filters["expr"])
        clauses.append(token_sql)
        params.extend(token_params)
    for tag in filters["tags"]:
        clauses.append("id IN (SELECT content_id FROM tags WHERE content_type = 'memory' AND tag = ?)")
        params.append(tag)
    where = " AND ".join(clauses)
    return f"SELECT 'memory' AS kind, id, created_at AS timestamp FROM memories WHERE {where}", params


def _token_like(column: str, q: str, expr: str | None) -> tuple[str, list]:
    tokens = _TOKEN.findall(q or "")
    if not tokens:
        return "1=1", []
    use_and = expr is None or " OR " not in expr
    joiner = " AND " if use_and else " OR "
    parts = [f"COALESCE({column}, '') LIKE ?" for _ in tokens]
    return f"({joiner.join(parts)})", [f"%{token}%" for token in tokens]


def _hydrate(connection, kind: str, item_id: int) -> dict:
    if kind in {"accessibility", "ocr"}:
        row = connection.execute("SELECT * FROM frames WHERE id = ?", (item_id,)).fetchone()
        return {
            "type": "Accessibility" if kind == "accessibility" else "OCR",
            "content": _frame_content(connection, row),
        }
    if kind == "audio":
        row = connection.execute(
            """
            SELECT t.*, s.name AS speaker_name, c.device_name, c.device_type, c.meeting_id, c.file_path
            FROM audio_transcriptions t
            LEFT JOIN speakers s ON s.id = t.speaker_id
            LEFT JOIN audio_chunks c ON c.id = t.chunk_id
            WHERE t.id = ?
            """,
            (item_id,),
        ).fetchone()
        return {
            "type": "Audio",
            "content": {
                "transcription_id": row["id"],
                "chunk_id": row["chunk_id"],
                "transcription": row["transcription"],
                "text": row["transcription"],
                "speaker_id": row["speaker_id"],
                "speaker_name": row["speaker_name"],
                "timestamp": row["timestamp"],
                "device_name": row["device_name"],
                "device_type": row["device_type"],
                "meeting_id": row["meeting_id"],
                "file_path": row["file_path"],
                "tags": _tags(connection, "audio", row["id"]),
            },
        }
    if kind == "input":
        row = connection.execute("SELECT * FROM ui_events WHERE id = ?", (item_id,)).fetchone()
        return {
            "type": "Input",
            "content": {
                "event_id": row["id"],
                "event_type": row["event_type"],
                "text": row["text"],
                "app_name": row["app_name"],
                "window_name": row["window_name"],
                "timestamp": row["timestamp"],
                "frame_id": row["frame_id"],
            },
        }
    row = connection.execute("SELECT * FROM memories WHERE id = ?", (item_id,)).fetchone()
    return {
        "type": "Memory",
        "content": {
            "memory_id": row["id"],
            "text": row["content"],
            "source": row["source"],
            "timestamp": row["created_at"],
            "frame_id": row["frame_id"],
            "tags": _tags(connection, "memory", row["id"]),
        },
    }


def _frame_content(connection, row) -> dict:
    return {
        "frame_id": row["id"],
        "text": row["full_text"],
        "timestamp": row["timestamp"],
        "app_name": row["app_name"],
        "window_name": row["window_name"],
        "browser_url": row["browser_url"],
        "file_path": row["snapshot_path"],
        "media_url": None if not row["snapshot_path"] else f"/media/{row['snapshot_path']}",
        "focused": bool(row["focused"]),
        "text_source": row["text_source"],
        "capture_trigger": row["capture_trigger"],
        "device_name": row["device_name"],
        "tags": _tags(connection, "frame", row["id"]),
    }


def _tags(connection, content_type: str, content_id: int) -> list[str]:
    return [
        row["tag"]
        for row in connection.execute(
            "SELECT tag FROM tags WHERE content_type = ? AND content_id = ? ORDER BY tag",
            (content_type, content_id),
        ).fetchall()
    ]


def _related(connection, data: list[dict]) -> dict:
    grouped: dict[str, set[str]] = {}
    for item in data:
        content = item["content"]
        if item["type"] in {"Accessibility", "OCR"}:
            content_type, content_id = "frame", content["frame_id"]
        elif item["type"] == "Audio":
            content_type, content_id = "audio", content["transcription_id"]
        elif item["type"] == "Memory":
            content_type, content_id = "memory", content["memory_id"]
        else:
            continue
        for tag in _tags(connection, content_type, content_id):
            namespace, _, value = tag.partition(":")
            key = f"{namespace}s" if namespace in {"person", "project", "customer"} else (namespace or "tags")
            grouped.setdefault(key, set()).add(value or tag)
    return {key: sorted(values) for key, values in grouped.items()}


def keyword(q: str, limit: int = 20) -> dict:
    if not q or not q.strip():
        raise db.ChronicleError("q is required")
    found = query(q=q, content_type="all", limit=limit, offset=0)
    needle = q.strip().lower()
    matches = []
    for item in found["data"]:
        content = item["content"]
        text = content.get("text") or ""
        positions = []
        start = 0
        lowered = text.lower()
        while True:
            index = lowered.find(needle, start)
            if index < 0:
                break
            positions.append({"start": index, "end": index + len(needle)})
            start = index + max(1, len(needle))
        matches.append(
            {
                "type": item["type"],
                "id": content.get("frame_id")
                or content.get("transcription_id")
                or content.get("event_id")
                or content.get("memory_id"),
                "timestamp": content.get("timestamp"),
                "app_name": content.get("app_name"),
                "positions": positions,
                "snippet": _snippet(text, needle),
            }
        )
    return {"data": matches, "pagination": found["pagination"]}


def _snippet(text: str, needle: str, radius: int = 80) -> str:
    lowered = text.lower()
    index = lowered.find(needle.lower())
    if index < 0:
        return text[: radius * 2].strip()
    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def frame_record(frame_id: int):
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM frames WHERE id = ?", (frame_id,)).fetchone()
    if row is None:
        raise db.ChronicleError("frame not found", 404)
    return row


def frame_public(row) -> dict:
    return {
        "frame_id": row["id"],
        "timestamp": row["timestamp"],
        "app_name": row["app_name"],
        "window_name": row["window_name"],
        "browser_url": row["browser_url"],
        "focused": bool(row["focused"]),
        "capture_trigger": row["capture_trigger"],
        "text_source": row["text_source"],
        "text_engine": row["text_engine"],
        "accessibility_text": row["accessibility_text"],
        "ocr_text": row["ocr_text"],
        "text": row["full_text"],
        "file_path": row["snapshot_path"],
        "media_url": None if not row["snapshot_path"] else f"/media/{row['snapshot_path']}",
        "device_name": row["device_name"],
        "duration_secs": row["duration_secs"],
        "session_id": row["session_id"],
    }


def frame_bundle(frame_id: int) -> dict:
    row = frame_record(frame_id)
    with db.conn() as connection:
        elements = connection.execute(
            "SELECT id, role, text, bounds FROM elements WHERE frame_id = ? ORDER BY id",
            (frame_id,),
        ).fetchall()
        nearby = connection.execute(
            """
            SELECT t.id, t.transcription, t.timestamp, s.name AS speaker_name
            FROM audio_transcriptions t
            LEFT JOIN speakers s ON s.id = t.speaker_id
            WHERE t.timestamp >= ? AND t.timestamp <= ?
            ORDER BY t.timestamp
            """,
            (_shift(row["timestamp"], -120), _shift(row["timestamp"], 180)),
        ).fetchall()
        previous_row = connection.execute(
            "SELECT id, app_name, window_name, timestamp FROM frames WHERE timestamp < ? ORDER BY timestamp DESC LIMIT 1",
            (row["timestamp"],),
        ).fetchone()
        next_row = connection.execute(
            "SELECT id, app_name, window_name, timestamp FROM frames WHERE timestamp > ? ORDER BY timestamp ASC LIMIT 1",
            (row["timestamp"],),
        ).fetchone()
        tags = _tags(connection, "frame", frame_id)
        events = connection.execute(
            """
            SELECT id, timestamp, event_type, app_name, text FROM ui_events
            WHERE frame_id = ? OR (timestamp >= ? AND timestamp <= ?)
            ORDER BY timestamp LIMIT 12
            """,
            (frame_id, _shift(row["timestamp"], -30), _shift(row["timestamp"], 30)),
        ).fetchall()
    return {
        "frame": frame_public(row),
        "elements": [_element_public(item) for item in elements],
        "events": [dict(item) for item in events],
        "audio": [dict(item) for item in nearby],
        "previous": None if previous_row is None else dict(previous_row),
        "next": None if next_row is None else dict(next_row),
        "tags": tags,
    }


def _element_public(row) -> dict:
    return {"id": row["id"], "role": row["role"], "text": row["text"], "bounds": parse_bounds(row["bounds"])}


def parse_bounds(raw: str | None) -> dict | None:
    if not raw:
        return None
    parts = str(raw).split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, width, height = (int(float(part)) for part in parts)
    except ValueError:
        return None
    return {"x": x, "y": y, "width": width, "height": height}


def _shift(timestamp: str, seconds: int) -> str:
    return utc_iso(datetime.fromisoformat(timestamp) + timedelta(seconds=seconds))


def elements(q: str | None = None, app_name: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    clauses = ["1=1"]
    params: list = []
    if q:
        expr = fts_expr(q, "AND")
        if expr:
            clauses.append("e.id IN (SELECT element_id FROM elements_fts WHERE elements_fts MATCH ?)")
            params.append(expr)
    if app_name:
        clauses.append("f.app_name LIKE ?")
        params.append(_like(app_name))
    where = " AND ".join(clauses)
    listing = f"""
        SELECT e.id, e.role, e.text, e.bounds, e.frame_id, f.app_name, f.window_name, f.timestamp
        FROM elements e JOIN frames f ON f.id = e.frame_id
        WHERE {where}
        ORDER BY f.timestamp DESC
        LIMIT ? OFFSET ?
    """
    count = f"SELECT COUNT(*) AS n FROM elements e JOIN frames f ON f.id = e.frame_id WHERE {where}"
    with db.conn() as connection:
        total = connection.execute(count, params).fetchone()["n"]
        found = connection.execute(listing, [*params, limit, offset]).fetchall()
    return {
        "data": [
            {
                "id": row["id"],
                "role": row["role"],
                "text": row["text"],
                "bounds": parse_bounds(row["bounds"]),
                "frame_id": row["frame_id"],
                "app_name": row["app_name"],
                "window_name": row["window_name"],
                "timestamp": row["timestamp"],
            }
            for row in found
        ],
        "pagination": {"limit": limit, "offset": offset, "total": total},
    }


def activity_summary(start_time: str | None = None, end_time: str | None = None) -> dict:
    zone = db.local_zone()
    start, end, limited = _window(start_time, end_time)
    if start is None:
        start = utc_iso(datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0))
    if end is None:
        end = utc_iso(datetime.now(zone))
    with db.conn() as connection:
        frames = connection.execute(
            "SELECT * FROM frames WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp",
            (start, end),
        ).fetchall()
        audio_seconds = connection.execute(
            "SELECT COALESCE(SUM(duration_secs), 0) AS n FROM audio_chunks WHERE timestamp >= ? AND timestamp <= ?",
            (start, end),
        ).fetchone()["n"]
        meetings = connection.execute(
            "SELECT id, title, started_at, ended_at FROM meetings WHERE started_at <= ? AND ended_at >= ?",
            (end, start),
        ).fetchall()
    apps: dict[str, dict] = {}
    windows: dict[str, float] = {}
    for frame in frames:
        seconds = frame["duration_secs"] or 0
        bucket = apps.setdefault(frame["app_name"], {"app_name": frame["app_name"], "seconds": 0, "frames": 0})
        bucket["seconds"] += seconds
        bucket["frames"] += 1
        label = f"{frame['app_name']} · {frame['window_name']}"
        windows[label] = windows.get(label, 0) + seconds
    payload = {
        "start_time": start,
        "end_time": end,
        "apps": sorted(apps.values(), key=lambda item: item["seconds"], reverse=True),
        "top_windows": [
            {"label": label, "seconds": seconds}
            for label, seconds in sorted(windows.items(), key=lambda item: item[1], reverse=True)[:6]
        ],
        "audio_seconds": audio_seconds,
        "meetings": [dict(row) for row in meetings],
        "narrative": _narrative(frames, meetings),
    }
    if limited:
        payload["plan_window"] = limited
    return payload


def _narrative(frames, meetings) -> str:
    if not frames:
        return "Nothing is stored in this window yet."
    by_app: dict[str, float] = {}
    for frame in frames:
        by_app[frame["app_name"]] = by_app.get(frame["app_name"], 0) + (frame["duration_secs"] or 0)
    top = ", ".join(name for name, _ in sorted(by_app.items(), key=lambda item: item[1], reverse=True)[:3])
    longest = max(frames, key=lambda frame: frame["duration_secs"] or 0)
    minutes = int(round((longest["duration_secs"] or 0) / 60))
    meeting_bit = ""
    if meetings:
        meeting_bit = " Meetings in this window: " + ", ".join(row["title"] for row in meetings) + "."
    return (
        f"Most of this window was spent in {top}. "
        f"The longest stretch was {longest['app_name']} — {longest['window_name']} ({minutes} min)."
        f"{meeting_bit}"
    )


def timeline(date: str | None = None) -> dict:
    zone = db.local_zone()
    with db.conn() as connection:
        all_frames = connection.execute("SELECT * FROM frames ORDER BY timestamp").fetchall()
        dates: list[str] = []
        seen: set[str] = set()
        for frame in all_frames:
            local_date = datetime.fromisoformat(frame["timestamp"]).astimezone(zone).date().isoformat()
            if local_date not in seen:
                seen.add(local_date)
                dates.append(local_date)
        if not dates:
            today = datetime.now(zone).date().isoformat()
            return {"date": today, "dates": [], "blocks": [], "frames": [], "audio": [], "narrative": "No history yet."}
        chosen = date if date in seen else dates[-1]
        year, month, day = (int(part) for part in chosen.split("-"))
        start_local = datetime(year, month, day, tzinfo=zone)
        start = utc_iso(start_local)
        end = utc_iso(start_local + timedelta(days=1) - timedelta(seconds=1))
        day_frames = [frame for frame in all_frames if start <= frame["timestamp"] <= end]
        audio = connection.execute(
            """
            SELECT t.id, t.transcription, t.timestamp, s.name AS speaker_name, c.meeting_id, m.title AS meeting_title
            FROM audio_transcriptions t
            LEFT JOIN speakers s ON s.id = t.speaker_id
            LEFT JOIN audio_chunks c ON c.id = t.chunk_id
            LEFT JOIN meetings m ON m.id = c.meeting_id
            WHERE t.timestamp >= ? AND t.timestamp <= ?
            ORDER BY t.timestamp
            """,
            (start, end),
        ).fetchall()
    blocks = _blocks(day_frames, list(audio))
    summary = activity_summary(start, end)
    return {
        "date": chosen,
        "dates": dates,
        "blocks": blocks,
        "frames": [frame_public(frame) for frame in day_frames],
        "audio": [dict(row) for row in audio],
        "narrative": summary["narrative"],
        "apps": summary["apps"],
    }


def _blocks(frames, audio_rows) -> list[dict]:
    blocks = []
    for frame in frames:
        start = datetime.fromisoformat(frame["timestamp"])
        duration = frame["duration_secs"] or 120
        end = start + timedelta(seconds=duration)
        has_audio = False
        for row in audio_rows:
            audio_time = datetime.fromisoformat(row["timestamp"])
            if start <= audio_time <= end:
                has_audio = True
                break
        blocks.append(
            {
                "start": utc_iso(start),
                "end": utc_iso(end),
                "app_name": frame["app_name"],
                "window_name": frame["window_name"],
                "browser_url": frame["browser_url"],
                "frame_id": frame["id"],
                "minutes": max(1, int(round(duration / 60))),
                "has_audio": has_audio,
                "text_source": frame["text_source"],
                "media_url": None if not frame["snapshot_path"] else f"/media/{frame['snapshot_path']}",
            }
        )
    return blocks


def raw_sql(sql: str) -> dict:
    statement = (sql or "").strip().rstrip(";").strip()
    if not statement:
        raise db.ChronicleError("sql is required")
    if ";" in statement:
        raise db.ChronicleError("only a single read-only statement is allowed")
    if not re.match(r"^(select|with)\b", statement, re.I):
        raise db.ChronicleError("only SELECT statements are allowed")
    if re.search(
        r"\b(insert|update|delete|drop|alter|attach|detach|pragma|replace|create|vacuum|reindex)\b",
        statement,
        re.I,
    ):
        raise db.ChronicleError("only read-only SQL is allowed")
    with db.conn() as connection:
        try:
            cursor = connection.execute(statement)
        except sqlite3.Error as exc:
            raise db.ChronicleError(str(exc)) from exc
        found = cursor.fetchmany(500)
        columns = [] if cursor.description is None else [item[0] for item in cursor.description]
    return {"columns": columns, "rows": [dict(row) for row in found], "truncated": len(found) == 500}


def list_memories(limit: int = 50) -> list[dict]:
    with db.conn() as connection:
        found = connection.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
    return [dict(row) for row in found]


def create_memory(content: str, source: str = "user", frame_id: int | None = None) -> dict:
    from chronicle.redact import redact

    text = redact(content) if db.redact_enabled() else content
    timestamp = utc_iso()
    with db.conn() as connection:
        cursor = connection.execute(
            "INSERT INTO memories(content, source, created_at, frame_id) VALUES (?, ?, ?, ?)",
            (text, source, timestamp, frame_id),
        )
        memory_id = int(cursor.lastrowid)
    return {"id": memory_id, "content": text, "source": source, "created_at": timestamp, "frame_id": frame_id}


def add_tags(content_type: str, content_id: int, tags: list[str]) -> dict:
    allowed = {"frame", "audio", "memory", "meeting"}
    if content_type not in allowed:
        raise db.ChronicleError(f"content type must be one of {sorted(allowed)}")
    clean = [tag.strip() for tag in tags if tag and tag.strip()]
    with db.conn() as connection:
        for tag in clean:
            exists = connection.execute(
                "SELECT 1 FROM tags WHERE content_type = ? AND content_id = ? AND tag = ?",
                (content_type, content_id, tag),
            ).fetchone()
            if exists is None:
                connection.execute(
                    "INSERT INTO tags(content_type, content_id, tag) VALUES (?, ?, ?)",
                    (content_type, content_id, tag),
                )
        stored = _tags(connection, content_type, content_id)
    return {"content_type": content_type, "content_id": content_id, "tags": stored}


def remove_tags(content_type: str, content_id: int, tags: list[str]) -> dict:
    with db.conn() as connection:
        for tag in tags:
            connection.execute(
                "DELETE FROM tags WHERE content_type = ? AND content_id = ? AND tag = ?",
                (content_type, content_id, tag),
            )
        stored = _tags(connection, content_type, content_id)
    return {"content_type": content_type, "content_id": content_id, "tags": stored}

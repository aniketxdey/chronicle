"""Recording sessions: capture everything for a preset amount of time, then build its graph."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chronicle import db
from chronicle.timeutil import format_duration, parse_duration, utc_iso


def _public(row) -> dict:
    item = dict(row)
    now = datetime.now(timezone.utc)
    started = datetime.fromisoformat(item["started_at"])
    finished = item["stopped_at"] or (item["ends_at"] if item["status"] != "recording" else None)
    end = datetime.fromisoformat(finished) if finished else now
    item["elapsed_secs"] = max(0, int((end - started).total_seconds()))
    item["elapsed"] = format_duration(item["elapsed_secs"])
    if item["status"] == "recording" and item["ends_at"]:
        item["remaining_secs"] = max(0, int((datetime.fromisoformat(item["ends_at"]) - now).total_seconds()))
    else:
        item["remaining_secs"] = None
    item["scope"] = f"session:{item['id']}"
    return item


def active() -> dict | None:
    finalize_due()
    with db.conn() as connection:
        row = connection.execute(
            "SELECT * FROM sessions WHERE status = 'recording' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _public(row) if row else None


def active_id() -> int | None:
    with db.conn() as connection:
        row = connection.execute("SELECT id FROM sessions WHERE status = 'recording' ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def start(duration: str | int | None = None, label: str | None = None, interval_secs: int | None = None) -> dict:
    seconds = parse_duration(duration)
    if seconds is not None and not 10 <= seconds <= 7 * 86400:
        raise db.ChronicleError("duration must be between 10 seconds and 7 days", 400)
    interval = int(interval_secs or db.get_setting("capture_interval_secs", "5") or 5)
    if not 1 <= interval <= 600:
        raise db.ChronicleError("interval_secs must be between 1 and 600", 400)
    current = active()
    if current:
        raise db.ChronicleError(f"recording {current['id']} is already running; stop it first", 409)
    now = datetime.now(timezone.utc)
    ends_at = utc_iso(now + timedelta(seconds=seconds)) if seconds else None
    with db.conn() as connection:
        cursor = connection.execute(
            """
            INSERT INTO sessions(label, started_at, ends_at, status, interval_secs)
            VALUES (?, ?, ?, 'recording', ?)
            """,
            ((label or "").strip() or None, utc_iso(now), ends_at, interval),
        )
        session_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO ui_events(timestamp, event_type, app_name, window_name, text, session_id) VALUES (?, 'session_start', '', '', ?, ?)",
            (utc_iso(now), f"Recording started for {format_duration(seconds) if seconds else 'an open-ended window'}", session_id),
        )
    db.set_setting("last_capture_error", "")
    return get(session_id)


def stop(session_id: int | None = None, status: str = "stopped") -> dict:
    target = session_id or active_id()
    if target is None:
        raise db.ChronicleError("no recording is running", 404)
    stamp = utc_iso()
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM sessions WHERE id = ?", (target,)).fetchone()
        if row is None:
            raise db.ChronicleError("session not found", 404)
        if row["status"] == "recording":
            ended = stamp
            if status == "complete" and row["ends_at"] and row["ends_at"] < stamp:
                ended = row["ends_at"]
            connection.execute(
                "INSERT INTO ui_events(timestamp, event_type, app_name, window_name, text, session_id) VALUES (?, 'session_stop', '', '', ?, ?)",
                (ended, f"Recording {status}", target),
            )
            counts = connection.execute(
                "SELECT (SELECT COUNT(*) FROM frames WHERE session_id = ?) AS f, (SELECT COUNT(*) FROM ui_events WHERE session_id = ?) AS e",
                (target, target),
            ).fetchone()
            connection.execute(
                "UPDATE sessions SET status = ?, stopped_at = ?, frames = ?, events = ? WHERE id = ?",
                (status, ended, counts["f"], counts["e"], target),
            )
    from chronicle import graph

    graph.build(target)
    graph.build(None)
    return get(target)


def finalize_due() -> list[int]:
    stamp = utc_iso()
    with db.conn() as connection:
        due = [
            int(row["id"])
            for row in connection.execute(
                "SELECT id FROM sessions WHERE status = 'recording' AND ends_at IS NOT NULL AND ends_at <= ?", (stamp,)
            )
        ]
    for session_id in due:
        stop(session_id, status="complete")
    return due


def refresh_counts(session_id: int) -> None:
    with db.conn() as connection:
        connection.execute(
            """
            UPDATE sessions SET
              frames = (SELECT COUNT(*) FROM frames WHERE session_id = ?),
              events = (SELECT COUNT(*) FROM ui_events WHERE session_id = ?)
            WHERE id = ?
            """,
            (session_id, session_id, session_id),
        )


def get(session_id: int) -> dict:
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise db.ChronicleError("session not found", 404)
    item = _public(row)
    with db.conn() as connection:
        apps = connection.execute(
            """
            SELECT app_name, COUNT(*) AS frames, COALESCE(SUM(duration_secs), 0) AS seconds
            FROM frames WHERE session_id = ? GROUP BY app_name ORDER BY seconds DESC LIMIT 8
            """,
            (session_id,),
        ).fetchall()
    item["apps"] = [dict(app) for app in apps]
    return item


def list_sessions(limit: int = 50) -> list[dict]:
    finalize_due()
    with db.conn() as connection:
        rows = connection.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),)).fetchall()
    return [_public(row) for row in rows]


def delete(session_id: int) -> dict:
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise db.ChronicleError("session not found", 404)
        if row["status"] == "recording":
            raise db.ChronicleError("stop the recording before deleting it", 409)
        paths = [r["snapshot_path"] for r in connection.execute("SELECT snapshot_path FROM frames WHERE session_id = ?", (session_id,))]
        connection.execute(
            "DELETE FROM elements_fts WHERE element_id IN (SELECT e.id FROM elements e JOIN frames f ON f.id = e.frame_id WHERE f.session_id = ?)",
            (session_id,),
        )
        connection.execute("DELETE FROM frames_fts WHERE frame_id IN (SELECT id FROM frames WHERE session_id = ?)", (session_id,))
        connection.execute("DELETE FROM elements WHERE frame_id IN (SELECT id FROM frames WHERE session_id = ?)", (session_id,))
        connection.execute("DELETE FROM tags WHERE content_type = 'frame' AND content_id IN (SELECT id FROM frames WHERE session_id = ?)", (session_id,))
        connection.execute("DELETE FROM frames WHERE session_id = ?", (session_id,))
        connection.execute("DELETE FROM ui_events WHERE session_id = ?", (session_id,))
        connection.execute("DELETE FROM kg_nodes WHERE scope = ?", (f"session:{session_id}",))
        connection.execute("DELETE FROM kg_edges WHERE scope = ?", (f"session:{session_id}",))
        connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    for path in paths:
        if path:
            (db.media_root() / path).unlink(missing_ok=True)
    from chronicle import graph

    graph.build(None)
    return {"deleted": session_id, "frames": len(paths)}

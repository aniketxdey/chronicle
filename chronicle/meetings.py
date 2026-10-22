"""Meetings and speaker labels."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chronicle import db
from chronicle.timeutil import utc_iso


def list_meetings() -> list[dict]:
    with db.conn() as connection:
        meetings = connection.execute("SELECT * FROM meetings ORDER BY started_at DESC").fetchall()
        payload = []
        for meeting in meetings:
            segments = connection.execute(
                """
                SELECT t.id, t.transcription, t.timestamp, t.speaker_id, s.name AS speaker_name,
                       c.device_name, c.device_type
                FROM audio_transcriptions t
                JOIN audio_chunks c ON c.id = t.chunk_id
                LEFT JOIN speakers s ON s.id = t.speaker_id
                WHERE c.meeting_id = ?
                ORDER BY t.timestamp
                """,
                (meeting["id"],),
            ).fetchall()
            speakers = []
            seen = set()
            for segment in segments:
                key = segment["speaker_id"]
                if key in seen:
                    continue
                seen.add(key)
                speakers.append({"id": segment["speaker_id"], "name": segment["speaker_name"]})
            payload.append({**dict(meeting), "speakers": speakers, "segments": [dict(row) for row in segments]})
    return payload


def status() -> dict:
    now = datetime.now(timezone.utc)
    current = None
    for meeting in list_meetings():
        ended = datetime.fromisoformat(meeting["ended_at"])
        started = datetime.fromisoformat(meeting["started_at"])
        if started <= now <= ended + timedelta(minutes=2):
            current = {"id": meeting["id"], "title": meeting["title"], "started_at": meeting["started_at"]}
            break
    return {"in_meeting": current is not None, "meeting": current}


def merge(ids: list[int]) -> dict:
    if len(ids) < 2:
        raise db.ChronicleError("merge needs at least two meetings")
    with db.conn() as connection:
        rows = []
        for meeting_id in ids:
            row = connection.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,)).fetchone()
            if row is None:
                raise db.ChronicleError(f"meeting {meeting_id} not found", 404)
            rows.append(row)
        keep = rows[0]
        start = min(row["started_at"] for row in rows)
        end = max(row["ended_at"] for row in rows)
        title = " & ".join(row["title"] for row in rows)
        connection.execute(
            "UPDATE meetings SET title = ?, started_at = ?, ended_at = ?, status = 'ended' WHERE id = ?",
            (title, start, end, keep["id"]),
        )
        for row in rows[1:]:
            connection.execute(
                "UPDATE audio_chunks SET meeting_id = ? WHERE meeting_id = ?",
                (keep["id"], row["id"]),
            )
            connection.execute("DELETE FROM meetings WHERE id = ?", (row["id"],))
        meeting_id = keep["id"]
    merged = next(item for item in list_meetings() if item["id"] == meeting_id)
    return merged


def unnamed_speakers() -> list[dict]:
    with db.conn() as connection:
        rows = connection.execute(
            """
            SELECT s.*, COUNT(t.id) AS segments
            FROM speakers s
            LEFT JOIN audio_transcriptions t ON t.speaker_id = s.id
            WHERE s.name IS NULL OR s.name = ''
            GROUP BY s.id
            ORDER BY s.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def update_speaker(speaker_id: int, name: str) -> dict:
    cleaned = name.strip()
    if not cleaned:
        raise db.ChronicleError("name is required")
    with db.conn() as connection:
        row = connection.execute("SELECT id FROM speakers WHERE id = ?", (speaker_id,)).fetchone()
        if row is None:
            raise db.ChronicleError("speaker not found", 404)
        connection.execute("UPDATE speakers SET name = ? WHERE id = ?", (cleaned, speaker_id))
    return {"id": speaker_id, "name": cleaned}


def merge_speakers(keep_id: int, drop_id: int) -> dict:
    if keep_id == drop_id:
        raise db.ChronicleError("pick two different speakers")
    with db.conn() as connection:
        keep = connection.execute("SELECT * FROM speakers WHERE id = ?", (keep_id,)).fetchone()
        drop = connection.execute("SELECT * FROM speakers WHERE id = ?", (drop_id,)).fetchone()
        if keep is None or drop is None:
            raise db.ChronicleError("speaker not found", 404)
        connection.execute(
            "UPDATE audio_transcriptions SET speaker_id = ? WHERE speaker_id = ?",
            (keep_id, drop_id),
        )
        connection.execute("DELETE FROM speakers WHERE id = ?", (drop_id,))
    return {"id": keep_id, "name": keep["name"], "merged_from": drop_id}


def list_speakers() -> list[dict]:
    with db.conn() as connection:
        rows = connection.execute(
            """
            SELECT s.*, COUNT(t.id) AS segments
            FROM speakers s
            LEFT JOIN audio_transcriptions t ON t.speaker_id = s.id
            GROUP BY s.id
            ORDER BY s.id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def touch_clock() -> str:
    return utc_iso()

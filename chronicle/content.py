"""Write captured history into SQLite and the media folder."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from chronicle import db
from chronicle.redact import redact
from chronicle.timeutil import utc_iso


def _clean(text: str) -> str:
    if db.redact_enabled():
        return redact(text or "")
    return text or ""


def _hash(app_name: str, window_name: str, text: str) -> str:
    payload = f"{app_name}\n{window_name}\n{text}".encode()
    return hashlib.sha256(payload).hexdigest()


def add_screen(
    *,
    text: str,
    app_name: str,
    window_name: str = "",
    timestamp: str | None = None,
    browser_url: str | None = None,
    trigger: str = "manual",
    text_source: str = "accessibility",
    focused: bool = True,
    duration_secs: float | None = None,
    device_name: str = "Display 1",
    tags: list[str] | None = None,
    elements: list[dict] | None = None,
    svg: str | None = None,
    image_path: str | None = None,
    ocr_text: str | None = None,
    dedupe: bool = True,
    session_id: int | None = None,
) -> dict:
    cleaned = _clean(text)
    cleaned_ocr = _clean(ocr_text) if ocr_text else None
    stamp = timestamp or utc_iso()
    digest = _hash(app_name, window_name, cleaned)
    with db.conn() as connection:
        if dedupe:
            previous = db.latest_frame_hash(connection)
            if previous and previous["text_hash"] == digest and previous["app_name"] == app_name:
                if duration_secs:
                    connection.execute(
                        "UPDATE frames SET duration_secs = COALESCE(duration_secs, 0) + ? WHERE id = ?",
                        (duration_secs, previous["id"]),
                    )
                return {"frame_id": previous["id"], "deduped": True, "timestamp": stamp}
        frame_id = db.insert_frame(
            connection,
            timestamp=stamp,
            app_name=app_name,
            window_name=window_name,
            full_text=cleaned,
            text_source=text_source,
            capture_trigger=trigger,
            browser_url=browser_url,
            focused=focused,
            device_name=device_name,
            duration_secs=duration_secs,
            ocr_text=cleaned_ocr,
            accessibility_text=cleaned if text_source == "accessibility" else "",
            text_hash=digest,
            tags=tags,
            elements=elements,
            session_id=session_id,
        )
        relative = None
        if svg:
            relative = f"frames/{frame_id}.svg"
            (db.media_root() / relative).write_text(svg)
        elif image_path:
            relative = image_path
        if relative:
            connection.execute("UPDATE frames SET snapshot_path = ? WHERE id = ?", (relative, frame_id))
    return {"frame_id": frame_id, "deduped": False, "timestamp": stamp, "media_url": None if not relative else f"/media/{relative}"}


def add_event(
    *,
    event_type: str,
    app_name: str,
    window_name: str = "",
    text: str = "",
    timestamp: str | None = None,
    frame_id: int | None = None,
    session_id: int | None = None,
) -> int:
    stamp = timestamp or utc_iso()
    cleaned = _clean(text)
    with db.conn() as connection:
        cursor = connection.execute(
            """
            INSERT INTO ui_events(timestamp, event_type, app_name, window_name, text, frame_id, session_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (stamp, event_type, app_name, window_name, cleaned, frame_id, session_id),
        )
        return int(cursor.lastrowid)


def add_audio(
    *,
    text: str,
    timestamp: str | None = None,
    speaker_name: str | None = None,
    device_name: str = "MacBook Microphone",
    device_type: str = "input",
    meeting_id: int | None = None,
    duration_secs: float = 20,
    tags: list[str] | None = None,
) -> dict:
    cleaned = _clean(text)
    stamp = timestamp or utc_iso()
    with db.conn() as connection:
        speaker_id = _speaker_id(connection, speaker_name)
        chunk = connection.execute(
            """
            INSERT INTO audio_chunks(timestamp, device_name, device_type, duration_secs, meeting_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (stamp, device_name, device_type, duration_secs, meeting_id),
        )
        chunk_id = int(chunk.lastrowid)
        cursor = connection.execute(
            """
            INSERT INTO audio_transcriptions(chunk_id, speaker_id, transcription, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (chunk_id, speaker_id, cleaned, stamp),
        )
        transcription_id = int(cursor.lastrowid)
        connection.execute(
            "INSERT INTO audio_fts(transcription_id, transcription) VALUES (?, ?)",
            (transcription_id, cleaned),
        )
        for tag in tags or []:
            connection.execute(
                "INSERT INTO tags(content_type, content_id, tag) VALUES ('audio', ?, ?)",
                (transcription_id, tag),
            )
    return {
        "transcription_id": transcription_id,
        "chunk_id": chunk_id,
        "speaker_id": speaker_id,
        "timestamp": stamp,
    }


def _speaker_id(connection, name: str | None) -> int:
    if name:
        row = connection.execute("SELECT id FROM speakers WHERE name = ?", (name,)).fetchone()
        if row:
            return int(row["id"])
    cursor = connection.execute(
        "INSERT INTO speakers(name, embedding_note, created_at) VALUES (?, ?, ?)",
        (
            name,
            "Name label only. No speaker-embedding model is installed on this machine.",
            utc_iso(),
        ),
    )
    return int(cursor.lastrowid)


def ensure_meeting(*, title: str, started_at: str, ended_at: str, app_name: str = "zoom.us") -> int:
    with db.conn() as connection:
        row = connection.execute("SELECT id FROM meetings WHERE title = ? AND started_at = ?", (title, started_at)).fetchone()
        if row:
            return int(row["id"])
        cursor = connection.execute(
            """
            INSERT INTO meetings(title, started_at, ended_at, app_name, status)
            VALUES (?, ?, ?, ?, 'ended')
            """,
            (title, started_at, ended_at, app_name),
        )
        return int(cursor.lastrowid)


def attach_meeting(transcription_id: int, meeting_id: int) -> None:
    with db.conn() as connection:
        connection.execute(
            """
            UPDATE audio_chunks SET meeting_id = ?
            WHERE id = (SELECT chunk_id FROM audio_transcriptions WHERE id = ?)
            """,
            (meeting_id, transcription_id),
        )


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

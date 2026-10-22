"""SQLite storage. All history stays under CHRONICLE_HOME (default: ~/.chronicle)."""

from __future__ import annotations

import os
import shutil
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from chronicle.timeutil import utc_iso

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS frames (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    app_name TEXT,
    window_name TEXT,
    browser_url TEXT,
    focused INTEGER DEFAULT 1,
    capture_trigger TEXT,
    text_source TEXT,
    accessibility_text TEXT,
    ocr_text TEXT,
    full_text TEXT,
    snapshot_path TEXT,
    device_name TEXT,
    text_hash TEXT,
    duration_secs REAL
);

CREATE TABLE IF NOT EXISTS elements (
    id INTEGER PRIMARY KEY,
    frame_id INTEGER NOT NULL,
    role TEXT,
    text TEXT,
    bounds TEXT,
    FOREIGN KEY(frame_id) REFERENCES frames(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ui_events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    event_type TEXT,
    app_name TEXT,
    window_name TEXT,
    text TEXT,
    frame_id INTEGER
);

CREATE TABLE IF NOT EXISTS speakers (
    id INTEGER PRIMARY KEY,
    name TEXT,
    embedding_note TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS meetings (
    id INTEGER PRIMARY KEY,
    title TEXT,
    started_at TEXT,
    ended_at TEXT,
    app_name TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS audio_chunks (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    device_name TEXT,
    device_type TEXT,
    duration_secs REAL,
    file_path TEXT,
    meeting_id INTEGER
);

CREATE TABLE IF NOT EXISTS audio_transcriptions (
    id INTEGER PRIMARY KEY,
    chunk_id INTEGER,
    speaker_id INTEGER,
    transcription TEXT,
    timestamp TEXT,
    FOREIGN KEY(chunk_id) REFERENCES audio_chunks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL,
    frame_id INTEGER
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    content_type TEXT NOT NULL,
    content_id INTEGER NOT NULL,
    tag TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipes (
    name TEXT PRIMARY KEY,
    schedule TEXT,
    enabled INTEGER DEFAULT 0,
    prompt TEXT,
    description TEXT,
    source TEXT,
    last_run TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS pipe_executions (
    id INTEGER PRIMARY KEY,
    pipe_name TEXT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    stdout TEXT,
    stderr TEXT,
    output_path TEXT
);

CREATE TABLE IF NOT EXISTS workflows (
    id INTEGER PRIMARY KEY,
    title TEXT,
    apps TEXT,
    description TEXT,
    evidence TEXT,
    automation TEXT,
    minutes INTEGER,
    score REAL,
    suggested_pipe TEXT,
    pipe_prompt TEXT,
    first_seen TEXT,
    last_seen TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    label TEXT,
    started_at TEXT NOT NULL,
    ends_at TEXT,
    stopped_at TEXT,
    status TEXT NOT NULL,
    interval_secs INTEGER,
    frames INTEGER DEFAULT 0,
    events INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS kg_nodes (
    scope TEXT NOT NULL,
    id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    weight REAL DEFAULT 0,
    seconds REAL DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    evidence TEXT,
    props TEXT,
    PRIMARY KEY (scope, id)
);

CREATE TABLE IF NOT EXISTS kg_edges (
    scope TEXT NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    rel TEXT NOT NULL,
    weight REAL DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    evidence TEXT,
    PRIMARY KEY (scope, src, dst, rel)
);

CREATE TABLE IF NOT EXISTS kg_shards (
    scope TEXT PRIMARY KEY,
    started_at TEXT,
    ended_at TEXT,
    version INTEGER NOT NULL,
    final INTEGER NOT NULL DEFAULT 0,
    marks TEXT,
    factor REAL NOT NULL DEFAULT 1,
    built_at TEXT
);

CREATE TABLE IF NOT EXISTS kg_dirty_nodes (id TEXT PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS kg_dirty_edges (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    rel TEXT NOT NULL,
    PRIMARY KEY (src, dst, rel)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS contexts (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    task TEXT,
    preset TEXT,
    session_id INTEGER,
    minutes INTEGER,
    start_at TEXT,
    end_at TEXT,
    markdown TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS frames_fts USING fts5(frame_id UNINDEXED, full_text);
CREATE VIRTUAL TABLE IF NOT EXISTS audio_fts USING fts5(transcription_id UNINDEXED, transcription);
CREATE VIRTUAL TABLE IF NOT EXISTS elements_fts USING fts5(element_id UNINDEXED, text);

CREATE INDEX IF NOT EXISTS idx_frames_ts ON frames(timestamp);
CREATE INDEX IF NOT EXISTS idx_frames_app ON frames(app_name);
CREATE INDEX IF NOT EXISTS idx_audio_ts ON audio_transcriptions(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_ts ON ui_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_tags_lookup ON tags(content_type, content_id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_src ON kg_edges(scope, src);
CREATE INDEX IF NOT EXISTS idx_kg_edges_dst ON kg_edges(scope, dst);
CREATE INDEX IF NOT EXISTS idx_kg_nodes_key ON kg_nodes(id);
CREATE INDEX IF NOT EXISTS idx_kg_edges_key ON kg_edges(src, dst, rel);
"""

MIGRATIONS = [
    ("frames", "session_id", "INTEGER"),
    ("ui_events", "session_id", "INTEGER"),
    ("kg_nodes", "eff_weight", "REAL"),
    ("kg_edges", "eff_weight", "REAL"),
    ("frames", "text_engine", "TEXT"),
]

DEFAULTS = {
    "onboarded": "0",
    "timezone": "local",
    "retention_days": "30",
    "redact": "1",
    "audio_running": "0",
    "capture_interval_secs": "5",
    "capture_clipboard": "1",
    "capture_screenshots": "1",
    "capture_ocr": "1",
    "capture_ax": "1",
    "ax_unlock_editors": "0",
    "idle_threshold_secs": "120",
    "ai_base_url": "",
    "ai_model": "",
    "ai_api_key": "",
    "last_capture_error": "",
    "last_capture_at": "",
}


class ChronicleError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def home() -> Path:
    return Path(os.environ.get("CHRONICLE_HOME", Path.home() / ".chronicle")).expanduser()


def media_root() -> Path:
    path = home() / "media"
    path.mkdir(parents=True, exist_ok=True)
    (path / "frames").mkdir(parents=True, exist_ok=True)
    return path


def pipes_root() -> Path:
    path = home() / "pipes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        home().mkdir(parents=True, exist_ok=True)
        media_root()
        pipes_root()
        _conn = sqlite3.connect(home() / "db.sqlite", check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
    return _conn


def close() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


@contextmanager
def conn():
    connection = get_conn()
    with _lock:
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def init() -> None:
    media_root()
    pipes_root()
    with conn() as connection:
        connection.executescript(SCHEMA)
        for table, column, kind in MIGRATIONS:
            existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            if column not in existing:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_frames_session ON frames(session_id)")
        for key, value in DEFAULTS.items():
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row is None:
                connection.execute("INSERT INTO settings(key, value) VALUES (?, ?)", (key, value))
        env_key = os.environ.get("CHRONICLE_AI_API_KEY", "")
        env_url = os.environ.get("CHRONICLE_AI_BASE_URL", "")
        env_model = os.environ.get("CHRONICLE_AI_MODEL", "")
        if env_key and not _raw_setting(connection, "ai_api_key"):
            _set(connection, "ai_api_key", env_key)
        if env_url and not _raw_setting(connection, "ai_base_url"):
            _set(connection, "ai_base_url", env_url)
        if env_model and not _raw_setting(connection, "ai_model"):
            _set(connection, "ai_model", env_model)


def _raw_setting(connection: sqlite3.Connection, key: str) -> str:
    row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return "" if row is None else (row["value"] or "")


def _set(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_setting(key: str, default: str | None = None) -> str | None:
    with conn() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def set_setting(key: str, value: str) -> None:
    with conn() as connection:
        _set(connection, key, value)


def get_bool(key: str, default: bool = False) -> bool:
    value = get_setting(key, "1" if default else "0") or ""
    return value in {"1", "true", "yes", "on"}


def local_zone():
    name = get_setting("timezone", "local") or "local"
    if name == "local":
        return datetime.now().astimezone().tzinfo
    try:
        return ZoneInfo(name)
    except Exception:
        return datetime.now().astimezone().tzinfo


def redact_enabled() -> bool:
    return get_bool("redact", True)


def rows(cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def wipe() -> None:
    close()
    root = home()
    for name in ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm"):
        path = root / name
        if path.exists():
            path.unlink()
    media = root / "media"
    pipes = root / "pipes"
    exports = root / "exports"
    if media.exists():
        shutil.rmtree(media)
    if pipes.exists():
        shutil.rmtree(pipes)
    if exports.exists():
        shutil.rmtree(exports)


def db_bytes() -> int:
    total = 0
    for name in ("db.sqlite", "db.sqlite-wal"):
        path = home() / name
        if path.exists():
            total += path.stat().st_size
    return total


def counts() -> dict:
    with conn() as connection:
        def n(table: str) -> int:
            return connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

        last = connection.execute("SELECT timestamp FROM frames ORDER BY timestamp DESC LIMIT 1").fetchone()
        return {
            "frames": n("frames"),
            "elements": n("elements"),
            "audio_chunks": n("audio_chunks"),
            "audio_transcriptions": n("audio_transcriptions"),
            "meetings": n("meetings"),
            "memories": n("memories"),
            "pipes": n("pipes"),
            "ui_events": n("ui_events"),
            "workflows": n("workflows"),
            "sessions": n("sessions"),
            "graph_nodes": connection.execute("SELECT COUNT(*) AS n FROM kg_nodes WHERE scope = 'all'").fetchone()["n"],
            "graph_edges": connection.execute("SELECT COUNT(*) AS n FROM kg_edges WHERE scope = 'all'").fetchone()["n"],
            "last_frame_at": None if last is None else last["timestamp"],
        }


def insert_frame(
    connection: sqlite3.Connection,
    *,
    timestamp: str,
    app_name: str,
    window_name: str,
    full_text: str,
    text_source: str = "accessibility",
    capture_trigger: str = "manual",
    browser_url: str | None = None,
    focused: bool = True,
    device_name: str = "Display 1",
    duration_secs: float | None = None,
    ocr_text: str | None = None,
    accessibility_text: str | None = None,
    snapshot_path: str | None = None,
    text_hash: str | None = None,
    tags: list[str] | None = None,
    elements: list[dict] | None = None,
    session_id: int | None = None,
    text_engine: str | None = None,
) -> int:
    cursor = connection.execute(
        """
        INSERT INTO frames (
            timestamp, app_name, window_name, browser_url, focused, capture_trigger,
            text_source, accessibility_text, ocr_text, full_text, snapshot_path,
            device_name, text_hash, duration_secs, session_id, text_engine
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            app_name,
            window_name,
            browser_url,
            1 if focused else 0,
            capture_trigger,
            text_source,
            accessibility_text if accessibility_text is not None else (full_text if text_source == "accessibility" else ""),
            ocr_text if ocr_text is not None else (full_text if text_source == "ocr" else ""),
            full_text,
            snapshot_path,
            device_name,
            text_hash,
            duration_secs,
            session_id,
            text_engine,
        ),
    )
    frame_id = int(cursor.lastrowid)
    connection.execute("INSERT INTO frames_fts(frame_id, full_text) VALUES (?, ?)", (frame_id, full_text))
    for element in elements or []:
        insert_element(connection, frame_id, element)
    for tag in tags or []:
        connection.execute(
            "INSERT INTO tags(content_type, content_id, tag) VALUES ('frame', ?, ?)",
            (frame_id, tag),
        )
    return frame_id


def insert_element(connection: sqlite3.Connection, frame_id: int, element: dict) -> int:
    bounds = element.get("bounds")
    if isinstance(bounds, dict):
        bounds = f"{bounds.get('x', 0)},{bounds.get('y', 0)},{bounds.get('width', 0)},{bounds.get('height', 0)}"
    cursor = connection.execute(
        "INSERT INTO elements(frame_id, role, text, bounds) VALUES (?, ?, ?, ?)",
        (frame_id, element.get("role", "text"), element.get("text", ""), bounds),
    )
    element_id = int(cursor.lastrowid)
    connection.execute(
        "INSERT INTO elements_fts(element_id, text) VALUES (?, ?)",
        (element_id, element.get("text", "")),
    )
    return element_id


def latest_frame_hash(connection: sqlite3.Connection) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT id, text_hash, app_name FROM frames ORDER BY id DESC LIMIT 1"
    ).fetchone()


def delete_range(start_iso: str, end_iso: str) -> dict:
    with conn() as connection:
        frame_rows = connection.execute(
            "SELECT id, snapshot_path FROM frames WHERE timestamp >= ? AND timestamp <= ?",
            (start_iso, end_iso),
        ).fetchall()
        frame_ids = [row["id"] for row in frame_rows]
        for row in frame_rows:
            if row["snapshot_path"]:
                path = media_root() / row["snapshot_path"]
                if path.exists():
                    path.unlink()
        if frame_ids:
            marks = ",".join("?" for _ in frame_ids)
            connection.execute(f"DELETE FROM elements WHERE frame_id IN ({marks})", frame_ids)
            connection.execute(
                f"DELETE FROM tags WHERE content_type = 'frame' AND content_id IN ({marks})",
                frame_ids,
            )
            connection.execute(f"DELETE FROM frames_fts WHERE frame_id IN ({marks})", frame_ids)
            connection.execute(f"DELETE FROM frames WHERE id IN ({marks})", frame_ids)

        chunks = connection.execute(
            "SELECT id FROM audio_chunks WHERE timestamp >= ? AND timestamp <= ?",
            (start_iso, end_iso),
        ).fetchall()
        chunk_ids = [row["id"] for row in chunks]
        transcription_ids: list[int] = []
        if chunk_ids:
            marks = ",".join("?" for _ in chunk_ids)
            transcription_ids = [
                row["id"]
                for row in connection.execute(
                    f"SELECT id FROM audio_transcriptions WHERE chunk_id IN ({marks})",
                    chunk_ids,
                ).fetchall()
            ]
            if transcription_ids:
                tmarks = ",".join("?" for _ in transcription_ids)
                connection.execute(
                    f"DELETE FROM tags WHERE content_type = 'audio' AND content_id IN ({tmarks})",
                    transcription_ids,
                )
                connection.execute(
                    f"DELETE FROM audio_fts WHERE transcription_id IN ({tmarks})",
                    transcription_ids,
                )
            connection.execute(f"DELETE FROM audio_transcriptions WHERE chunk_id IN ({marks})", chunk_ids)
            connection.execute(f"DELETE FROM audio_chunks WHERE id IN ({marks})", chunk_ids)

        events = connection.execute(
            "DELETE FROM ui_events WHERE timestamp >= ? AND timestamp <= ?",
            (start_iso, end_iso),
        ).rowcount
        memories = connection.execute(
            "DELETE FROM memories WHERE created_at >= ? AND created_at <= ?",
            (start_iso, end_iso),
        ).rowcount
        meetings = connection.execute(
            """
            DELETE FROM meetings
            WHERE started_at >= ? AND ended_at <= ?
              AND id NOT IN (SELECT meeting_id FROM audio_chunks WHERE meeting_id IS NOT NULL)
            """,
            (start_iso, end_iso),
        ).rowcount
        return {
            "frames": len(frame_ids),
            "audio": len(transcription_ids),
            "events": events if events and events > 0 else 0,
            "memories": memories if memories and memories > 0 else 0,
            "meetings": meetings if meetings and meetings > 0 else 0,
        }


def enforce_retention() -> dict:
    days = int(get_setting("retention_days", "30") or "30")
    if days <= 0:
        return {"skipped": True, "frames": 0, "audio": 0, "events": 0, "memories": 0, "meetings": 0}
    cutoff = utc_iso(datetime.now(timezone.utc) - timedelta(days=days))
    deleted = delete_range("1970-01-01T00:00:00+00:00", cutoff)
    deleted["skipped"] = False
    deleted["cutoff"] = cutoff
    return deleted

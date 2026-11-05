"""The source protocol. Every input Chronicle learns from is a ``Source`` that yields normalized ``Item``s.

The Privacy & Sources page, permissions, and sync scheduling all come from the registry, so a new source
needs no UI work: subclass ``Source``, set its metadata, implement ``sync``, and ``register`` it.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from chronicle import db, itemgraph, redact
from chronicle.timeutil import utc_iso

CATEGORIES = ["screen", "email", "calendar", "messages", "files", "browser", "ai"]
TEXT_CAP = 20_000


@dataclass
class Item:
    kind: str
    ext_id: str
    ts: str
    title: str = ""
    text: str = ""
    url: str | None = None
    thread: str | None = None
    actors: list[dict] = field(default_factory=list)
    from_me: bool = False
    props: dict = field(default_factory=dict)


@dataclass
class Status:
    state: str  # "connected", "needs_permission", "needs_setup", "off", "error", "unavailable"
    detail: str = ""


class Source:
    id: str = ""
    name: str = ""
    category: str = ""
    description: str = ""
    permissions: list[str] = []
    default_enabled: bool = False
    importable: bool = False
    oauth: str | None = None
    icon: str = ""
    phase: str = "v1"

    def available(self) -> Status:
        return Status("connected")

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        """Yield items newest-last; yield a string to checkpoint the cursor for the next run."""
        return iter(())

    def import_file(self, path: Path, options: dict) -> Iterator[Item]:
        raise db.ChronicleError(f"{self.name} has no file import", 400)

    def describe(self) -> dict:
        return {
            "id": self.id, "name": self.name, "category": self.category, "description": self.description,
            "permissions": self.permissions, "importable": self.importable, "oauth": self.oauth, "icon": self.icon,
            "phase": self.phase, "connectable": True, "managed": getattr(self, "managed", False),
        }


REGISTRY: dict[str, Source] = {}


def register(source: Source) -> Source:
    REGISTRY[source.id] = source
    return source


def get(source_id: str) -> Source:
    _load()
    source = REGISTRY.get(source_id)
    if source is None:
        raise db.ChronicleError(f"unknown source {source_id!r}", 404)
    return source


_loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from chronicle.sources import ai, cloud, local  # noqa: F401


# State

def state(source_id: str) -> dict:
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM source_state WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        source = REGISTRY.get(source_id)
        return {"id": source_id, "enabled": bool(source and source.default_enabled), "options": {}, "cursor": None,
                "last_sync": None, "last_error": None, "items": 0}
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    data["options"] = json.loads(data["options"] or "{}")
    return data


def _save(source_id: str, **values) -> None:
    current = state(source_id)
    merged = {**current, **values}
    with db.conn() as connection:
        connection.execute(
            """
            INSERT INTO source_state(id, enabled, options, cursor, last_sync, last_error, items) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET enabled = excluded.enabled, options = excluded.options, cursor = excluded.cursor,
              last_sync = excluded.last_sync, last_error = excluded.last_error, items = excluded.items
            """,
            (source_id, int(merged["enabled"]), json.dumps(merged["options"] or {}), merged["cursor"], merged["last_sync"],
             merged["last_error"], merged["items"] or 0),
        )


def list_sources() -> list[dict]:
    _load()
    out = []
    for source in REGISTRY.values():
        info = state(source.id)
        try:
            status = source.available()
        except Exception as exc:  # noqa: BLE001
            status = Status("error", str(exc))
        if info["enabled"] and status.state == "connected" and info["last_error"]:
            status = Status("error", info["last_error"])
        if not info["enabled"] and status.state == "connected":
            status = Status("off", status.detail)
        out.append({**source.describe(), **info, "status": status.state, "status_detail": status.detail})
    return out


def configure(source_id: str, enabled: bool | None = None, options: dict | None = None) -> dict:
    source = get(source_id)
    values: dict = {}
    if enabled is not None:
        values["enabled"] = enabled
    if options is not None:
        values["options"] = {**state(source_id)["options"], **options}
    _save(source_id, **values)
    if enabled is not None and hasattr(source, "on_toggle"):
        source.on_toggle(enabled)
    return {**source.describe(), **state(source_id)}


def disconnect(source_id: str, forget: bool = False) -> dict:
    """Turn a source off. With ``forget``, also delete everything it brought in."""
    get(source_id)
    _save(source_id, enabled=False, cursor=None)
    removed = 0
    if forget:
        with db.conn() as connection:
            ids = [row["id"] for row in connection.execute("SELECT id FROM items WHERE source = ?", (source_id,))]
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                marks = ",".join("?" for _ in batch)
                connection.execute(f"DELETE FROM items_fts WHERE item_id IN ({marks})", batch)
            removed = connection.execute("DELETE FROM items WHERE source = ?", (source_id,)).rowcount
            itemgraph.drop_source(connection, source_id)
        _save(source_id, items=0)
        from chronicle import graph

        graph.aggregate()
    return {"id": source_id, "enabled": False, "items_deleted": removed}


# Ingestion

def _clean(item: Item, redacting: bool) -> Item:
    if redacting:
        item.text = redact.redact(item.text or "")
        item.title = redact.redact(item.title or "")
    item.text = (item.text or "")[:TEXT_CAP]
    item.title = (item.title or "")[:500]
    return item


def store(source: Source, items: list[Item]) -> int:
    if not items:
        return 0
    redacting = db.redact_enabled()
    added = 0
    with db.conn() as connection:
        for raw in items:
            item = _clean(raw, redacting)
            cursor = connection.execute(
                """
                INSERT INTO items(source, category, kind, ext_id, ts, title, text, url, thread, actors, from_me, props)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source, ext_id) DO NOTHING
                """,
                (source.id, source.category, item.kind, item.ext_id, item.ts, item.title, item.text, item.url, item.thread,
                 json.dumps(item.actors), int(item.from_me), json.dumps(item.props)),
            )
            if cursor.rowcount:
                added += 1
                connection.execute("INSERT INTO items_fts(item_id, title, text) VALUES (?, ?, ?)",
                                   (cursor.lastrowid, item.title, item.text))
    return added


def run(source_id: str, *, force: bool = False, limit: int = 20_000) -> dict:
    """Sync one source now. Items are stored in batches; the cursor advances only at checkpoints."""
    source = get(source_id)
    info = state(source_id)
    if not info["enabled"] and not force:
        raise db.ChronicleError(f"{source.name} is turned off", 409)
    status = source.available()
    if status.state not in {"connected", "off"}:
        _save(source_id, last_error=status.detail or status.state)
        return {"id": source_id, "added": 0, "status": status.state, "detail": status.detail}
    added, batch, cursor, seen = 0, [], info["cursor"], 0
    try:
        for value in source.sync(info["cursor"], info["options"]):
            if isinstance(value, str):
                added += store(source, batch)
                batch = []
                cursor = value
                _save(source_id, cursor=cursor)
                continue
            batch.append(value)
            seen += 1
            if len(batch) >= 200:
                added += store(source, batch)
                batch = []
            if seen >= limit:
                break
        added += store(source, batch)
    except db.ChronicleError as exc:
        _save(source_id, last_error=exc.message, last_sync=utc_iso())
        raise
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _save(source_id, last_error=f"{type(exc).__name__}: {exc}", last_sync=utc_iso())
        return {"id": source_id, "added": added, "status": "error", "detail": str(exc)}
    with db.conn() as connection:
        total = connection.execute("SELECT COUNT(*) AS n FROM items WHERE source = ?", (source_id,)).fetchone()["n"]
    _save(source_id, cursor=cursor, last_sync=utc_iso(), last_error=None, items=total)
    if added:
        itemgraph.refresh()
    return {"id": source_id, "added": added, "items": total, "status": "connected"}


def import_file(source_id: str, path: str, options: dict | None = None) -> dict:
    source = get(source_id)
    file = Path(path).expanduser()
    if not file.exists():
        raise db.ChronicleError(f"{file} doesn't exist", 404)
    items = list(source.import_file(file, options or {}))
    added = store(source, items)
    with db.conn() as connection:
        total = connection.execute("SELECT COUNT(*) AS n FROM items WHERE source = ?", (source_id,)).fetchone()["n"]
    _save(source_id, enabled=True, last_sync=utc_iso(), last_error=None, items=total)
    if added:
        itemgraph.refresh()
    return {"id": source_id, "added": added, "items": total}


def ingest(source_id: str, items: list[Item]) -> dict:
    """Items pushed from outside (the browser extension, the Mac app)."""
    source = get(source_id)
    if not state(source_id)["enabled"]:
        raise db.ChronicleError(f"{source.name} is turned off in Chronicle", 409)
    added = store(source, items)
    with db.conn() as connection:
        total = connection.execute("SELECT COUNT(*) AS n FROM items WHERE source = ?", (source_id,)).fetchone()["n"]
    _save(source_id, last_sync=utc_iso(), items=total)
    return {"id": source_id, "added": added, "items": total}


def search_items(q: str, limit: int = 8) -> list[dict]:
    """Best-matching source items, ranked by BM25, for answering questions over everything Chronicle has read."""
    terms = [term for term in re.findall(r"[\w@.+-]{3,}", q or "")][:32]
    if not terms:
        return []
    expr = " OR ".join('"' + term.replace('"', "") + '"' for term in terms)
    with db.conn() as connection:
        rows = connection.execute(
            """
            SELECT i.id, i.source, i.ts, i.title, i.url, snippet(items_fts, 2, '', '', ' … ', 48) AS snip
            FROM items_fts JOIN items i ON i.id = items_fts.item_id
            WHERE items_fts MATCH ? ORDER BY bm25(items_fts) LIMIT ?
            """,
            (expr, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def sync_enabled() -> list[dict]:
    _load()
    results = []
    for source in REGISTRY.values():
        if source.importable and not getattr(source, "syncs", False):
            continue
        if state(source.id)["enabled"] and type(source).sync is not Source.sync:
            try:
                results.append(run(source.id))
            except db.ChronicleError as exc:
                results.append({"id": source.id, "status": "error", "detail": exc.message})
    return results


# Helpers for sources that read app databases

@contextmanager
def snapshot(path: Path):
    """Open a copy of an app's SQLite database (apps keep theirs locked and in WAL mode)."""
    folder = Path(tempfile.mkdtemp(prefix="chronicle-src-"))
    try:
        target = folder / path.name
        shutil.copy2(path, target)
        for suffix in ("-wal", "-shm"):
            extra = path.with_name(path.name + suffix)
            if extra.exists():
                shutil.copy2(extra, folder / (path.name + suffix))
        connection = sqlite3.connect(target)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def readable(path: Path) -> Status:
    if not path.exists():
        return Status("unavailable", "Not found on this computer.")
    try:
        with open(path, "rb"):
            pass
    except PermissionError:
        return Status("needs_permission", "Needs Full Disk Access for the app running Chronicle: System Settings › Privacy & Security.")
    return Status("connected")

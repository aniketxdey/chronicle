"""The knowledge graph's legend: categories people can rename, recolor, add, and remove, and per-node edits.

Categories and edits live outside the graph's own tables, so they survive rebuilds and apply wherever graph
nodes are read: the app, the API, context packs, and MCP.
"""

from __future__ import annotations

import functools
import json
import re

from chronicle import db

PALETTE = ["#fff1a8", "#ffd60a", "#ffa51f", "#ff5a1f", "#ffe066", "#ff8a1f", "#ff3d14", "#c2541b"]
OTHER = "other"
DEFAULTS = [
    {"id": "people", "label": "People", "color": "#ff5a1f", "kinds": ["person"]},
    {"id": "apps", "label": "Apps", "color": "#ffd60a", "kinds": ["app", "ai_tool", "window"]},
    {"id": "websites", "label": "Websites", "color": "#fff1a8", "kinds": ["site", "page", "query"]},
    {"id": "files", "label": "Files", "color": "#ffa51f", "kinds": ["file", "document"]},
    {"id": OTHER, "label": "Other", "color": "#c2541b", "kinds": []},
]
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _ensure(connection) -> None:
    if connection.execute("SELECT 1 FROM graph_categories LIMIT 1").fetchone():
        return
    for position, item in enumerate(DEFAULTS):
        connection.execute("INSERT INTO graph_categories(id, label, color, kinds, position) VALUES (?, ?, ?, ?, ?)",
                           (item["id"], item["label"], item["color"], json.dumps(item["kinds"]), position))


def categories() -> list[dict]:
    with db.conn() as connection:
        _ensure(connection)
        rows = connection.execute("SELECT * FROM graph_categories ORDER BY position").fetchall()
    return [{"id": row["id"], "label": row["label"], "color": row["color"], "kinds": json.loads(row["kinds"])} for row in rows]


def overrides() -> dict[str, dict]:
    with db.conn() as connection:
        rows = connection.execute("SELECT * FROM node_overrides WHERE label IS NOT NULL OR category IS NOT NULL").fetchall()
    return {row["id"]: {"label": row["label"], "category": row["category"]} for row in rows}


def overview() -> dict:
    """The legend, every kind of node in the graph, and how many nodes each category holds across all history."""
    items = categories()
    edits = overrides()
    by_kind = {kind: item["id"] for item in items for kind in item["kinds"]}
    with db.conn() as connection:
        rows = connection.execute("SELECT id, kind FROM kg_nodes WHERE scope = 'all'").fetchall()
    kinds: dict[str, int] = {}
    counts: dict[str, int] = {}
    known = {item["id"] for item in items}
    for row in rows:
        kinds[row["kind"]] = kinds.get(row["kind"], 0) + 1
        chosen = (edits.get(row["id"]) or {}).get("category")
        category = chosen if chosen in known else by_kind.get(row["kind"], OTHER)
        counts[category] = counts.get(category, 0) + 1
    return {"categories": [{**item, "count": counts.get(item["id"], 0)} for item in items], "kinds": kinds, "overrides": edits, "palette": PALETTE}


def _slug(label: str, taken: set[str]) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "category"
    key, number = stem, 2
    while key in taken:
        key, number = f"{stem}-{number}", number + 1
    taken.add(key)
    return key


def save(items: list[dict]) -> dict:
    """Replace the legend. Each kind belongs to one category, ``other`` always stays, and nodes moved by hand into a removed category go back to their kind's."""
    cleaned: list[dict] = []
    ids = {str(item.get("id")) for item in items if item.get("id")}
    taken = set(ids) | {OTHER}
    seen: set[str] = set()
    for item in items:
        label = str(item.get("label") or "").strip()[:60]
        if not label:
            raise db.ChronicleError("every category needs a name", 400)
        color = str(item.get("color") or "")
        if not _COLOR.match(color):
            raise db.ChronicleError(f"{label} needs a color like #ffd60a", 400)
        kinds = [str(kind) for kind in item.get("kinds") or [] if str(kind) not in seen]
        seen.update(kinds)
        cleaned.append({"id": str(item.get("id") or _slug(label, taken)), "label": label, "color": color, "kinds": [] if item.get("id") == OTHER else kinds})
    if not any(item["id"] == OTHER for item in cleaned):
        cleaned.append(next(item for item in DEFAULTS if item["id"] == OTHER))
    keep = {item["id"] for item in cleaned}
    with db.conn() as connection:
        connection.execute("DELETE FROM graph_categories")
        for position, item in enumerate(cleaned):
            connection.execute("INSERT INTO graph_categories(id, label, color, kinds, position) VALUES (?, ?, ?, ?, ?)",
                               (item["id"], item["label"], item["color"], json.dumps(item["kinds"]), position))
        marks = ",".join("?" for _ in keep)
        connection.execute(f"UPDATE node_overrides SET category = NULL WHERE category IS NOT NULL AND category NOT IN ({marks})", list(keep))
        connection.execute("DELETE FROM node_overrides WHERE label IS NULL AND category IS NULL AND cluster IS NULL")
    return overview()


def set_override(node_id: str, label: str | None = None, category: str | None = None) -> dict:
    """Rename one node or move it to a category. Blank values go back to what the graph says."""
    node_id = (node_id or "").strip()
    if not node_id:
        raise db.ChronicleError("a node id is required", 400)
    label = (label or "").strip()[:200] or None
    category = (category or "").strip() or None
    if category and category not in {item["id"] for item in categories()}:
        raise db.ChronicleError(f"no category called {category}", 400)
    with db.conn() as connection:
        connection.execute(
            "INSERT INTO node_overrides(id, label, category) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET label = excluded.label, category = excluded.category",
            (node_id, label, category),
        )
        connection.execute("DELETE FROM node_overrides WHERE id = ? AND label IS NULL AND category IS NULL AND cluster IS NULL", (node_id,))
    return overview()


def _resolver():
    items = categories()
    edits = overrides()
    by_id = {item["id"]: item for item in items}
    by_kind = {kind: item for item in items for kind in item["kinds"]}

    def apply(node: dict) -> dict:
        edit = edits.get(node["id"]) or {}
        category = by_id.get(edit.get("category")) or by_kind.get(node["kind"]) or by_id[OTHER]
        node.update(category=category["id"], category_label=category["label"], color=category["color"])
        if edit.get("label"):
            node["label"] = edit["label"]
        return node

    return apply


def _walk(value, apply) -> None:
    if isinstance(value, dict):
        if {"id", "kind", "label"} <= value.keys() and isinstance(value["id"], str):
            apply(value)
        for inner in value.values():
            _walk(inner, apply)
    elif isinstance(value, list):
        for inner in value:
            _walk(inner, apply)


def decorate(value):
    _walk(value, _resolver())
    return value


def decorated(function):
    """Mark every graph node a reader returns with its category and any edit the person made."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        return decorate(function(*args, **kwargs))

    return wrapper

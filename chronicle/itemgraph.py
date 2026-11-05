"""Source items (mail, messages, calendar, files, browser history, AI chats) -> graph shards.

Each source gets one shard per day (``items:<source>:<YYYY-MM-DD>``), so the existing aggregation folds them
into ``all`` with the same decay as screen shards, and disconnecting a source just drops its shards.
"""

from __future__ import annotations

import json

from chronicle import db, graph, keyphrases, shells

SOURCE_APPS = {
    "apple-mail": "Mail", "windows-mail": "Windows Mail", "outlook": "Outlook", "imessage": "Messages", "whatsapp": "WhatsApp",
    "slack": "Slack", "notion": "Notion", "local-folders": "Finder",
    "apple-calendar": "Calendar", "google-calendar": "Google Calendar",
}
ITEM_CAP = 6


def scope(source: str, day: str) -> str:
    return f"items:{source}:{day}"


def _actors(row) -> list[dict]:
    try:
        return [a for a in json.loads(row["actors"] or "[]") if isinstance(a, dict)]
    except json.JSONDecodeError:
        return []


def build_day(connection, source: str, day: str) -> dict:
    name = scope(source, day)
    rows = connection.execute(
        "SELECT * FROM items WHERE source = ? AND substr(ts, 1, 10) = ? ORDER BY ts, id", (source, day)
    ).fetchall()
    if not rows:
        graph.drop_shard(connection, name)
        return {"scope": name, "items": 0}
    builder = graph._Builder(name)
    builder.people = graph._people_index(connection)
    corpus = keyphrases.Corpus(exclude={*builder.people, *(a.get("name", "") for row in rows for a in _actors(row))})
    for row in rows:
        if row["category"] == "ai" and not row["from_me"] and row["kind"] != "conversation":
            continue
        corpus.add(row["id"], row["title"], (row["text"] or "")[:3000], row["url"], 60)
    corpus.rank()
    for row in rows:
        _fold(builder, corpus, row)
    graph._finish(builder)
    graph._write(connection, builder, replace=True)
    graph._save_shard(connection, name, rows[0]["ts"], rows[-1]["ts"], final=True, marks=None)
    return {"scope": name, "items": len(rows), "nodes": len(builder.nodes), "edges": len(builder.edges)}


def _fold(builder, corpus, row) -> None:
    ref = {"type": "item", "id": row["id"], "timestamp": row["ts"], "source": row["source"]}
    props = json.loads(row["props"] or "{}")
    category = row["category"]
    entities: list[str] = []
    anchor: str | None = None

    if category == "ai":
        tool = shells.ai_tool(props.get("tool") or "") or {"id": props.get("tool") or "ai", "label": props.get("tool") or "AI"}
        tool_id = builder.node("ai_tool", tool["id"], tool["label"], ref=ref)
        if row["thread"]:
            title = row["title"] or "Untitled chat"
            anchor = builder.node("ai_chat", f"{tool['id']}|{row['thread']}", title, ref=ref,
                                  props={"tool": tool["id"], "url": row["url"], "thread": row["thread"]})
            chat = builder.nodes[anchor]["props"]
            if row["from_me"]:
                chat["prompts"] = chat.get("prompts", 0) + 1
            if props.get("model"):
                chat["model"] = props["model"]
            builder.edge(tool_id, anchor, "has_chat", ref=ref)
        else:
            anchor = tool_id
        if props.get("project"):
            project = builder.node("project", props["project"].lower(), props["project"], ref=ref)
            builder.edge(anchor, project, "in_project", ref=ref)
            entities.append(project)
        if not row["from_me"] and row["kind"] != "conversation":
            return
    elif category == "calendar":
        anchor = builder.node("event", f"{row['source']}|{row['ext_id']}", row["title"] or "Event", ref=ref,
                              props={"start": props.get("start") or row["ts"], "end": props.get("end"), "url": row["url"]})
        if props.get("location"):
            place = builder.node("place", props["location"], props["location"], ref=ref)
            builder.edge(anchor, place, "located_in", ref=ref)
            entities.append(place)
    elif category == "files":
        anchor = builder.node("document", f"{row['source']}|{row['ext_id']}", row["title"] or row["ext_id"], ref=ref,
                              props={"url": row["url"], "path": props.get("path")})
    elif category == "browser":
        site = graph._site(row["url"])
        if site:
            host, page = site
            tool = shells.ai_tool_for_url(row["url"])
            anchor = builder.node("site", host, host, ref=ref)
            page_id = builder.node("page", page, row["title"] or page, ref=ref, props={"url": row["url"]})
            builder.edge(page_id, anchor, "on_site", ref=ref)
            if tool:
                builder.node("ai_tool", tool["id"], tool["label"], ref=ref)
            entities.append(page_id)
    else:
        app = SOURCE_APPS.get(row["source"])
        if app:
            anchor = builder.node("app", app, app, ref=ref)

    for actor in _actors(row):
        if actor.get("me") or not (actor.get("name") or "").strip():
            continue
        name = actor["name"].strip()
        key, label = builder.people.get(name.lower(), (name, name))
        person = builder.node("person", key, label, ref=ref, props={"handle": actor.get("handle")} if actor.get("handle") else None)
        if anchor:
            rel = {"calendar": "attends", "ai": "mentions"}.get(category, "talks_with")
            builder.edge(person, anchor, rel, ref=ref)
        entities.append(person)

    for key, label, _score in corpus.topics(row["id"])[:ITEM_CAP]:
        topic = builder.node("topic", key, label, ref=ref)
        if anchor:
            builder.edge(anchor, topic, "about", ref=ref)
        entities.append(topic)
    for kind, key, label in graph.extract(f"{row['title'] or ''}\n{(row['text'] or '')[:2000]}", builder.people)[:ITEM_CAP]:
        if kind == "page":
            continue
        node = builder.node(kind, key, label, ref=ref)
        if anchor:
            builder.edge(anchor, node, "mentions", ref=ref)
        entities.append(node)
    graph._co_occur(builder, entities, ref)


def dirty_days(connection, since_id: int) -> set[tuple[str, str]]:
    return {
        (row["source"], row["day"])
        for row in connection.execute("SELECT DISTINCT source, substr(ts, 1, 10) AS day FROM items WHERE id > ?", (since_id,))
    }


MARK = "items_graph_mark"


def refresh() -> dict:
    """Fold items added since the last refresh into their day shards, then into ``all``. Item ids are never reused."""
    mark = int(db.get_setting(MARK, "0") or 0)
    with db.conn() as connection:
        days = dirty_days(connection, mark)
        top = connection.execute("SELECT COALESCE(MAX(id), 0) AS n FROM items").fetchone()["n"]
        for source, day in sorted(days):
            build_day(connection, source, day)
    db.set_setting(MARK, str(top))
    if days:
        graph.aggregate()
    return {"days": len(days)}


def forget_range(start_iso: str, end_iso: str) -> int:
    """Delete items in a time range and rebuild the day shards they were in."""
    with db.conn() as connection:
        rows = connection.execute("SELECT id, source, substr(ts, 1, 10) AS day FROM items WHERE ts >= ? AND ts <= ?", (start_iso, end_iso)).fetchall()
        ids = [row["id"] for row in rows]
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            marks = ",".join("?" for _ in batch)
            connection.execute(f"DELETE FROM items_fts WHERE item_id IN ({marks})", batch)
            connection.execute(f"DELETE FROM items WHERE id IN ({marks})", batch)
        for source, day in sorted({(row["source"], row["day"]) for row in rows}):
            build_day(connection, source, day)
    if ids:
        graph.aggregate()
    return len(ids)


def drop_source(connection, source: str) -> int:
    scopes = [row["scope"] for row in connection.execute("SELECT scope FROM kg_shards WHERE scope LIKE ?", (f"items:{source}:%",))]
    for name in scopes:
        graph.drop_shard(connection, name)
    return len(scopes)

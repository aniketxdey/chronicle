"""Knowledge graph over recorded frames, system events, and transcripts.

A graph lives in a scope: ``all`` for the whole history, or ``session:<id>``
for one recording. Building a scope replaces it.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urlsplit

from chronicle import db
from chronicle.timeutil import utc_iso

EVIDENCE_CAP = 8
ENTITY_CAP = 14

_TICKET = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-\d{1,6})\b")
_TICKET_STOP = {"UTF", "ISO", "SHA", "MD", "AES", "RSA", "COVID", "GPT", "TLS", "SSL", "HTTP", "IPV"}
_PR = re.compile(r"\b(?:PR|pull request|pull)\s*[#/]?\s*(\d{1,6})\b", re.I)
_URL = re.compile(r"https?://[^\s<>'\")\]]+")
_FILE = re.compile(
    r"(?<![\w.-])([\w-]+(?:\.[\w-]+)*\.(?:py|ts|tsx|js|jsx|mjs|rs|go|java|kt|swift|rb|php|c|cc|cpp|h|hpp|cs|"
    r"md|mdx|json|ya?ml|toml|sql|sh|css|scss|html|ipynb|csv|tf|proto|vue|svelte|lua|ex|exs))\b"
)
_SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CAMEL = re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]+){1,5}\b")
_TOKEN = re.compile(r"[A-Za-z0-9_.#/-]{2,}")

TAG_KINDS = {"person": "person", "project": "project", "customer": "customer"}
KIND_ORDER = [
    "activity", "app", "window", "site", "page", "person", "project", "customer", "company", "organization", "product",
    "concept", "place", "event", "list", "document", "category", "ticket", "pr", "file", "identifier", "meeting", "topic",
    "session",
]
_LLM_TYPED = "json_extract(e.props, '$.source') = 'llm' AND json_extract(e.props, '$.auto') IS NULL"


def scope_for(session_id: int | None) -> str:
    return "all" if not session_id else f"session:{int(session_id)}"


def _slug(value: str) -> str:
    return re.sub(r"\s+", "-", (value or "").strip()).lower()


def node_id(kind: str, key: str) -> str:
    return f"{kind}:{_slug(key)}"


class _Builder:
    def __init__(self, scope: str):
        self.scope = scope
        self.people: dict[str, tuple[str, str]] = {}
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    def node(self, kind: str, key: str, label: str, *, ref: dict | None = None, seconds: float = 0, props=None) -> str:
        ident = node_id(kind, key)
        item = self.nodes.get(ident)
        if item is None:
            item = {
                "id": ident, "kind": kind, "label": label.strip()[:200] or key, "weight": 0.0,
                "seconds": 0.0, "first_seen": None, "last_seen": None, "evidence": [], "props": dict(props or {}),
            }
            self.nodes[ident] = item
        item["weight"] += 1
        item["seconds"] += seconds or 0
        if ref:
            self._touch(item, ref)
        return ident

    def edge(self, src: str, dst: str, rel: str, *, ref: dict | None = None, weight: float = 1, undirected=False):
        if src == dst:
            return
        if undirected and dst < src:
            src, dst = dst, src
        key = (src, dst, rel)
        item = self.edges.get(key)
        if item is None:
            item = {"src": src, "dst": dst, "rel": rel, "weight": 0.0, "first_seen": None, "last_seen": None, "evidence": [], "props": {}}
            self.edges[key] = item
        item["weight"] += weight
        if ref:
            self._touch(item, ref)

    @staticmethod
    def _touch(item: dict, ref: dict) -> None:
        stamp = ref.get("timestamp")
        if stamp:
            if item["first_seen"] is None or stamp < item["first_seen"]:
                item["first_seen"] = stamp
            if item["last_seen"] is None or stamp > item["last_seen"]:
                item["last_seen"] = stamp
        evidence = item["evidence"]
        if len(evidence) < EVIDENCE_CAP and ref not in evidence:
            evidence.append(ref)


def _people_index(connection) -> dict[str, tuple[str, str]]:
    """Map lowercase full names and first names to (key, label)."""
    index: dict[str, tuple[str, str]] = {}
    for row in connection.execute("SELECT DISTINCT name FROM speakers WHERE name IS NOT NULL AND name != ''"):
        full = row["name"].strip()
        index[full.lower()] = (full, full)
        first = full.split()[0].lower()
        index.setdefault(first, (full, full))
    for row in connection.execute("SELECT DISTINCT tag FROM tags WHERE tag LIKE 'person:%'"):
        value = row["tag"].split(":", 1)[1].strip()
        if value and value.lower() not in index:
            index[value.lower()] = (value, value.title())
    return index


def extract(text: str, people: dict[str, tuple[str, str]] | None = None) -> list[tuple[str, str, str]]:
    """Return (kind, key, label) entities found in free text."""
    text = text or ""
    found: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, key: str, label: str) -> None:
        marker = (kind, _slug(key))
        if marker not in seen:
            seen.add(marker)
            found.append((kind, key, label))

    for url in _URL.findall(text):
        site = _site(url.rstrip(".,;:"))
        if site:
            add("page", site[1], site[1])
            pull = re.search(r"/pull/(\d+)", site[1])
            if pull:
                add("pr", pull.group(1), f"PR {pull.group(1)}")
    # Query strings are tracking noise; only host and path take part in entity matching.
    text = _URL.sub(lambda m: " ".join((_site(m.group(0)) or ("", ""))[1].split("/")), text)
    for match in _TICKET.findall(text):
        if match.split("-")[0] not in _TICKET_STOP:
            add("ticket", match, match)
    for match in _PR.findall(text):
        add("pr", match, f"PR {match}")
    for match in _FILE.findall(text):
        if not re.fullmatch(r"[\d.]+", match.rsplit(".", 1)[0]):
            add("file", match, match)
    for match in _SNAKE.findall(text):
        if len(match) >= 5:
            add("identifier", match, match)
    for match in _CAMEL.findall(text):
        if len(match) >= 6:
            add("identifier", match, match)
    if people:
        lowered = text.lower()
        for alias, (key, label) in people.items():
            if re.search(rf"\b{re.escape(alias)}\b", lowered):
                add("person", key, label)
    return found[: ENTITY_CAP * 2]


def _site(url: str | None) -> tuple[str, str] | None:
    if not url:
        return None
    parts = urlsplit(url if "://" in url else f"https://{url}")
    if not parts.netloc:
        return None
    host = parts.netloc.lower().removeprefix("www.")
    page = f"{host}{parts.path}".rstrip("/")
    return host, page


def _session_bounds(connection, session_id: int) -> tuple[str, str]:
    row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise db.ChronicleError("session not found", 404)
    end = row["stopped_at"] or row["ends_at"] or utc_iso()
    return row["started_at"], end


def build(session_id: int | None = None) -> dict:
    scope = scope_for(session_id)
    graph = _Builder(scope)
    with db.conn() as connection:
        people = _people_index(connection)
        graph.people = people
        if session_id:
            start, end = _session_bounds(connection, session_id)
            frames = connection.execute(
                "SELECT * FROM frames WHERE session_id = ? ORDER BY timestamp, id", (session_id,)
            ).fetchall()
            events = connection.execute(
                "SELECT * FROM ui_events WHERE session_id = ? ORDER BY timestamp, id", (session_id,)
            ).fetchall()
            window = ("t.timestamp >= ? AND t.timestamp <= ?", (start, end))
            meetings = connection.execute(
                "SELECT * FROM meetings WHERE started_at <= ? AND ended_at >= ?", (end, start)
            ).fetchall()
        else:
            frames = connection.execute("SELECT * FROM frames ORDER BY timestamp, id").fetchall()
            events = connection.execute("SELECT * FROM ui_events ORDER BY timestamp, id").fetchall()
            window = ("1=1", ())
            meetings = connection.execute("SELECT * FROM meetings").fetchall()
        audio = connection.execute(
            f"""
            SELECT t.id, t.transcription, t.timestamp, s.name AS speaker_name, c.meeting_id
            FROM audio_transcriptions t
            LEFT JOIN speakers s ON s.id = t.speaker_id
            LEFT JOIN audio_chunks c ON c.id = t.chunk_id
            WHERE {window[0]}
            ORDER BY t.timestamp
            """,
            window[1],
        ).fetchall()
        frame_tags: dict[int, list[str]] = {}
        audio_tags: dict[int, list[str]] = {}
        for row in connection.execute("SELECT content_type, content_id, tag FROM tags"):
            if row["content_type"] == "frame":
                frame_tags.setdefault(row["content_id"], []).append(row["tag"])
            elif row["content_type"] == "audio":
                audio_tags.setdefault(row["content_id"], []).append(row["tag"])

        session_node = None
        if session_id:
            label_row = connection.execute("SELECT label FROM sessions WHERE id = ?", (session_id,)).fetchone()
            session_node = graph.node("session", str(session_id), (label_row["label"] if label_row else None) or f"Recording {session_id}")

        previous_app = None
        previous_window = None
        for frame in frames:
            ref = {"type": "frame", "id": frame["id"], "timestamp": frame["timestamp"]}
            seconds = frame["duration_secs"] or 0
            app_name = frame["app_name"] or "Unknown"
            app_id = graph.node("app", app_name, app_name, ref=ref, seconds=seconds)
            window_title = frame["window_name"] or "(untitled)"
            window_id = graph.node(
                "window", f"{app_name}|{window_title}", window_title, ref=ref, seconds=seconds, props={"app": app_name}
            )
            graph.edge(app_id, window_id, "has_window", ref=ref)
            if session_node:
                graph.edge(session_node, app_id, "captured", ref=ref, weight=max(seconds, 1))
            if previous_app and previous_app != app_id:
                graph.edge(previous_app, app_id, "switched_to", ref=ref)
            if previous_window and previous_window != window_id:
                graph.edge(previous_window, window_id, "then", ref=ref)
            previous_app, previous_window = app_id, window_id

            entities: list[str] = []
            site = _site(frame["browser_url"])
            if site:
                host, page = site
                site_id = graph.node("site", host, host, ref=ref, seconds=seconds)
                page_id = graph.node("page", page, window_title if window_title != "(untitled)" else page, ref=ref, seconds=seconds, props={"url": frame["browser_url"]})
                graph.edge(page_id, site_id, "on_site", ref=ref)
                graph.edge(window_id, page_id, "visited", ref=ref)
                entities.append(page_id)
            text = f"{window_title}\n{frame['browser_url'] or ''}\n{frame['full_text'] or ''}"
            for kind, key, label in extract(text, people)[:ENTITY_CAP]:
                entity_id = graph.node(kind, key, label, ref=ref, seconds=seconds)
                graph.edge(window_id, entity_id, "shows", ref=ref)
                entities.append(entity_id)
            for tag in frame_tags.get(frame["id"], []):
                entity_id = _tag_node(graph, tag, ref, seconds)
                graph.edge(window_id, entity_id, "about", ref=ref)
                entities.append(entity_id)
            _co_occur(graph, entities, ref)

        for event in events:
            ref = {"type": "event", "id": event["id"], "timestamp": event["timestamp"], "event": event["event_type"]}
            if not event["app_name"]:
                continue
            app_id = graph.node("app", event["app_name"], event["app_name"], ref=ref)
            graph.nodes[app_id]["weight"] -= 1
            graph.nodes[app_id]["props"].setdefault("events", {})
            counts = graph.nodes[app_id]["props"]["events"]
            counts[event["event_type"]] = counts.get(event["event_type"], 0) + 1
            if event["event_type"] in {"clipboard", "text", "typing"} and event["text"]:
                for kind, key, label in extract(event["text"], people)[:6]:
                    entity_id = graph.node(kind, key, label, ref=ref)
                    graph.edge(app_id, entity_id, "copied" if event["event_type"] == "clipboard" else "typed", ref=ref)

        meeting_ids: dict[int, str] = {}
        for meeting in meetings:
            ref = {"type": "meeting", "id": meeting["id"], "timestamp": meeting["started_at"]}
            start = datetime.fromisoformat(meeting["started_at"])
            stop = datetime.fromisoformat(meeting["ended_at"])
            meeting_ids[meeting["id"]] = graph.node(
                "meeting", str(meeting["id"]), meeting["title"] or f"Meeting {meeting['id']}", ref=ref,
                seconds=max(0.0, (stop - start).total_seconds()), props={"app": meeting["app_name"]},
            )
            if session_node:
                graph.edge(session_node, meeting_ids[meeting["id"]], "captured", ref=ref)

        for line in audio:
            ref = {"type": "audio", "id": line["id"], "timestamp": line["timestamp"]}
            meeting_node = meeting_ids.get(line["meeting_id"]) if line["meeting_id"] else None
            entities = []
            if line["speaker_name"]:
                person_id = graph.node("person", line["speaker_name"], line["speaker_name"], ref=ref)
                entities.append(person_id)
                if meeting_node:
                    graph.edge(person_id, meeting_node, "spoke_in", ref=ref)
            for kind, key, label in extract(line["transcription"], people)[:ENTITY_CAP]:
                entity_id = graph.node(kind, key, label, ref=ref)
                if meeting_node:
                    graph.edge(meeting_node, entity_id, "discussed", ref=ref)
                entities.append(entity_id)
            for tag in audio_tags.get(line["id"], []):
                entities.append(_tag_node(graph, tag, ref, 0))
            _co_occur(graph, entities, ref)

        from chronicle import tuner

        replayed = tuner.replay(connection, scope, graph.nodes, graph.edges)
        connection.execute("DELETE FROM kg_nodes WHERE scope = ?", (scope,))
        connection.execute("DELETE FROM kg_edges WHERE scope = ?", (scope,))
        connection.executemany(
            """
            INSERT INTO kg_nodes(scope, id, kind, label, weight, seconds, first_seen, last_seen, evidence, props)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (scope, n["id"], n["kind"], n["label"], max(n["weight"], 0), round(n["seconds"], 1), n["first_seen"],
                 n["last_seen"], json.dumps(n["evidence"]), json.dumps(n["props"]))
                for n in graph.nodes.values()
            ],
        )
        connection.executemany(
            """
            INSERT INTO kg_edges(scope, src, dst, rel, weight, first_seen, last_seen, evidence, props)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (scope, e["src"], e["dst"], e["rel"], round(e["weight"], 1), e["first_seen"], e["last_seen"],
                 json.dumps(e["evidence"]), json.dumps(e.get("props") or {}))
                for e in graph.edges.values()
            ],
        )
    db.set_setting(f"graph_built_at:{scope}", utc_iso())
    return {"scope": scope, "nodes": len(graph.nodes), "edges": len(graph.edges), "built_at": utc_iso(), "patches": replayed}


def _tag_node(graph: _Builder, tag: str, ref: dict, seconds: float) -> str:
    namespace, _, value = tag.partition(":")
    if not value:
        return graph.node("topic", tag, tag, ref=ref, seconds=seconds)
    kind = TAG_KINDS.get(namespace, "topic")
    if kind == "person":
        key, label = graph.people.get(value.lower(), (value, value.title()))
        return graph.node("person", key, label, ref=ref, seconds=seconds)
    return graph.node(kind, value, value, ref=ref, seconds=seconds)


def _co_occur(graph: _Builder, entities: list[str], ref: dict) -> None:
    unique = list(dict.fromkeys(entities))[:ENTITY_CAP]
    for index, left in enumerate(unique):
        for right in unique[index + 1 :]:
            graph.edge(left, right, "co_occurs", ref=ref, undirected=True)


def ensure(session_id: int | None = None) -> None:
    scope = scope_for(session_id)
    with db.conn() as connection:
        has = connection.execute("SELECT 1 FROM kg_nodes WHERE scope = ? LIMIT 1", (scope,)).fetchone()
        has_frames = connection.execute("SELECT 1 FROM frames LIMIT 1").fetchone()
    if has is None and has_frames is not None:
        build(session_id)


def _node_public(row) -> dict:
    return {
        "id": row["id"], "kind": row["kind"], "label": row["label"], "weight": row["weight"],
        "seconds": row["seconds"], "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "evidence": json.loads(row["evidence"] or "[]"), "props": json.loads(row["props"] or "{}"),
    }


def _edge_public(row) -> dict:
    return {
        "src": row["src"], "dst": row["dst"], "rel": row["rel"], "weight": row["weight"],
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "evidence": json.loads(row["evidence"] or "[]"), "props": json.loads(row["props"] or "{}"),
    }


def llm_view(connection, scope: str, top: int = 8) -> dict:
    """LLM-tuned content of a scope: activity summaries, typed relations, and added entities."""
    activities = connection.execute(
        "SELECT * FROM kg_nodes WHERE scope = ? AND kind = 'activity' ORDER BY seconds DESC, weight DESC LIMIT ?", (scope, top)
    ).fetchall()
    involved: dict[str, list[str]] = {}
    for row in connection.execute(
        """
        SELECT e.src, n.label FROM kg_edges e JOIN kg_nodes n ON n.scope = e.scope AND n.id = e.dst
        WHERE e.scope = ? AND e.rel = 'involves' ORDER BY n.seconds DESC
        """,
        (scope,),
    ):
        involved.setdefault(row["src"], []).append(row["label"])
    relations = connection.execute(
        f"""
        SELECT e.*, s.label AS src_label, d.label AS dst_label FROM kg_edges e
        JOIN kg_nodes s ON s.scope = e.scope AND s.id = e.src
        JOIN kg_nodes d ON d.scope = e.scope AND d.id = e.dst
        WHERE e.scope = ? AND {_LLM_TYPED}
        ORDER BY json_extract(e.props, '$.confidence') DESC, e.weight DESC, e.src LIMIT ?
        """,
        (scope, top * 3),
    ).fetchall()
    entities = connection.execute(
        """
        SELECT * FROM kg_nodes WHERE scope = ? AND json_extract(props, '$.source') = 'llm' AND kind NOT IN ('activity', 'category')
        ORDER BY seconds DESC, weight DESC LIMIT ?
        """,
        (scope, top * 2),
    ).fetchall()
    if scope == "all":
        count = connection.execute("SELECT COUNT(*) AS n FROM kg_llm_patches").fetchone()["n"]
    else:
        count = connection.execute("SELECT COUNT(*) AS n FROM kg_llm_patches WHERE scope = ?", (scope,)).fetchone()["n"]
    return {
        "patches": count,
        "activities": [{"id": row["id"], "summary": row["label"], "nodes": involved.get(row["id"], [])[:6]} for row in activities],
        "relations": [
            {"src": row["src"], "rel": row["rel"], "dst": row["dst"], "from": row["src_label"], "to": row["dst_label"],
             "confidence": json.loads(row["props"] or "{}").get("confidence")}
            for row in relations
        ],
        "entities": [
            {"id": row["id"], "kind": row["kind"], "label": row["label"], "traits": json.loads(row["props"] or "{}").get("traits", [])}
            for row in entities
        ],
    }


def summary(session_id: int | None = None, top: int = 8) -> dict:
    ensure(session_id)
    scope = scope_for(session_id)
    with db.conn() as connection:
        kinds = connection.execute(
            "SELECT kind, COUNT(*) AS n FROM kg_nodes WHERE scope = ? GROUP BY kind", (scope,)
        ).fetchall()
        tops: dict[str, list[dict]] = {}
        for row in kinds:
            found = connection.execute(
                """
                SELECT * FROM kg_nodes WHERE scope = ? AND kind = ?
                ORDER BY seconds DESC, weight DESC LIMIT ?
                """,
                (scope, row["kind"], top),
            ).fetchall()
            tops[row["kind"]] = [{"id": item["id"], "label": item["label"], "weight": item["weight"], "seconds": item["seconds"]} for item in found]
        transitions = connection.execute(
            """
            SELECT e.*, s.label AS src_label, d.label AS dst_label, s.props AS src_props, d.props AS dst_props
            FROM kg_edges e
            JOIN kg_nodes s ON s.scope = e.scope AND s.id = e.src
            JOIN kg_nodes d ON d.scope = e.scope AND d.id = e.dst
            WHERE e.scope = ? AND e.rel IN ('switched_to', 'then')
            ORDER BY e.weight DESC LIMIT ?
            """,
            (scope, top * 2),
        ).fetchall()
        edge_count = connection.execute("SELECT COUNT(*) AS n FROM kg_edges WHERE scope = ?", (scope,)).fetchone()["n"]
        tuned = llm_view(connection, scope, top)
    order = {kind: index for index, kind in enumerate(KIND_ORDER)}
    return {
        "scope": scope,
        "built_at": db.get_setting(f"graph_built_at:{scope}"),
        "counts": {row["kind"]: row["n"] for row in sorted(kinds, key=lambda r: order.get(r["kind"], 99))},
        "edges": edge_count,
        "llm": tuned,
        "top": tops,
        "transitions": [
            {
                "rel": row["rel"], "from": _transition_label(row["src_label"], row["src_props"]),
                "to": _transition_label(row["dst_label"], row["dst_props"]), "count": row["weight"],
                "src": row["src"], "dst": row["dst"],
            }
            for row in transitions
        ],
    }


def _transition_label(label: str, props: str | None) -> str:
    app = json.loads(props or "{}").get("app")
    return f"{app} · {label}" if app else label


def find(q: str | None = None, kind: str | None = None, limit: int = 20, session_id: int | None = None) -> list[dict]:
    ensure(session_id)
    scope = scope_for(session_id)
    clauses = ["scope = ?"]
    params: list = [scope]
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    tokens = [token.lower() for token in _TOKEN.findall(q or "")][:8]
    score = "0"
    if tokens:
        parts = []
        for token in tokens:
            parts.append("(lower(label) LIKE ? OR id LIKE ?)")
            params.extend([f"%{token}%", f"%{token}%"])
        clauses.append("(" + " OR ".join(parts) + ")")
        score = " + ".join(f"(CASE WHEN lower(label) LIKE '%' || ? || '%' THEN 1 ELSE 0 END)" for _ in tokens)
        params_score = tokens
    else:
        params_score = []
    sql = f"""
        SELECT *, ({score}) AS hits FROM kg_nodes WHERE {' AND '.join(clauses)}
        ORDER BY hits DESC, seconds DESC, weight DESC LIMIT ?
    """
    with db.conn() as connection:
        rows = connection.execute(sql, [*params_score, *params, max(1, min(limit, 200))]).fetchall()
    return [_node_public(row) for row in rows]


def node(node_id: str, session_id: int | None = None, limit: int = 25) -> dict:
    ensure(session_id)
    scope = scope_for(session_id)
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM kg_nodes WHERE scope = ? AND id = ?", (scope, node_id)).fetchone()
        if row is None:
            fallback = connection.execute(
                "SELECT * FROM kg_nodes WHERE scope = ? AND lower(label) = lower(?) ORDER BY weight DESC LIMIT 1",
                (scope, node_id),
            ).fetchone()
            if fallback is None:
                raise db.ChronicleError(f"no graph node {node_id!r} in {scope}", 404)
            row = fallback
        edges = connection.execute(
            """
            SELECT e.*, n.kind AS other_kind, n.label AS other_label, n.id AS other_id,
                   CASE WHEN e.src = ? THEN 'out' ELSE 'in' END AS direction
            FROM kg_edges e
            JOIN kg_nodes n ON n.scope = e.scope AND n.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END
            WHERE e.scope = ? AND (e.src = ? OR e.dst = ?)
            ORDER BY e.weight DESC LIMIT ?
            """,
            (row["id"], row["id"], scope, row["id"], row["id"], max(1, min(limit, 200))),
        ).fetchall()
    return {
        "scope": scope,
        "node": _node_public(row),
        "neighbors": [
            {
                "id": edge["other_id"], "kind": edge["other_kind"], "label": edge["other_label"],
                "rel": edge["rel"], "direction": edge["direction"], "weight": edge["weight"],
                "last_seen": edge["last_seen"], "evidence": json.loads(edge["evidence"] or "[]")[:3],
            }
            for edge in edges
        ],
    }


def subgraph(task: str | None, session_id: int | None = None, max_nodes: int = 30) -> dict:
    """Nodes relevant to a task plus their strongest neighbors. Without a task, the heaviest nodes."""
    ensure(session_id)
    scope = scope_for(session_id)
    seeds = find(task, limit=max(4, max_nodes // 3), session_id=session_id) if task else []
    if task and not seeds:
        seeds = []
    chosen: dict[str, dict] = {item["id"]: item for item in seeds}
    with db.conn() as connection:
        if not task:
            rows = connection.execute(
                """
                SELECT * FROM kg_nodes WHERE scope = ? AND kind NOT IN ('window', 'session')
                ORDER BY seconds DESC, weight DESC LIMIT ?
                """,
                (scope, max_nodes),
            ).fetchall()
            for row in rows:
                chosen[row["id"]] = _node_public(row)
        for seed in list(chosen.values())[: max_nodes // 2 or 1]:
            neighbors = connection.execute(
                """
                SELECT n.* FROM kg_edges e
                JOIN kg_nodes n ON n.scope = e.scope AND n.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END
                WHERE e.scope = ? AND (e.src = ? OR e.dst = ?) AND e.rel != 'has_window'
                ORDER BY e.weight DESC LIMIT 6
                """,
                (seed["id"], scope, seed["id"], seed["id"]),
            ).fetchall()
            for row in neighbors:
                if len(chosen) >= max_nodes:
                    break
                chosen.setdefault(row["id"], _node_public(row))
        ids = list(chosen)
        edges = []
        if ids:
            marks = ",".join("?" for _ in ids)
            edges = connection.execute(
                f"""
                SELECT * FROM kg_edges WHERE scope = ? AND src IN ({marks}) AND dst IN ({marks})
                ORDER BY weight DESC LIMIT ?
                """,
                (scope, *ids, *ids, max_nodes * 3),
            ).fetchall()
    return {"scope": scope, "seeds": [item["id"] for item in seeds], "nodes": list(chosen.values()), "edges": [_edge_public(e) for e in edges]}


def export(session_id: int | None = None, limit: int = 160) -> dict:
    ensure(session_id)
    scope = scope_for(session_id)
    with db.conn() as connection:
        rows = connection.execute(
            "SELECT * FROM kg_nodes WHERE scope = ? ORDER BY seconds DESC, weight DESC LIMIT ?",
            (scope, max(1, min(limit, 1000))),
        ).fetchall()
        ids = [row["id"] for row in rows]
        edges = []
        if ids:
            marks = ",".join("?" for _ in ids)
            edges = connection.execute(
                f"SELECT * FROM kg_edges WHERE scope = ? AND src IN ({marks}) AND dst IN ({marks}) ORDER BY weight DESC LIMIT ?",
                (scope, *ids, *ids, limit * 4),
            ).fetchall()
    return {
        "scope": scope,
        "built_at": db.get_setting(f"graph_built_at:{scope}"),
        "nodes": [_node_public(row) for row in rows],
        "edges": [_edge_public(edge) for edge in edges],
    }

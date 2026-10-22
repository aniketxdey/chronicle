"""Knowledge graph over recorded frames, system events, and transcripts.

History is split into shards: one per recording (``session:<id>``) and one per
hour of frames captured outside any recording (``loose:<YYYY-MM-DDTHH>``). A
shard is extracted from raw rows once (a live recording is extended
incrementally). The ``all`` scope is never extracted; it is a SQL aggregation of
shard rows where each shard counts ``0.5 ** (age_hours / half_life)``.

Exponential decay scales every shard by the same factor as time passes, so
``eff_weight`` is stored relative to an anchor time and rescaled on read; only
keys whose shard rows changed are regrouped.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from chronicle import db, keyphrases
from chronicle.timeutil import utc_iso

EVIDENCE_CAP = 8
ENTITY_CAP = 14
GRAPH_VERSION = 2
HALF_LIFE_HOURS = 72.0
REAGGREGATE_SECS = 600
_NO_DECAY = {"0", "off", "none", "never", "inf", "infinity"}

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
    "app", "window", "site", "page", "query", "topic", "person", "project", "customer", "ticket", "pr",
    "file", "identifier", "meeting", "session",
]


def scope_for(session_id: int | None) -> str:
    return "all" if not session_id else f"session:{int(session_id)}"


def _slug(value: str) -> str:
    return re.sub(r"\s+", "-", (value or "").strip()).lower()


class _Builder:
    def __init__(self, scope: str):
        self.scope = scope
        self.people: dict[str, tuple[str, str]] = {}
        self.nodes: dict[str, dict] = {}
        self.edges: dict[tuple[str, str, str], dict] = {}

    def node(self, kind: str, key: str, label: str, *, ref: dict | None = None, seconds: float = 0, props=None) -> str:
        node_id = f"{kind}:{_slug(key)}"
        item = self.nodes.get(node_id)
        if item is None:
            item = {
                "id": node_id, "kind": kind, "label": label.strip()[:200] or key, "weight": 0.0,
                "seconds": 0.0, "first_seen": None, "last_seen": None, "evidence": [], "props": dict(props or {}),
            }
            self.nodes[node_id] = item
        item["weight"] += 1
        item["seconds"] += seconds or 0
        if ref:
            self._touch(item, ref)
        return node_id

    def edge(self, src: str, dst: str, rel: str, *, ref: dict | None = None, weight: float = 1, undirected=False):
        if src == dst:
            return
        if undirected and dst < src:
            src, dst = dst, src
        key = (src, dst, rel)
        item = self.edges.get(key)
        if item is None:
            item = {"src": src, "dst": dst, "rel": rel, "weight": 0.0, "first_seen": None, "last_seen": None, "evidence": []}
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


# Extraction: raw rows -> one shard

_AUDIO_SQL = """
    SELECT t.id, t.transcription, t.timestamp, s.name AS speaker_name, m.id AS meeting_id
    FROM audio_transcriptions t
    LEFT JOIN speakers s ON s.id = t.speaker_id
    LEFT JOIN audio_chunks c ON c.id = t.chunk_id
    LEFT JOIN meetings m ON m.id = c.meeting_id
"""


def _tags(connection, content_type: str, ids: list[int]) -> dict[int, list[str]]:
    found: dict[int, list[str]] = {}
    if not ids:
        return found
    wanted = set(ids)
    for row in connection.execute(
        "SELECT content_id, tag FROM tags WHERE content_type = ? AND content_id BETWEEN ? AND ?",
        (content_type, min(ids), max(ids)),
    ):
        if row["content_id"] in wanted:
            found.setdefault(row["content_id"], []).append(row["tag"])
    return found


def _extract(graph: _Builder, connection, frames, events, meetings, audio, session_node: str | None, state: dict) -> dict:
    """Fold raw rows into ``graph``. ``state`` carries the previous app/window across incremental passes."""
    people = graph.people
    corpus = keyphrases.Corpus(exclude={*people, *(frame["app_name"] or "" for frame in frames)})
    for frame in frames:
        corpus.add(frame["id"], frame["window_name"], frame["full_text"], frame["browser_url"], frame["duration_secs"] or 0)
    corpus.rank()
    frame_tags = _tags(connection, "frame", [frame["id"] for frame in frames])
    audio_tags = _tags(connection, "audio", [line["id"] for line in audio])
    previous_app, previous_window = state.get("app"), state.get("window")
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
        searched = corpus.query(frame["id"])
        query_id, query_topics = None, []
        if searched:
            engine, query = searched
            query_id = graph.node("query", query.lower(), query, ref=ref, seconds=seconds, props={"engine": engine})
            graph.edge(page_id if site else window_id, query_id, "searched", ref=ref)
            query_topics = corpus.query_topics(query)
            entities.append(query_id)
        for topic_key in dict.fromkeys([*query_topics, *(key for key, _, _ in corpus.topics(frame["id"]))]):
            topic_id = graph.node("topic", topic_key, corpus.labels[topic_key], ref=ref, seconds=seconds)
            graph.edge(window_id, topic_id, "shows", ref=ref)
            if query_id and topic_key in query_topics:
                graph.edge(query_id, topic_id, "about", ref=ref)
            entities.append(topic_id)
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

    for meeting in meetings:
        ref = {"type": "meeting", "id": meeting["id"], "timestamp": meeting["started_at"]}
        start = datetime.fromisoformat(meeting["started_at"])
        stop = datetime.fromisoformat(meeting["ended_at"] or meeting["started_at"])
        meeting_node = graph.node(
            "meeting", str(meeting["id"]), meeting["title"] or f"Meeting {meeting['id']}", ref=ref,
            seconds=max(0.0, (stop - start).total_seconds()), props={"app": meeting["app_name"]},
        )
        if session_node:
            graph.edge(session_node, meeting_node, "captured", ref=ref)

    for line in audio:
        ref = {"type": "audio", "id": line["id"], "timestamp": line["timestamp"]}
        # The meeting node may live in another shard; the edge resolves once shards are aggregated.
        meeting_node = f"meeting:{line['meeting_id']}" if line["meeting_id"] else None
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
    _apply_scores(graph, corpus)
    return {"app": previous_app, "window": previous_window}


def _apply_scores(graph: _Builder, corpus: keyphrases.Corpus) -> None:
    """Keyphrase scores (0-1, shard max = 1) go into props and scale weight; a query scores as its best topic."""
    for key, score in corpus.scores.items():
        item = graph.nodes.get(f"topic:{_slug(key)}")
        if item is not None:
            item["props"]["score"] = score
            item["weight"] = round(item["weight"] * (1 + score), 2)
    for (src, dst, rel) in graph.edges:
        if rel == "about" and src.startswith("query:") and dst in graph.nodes:
            props = graph.nodes[src]["props"]
            props["score"] = max(props.get("score", 0), graph.nodes[dst]["props"].get("score", 0))
    for item in graph.nodes.values():
        if item["kind"] == "query":
            item["props"].setdefault("score", keyphrases.MIN_SCORE)
            item["weight"] = round(item["weight"] * (1 + item["props"]["score"]), 2)


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


# Writing shard rows. Incremental passes add onto existing rows.

_UPSERT_NODE = """
    INSERT INTO kg_nodes(scope, id, kind, label, weight, seconds, first_seen, last_seen, evidence, props)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(scope, id) DO UPDATE SET
      weight = weight + excluded.weight,
      seconds = ROUND(seconds + excluded.seconds, 1),
      first_seen = CASE WHEN first_seen IS NULL OR excluded.first_seen < first_seen THEN excluded.first_seen ELSE first_seen END,
      last_seen = CASE WHEN last_seen IS NULL OR excluded.last_seen > last_seen THEN excluded.last_seen ELSE last_seen END,
      evidence = kg_merge_refs(evidence, excluded.evidence),
      props = kg_merge_props(props, excluded.props)
"""

_UPSERT_EDGE = """
    INSERT INTO kg_edges(scope, src, dst, rel, weight, first_seen, last_seen, evidence)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(scope, src, dst, rel) DO UPDATE SET
      weight = ROUND(weight + excluded.weight, 1),
      first_seen = CASE WHEN first_seen IS NULL OR excluded.first_seen < first_seen THEN excluded.first_seen ELSE first_seen END,
      last_seen = CASE WHEN last_seen IS NULL OR excluded.last_seen > last_seen THEN excluded.last_seen ELSE last_seen END,
      evidence = kg_merge_refs(evidence, excluded.evidence)
"""


def _merge_refs(old: str | None, new: str | None) -> str:
    merged = json.loads(old or "[]")
    for ref in json.loads(new or "[]"):
        if len(merged) >= EVIDENCE_CAP:
            break
        if ref not in merged:
            merged.append(ref)
    return json.dumps(merged)


def _merge_props(old: str | None, new: str | None) -> str:
    kept, added = json.loads(old or "{}"), json.loads(new or "{}")
    events = dict(kept.get("events") or {})
    for name, count in (added.get("events") or {}).items():
        events[name] = events.get(name, 0) + count
    merged = {**added, **kept}
    if events:
        merged["events"] = events
    return json.dumps(merged)


def _mark_dirty(connection, scope: str, graph: _Builder | None = None) -> None:
    """Queue keys whose shard rows change so the next aggregation regroups only them."""
    if graph is None:
        connection.execute("INSERT OR IGNORE INTO kg_dirty_nodes(id) SELECT id FROM kg_nodes WHERE scope = ?", (scope,))
        connection.execute(
            "INSERT OR IGNORE INTO kg_dirty_edges(src, dst, rel) SELECT src, dst, rel FROM kg_edges WHERE scope = ?", (scope,)
        )
        return
    connection.executemany("INSERT OR IGNORE INTO kg_dirty_nodes(id) VALUES (?)", [(node_id,) for node_id in graph.nodes])
    connection.executemany("INSERT OR IGNORE INTO kg_dirty_edges(src, dst, rel) VALUES (?, ?, ?)", list(graph.edges))


def _write(connection, graph: _Builder, *, replace: bool) -> None:
    if replace:
        _mark_dirty(connection, graph.scope)
        connection.execute("DELETE FROM kg_nodes WHERE scope = ?", (graph.scope,))
        connection.execute("DELETE FROM kg_edges WHERE scope = ?", (graph.scope,))
    _mark_dirty(connection, graph.scope, graph)
    connection.create_function("kg_merge_refs", 2, _merge_refs, deterministic=True)
    connection.create_function("kg_merge_props", 2, _merge_props, deterministic=True)
    connection.executemany(
        _UPSERT_NODE,
        [
            (graph.scope, n["id"], n["kind"], n["label"], max(n["weight"], 0), round(n["seconds"], 1), n["first_seen"],
             n["last_seen"], json.dumps(n["evidence"]), json.dumps(n["props"]))
            for n in graph.nodes.values()
        ],
    )
    connection.executemany(
        _UPSERT_EDGE,
        [
            (graph.scope, e["src"], e["dst"], e["rel"], round(e["weight"], 1), e["first_seen"], e["last_seen"], json.dumps(e["evidence"]))
            for e in graph.edges.values()
        ],
    )


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _save_shard(connection, scope: str, started_at: str | None, ended_at: str | None, *, final: bool, marks: dict | None) -> None:
    connection.execute(
        """
        INSERT INTO kg_shards(scope, started_at, ended_at, version, final, marks, built_at) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scope) DO UPDATE SET started_at = excluded.started_at, ended_at = excluded.ended_at,
          version = excluded.version, final = excluded.final, marks = excluded.marks, built_at = excluded.built_at
        """,
        (scope, started_at, ended_at, GRAPH_VERSION, int(final), json.dumps(marks) if marks is not None else None, _stamp()),
    )
    if scope.startswith("session:"):
        db._set(connection, f"graph_built_at:{scope}", utc_iso())


def drop_shard(connection, scope: str) -> None:
    _mark_dirty(connection, scope)
    connection.execute("DELETE FROM kg_nodes WHERE scope = ?", (scope,))
    connection.execute("DELETE FROM kg_edges WHERE scope = ?", (scope,))
    connection.execute("DELETE FROM kg_shards WHERE scope = ?", (scope,))


class _Raced(Exception):
    """Another process advanced the shard's watermark first; its pass already covered these rows."""


def _session_pass(connection, session_id: int, *, incremental: bool) -> dict:
    row = connection.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row is None:
        raise db.ChronicleError("session not found", 404)
    scope = scope_for(session_id)
    live = row["status"] == "recording"
    start = row["started_at"]
    end = None if live else (row["stopped_at"] or row["ends_at"] or start)
    meta = connection.execute("SELECT * FROM kg_shards WHERE scope = ?", (scope,)).fetchone()
    previous = None
    if incremental and meta is not None and meta["version"] == GRAPH_VERSION and meta["marks"]:
        previous = json.loads(meta["marks"])
    marks = previous or {"frame": 0, "event": 0, "audio": 0, "meeting": 0, "state": {}}
    # Capture dedupe extends the newest frame's duration in place, so a live pass leaves it for later.
    hold = " AND id < (SELECT MAX(id) FROM frames WHERE session_id = ?)" if live else ""
    frames = connection.execute(
        f"SELECT * FROM frames WHERE session_id = ? AND id > ?{hold} ORDER BY timestamp, id",
        (session_id, marks["frame"], *([session_id] if live else [])),
    ).fetchall()
    events = connection.execute(
        "SELECT * FROM ui_events WHERE session_id = ? AND id > ? ORDER BY timestamp, id", (session_id, marks["event"])
    ).fetchall()
    upper = end or "9999"
    meetings = connection.execute(
        "SELECT * FROM meetings WHERE id > ? AND started_at >= ? AND started_at <= ?", (marks["meeting"], start, upper)
    ).fetchall()
    audio = connection.execute(
        _AUDIO_SQL + " WHERE t.id > ? AND t.timestamp >= ? AND t.timestamp <= ? ORDER BY t.timestamp",
        (marks["audio"], start, upper),
    ).fetchall()

    graph = _Builder(scope)
    graph.people = _people_index(connection)
    session_node = f"session:{session_id}"
    if previous is None:
        session_node = graph.node("session", str(session_id), row["label"] or f"Recording {session_id}")
    state = _extract(graph, connection, frames, events, meetings, audio, session_node, marks.get("state") or {})
    advanced = {
        "frame": max([marks["frame"], *(f["id"] for f in frames)]),
        "event": max([marks["event"], *(e["id"] for e in events)]),
        "audio": max([marks["audio"], *(a["id"] for a in audio)]),
        "meeting": max([marks["meeting"], *(m["id"] for m in meetings)]),
        "state": state,
    }
    if previous is not None:
        claimed = connection.execute(
            "UPDATE kg_shards SET marks = ? WHERE scope = ? AND marks = ?", (json.dumps(advanced), scope, meta["marks"])
        ).rowcount
        if not claimed:
            raise _Raced
    _write(connection, graph, replace=previous is None)
    _save_shard(connection, scope, start, end, final=not live, marks=advanced)
    return {
        "scope": scope, "incremental": previous is not None, "frames": len(frames), "events": len(events),
        "nodes": len(graph.nodes), "edges": len(graph.edges), "built_at": utc_iso(),
    }


def _windows(connection) -> list[tuple[int, str, str | None]]:
    """(session id, start, end) for every recording; end is None while it is live."""
    return [
        (row["id"], row["started_at"], None if row["status"] == "recording" else (row["stopped_at"] or row["ends_at"] or row["started_at"]))
        for row in connection.execute("SELECT * FROM sessions ORDER BY started_at")
    ]


def _owner(stamp: str, windows) -> str:
    for session_id, start, end in windows:
        if start <= stamp and (end is None or stamp <= end):
            return scope_for(session_id)
    return f"loose:{stamp[:13]}"


def _loose_pass(connection, hour: str, windows) -> dict:
    scope = f"loose:{hour}"
    start = f"{hour}:00:00+00:00"
    end = utc_iso(datetime.fromisoformat(start) + timedelta(hours=1))
    span = (start, end)
    frames = connection.execute(
        "SELECT * FROM frames WHERE session_id IS NULL AND timestamp >= ? AND timestamp < ? ORDER BY timestamp, id", span
    ).fetchall()
    events = connection.execute(
        "SELECT * FROM ui_events WHERE session_id IS NULL AND timestamp >= ? AND timestamp < ? ORDER BY timestamp, id", span
    ).fetchall()
    meetings = [
        m for m in connection.execute("SELECT * FROM meetings WHERE started_at >= ? AND started_at < ?", span)
        if _owner(m["started_at"], windows) == scope
    ]
    audio = [
        a for a in connection.execute(_AUDIO_SQL + " WHERE t.timestamp >= ? AND t.timestamp < ? ORDER BY t.timestamp", span)
        if _owner(a["timestamp"], windows) == scope
    ]
    if not (frames or events or meetings or audio):
        drop_shard(connection, scope)
        return {"scope": scope, "frames": 0, "nodes": 0, "edges": 0}
    graph = _Builder(scope)
    graph.people = _people_index(connection)
    _extract(graph, connection, frames, events, meetings, audio, None, {})
    _write(connection, graph, replace=True)
    last = max([*(f["timestamp"] for f in frames), *(e["timestamp"] for e in events),
                *(m["started_at"] for m in meetings), *(a["timestamp"] for a in audio)])
    _save_shard(connection, scope, start, last, final=True, marks=None)
    return {"scope": scope, "frames": len(frames), "nodes": len(graph.nodes), "edges": len(graph.edges)}


def _raw_marks(connection) -> dict:
    row = connection.execute(
        """
        SELECT (SELECT MAX(id) FROM frames) AS frames, (SELECT MAX(id) FROM ui_events) AS ui_events,
               (SELECT MAX(id) FROM audio_transcriptions) AS audio, (SELECT MAX(id) FROM meetings) AS meetings
        """
    ).fetchone()
    return {key: row[key] or 0 for key in row.keys()}


def refresh(session_id: int, incremental: bool | None = None) -> dict:
    """Bring a live session's shard up to date.

    Keyphrases, tasks and merges are scored over the whole session, so by default a refresh re-extracts
    the session; ``CHRONICLE_GRAPH_INCREMENTAL=1`` folds in only new rows instead (cheaper, cruder).
    """
    if incremental is None:
        incremental = os.environ.get("CHRONICLE_GRAPH_INCREMENTAL") == "1"
    try:
        with db.conn() as connection:
            return _session_pass(connection, session_id, incremental=incremental)
    except _Raced:
        return {"scope": scope_for(session_id), "incremental": True, "frames": 0, "raced": True}


def sync() -> dict:
    """Bring every shard up to date with raw history. Cheap when nothing changed."""
    with db.conn() as connection:
        windows = _windows(connection)
        seen = json.loads(db._raw_setting(connection, "graph_sync_marks") or "{}")
        current = _raw_marks(connection)
        shards = {row["scope"]: row for row in connection.execute("SELECT scope, version, final FROM kg_shards")}
        known = {scope_for(session_id) for session_id, _, _ in windows}
        for scope in shards:
            if scope.startswith("session:") and scope not in known:
                drop_shard(connection, scope)
        full: set[int] = set()
        live: set[int] = set()
        hours: set[str] = set()
        for session_id, _, end in windows:
            meta = shards.get(scope_for(session_id))
            if end is None:
                live.add(session_id)
            elif meta is None or meta["version"] != GRAPH_VERSION or not meta["final"]:
                full.add(session_id)
        hours.update(scope[6:] for scope, meta in shards.items() if scope.startswith("loose:") and meta["version"] != GRAPH_VERSION)
        for table in ("frames", "ui_events"):
            hours.update(
                row[0] for row in connection.execute(
                    f"SELECT DISTINCT substr(timestamp, 1, 13) FROM {table} WHERE session_id IS NULL AND id > ?", (seen.get(table, 0),)
                ) if row[0]
            )
        stamps = [row[0] for row in connection.execute("SELECT timestamp FROM audio_transcriptions WHERE id > ?", (seen.get("audio", 0),))]
        stamps += [row[0] for row in connection.execute("SELECT started_at FROM meetings WHERE id > ?", (seen.get("meetings", 0),))]
        for stamp in filter(None, stamps):
            owner = _owner(stamp, windows)
            if owner.startswith("loose:"):
                hours.add(owner[6:])
            elif int(owner.split(":")[1]) not in live:
                full.add(int(owner.split(":")[1]))
    for session_id in sorted(full):
        with db.conn() as connection:
            _session_pass(connection, session_id, incremental=False)
    for session_id in sorted(live):
        refresh(session_id)
    for hour in sorted(hours):
        with db.conn() as connection:
            _loose_pass(connection, hour, windows)
    db.set_setting("graph_sync_marks", json.dumps(current))
    return {"sessions": len(full), "live": len(live), "hours": len(hours)}


# Aggregation: shard rows -> `all`

ANCHOR_SPAN = 64

# CROSS JOIN pins the loop order: drive from the few dirty keys, not a scan of every shard row.
_NODE_SOURCE = {
    "full": "FROM kg_shards s JOIN kg_nodes n ON n.scope = s.scope WHERE n.kind != 'session'",
    "dirty": """FROM kg_dirty_nodes d CROSS JOIN kg_nodes n ON n.id = d.id CROSS JOIN kg_shards s ON s.scope = n.scope
                WHERE n.kind != 'session'""",
}
_EDGE_SOURCE = {
    "full": "FROM kg_shards s JOIN kg_edges e ON e.scope = s.scope WHERE e.src NOT LIKE 'session:%' AND e.dst NOT LIKE 'session:%'",
    "dirty": """FROM kg_dirty_edges d CROSS JOIN kg_edges e ON e.src = d.src AND e.dst = d.dst AND e.rel = d.rel
                CROSS JOIN kg_shards s ON s.scope = e.scope WHERE e.src NOT LIKE 'session:%' AND e.dst NOT LIKE 'session:%'""",
}

# Label, kind, and props come from the most recent shard. Evidence is re-ranked newest-first only for keys
# seen in several shards; a key from one shard keeps that shard's evidence as is.
_AGG_NODES = """
    WITH pieces AS (SELECT n.*, s.factor, COALESCE(s.ended_at, '9999') AS shard_end {source}),
    latest AS (SELECT id, MAX(shard_end), kind, label, props, evidence FROM pieces GROUP BY id),
    totals AS (
        SELECT id, COUNT(*) AS shards, SUM(weight) AS weight, ROUND(SUM(seconds), 1) AS seconds,
               MIN(first_seen) AS first_seen, MAX(last_seen) AS last_seen, SUM(weight * factor) AS eff
        FROM pieces GROUP BY id
    ),
    refs AS (
        SELECT p.id, j.value AS ref,
               ROW_NUMBER() OVER (PARTITION BY p.id ORDER BY json_extract(j.value, '$.timestamp') DESC) AS rn
        FROM pieces p JOIN totals t ON t.id = p.id AND t.shards > 1, json_each(p.evidence) AS j
    ),
    merged AS (
        SELECT id, json_group_array(json(ref)) AS evidence
        FROM (SELECT id, ref FROM refs WHERE rn <= :cap ORDER BY id, rn) GROUP BY id
    )
    INSERT INTO kg_nodes(scope, id, kind, label, weight, seconds, first_seen, last_seen, evidence, props, eff_weight)
    SELECT 'all', t.id, l.kind, l.label, t.weight, t.seconds, t.first_seen, t.last_seen,
           COALESCE(m.evidence, l.evidence), l.props, t.eff
    FROM totals t JOIN latest l ON l.id = t.id LEFT JOIN merged m ON m.id = t.id
"""

_AGG_EDGES = """
    WITH pieces AS (SELECT e.*, s.factor {source}),
    totals AS (
        SELECT src, dst, rel, COUNT(*) AS shards, ROUND(SUM(weight), 1) AS weight, MIN(first_seen) AS first_seen,
               MAX(last_seen) AS last_seen, MAX(evidence) AS evidence, SUM(weight * factor) AS eff
        FROM pieces GROUP BY src, dst, rel
    ),
    refs AS (
        SELECT p.src, p.dst, p.rel, j.value AS ref,
               ROW_NUMBER() OVER (PARTITION BY p.src, p.dst, p.rel ORDER BY json_extract(j.value, '$.timestamp') DESC) AS rn
        FROM pieces p JOIN totals t ON t.src = p.src AND t.dst = p.dst AND t.rel = p.rel AND t.shards > 1,
             json_each(p.evidence) AS j
    ),
    merged AS (
        SELECT src, dst, rel, json_group_array(json(ref)) AS evidence
        FROM (SELECT src, dst, rel, ref FROM refs WHERE rn <= :cap ORDER BY src, dst, rel, rn) GROUP BY src, dst, rel
    )
    INSERT INTO kg_edges(scope, src, dst, rel, weight, first_seen, last_seen, evidence, eff_weight)
    SELECT 'all', t.src, t.dst, t.rel, t.weight, t.first_seen, t.last_seen, COALESCE(m.evidence, t.evidence), t.eff
    FROM totals t LEFT JOIN merged m ON m.src = t.src AND m.dst = t.dst AND m.rel = t.rel
"""


def _half_life(connection) -> float:
    raw = (db._raw_setting(connection, "graph_half_life_hours") or os.environ.get("CHRONICLE_HALF_LIFE_HOURS") or "").strip().lower()
    if raw in _NO_DECAY:
        return math.inf
    try:
        value = float(raw) if raw else HALF_LIFE_HOURS
    except ValueError:
        return HALF_LIFE_HOURS
    return value if value > 0 else math.inf


def decay(age_hours: float, half_life: float) -> float:
    return 0.5 ** (age_hours / half_life)


def _hours(delta: timedelta) -> float:
    return delta.total_seconds() / 3600


def _state(connection) -> dict:
    return json.loads(db._raw_setting(connection, "graph_all_state") or "{}")


def _scale(connection) -> float:
    """Decay since the anchor. Every shard decays by the same factor, so stored ``eff_weight`` only needs rescaling."""
    anchor = _state(connection).get("anchor")
    if not anchor:
        return 1.0
    return decay(_hours(datetime.now(timezone.utc) - datetime.fromisoformat(anchor)), _half_life(connection))


def aggregate(full: bool = False) -> dict:
    """Materialize ``all`` from shard rows with SQL; no raw frames are read.

    ``eff_weight`` is Σ weight × 0.5 ** ((anchor - shard_end) / half_life). Only keys queued in kg_dirty_* are
    regrouped unless the anchor is reset (first run, half-life change, or after ANCHOR_SPAN half-lives).
    """
    began = time.perf_counter()
    now = datetime.now(timezone.utc)
    with db.conn() as connection:
        half = _half_life(connection)
        state = _state(connection)
        anchor = datetime.fromisoformat(state["anchor"]) if state.get("anchor") else None
        if full or anchor is None or state.get("half_life") != str(half) or _hours(now - anchor) / half > ANCHOR_SPAN:
            full, anchor = True, now
        shards = connection.execute("SELECT scope, ended_at FROM kg_shards").fetchall()
        connection.executemany(
            "UPDATE kg_shards SET factor = ? WHERE scope = ?",
            [
                (decay(_hours(anchor - (datetime.fromisoformat(row["ended_at"]) if row["ended_at"] else now)), half), row["scope"])
                for row in shards
            ],
        )
        mode = "full" if full else "dirty"
        dirty = {
            "nodes": connection.execute("SELECT COUNT(*) AS n FROM kg_dirty_nodes").fetchone()["n"],
            "edges": connection.execute("SELECT COUNT(*) AS n FROM kg_dirty_edges").fetchone()["n"],
        }
        if full:
            connection.execute("DELETE FROM kg_nodes WHERE scope = 'all'")
            connection.execute("DELETE FROM kg_edges WHERE scope = 'all'")
        else:
            connection.execute("DELETE FROM kg_nodes WHERE scope = 'all' AND id IN (SELECT id FROM kg_dirty_nodes)")
            connection.execute(
                "DELETE FROM kg_edges WHERE scope = 'all' AND (src, dst, rel) IN (SELECT src, dst, rel FROM kg_dirty_edges)"
            )
        params = {"cap": EVIDENCE_CAP}
        connection.execute(_AGG_NODES.format(source=_NODE_SOURCE[mode]), params)
        connection.execute(_AGG_EDGES.format(source=_EDGE_SOURCE[mode]), params)
        events: dict[str, dict[str, int]] = {}
        for row in connection.execute(
            f"""
            SELECT n.id, json_extract(n.props, '$.events') AS events {_NODE_SOURCE[mode]}
              AND n.kind = 'app' AND json_extract(n.props, '$.events') IS NOT NULL
            """
        ):
            counts = events.setdefault(row["id"], {})
            for name, count in json.loads(row["events"]).items():
                counts[name] = counts.get(name, 0) + count
        connection.executemany(
            "UPDATE kg_nodes SET props = json_set(COALESCE(props, '{}'), '$.events', json(?)) WHERE scope = 'all' AND id = ?",
            [(json.dumps(counts), node_id) for node_id, counts in events.items()],
        )
        connection.execute("DELETE FROM kg_dirty_nodes")
        connection.execute("DELETE FROM kg_dirty_edges")
        nodes = connection.execute("SELECT COUNT(*) AS n FROM kg_nodes WHERE scope = 'all'").fetchone()["n"]
        edges = connection.execute("SELECT COUNT(*) AS n FROM kg_edges WHERE scope = 'all'").fetchone()["n"]
        db._set(connection, "graph_built_at:all", utc_iso(now))
        db._set(connection, "graph_all_state", json.dumps({"anchor": anchor.isoformat(), "at": _stamp(), "half_life": str(half)}))
    return {
        "scope": "all", "mode": mode, "nodes": nodes, "edges": edges, "regrouped": None if full else dirty, "shards": len(shards),
        "half_life_hours": None if math.isinf(half) else half, "built_at": utc_iso(now),
        "ms": round((time.perf_counter() - began) * 1000, 1),
    }


def _all_stale(connection) -> bool:
    state = _state(connection)
    if not state.get("anchor") or state.get("half_life") != str(_half_life(connection)):
        return True
    if _raw_marks(connection) != json.loads(db._raw_setting(connection, "graph_sync_marks") or "{}"):
        return True
    if (datetime.now(timezone.utc) - datetime.fromisoformat(state["at"])).total_seconds() > REAGGREGATE_SECS:
        return True
    return connection.execute(
        "SELECT EXISTS(SELECT 1 FROM kg_dirty_nodes) OR EXISTS(SELECT 1 FROM kg_dirty_edges) AS dirty"
    ).fetchone()["dirty"] == 1


def build(session_id: int | None = None) -> dict:
    """Re-extract one session's shard, or sync every shard; then re-aggregate ``all``."""
    if session_id:
        with db.conn() as connection:
            result = _session_pass(connection, session_id, incremental=False)
        aggregate()
        return result
    synced = sync()
    return {**aggregate(), "synced": synced}


def rebuild() -> dict:
    """Drop every shard and re-extract all history, e.g. after the extractors change."""
    with db.conn() as connection:
        connection.execute("DELETE FROM kg_nodes WHERE scope IN (SELECT scope FROM kg_shards)")
        connection.execute("DELETE FROM kg_edges WHERE scope IN (SELECT scope FROM kg_shards)")
        for table in ("kg_shards", "kg_dirty_nodes", "kg_dirty_edges"):
            connection.execute(f"DELETE FROM {table}")
        connection.execute("DELETE FROM settings WHERE key = 'graph_sync_marks'")
    synced = sync()
    return {**aggregate(full=True), "synced": synced}


def forget(start: str, end: str) -> dict:
    """Re-extract shards overlapping a deleted time range so removed history leaves the graph."""
    with db.conn() as connection:
        windows = _windows(connection)
        hours = [
            row["scope"][6:]
            for row in connection.execute(
                "SELECT scope FROM kg_shards WHERE scope LIKE 'loose:%' AND started_at <= ? AND ended_at >= ?", (end, start)
            )
        ]
    for session_id, began, ended in windows:
        if began <= end and (ended is None or ended >= start):
            with db.conn() as connection:
                _session_pass(connection, session_id, incremental=False)
    for hour in hours:
        with db.conn() as connection:
            _loose_pass(connection, hour, windows)
    return aggregate()


def build_unsharded(scope: str = "unsharded") -> dict:
    """The pre-shard full build: one extraction over every raw row. Kept for benchmarks and equivalence checks."""
    graph = _Builder(scope)
    with db.conn() as connection:
        graph.people = _people_index(connection)
        frames = connection.execute("SELECT * FROM frames ORDER BY timestamp, id").fetchall()
        events = connection.execute("SELECT * FROM ui_events ORDER BY timestamp, id").fetchall()
        meetings = connection.execute("SELECT * FROM meetings").fetchall()
        audio = connection.execute(_AUDIO_SQL + " ORDER BY t.timestamp").fetchall()
        _extract(graph, connection, frames, events, meetings, audio, None, {})
        _write(connection, graph, replace=True)
    return {"scope": scope, "nodes": len(graph.nodes), "edges": len(graph.edges)}


def ensure(session_id: int | None = None) -> None:
    if session_id:
        with db.conn() as connection:
            row = connection.execute(
                "SELECT s.status, k.final FROM sessions s LEFT JOIN kg_shards k ON k.scope = ? WHERE s.id = ?",
                (scope_for(session_id), session_id),
            ).fetchone()
        if row is None:
            return
        if row["status"] == "recording":
            refresh(session_id)
        elif not row["final"]:
            with db.conn() as connection:
                _session_pass(connection, session_id, incremental=False)
        return
    with db.conn() as connection:
        stale = _all_stale(connection)
    if stale:
        sync()
        aggregate()


# Queries

def _rank(scope: str, alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    if scope == "all":
        return f"{p}eff_weight DESC, {p}seconds DESC"
    # A keyphrase score of 1 counts like a minute on screen, so scored topics compete with long-viewed windows.
    return f"({p}seconds + 60 * COALESCE(json_extract({p}props, '$.score'), 0)) DESC, {p}weight DESC"


def _edge_rank(scope: str, alias: str = "") -> str:
    p = f"{alias}." if alias else ""
    return f"{p}eff_weight DESC, {p}weight DESC" if scope == "all" else f"{p}weight DESC"


def _effective(row, scale: float) -> float | None:
    value = row["eff_weight"] if "eff_weight" in row.keys() else None
    return None if value is None else round(value * scale, 3)


def _node_public(row, scale: float = 1.0) -> dict:
    return {
        "id": row["id"], "kind": row["kind"], "label": row["label"], "weight": row["weight"],
        "effective_weight": _effective(row, scale), "seconds": row["seconds"], "first_seen": row["first_seen"],
        "last_seen": row["last_seen"], "evidence": json.loads(row["evidence"] or "[]"), "props": json.loads(row["props"] or "{}"),
    }


def _edge_public(row, scale: float = 1.0) -> dict:
    return {
        "src": row["src"], "dst": row["dst"], "rel": row["rel"], "weight": row["weight"], "effective_weight": _effective(row, scale),
        "first_seen": row["first_seen"], "last_seen": row["last_seen"],
        "evidence": json.loads(row["evidence"] or "[]"),
    }


def summary(session_id: int | None = None, top: int = 8) -> dict:
    ensure(session_id)
    scope = scope_for(session_id)
    with db.conn() as connection:
        scale = _scale(connection) if scope == "all" else 1.0
        kinds = connection.execute(
            "SELECT kind, COUNT(*) AS n FROM kg_nodes WHERE scope = ? GROUP BY kind", (scope,)
        ).fetchall()
        tops: dict[str, list[dict]] = {}
        for row in kinds:
            found = connection.execute(
                f"SELECT * FROM kg_nodes WHERE scope = ? AND kind = ? ORDER BY {_rank(scope)} LIMIT ?",
                (scope, row["kind"], top),
            ).fetchall()
            tops[row["kind"]] = [
                {"id": item["id"], "label": item["label"], "weight": item["weight"], "effective_weight": _effective(item, scale),
                 "seconds": item["seconds"],
                 **({"score": score} if (score := json.loads(item["props"] or "{}").get("score")) is not None else {})}
                for item in found
            ]
        transitions = connection.execute(
            f"""
            SELECT e.*, s.label AS src_label, d.label AS dst_label, s.props AS src_props, d.props AS dst_props
            FROM kg_edges e
            JOIN kg_nodes s ON s.scope = e.scope AND s.id = e.src
            JOIN kg_nodes d ON d.scope = e.scope AND d.id = e.dst
            WHERE e.scope = ? AND e.rel IN ('switched_to', 'then')
            ORDER BY {_edge_rank(scope, 'e')} LIMIT ?
            """,
            (scope, top * 2),
        ).fetchall()
        edge_count = connection.execute("SELECT COUNT(*) AS n FROM kg_edges WHERE scope = ?", (scope,)).fetchone()["n"]
        half = _half_life(connection) if scope == "all" else None
    order = {kind: index for index, kind in enumerate(KIND_ORDER)}
    result = {
        "scope": scope,
        "built_at": db.get_setting(f"graph_built_at:{scope}"),
        "counts": {row["kind"]: row["n"] for row in sorted(kinds, key=lambda r: order.get(r["kind"], 99))},
        "edges": edge_count,
        "top": tops,
        "transitions": [
            {
                "rel": row["rel"], "from": _transition_label(row["src_label"], row["src_props"]),
                "to": _transition_label(row["dst_label"], row["dst_props"]), "count": row["weight"],
                "effective_weight": _effective(row, scale), "src": row["src"], "dst": row["dst"],
            }
            for row in transitions
        ],
    }
    if half is not None:
        result["half_life_hours"] = None if math.isinf(half) else half
    return result


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
        ORDER BY hits DESC, {_rank(scope)} LIMIT ?
    """
    with db.conn() as connection:
        scale = _scale(connection) if scope == "all" else 1.0
        rows = connection.execute(sql, [*params_score, *params, max(1, min(limit, 200))]).fetchall()
    return [_node_public(row, scale) for row in rows]


def node(node_id: str, session_id: int | None = None, limit: int = 25) -> dict:
    ensure(session_id)
    scope = scope_for(session_id)
    with db.conn() as connection:
        scale = _scale(connection) if scope == "all" else 1.0
        row = connection.execute("SELECT * FROM kg_nodes WHERE scope = ? AND id = ?", (scope, node_id)).fetchone()
        if row is None:
            fallback = connection.execute(
                f"SELECT * FROM kg_nodes WHERE scope = ? AND lower(label) = lower(?) ORDER BY {_rank(scope)} LIMIT 1",
                (scope, node_id),
            ).fetchone()
            if fallback is None:
                raise db.ChronicleError(f"no graph node {node_id!r} in {scope}", 404)
            row = fallback
        edges = connection.execute(
            f"""
            SELECT e.*, n.kind AS other_kind, n.label AS other_label, n.id AS other_id,
                   CASE WHEN e.src = ? THEN 'out' ELSE 'in' END AS direction
            FROM kg_edges e
            JOIN kg_nodes n ON n.scope = e.scope AND n.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END
            WHERE e.scope = ? AND (e.src = ? OR e.dst = ?)
            ORDER BY {_edge_rank(scope, 'e')} LIMIT ?
            """,
            (row["id"], row["id"], scope, row["id"], row["id"], max(1, min(limit, 200))),
        ).fetchall()
    return {
        "scope": scope,
        "node": _node_public(row, scale),
        "neighbors": [
            {
                "id": edge["other_id"], "kind": edge["other_kind"], "label": edge["other_label"],
                "rel": edge["rel"], "direction": edge["direction"], "weight": edge["weight"],
                "effective_weight": _effective(edge, scale), "last_seen": edge["last_seen"],
                "evidence": json.loads(edge["evidence"] or "[]")[:3],
            }
            for edge in edges
        ],
    }


def subgraph(task: str | None, session_id: int | None = None, max_nodes: int = 30) -> dict:
    """Nodes relevant to a task plus their strongest neighbors. Without a task, the heaviest nodes."""
    ensure(session_id)
    scope = scope_for(session_id)
    seeds = find(task, limit=max(4, max_nodes // 3), session_id=session_id) if task else []
    chosen: dict[str, dict] = {item["id"]: item for item in seeds}
    with db.conn() as connection:
        scale = _scale(connection) if scope == "all" else 1.0
        if not task:
            rows = connection.execute(
                f"""
                SELECT * FROM kg_nodes WHERE scope = ? AND kind NOT IN ('window', 'session')
                ORDER BY {_rank(scope)} LIMIT ?
                """,
                (scope, max_nodes),
            ).fetchall()
            for row in rows:
                chosen[row["id"]] = _node_public(row, scale)
        for seed in list(chosen.values())[: max_nodes // 2 or 1]:
            neighbors = connection.execute(
                f"""
                SELECT n.* FROM kg_edges e
                JOIN kg_nodes n ON n.scope = e.scope AND n.id = CASE WHEN e.src = ? THEN e.dst ELSE e.src END
                WHERE e.scope = ? AND (e.src = ? OR e.dst = ?) AND e.rel != 'has_window'
                ORDER BY {_edge_rank(scope, 'e')} LIMIT 6
                """,
                (seed["id"], scope, seed["id"], seed["id"]),
            ).fetchall()
            for row in neighbors:
                if len(chosen) >= max_nodes:
                    break
                chosen.setdefault(row["id"], _node_public(row, scale))
        ids = list(chosen)
        edges = []
        if ids:
            marks = ",".join("?" for _ in ids)
            edges = connection.execute(
                f"""
                SELECT * FROM kg_edges WHERE scope = ? AND src IN ({marks}) AND dst IN ({marks})
                ORDER BY {_edge_rank(scope)} LIMIT ?
                """,
                (scope, *ids, *ids, max_nodes * 3),
            ).fetchall()
    return {"scope": scope, "seeds": [item["id"] for item in seeds], "nodes": list(chosen.values()), "edges": [_edge_public(e, scale) for e in edges]}


def export(session_id: int | None = None, limit: int = 160) -> dict:
    ensure(session_id)
    scope = scope_for(session_id)
    with db.conn() as connection:
        scale = _scale(connection) if scope == "all" else 1.0
        rows = connection.execute(
            f"SELECT * FROM kg_nodes WHERE scope = ? ORDER BY {_rank(scope)} LIMIT ?",
            (scope, max(1, min(limit, 1000))),
        ).fetchall()
        ids = [row["id"] for row in rows]
        edges = []
        if ids:
            marks = ",".join("?" for _ in ids)
            edges = connection.execute(
                f"SELECT * FROM kg_edges WHERE scope = ? AND src IN ({marks}) AND dst IN ({marks}) ORDER BY {_edge_rank(scope)} LIMIT ?",
                (scope, *ids, *ids, limit * 4),
            ).fetchall()
    return {
        "scope": scope,
        "built_at": db.get_setting(f"graph_built_at:{scope}"),
        "nodes": [_node_public(row, scale) for row in rows],
        "edges": [_edge_public(edge, scale) for edge in edges],
    }

"""LLM graph tuning: the model reviews the heuristic graph and replies with patch ops.

Accepted ops are stored in ``kg_llm_patches`` and replayed in order after every
``graph.build()``, so tuning survives rebuilds until ``reset()`` removes it.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from urllib.parse import parse_qs, urlsplit

from chronicle import db, graph, llm
from chronicle.redact import redact
from chronicle.timeutil import utc_iso

PROMPT_VERSION = 1
ROUNDS = 2
BATCH_NODES = 40
MAX_NODES = 120
MAX_EDGES = 80
MAX_EVIDENCE = 30
SNIPPET = 300

ENTITY_KINDS = [
    "company", "organization", "product", "person", "project", "topic", "concept", "place", "event", "list", "document", "category",
]
RETYPE_KINDS = sorted(set(ENTITY_KINDS) | set(graph.KIND_ORDER) - {"session", "activity"})
RELATIONS = [
    "is_a", "part_of", "works_on", "competes_with", "located_in", "researching", "mentions", "listed_in", "uses", "related_to",
]
PROTECTED = {"session"}
TRAIT_CAP = 12

SYSTEM = """You tune a personal knowledge graph that Chronicle built heuristically from a screen recording \
(app names, window titles, URLs, OCR text, dwell seconds). The heuristics only catch code-style tokens, so \
real-world entities and facts are missing. Fix the graph using tool calls only:
- add_entity for companies, products, people, organizations, concepts, lists, and places the evidence clearly \
names. Set seen_in to the node ids whose evidence names it.
- add_relation for facts the evidence supports, e.g. Crusoe is_a company, Crusoe listed_in Forbes AI 50, \
session researching physical AI. Endpoints are node ids or names of entities you add; for is_a the target may be \
a plain class like "company". Give confidence from 0 to 1 and a short evidence quote.
- rename_node to clean labels (strip " - Google Search" or site suffixes), retype_node when a kind is wrong, \
merge_nodes for duplicates, add_trait for short facts, drop_node only for obvious noise.
- set_activity once or twice: one sentence on what the user was doing, with the node ids involved.
Only reference ids that appear in the graph or entities you add in this reply. Do not invent facts the evidence \
does not support. If nothing needs changing, make no tool calls."""


def _fn(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False},
    }


_S = {"type": "string"}
_LIST = {"type": "array", "items": {"type": "string"}}
TOOLS = [
    _fn("add_entity", "Add a real-world entity the evidence names but the graph lacks.", {
        "name": _S, "kind": {"type": "string", "enum": ENTITY_KINDS}, "traits": _LIST, "aliases": _LIST,
        "seen_in": {**_LIST, "description": "Existing node ids whose evidence names this entity."},
    }),
    _fn("retype_node", "Change the kind of an existing node.", {"id": _S, "kind": {"type": "string", "enum": RETYPE_KINDS}}),
    _fn("rename_node", "Give an existing node a cleaner label.", {"id": _S, "label": _S}),
    _fn("merge_nodes", "Fold duplicate nodes into keep_id.", {"keep_id": _S, "drop_ids": _LIST}),
    _fn("add_relation", "Add a typed relation between two nodes.", {
        "source": _S, "relation": {"type": "string", "enum": RELATIONS}, "target": _S,
        "confidence": {"type": "number", "description": "0 to 1."}, "evidence": _S,
    }),
    _fn("add_trait", "Attach a short descriptive trait to a node.", {"node": _S, "trait": _S}),
    _fn("set_activity", "One-sentence summary of what the user was doing, linked to the nodes involved.", {"summary": _S, "node_ids": _LIST}),
    _fn("drop_node", "Remove an obvious noise node.", {"id": _S, "reason": _S}),
]
SPECS = {tool["name"]: tool["parameters"]["properties"] for tool in TOOLS}


class Rejected(ValueError):
    pass


def check(name: str, args) -> dict:
    """Validate tool-call arguments against the op schema and return a normalized op."""
    spec = SPECS.get(name)
    if spec is None:
        raise Rejected(f"unknown op {name!r}")
    if not isinstance(args, dict):
        raise Rejected("arguments must be an object")
    extra = set(args) - set(spec)
    if extra:
        raise Rejected(f"unexpected fields {sorted(extra)}")
    op: dict = {"op": name}
    for key, rule in spec.items():
        value = args.get(key)
        if rule["type"] == "string":
            if not isinstance(value, str) or not value.strip():
                raise Rejected(f"{key} must be a non-empty string")
            value = " ".join(value.split())[:200]
            if "enum" in rule and value not in rule["enum"]:
                raise Rejected(f"{key} must be one of {rule['enum']}")
        elif rule["type"] == "array":
            value = [] if value is None else value
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise Rejected(f"{key} must be a list of strings")
            value = list(dict.fromkeys(" ".join(item.split())[:200] for item in value if item.strip()))[:20]
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise Rejected(f"{key} must be a number from 0 to 1")
            value = round(float(value), 2)
        op[key] = value
    return op


def _resolve(nodes: dict, ref: str) -> str | None:
    if ref in nodes:
        return ref
    lowered = ref.strip().lower()
    # Keyphrase topics often share a name with a typed entity; the model means the entity it typed.
    matches = [
        item for item in nodes.values()
        if lowered in [item["label"].lower(), *(alias.lower() for alias in item["props"].get("aliases", []))]
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: (item["props"].get("source") == "llm", item["kind"] != "topic", item["weight"]))["id"]


def _need(nodes: dict, ref: str, field: str, mutable: bool = False) -> str:
    found = _resolve(nodes, ref)
    if found is None:
        raise Rejected(f"{field}: unknown node {ref!r}")
    if mutable and nodes[found]["kind"] in PROTECTED:
        raise Rejected(f"{field}: {found} cannot be changed")
    return found


def _extend(props: dict, key: str, values: list[str], cap: int = TRAIT_CAP) -> None:
    current = props.setdefault(key, [])
    lowered = {item.lower() for item in current}
    for value in values:
        if value.lower() not in lowered and len(current) < cap:
            current.append(value)
            lowered.add(value.lower())


def _absorb(target: dict, other: dict) -> None:
    for key, pick in (("first_seen", min), ("last_seen", max)):
        stamps = [stamp for stamp in (target[key], other[key]) if stamp]
        target[key] = pick(stamps) if stamps else None
    for ref in other["evidence"]:
        if len(target["evidence"]) < graph.EVIDENCE_CAP and ref not in target["evidence"]:
            target["evidence"].append(ref)


def _new_node(nodes: dict, kind: str, name: str) -> dict:
    node_id = graph.node_id(kind, name)
    if node_id not in nodes:
        nodes[node_id] = {
            "id": node_id, "kind": kind, "label": name, "weight": 0.0, "seconds": 0.0,
            "first_seen": None, "last_seen": None, "evidence": [], "props": {"source": "llm"},
        }
    return nodes[node_id]


def _edge(edges: dict, src: str, dst: str, rel: str, weight: float, props: dict) -> None:
    key = (src, dst, rel)
    item = edges.get(key)
    if item is None:
        item = edges[key] = {"src": src, "dst": dst, "rel": rel, "weight": 0.0, "first_seen": None, "last_seen": None,
                             "evidence": [], "props": {}}
    item["weight"] = max(item["weight"], weight)
    item.setdefault("props", {}).update(props)


def _link(nodes: dict, edges: dict, owner: dict, ids: list[str], rel: str, outward: bool) -> None:
    for other_id in ids:
        other = nodes[other_id]
        owner["seconds"] = max(owner["seconds"], other["seconds"])
        _absorb(owner, other)
        src, dst = (owner["id"], other_id) if outward else (other_id, owner["id"])
        _edge(edges, src, dst, rel, 1.0, {"source": "llm", "auto": True})
    owner["weight"] = max(owner["weight"], len(ids), 1)


def apply_op(nodes: dict, edges: dict, op: dict) -> dict:
    """Apply one checked op in place. Returns the op with references resolved to ids; raises Rejected."""
    name = op["op"]
    op = dict(op)
    if name == "add_entity":
        seen = [_need(nodes, ref, "seen_in") for ref in op["seen_in"]]
        item = _new_node(nodes, op["kind"], op["name"])
        _extend(item["props"], "traits", op["traits"])
        _extend(item["props"], "aliases", [alias for alias in op["aliases"] if alias.lower() != item["label"].lower()])
        _link(nodes, edges, item, [ref for ref in seen if ref != item["id"]], "mentions", outward=False)
        op["seen_in"] = seen
    elif name == "retype_node":
        node_id = op["id"] = _need(nodes, op["id"], "id", mutable=True)
        item = nodes[node_id]
        item["props"].setdefault("retyped_from", item["kind"])
        item["kind"] = op["kind"]
    elif name == "rename_node":
        node_id = op["id"] = _need(nodes, op["id"], "id", mutable=True)
        item = nodes[node_id]
        item["props"].setdefault("original_label", item["label"])
        item["label"] = op["label"]
    elif name == "merge_nodes":
        keep = op["keep_id"] = _need(nodes, op["keep_id"], "keep_id", mutable=True)
        drops = [_need(nodes, ref, "drop_ids", mutable=True) for ref in op["drop_ids"]]
        drops = [ref for ref in dict.fromkeys(drops) if ref != keep]
        if not drops:
            raise Rejected("drop_ids must name other existing nodes")
        op["drop_ids"] = drops
        target = nodes[keep]
        for drop in drops:
            other = nodes.pop(drop)
            target["weight"] += other["weight"]
            target["seconds"] += other["seconds"]
            _absorb(target, other)
            _extend(target["props"], "aliases", [other["label"]] if other["label"].lower() != target["label"].lower() else [])
            _extend(target["props"], "merged", [drop], cap=100)
            for key in [key for key in edges if drop in key[:2]]:
                edge = edges.pop(key)
                src = keep if edge["src"] == drop else edge["src"]
                dst = keep if edge["dst"] == drop else edge["dst"]
                if src == dst:
                    continue
                if edge["rel"] == "co_occurs" and dst < src:
                    src, dst = dst, src
                existing = edges.get((src, dst, edge["rel"]))
                if existing is None:
                    edges[(src, dst, edge["rel"])] = {**edge, "src": src, "dst": dst}
                else:
                    existing["weight"] += edge["weight"]
                    _absorb(existing, edge)
                    existing.setdefault("props", {}).update(edge.get("props") or {})
    elif name == "add_relation":
        src = op["source"] = _need(nodes, op["source"], "source")
        dst = _resolve(nodes, op["target"])
        if dst is None:
            if op["relation"] != "is_a":
                raise Rejected(f"target: unknown node {op['target']!r}")
            dst = _new_node(nodes, "category", op["target"].lower())["id"]
            nodes[dst]["weight"] = max(nodes[dst]["weight"], 1)
        else:
            op["target"] = dst
        if src == dst:
            raise Rejected("source and target are the same node")
        props = {"source": "llm", "confidence": op["confidence"], "evidence": op["evidence"]}
        _edge(edges, src, dst, op["relation"], round(1 + 4 * op["confidence"], 1), props)
    elif name == "add_trait":
        node_id = op["node"] = _need(nodes, op["node"], "node")
        _extend(nodes[node_id]["props"], "traits", [op["trait"]])
    elif name == "set_activity":
        ids = list(dict.fromkeys(_need(nodes, ref, "node_ids") for ref in op["node_ids"]))
        if not ids:
            raise Rejected("node_ids must name at least one node")
        op["node_ids"] = ids
        item = _new_node(nodes, "activity", op["summary"][:80])
        item["label"] = op["summary"]
        _link(nodes, edges, item, ids, "involves", outward=True)
    elif name == "drop_node":
        node_id = op["id"] = _need(nodes, op["id"], "id", mutable=True)
        nodes.pop(node_id)
        for key in [key for key in edges if node_id in key[:2]]:
            edges.pop(key)
    else:
        raise Rejected(f"unknown op {name!r}")
    return op


def patches(connection, scope: str) -> list[dict]:
    """Ops to replay for a scope. ``all`` gets only its own: session patches reach it through their shards."""
    rows = connection.execute("SELECT op FROM kg_llm_patches WHERE scope = ? ORDER BY id", (scope,)).fetchall()
    return [json.loads(row["op"]) for row in rows]


def replay(connection, scope: str, nodes: dict, edges: dict) -> dict:
    applied = skipped = 0
    for op in patches(connection, scope):
        try:
            apply_op(nodes, edges, op)
            applied += 1
        except (Rejected, KeyError):
            skipped += 1
    return {"applied": applied, "skipped": skipped}


def _load(scope: str) -> tuple[dict, dict]:
    with db.conn() as connection:
        nodes = {row["id"]: graph._node_public(row) for row in connection.execute("SELECT * FROM kg_nodes WHERE scope = ?", (scope,))}
        edges = {}
        for row in connection.execute("SELECT * FROM kg_edges WHERE scope = ?", (scope,)):
            edge = graph._edge_public(row)
            edges[(edge["src"], edge["dst"], edge["rel"])] = edge
    return nodes, edges


def _short_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    params = parse_qs(parts.query)
    query = params.get("q") or params.get("query")
    short = f"{parts.netloc.removeprefix('www.')}{parts.path}".rstrip("/")
    return f"{short}?q={query[0][:120]}" if query else short


def _evidence(frame_ids: list[int]) -> list[dict]:
    if not frame_ids:
        return []
    marks = ",".join("?" for _ in frame_ids)
    with db.conn() as connection:
        rows = connection.execute(
            f"SELECT id, app_name, window_name, browser_url, full_text, duration_secs FROM frames WHERE id IN ({marks}) ORDER BY timestamp, id",
            frame_ids,
        ).fetchall()
    grouped: dict[tuple, dict] = {}
    for row in rows:
        text = re.sub(r"https?://\S+", " ", row["full_text"] or "")
        if row["window_name"]:
            text = text.replace(row["window_name"], " ")
        text = redact(" ".join(text.split()))[:SNIPPET]
        key = (row["app_name"], row["window_name"], _short_url(row["browser_url"]), text)
        item = grouped.setdefault(key, {
            "app": key[0], "window": redact(key[1] or ""), "url": redact(key[2]) if key[2] else None, "text": text,
            "seconds": 0.0, "frames": [],
        })
        item["seconds"] += row["duration_secs"] or 0
        item["frames"].append(row["id"])
    items = sorted(grouped.values(), key=lambda item: -item["seconds"])[:MAX_EVIDENCE]
    return [{**item, "seconds": round(item["seconds"]), "frames": item["frames"][:3]} for item in items]


def _compact(node: dict) -> dict:
    props = {key: node["props"][key] for key in ("app", "url", "traits", "aliases", "source") if node["props"].get(key)}
    return {"id": node["id"], "kind": node["kind"], "label": node["label"], "seconds": round(node["seconds"]),
            "weight": round(node["weight"]), **({"props": props} if props else {})}


def prompts(nodes: dict, edges: dict, label: str | None, feedback: list[dict] | None = None) -> list[str]:
    anchors = [node for node in nodes.values() if node["kind"] == "session"]
    ranked = sorted((node for node in nodes.values() if node["kind"] != "session"),
                    key=lambda node: (-node["seconds"], -node["weight"], node["id"]))[:MAX_NODES]
    out = []
    for start in range(0, max(len(ranked), 1), BATCH_NODES):
        chunk = anchors + ranked[start:start + BATCH_NODES]
        if not chunk:
            break
        ids = {node["id"] for node in chunk}
        chunk_edges = sorted((edge for edge in edges.values() if edge["src"] in ids and edge["dst"] in ids),
                             key=lambda edge: (-edge["weight"], edge["src"], edge["dst"], edge["rel"]))[:MAX_EDGES]
        frames = sorted({ref["id"] for node in chunk for ref in node["evidence"] if ref.get("type") == "frame"})
        payload = {
            "recording": label,
            "nodes": [_compact(node) for node in chunk],
            "edges": [{"src": e["src"], "rel": e["rel"], "dst": e["dst"], "weight": round(e["weight"], 1)} for e in chunk_edges],
            "evidence": _evidence(frames[:400]),
        }
        if feedback:
            payload["rejected_last_round"] = feedback[:20]
        out.append(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return out


def _digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def _call(provider: llm.Provider, user: str, use_cache: bool) -> tuple[list[dict], bool]:
    key = _digest(PROMPT_VERSION, provider.name, provider.model, SYSTEM, user, TOOLS)
    if use_cache:
        with db.conn() as connection:
            row = connection.execute("SELECT response FROM llm_cache WHERE key = ?", (key,)).fetchone()
        if row is not None:
            return json.loads(row["response"]), True
    calls = provider.call_tools(SYSTEM, user, TOOLS)
    if not isinstance(calls, list):
        raise llm.LLMError("provider returned no tool-call list")
    with db.conn() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO llm_cache(key, model, response, created_at) VALUES (?, ?, ?, ?)",
            (key, provider.model, json.dumps(calls), utc_iso()),
        )
    return calls, False


def _label(session_id: int | None) -> str | None:
    if not session_id:
        return "all history"
    with db.conn() as connection:
        row = connection.execute("SELECT label FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return row["label"] if row else None


def _rebuild(session_id: int | None) -> None:
    if session_id:
        graph.build(session_id)
        return
    graph.sync()
    graph.aggregate(full=True)


def tune(session_id: int | None = None, rounds: int = ROUNDS, force: bool = False, provider: llm.Provider | None = None) -> dict:
    """Run up to ``rounds`` review passes over a scope's graph and persist the accepted ops."""
    scope = graph.scope_for(session_id)
    try:
        provider = provider or llm.provider()
    except llm.LLMError as exc:
        raise db.ChronicleError(f"LLM provider: {exc.message}", 400) from exc
    if provider is None:
        raise db.ChronicleError("LLM tuning is off; set CHRONICLE_LLM=openai and OPENAI_API_KEY (see README)", 400)
    with db.conn() as connection:
        if session_id:
            graph._session_bounds(connection, session_id)
        stored = {row["op"] for row in connection.execute("SELECT op FROM kg_llm_patches WHERE scope = ?", (scope,))}
    graph.ensure(session_id)
    label = _label(session_id)
    nodes, edges = _load(scope)
    state_key = f"llm_tuned:{scope}"
    result = {"scope": scope, "provider": provider.name, "model": provider.model, "status": "ok", "rounds": 0,
              "calls": 0, "cached": 0, "applied": 0, "rejected": [], "ops": []}
    if not force and db.get_setting(state_key) == _digest(provider.model, prompts(nodes, edges, label)):
        return {**result, "status": "up_to_date"}
    feedback: list[dict] = []
    for _ in range(max(1, min(int(rounds), 5))):
        batch_prompts = prompts(nodes, edges, label, feedback)
        feedback = []
        accepted: list[dict] = []
        for user in batch_prompts:
            try:
                calls, hit = _call(provider, user, use_cache=not force)
            except llm.LLMError as exc:
                result.update(status="error", error=exc.message, retryable=exc.retryable)
                break
            except Exception as exc:  # noqa: BLE001
                result.update(status="error", error=f"{type(exc).__name__}: {exc}", retryable=False)
                break
            result["calls"] += 1
            result["cached"] += int(hit)
            for call in calls:
                name = call.get("name") if isinstance(call, dict) else None
                arguments = call.get("arguments") if isinstance(call, dict) else None
                try:
                    op = apply_op(nodes, edges, check(str(name), arguments))
                except Rejected as exc:
                    feedback.append({"op": name, "arguments": arguments, "error": str(exc)})
                    continue
                marker = json.dumps(op, sort_keys=True)
                if marker not in stored:
                    stored.add(marker)
                    accepted.append(op)
        result["rounds"] += 1
        result["rejected"] += feedback
        if accepted:
            stamp = utc_iso()
            with db.conn() as connection:
                connection.executemany(
                    "INSERT INTO kg_llm_patches(scope, op, model, created_at) VALUES (?, ?, ?, ?)",
                    [(scope, json.dumps(op, sort_keys=True), provider.model, stamp) for op in accepted],
                )
            result["applied"] += len(accepted)
            result["ops"] += accepted
            graph.build(session_id)
            nodes, edges = _load(scope)
        if result["status"] == "error" or not accepted:
            break
    if result["status"] == "ok":
        db.set_setting(state_key, _digest(provider.model, prompts(nodes, edges, label)))
        db.set_setting(f"llm_tuned_at:{scope}", utc_iso())
        db.set_setting("last_llm_error", "")
    else:
        db.set_setting("last_llm_error", f"{utc_iso()} {scope}: {result['error']}")
    return result


def reset(session_id: int | None = None) -> dict:
    scope = graph.scope_for(session_id)
    with db.conn() as connection:
        removed = connection.execute("DELETE FROM kg_llm_patches WHERE scope = ?", (scope,)).rowcount
        connection.execute("DELETE FROM settings WHERE key IN (?, ?)", (f"llm_tuned:{scope}", f"llm_tuned_at:{scope}"))
    _rebuild(session_id)
    return {"scope": scope, "removed": removed}


_threads: list[threading.Thread] = []


def after_session(session_id: int) -> threading.Thread | None:
    """Tune a finished recording, then write its General context file, in the background. Never raises."""
    try:
        if not llm.enabled():
            return None
    except Exception:  # noqa: BLE001
        return None

    def run() -> None:
        try:
            if tune(session_id).get("status") == "error":
                return
            from chronicle import contexts

            contexts.build(preset_id="general", session_id=session_id)
        except Exception as exc:  # noqa: BLE001
            try:
                db.set_setting("last_llm_error", f"{utc_iso()} session:{session_id}: {exc}")
            except Exception:  # noqa: BLE001
                pass

    thread = threading.Thread(target=run, name=f"chronicle-tune-{session_id}", daemon=True)
    thread.start()
    _threads[:] = [item for item in _threads if item.is_alive()] + [thread]
    return thread


def pending() -> bool:
    return any(thread.is_alive() for thread in _threads)


def wait(timeout: float = 180) -> bool:
    """Join pending background tunes. True when none are left running."""
    for thread in list(_threads):
        thread.join(timeout)
        if not thread.is_alive():
            _threads.remove(thread)
    return not _threads

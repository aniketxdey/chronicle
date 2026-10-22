"""Model Context Protocol server for Chronicle.

One JSON-RPC core, two transports:
- stdio: newline-delimited JSON on stdin/stdout (Claude Desktop, Claude Code, Cursor,
  VS Code, Windsurf, Codex, Gemini CLI, Zed, Cline, ...). Run ``chronicle mcp``.
- Streamable HTTP: POST /mcp on the Chronicle server (ChatGPT connectors, the OpenAI
  Responses API, the Anthropic MCP connector, and any remote client).

Includes ``search`` and ``fetch`` with the result shapes ChatGPT connectors and deep
research expect.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
import traceback

from chronicle import __version__, context, db, graph, meetings, search, sessions, tuner

PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "chronicle", "title": "Chronicle", "version": __version__}
INSTRUCTIONS = (
    "Chronicle records this computer's screen and system events and keeps a knowledge graph of the apps, "
    "windows, pages, files, tickets, people, and projects involved. Call get_context first, with the task you "
    "are about to do, to see what is on screen and what the user has been working on. Use get_frame with "
    "include_image=true when you need to look at a screenshot. Use start_recording to capture a demonstration "
    "for a preset amount of time, then get_context(session_id=...) to learn from it."
)

KINDS = graph.KIND_ORDER


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


SESSION_ID = {"type": "integer", "description": "Recording session id. Omit to use all history."}
READ = {"readOnlyHint": True, "openWorldHint": False}

TOOLS = [
    {
        "name": "get_context",
        "title": "Get computer context",
        "description": (
            "Context pack for a computer-use agent: what is on screen now, recent activity, habitual app/window "
            "transitions, the knowledge-graph entities relevant to the task, and cited evidence. Call this before acting."
        ),
        "inputSchema": _schema({
            "task": {"type": "string", "description": "What you are about to do, in plain words. Focuses the graph and evidence."},
            "minutes": {"type": "integer", "description": "Only consider the last N minutes."},
            "session_id": SESSION_ID,
            "max_nodes": {"type": "integer", "description": "Graph nodes to include (4-80, default 24)."},
        }),
        "annotations": {**READ, "title": "Get computer context"},
    },
    {
        "name": "search_history",
        "title": "Search history",
        "description": "Full-text search over captured screen text, system events, transcripts, and memories.",
        "inputSchema": _schema({
            "query": {"type": "string"},
            "content_type": {"type": "string", "enum": ["all", "ocr", "accessibility", "audio", "input", "memory"]},
            "app_name": {"type": "string"},
            "start_time": {"type": "string", "description": "ISO timestamp or relative like '2h ago'."},
            "end_time": {"type": "string"},
            "limit": {"type": "integer", "description": "1-50, default 10."},
        }, ["query"]),
        "annotations": {**READ, "title": "Search history"},
    },
    {
        "name": "query_graph",
        "title": "Query knowledge graph",
        "description": "Find knowledge-graph entities by text and/or kind, ranked by time spent and frequency.",
        "inputSchema": _schema({
            "query": {"type": "string", "description": "Words to match against entity labels."},
            "kind": {"type": "string", "enum": KINDS},
            "session_id": SESSION_ID,
            "limit": {"type": "integer", "description": "1-100, default 20."},
        }),
        "annotations": {**READ, "title": "Query knowledge graph"},
    },
    {
        "name": "get_entity",
        "title": "Get graph entity",
        "description": "One knowledge-graph entity with its neighbors, relations, and evidence frames. Accepts an id like 'ticket:bill-142' or a label.",
        "inputSchema": _schema({"id": {"type": "string"}, "session_id": SESSION_ID}, ["id"]),
        "annotations": {**READ, "title": "Get graph entity"},
    },
    {
        "name": "graph_summary",
        "title": "Graph summary",
        "description": "Counts by entity kind, the top entities of each kind, and the most common transitions.",
        "inputSchema": _schema({"session_id": SESSION_ID}),
        "annotations": {**READ, "title": "Graph summary"},
    },
    {
        "name": "get_frame",
        "title": "Get frame",
        "description": "One captured frame: app, window, URL, text, nearby events. With include_image=true, also the screenshot.",
        "inputSchema": _schema({
            "frame_id": {"type": "integer"},
            "include_image": {"type": "boolean", "description": "Attach the screenshot (JPEG or PNG) when one exists."},
        }, ["frame_id"]),
        "annotations": {**READ, "title": "Get frame"},
    },
    {
        "name": "activity_summary",
        "title": "Activity summary",
        "description": "Time per app, top windows, meetings, and a short narrative for a time range (default today).",
        "inputSchema": _schema({"start_time": {"type": "string"}, "end_time": {"type": "string"}}),
        "annotations": {**READ, "title": "Activity summary"},
    },
    {
        "name": "list_sessions",
        "title": "List recordings",
        "description": "Recording sessions with status, duration, frame and event counts, and top apps.",
        "inputSchema": _schema({"limit": {"type": "integer"}}),
        "annotations": {**READ, "title": "List recordings"},
    },
    {
        "name": "start_recording",
        "title": "Start recording",
        "description": (
            "Record the screen and system events (app switches, window focus, URLs, clipboard, idle) for a preset "
            "duration such as '5m', '30m', or '2h'. The graph for the session is built when it ends."
        ),
        "inputSchema": _schema({
            "duration": {"type": "string", "description": "How long to record, e.g. '10m', '1h30m'. Omit for until stopped."},
            "label": {"type": "string", "description": "What is being recorded, e.g. 'filing an expense report'."},
            "interval_secs": {"type": "integer", "description": "Seconds between samples (1-600, default 5)."},
        }),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False, "title": "Start recording"},
    },
    {
        "name": "stop_recording",
        "title": "Stop recording",
        "description": "Stop the running recording (or the given one) and build its knowledge graph.",
        "inputSchema": _schema({"session_id": SESSION_ID}),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False, "title": "Stop recording"},
    },
    {
        "name": "rebuild_graph",
        "title": "Rebuild graph",
        "description": "Rebuild the knowledge graph for all history or one session from the stored frames and events.",
        "inputSchema": _schema({"session_id": SESSION_ID}),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False, "title": "Rebuild graph"},
    },
    {
        "name": "tune_graph",
        "title": "Tune graph with an LLM",
        "description": (
            "Have the configured LLM (CHRONICLE_LLM=openai) review the heuristic graph and patch it: add real-world "
            "entities, typed relations, and activity summaries, merge duplicates, clean labels, drop noise. Patches "
            "survive rebuilds; reset=true removes them. Off unless an LLM provider is configured."
        ),
        "inputSchema": _schema({
            "session_id": SESSION_ID,
            "rounds": {"type": "integer", "description": "Review passes (1-5, default 2)."},
            "force": {"type": "boolean", "description": "Ignore the response cache and the up-to-date check."},
            "reset": {"type": "boolean", "description": "Remove all LLM patches for the scope instead of tuning."},
        }),
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True, "title": "Tune graph"},
    },
    {
        "name": "list_meetings",
        "title": "List meetings",
        "description": "Meetings with speakers and transcript segments.",
        "inputSchema": _schema({"limit": {"type": "integer"}}),
        "annotations": {**READ, "title": "List meetings"},
    },
    {
        "name": "search",
        "title": "Search (connector)",
        "description": "Search Chronicle history and the knowledge graph. Returns ids to pass to fetch.",
        "inputSchema": _schema({"query": {"type": "string"}}, ["query"]),
        "annotations": {**READ, "title": "Search (connector)"},
    },
    {
        "name": "fetch",
        "title": "Fetch (connector)",
        "description": "Fetch the full record for an id returned by search (frame:N, audio:N, event:N, memory:N, node:<id>).",
        "inputSchema": _schema({"id": {"type": "string"}}, ["id"]),
        "annotations": {**READ, "title": "Fetch (connector)"},
    },
]

RESOURCES = [
    {"uri": "chronicle://context/current", "name": "current-context", "title": "Current computer context",
     "description": "The context pack for the last two hours of activity.", "mimeType": "text/markdown"},
    {"uri": "chronicle://graph/summary", "name": "graph-summary", "title": "Knowledge graph summary",
     "description": "Entity counts, top entities, and common transitions.", "mimeType": "application/json"},
    {"uri": "chronicle://sessions", "name": "sessions", "title": "Recording sessions",
     "description": "All recording sessions.", "mimeType": "application/json"},
]
RESOURCE_TEMPLATES = [
    {"uriTemplate": "chronicle://context/session/{session_id}", "name": "session-context", "title": "Context for a recording",
     "description": "The context pack for one recording session.", "mimeType": "text/markdown"},
    {"uriTemplate": "chronicle://frame/{frame_id}", "name": "frame", "title": "Captured frame",
     "description": "One captured frame as JSON.", "mimeType": "application/json"},
]
PROMPTS = [
    {
        "name": "use_my_computer_context",
        "title": "Ground a task in my computer context",
        "description": "Pull Chronicle context for a task before acting on the computer.",
        "arguments": [{"name": "task", "description": "What should be done", "required": True}],
    },
    {
        "name": "learn_from_recording",
        "title": "Learn a workflow from a recording",
        "description": "Turn a recorded session into step-by-step instructions an agent can follow.",
        "arguments": [{"name": "session_id", "description": "Recording session id", "required": True}],
    },
]


def public_url() -> str:
    return os.environ.get("CHRONICLE_PUBLIC_URL", "http://127.0.0.1:3030").rstrip("/")


def _int(value, default=None):
    if value is None or value == "":
        return default
    return int(value)


def _text(value) -> dict:
    if not isinstance(value, str):
        value = json.dumps(value, indent=1, default=str)
    return {"type": "text", "text": value}


def _frame_payload(frame_id: int) -> dict:
    bundle = search.frame_bundle(frame_id)
    return bundle


def call_tool(name: str, args: dict) -> list[dict]:
    args = args or {}
    if name == "get_context":
        pack = context.build(
            task=args.get("task"), minutes=_int(args.get("minutes")), session_id=_int(args.get("session_id")),
            max_nodes=_int(args.get("max_nodes"), 24),
        )
        return [_text(pack["markdown"])]
    if name == "search_history":
        found = search.query(
            q=args.get("query"), content_type=args.get("content_type") or "all", app_name=args.get("app_name"),
            start_time=args.get("start_time"), end_time=args.get("end_time"),
            limit=max(1, min(_int(args.get("limit"), 10), 50)),
        )
        rows = []
        for item in found["data"]:
            body = item["content"]
            rows.append({
                "type": item["type"], "timestamp": body.get("timestamp"), "frame_id": body.get("frame_id"),
                "app": body.get("app_name"), "window": body.get("window_name"), "url": body.get("browser_url"),
                "speaker": body.get("speaker_name"), "text": (body.get("text") or "")[:500],
            })
        return [_text({"total": found["pagination"]["total"], "results": rows})]
    if name == "query_graph":
        nodes = graph.find(args.get("query"), kind=args.get("kind"), limit=max(1, min(_int(args.get("limit"), 20), 100)),
                           session_id=_int(args.get("session_id")))
        return [_text([{k: n[k] for k in ("id", "kind", "label", "seconds", "weight", "last_seen")} |
                       {"frames": [e["id"] for e in n["evidence"] if e.get("type") == "frame"][:4]} for n in nodes])]
    if name == "get_entity":
        return [_text(graph.node(str(args["id"]), session_id=_int(args.get("session_id"))))]
    if name == "graph_summary":
        return [_text(graph.summary(session_id=_int(args.get("session_id"))))]
    if name == "get_frame":
        frame_id = int(args["frame_id"])
        bundle = _frame_payload(frame_id)
        parts = [_text(bundle)]
        if args.get("include_image"):
            row = search.frame_record(frame_id)
            path = row["snapshot_path"]
            if path and path.rsplit(".", 1)[-1].lower() in {"jpg", "jpeg", "png"}:
                file = db.media_root() / path
                if file.exists():
                    mime = "image/png" if path.endswith(".png") else "image/jpeg"
                    parts.append({"type": "image", "data": base64.b64encode(file.read_bytes()).decode(), "mimeType": mime})
            if len(parts) == 1:
                parts.append(_text("No screenshot is stored for this frame (sample frames and title-only frames have none)."))
        return parts
    if name == "activity_summary":
        return [_text(search.activity_summary(args.get("start_time"), args.get("end_time")))]
    if name == "list_sessions":
        return [_text(sessions.list_sessions(limit=_int(args.get("limit"), 20)))]
    if name == "start_recording":
        started = sessions.start(args.get("duration"), label=args.get("label"), interval_secs=_int(args.get("interval_secs")))
        note = (
            "Recording started. Frames are captured by whichever Chronicle process holds the recorder lock "
            "(this MCP server or `chronicle serve`). On macOS the host app needs Screen Recording and Accessibility permission."
        )
        return [_text({"session": started, "note": note})]
    if name == "stop_recording":
        return [_text(sessions.stop(_int(args.get("session_id"))))]
    if name == "rebuild_graph":
        return [_text(graph.build(_int(args.get("session_id"))))]
    if name == "tune_graph":
        session_id = _int(args.get("session_id"))
        if args.get("reset"):
            return [_text(tuner.reset(session_id))]
        return [_text(tuner.tune(session_id, rounds=_int(args.get("rounds"), 2), force=bool(args.get("force"))))]
    if name == "list_meetings":
        items = meetings.list_meetings()[: _int(args.get("limit"), 10)]
        return [_text(items)]
    if name == "search":
        return [_text({"results": connector_search(str(args.get("query") or ""))})]
    if name == "fetch":
        return [_text(connector_fetch(str(args["id"])))]
    raise KeyError(name)


def connector_search(query: str, limit: int = 10) -> list[dict]:
    base = public_url()
    results = []
    for node in graph.find(query, limit=4):
        results.append({"id": f"node:{node['id']}", "title": f"[{node['kind']}] {node['label']}", "url": f"{base}/#/graph?node={node['id']}"})
    found = search.query(q=query, limit=limit)
    for item in found["data"]:
        body = item["content"]
        kind = item["type"].lower()
        if kind in {"ocr", "accessibility"}:
            ident = f"frame:{body['frame_id']}"
            title = f"{body.get('app_name')} · {body.get('window_name') or ''}".strip(" ·")
            url = f"{base}/#/frame/{body['frame_id']}"
        elif kind == "audio":
            ident = f"audio:{body['transcription_id']}"
            title = f"{body.get('speaker_name') or 'Speaker'}: {(body.get('text') or '')[:60]}"
            url = f"{base}/#/meetings"
        elif kind == "input":
            ident = f"event:{body['event_id']}"
            title = f"{body.get('event_type')} in {body.get('app_name')}"
            url = f"{base}/#/timeline"
        else:
            ident = f"memory:{body['memory_id']}"
            title = f"Memory: {(body.get('text') or '')[:60]}"
            url = f"{base}/#/search"
        results.append({"id": ident, "title": title, "url": url})
    return results[:limit]


def connector_fetch(ident: str) -> dict:
    kind, _, value = ident.partition(":")
    base = public_url()
    if kind == "node":
        data = graph.node(value)
        node = data["node"]
        lines = [f"{node['label']} ({node['kind']}), seen {int(node['weight'])} times, {int(node['seconds'])}s on screen."]
        lines += [f"{n['direction']} {n['rel']}: [{n['kind']}] {n['label']}" for n in data["neighbors"]]
        return {"id": ident, "title": node["label"], "text": "\n".join(lines), "url": f"{base}/#/graph?node={node['id']}", "metadata": {"kind": node["kind"]}}
    if kind == "frame":
        bundle = search.frame_bundle(int(value))
        frame = bundle.get("frame", bundle)
        text = json.dumps(bundle, indent=1, default=str)
        return {"id": ident, "title": f"{frame.get('app_name')} · {frame.get('window_name')}", "text": text,
                "url": f"{base}/#/frame/{value}", "metadata": {"timestamp": frame.get("timestamp")}}
    with db.conn() as connection:
        if kind == "audio":
            row = connection.execute(
                "SELECT t.*, s.name AS speaker FROM audio_transcriptions t LEFT JOIN speakers s ON s.id = t.speaker_id WHERE t.id = ?",
                (int(value),),
            ).fetchone()
            if row:
                return {"id": ident, "title": f"{row['speaker'] or 'Speaker'} at {row['timestamp']}", "text": row["transcription"],
                        "url": f"{base}/#/meetings", "metadata": {"timestamp": row["timestamp"]}}
        if kind == "event":
            row = connection.execute("SELECT * FROM ui_events WHERE id = ?", (int(value),)).fetchone()
            if row:
                return {"id": ident, "title": f"{row['event_type']} in {row['app_name']}", "text": row["text"] or "",
                        "url": f"{base}/#/timeline", "metadata": {"timestamp": row["timestamp"], "frame_id": row["frame_id"]}}
        if kind == "memory":
            row = connection.execute("SELECT * FROM memories WHERE id = ?", (int(value),)).fetchone()
            if row:
                return {"id": ident, "title": "Memory", "text": row["content"], "url": f"{base}/#/search",
                        "metadata": {"timestamp": row["created_at"]}}
    raise db.ChronicleError(f"unknown id {ident!r}", 404)


def read_resource(uri: str) -> dict:
    if uri == "chronicle://context/current":
        return {"uri": uri, "mimeType": "text/markdown", "text": context.build()["markdown"]}
    if uri == "chronicle://graph/summary":
        return {"uri": uri, "mimeType": "application/json", "text": json.dumps(graph.summary(), default=str)}
    if uri == "chronicle://sessions":
        return {"uri": uri, "mimeType": "application/json", "text": json.dumps(sessions.list_sessions(), default=str)}
    if uri.startswith("chronicle://context/session/"):
        session_id = int(uri.rsplit("/", 1)[1])
        return {"uri": uri, "mimeType": "text/markdown", "text": context.build(session_id=session_id)["markdown"]}
    if uri.startswith("chronicle://frame/"):
        frame_id = int(uri.rsplit("/", 1)[1])
        return {"uri": uri, "mimeType": "application/json", "text": json.dumps(search.frame_bundle(frame_id), default=str)}
    raise db.ChronicleError(f"unknown resource {uri}", 404)


def get_prompt(name: str, args: dict) -> dict:
    if name == "use_my_computer_context":
        task = args.get("task") or "the task"
        text = (
            f"Before working on: {task}\n"
            "1. Call get_context with this task.\n"
            "2. If the context names a frame that matters, call get_frame with include_image=true.\n"
            "3. Use query_graph or get_entity to resolve names, tickets, files, and pages before acting.\n"
            "4. Then do the task, citing frame ids for anything you learned from history."
        )
        return {"description": "Ground a task in Chronicle context", "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
    if name == "learn_from_recording":
        session_id = args.get("session_id")
        text = (
            f"Call get_context with session_id={session_id} and graph_summary with session_id={session_id}. "
            "Reconstruct what the user did as numbered steps (app, window, URL, action), note inputs that vary between runs, "
            "and end with a checklist a computer-use agent can follow to repeat it."
        )
        return {"description": "Learn a workflow from a recording", "messages": [{"role": "user", "content": {"type": "text", "text": text}}]}
    raise db.ChronicleError(f"unknown prompt {name}", 404)


def _result(message_id, result) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id, code: int, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def negotiate(requested: str | None) -> str:
    return requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]


def handle(message):
    """Handle one JSON-RPC message (or a batch). Returns the response, or None for notifications."""
    if isinstance(message, list):
        replies = [reply for reply in (handle(item) for item in message) if reply is not None]
        return replies or None
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}
    if method is None:
        return None
    if message_id is None:
        return None
    try:
        if method == "initialize":
            return _result(message_id, {
                "protocolVersion": negotiate(params.get("protocolVersion")),
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            })
        if method == "ping":
            return _result(message_id, {})
        if method == "tools/list":
            return _result(message_id, {"tools": TOOLS})
        if method == "tools/call":
            name = params.get("name")
            if name not in {tool["name"] for tool in TOOLS}:
                return _error(message_id, -32602, f"unknown tool: {name}")
            try:
                content = call_tool(name, params.get("arguments") or {})
                return _result(message_id, {"content": content, "isError": False})
            except db.ChronicleError as exc:
                return _result(message_id, {"content": [_text(exc.message)], "isError": True})
            except (KeyError, ValueError, TypeError) as exc:
                return _result(message_id, {"content": [_text(f"bad arguments for {name}: {exc}")], "isError": True})
        if method == "resources/list":
            return _result(message_id, {"resources": RESOURCES})
        if method == "resources/templates/list":
            return _result(message_id, {"resourceTemplates": RESOURCE_TEMPLATES})
        if method == "resources/read":
            return _result(message_id, {"contents": [read_resource(params.get("uri", ""))]})
        if method == "prompts/list":
            return _result(message_id, {"prompts": PROMPTS})
        if method == "prompts/get":
            return _result(message_id, get_prompt(params.get("name", ""), params.get("arguments") or {}))
        return _error(message_id, -32601, f"method not found: {method}")
    except db.ChronicleError as exc:
        return _error(message_id, -32602 if exc.status == 404 else -32603, exc.message)
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc(file=sys.stderr)
        return _error(message_id, -32603, f"internal error: {exc}")


def serve_stdio(record: bool = True) -> None:
    """Newline-delimited JSON-RPC over stdin/stdout. Logs go to stderr only."""
    from chronicle import capture, seed

    seed.ensure()
    stop = None
    if record and os.environ.get("CHRONICLE_DISABLE_CAPTURE") != "1":
        _thread, stop = capture.start_worker()
    out = sys.stdout
    write_lock = threading.Lock()
    print(f"chronicle mcp {__version__} on stdio; data in {db.home()}", file=sys.stderr, flush=True)
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                reply = _error(None, -32700, "parse error")
            else:
                reply = handle(message)
            if reply is not None:
                with write_lock:
                    out.write(json.dumps(reply, separators=(",", ":"), default=str) + "\n")
                    out.flush()
    finally:
        if stop is not None:
            stop.set()


if __name__ == "__main__":
    serve_stdio()

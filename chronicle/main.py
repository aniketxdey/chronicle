"""Chronicle HTTP server: web UI, REST API, and the Streamable HTTP MCP endpoint at /mcp."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chronicle import __version__, capture, clients, content, context, db, graph, mcp_server, meetings, pipes, search, seed, sessions, workflows
from chronicle import ask as ask_mod
from chronicle.timeutil import parse_time, utc_iso

WEB = Path(__file__).parent / "web"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed.ensure()
    workflows.refresh()
    stop = asyncio.Event()
    tasks = []
    recorder = None
    if os.environ.get("CHRONICLE_DISABLE_JOBS") != "1":
        tasks.append(asyncio.create_task(_jobs(stop)))
    if os.environ.get("CHRONICLE_DISABLE_CAPTURE") != "1" and os.environ.get("CHRONICLE_DISABLE_JOBS") != "1":
        recorder = capture.start_worker()
    yield
    stop.set()
    if recorder:
        recorder[1].set()
    for task in tasks:
        await task


async def _jobs(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            for name in pipes.due_names():
                await asyncio.to_thread(pipes.execute_sync, name)
            pruned = await asyncio.to_thread(db.enforce_retention)
            if not pruned["skipped"] and (pruned["frames"] or pruned["audio"] or pruned["events"] or pruned["meetings"]):
                await asyncio.to_thread(graph.forget, "0000", pruned["cutoff"])
        except Exception as exc:  # noqa: BLE001
            print(f"chronicle jobs: {exc}", file=sys.stderr)
        try:
            await asyncio.wait_for(stop.wait(), timeout=30)
        except TimeoutError:
            pass


app = FastAPI(title="Chronicle", version=__version__, lifespan=lifespan)


def _token() -> str | None:
    return os.environ.get("CHRONICLE_TOKEN") or None


def _authorized(request: Request) -> bool:
    token = _token()
    if not token:
        return True
    header = request.headers.get("authorization", "")
    supplied = header[7:].strip() if header.lower().startswith("bearer ") else request.query_params.get("token", "")
    path = request.url.path
    if path.startswith("/mcp/"):
        supplied = supplied or path.split("/", 2)[2]
    return bool(supplied) and hmac.compare_digest(supplied, token)


@app.middleware("http")
async def guard(request: Request, call_next):
    """Reject cross-site requests from browsers, and require the token when one is configured."""
    origin = request.headers.get("origin")
    if origin and origin != "null":
        host = urlsplit(origin).hostname or ""
        own = (request.headers.get("host") or "").split(":")[0]
        if host not in LOCAL_HOSTS and host != own:
            return JSONResponse({"error": f"origin {origin} is not allowed"}, status_code=403)
    path = request.url.path
    public = path == "/" or path.startswith("/static/") or path in {"/favicon.ico", "/health"}
    if not public and not _authorized(request):
        return JSONResponse({"error": "missing or wrong token"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return await call_next(request)


@app.exception_handler(db.ChronicleError)
async def chronicle_error(_request, exc: db.ChronicleError):
    return JSONResponse({"error": exc.message}, status_code=exc.status)


class AddBody(BaseModel):
    content_type: str = "accessibility"
    text: str
    app_name: str = "Unknown"
    window_name: str = ""
    browser_url: str | None = None
    timestamp: str | None = None
    trigger: str = "manual"
    text_source: str | None = None
    speaker_name: str | None = None
    device_name: str = "Microphone"
    device_type: str = "input"
    focused: bool = True
    tags: list[str] = Field(default_factory=list)
    session_id: int | None = None


class SqlBody(BaseModel):
    sql: str


class AskBody(BaseModel):
    question: str


class MemoryBody(BaseModel):
    content: str
    source: str = "user"
    frame_id: int | None = None


class TagBody(BaseModel):
    tags: list[str]


class SpeakerBody(BaseModel):
    id: int
    name: str


class SpeakerMergeBody(BaseModel):
    keep_id: int
    drop_id: int


class MeetingMergeBody(BaseModel):
    ids: list[int]


class RangeBody(BaseModel):
    start_time: str
    end_time: str


class RetentionBody(BaseModel):
    days: int


class SettingsBody(BaseModel):
    onboarded: bool | None = None
    timezone: str | None = None
    retention_days: int | None = None
    redact: bool | None = None
    capture_interval_secs: int | None = None
    capture_clipboard: bool | None = None
    capture_screenshots: bool | None = None
    capture_ocr: bool | None = None
    idle_threshold_secs: int | None = None
    graph_half_life_hours: float | None = Field(default=None, ge=0, description="0 disables decay")
    ai_base_url: str | None = None
    ai_model: str | None = None
    ai_api_key: str | None = None


class PipeInstallBody(BaseModel):
    name: str | None = None
    source: str | None = None
    schedule: str | None = None
    prompt: str | None = None
    description: str | None = None


class PipeEnableBody(BaseModel):
    enabled: bool = True


class PipeConfigBody(BaseModel):
    schedule: str | None = None
    prompt: str | None = None


class SessionBody(BaseModel):
    duration: str | None = "30m"
    label: str | None = None
    interval_secs: int | None = None


class GraphBuildBody(BaseModel):
    session_id: int | None = None


class InstallBody(BaseModel):
    dry_run: bool = False


def _parse(value: str | None) -> str | None:
    try:
        return parse_time(value, db.local_zone())
    except ValueError as exc:
        raise db.ChronicleError(str(exc)) from exc


@app.get("/")
def root():
    page = WEB / "index.html"
    if page.exists():
        return FileResponse(page, headers={"Cache-Control": "no-cache"})
    return {"name": "Chronicle", "docs": "/docs", "mcp": "/mcp"}


@app.get("/health")
def health():
    info = db.counts()
    active = sessions.active()
    return {
        "status": "ok",
        "version": __version__,
        "platform": sys.platform,
        "home": str(db.home()),
        "frames": info["frames"],
        "events": info["ui_events"],
        "sessions": info["sessions"],
        "graph_nodes": info["graph_nodes"],
        "graph_edges": info["graph_edges"],
        "meetings": info["meetings"],
        "last_frame_at": info["last_frame_at"],
        "db_bytes": db.db_bytes(),
        "recording": active,
        "token_required": bool(_token()),
        "mcp": {"http": f"{mcp_server.public_url()}/mcp", "stdio": "chronicle mcp"},
    }


# Recording sessions


@app.get("/sessions")
def list_sessions(limit: int = 50):
    return {"data": sessions.list_sessions(limit)}


@app.post("/sessions")
def start_session(body: SessionBody):
    try:
        return sessions.start(body.duration, label=body.label, interval_secs=body.interval_secs)
    except ValueError as exc:
        raise db.ChronicleError(str(exc)) from exc


@app.get("/sessions/active")
def active_session():
    return {"session": sessions.active()}


@app.get("/sessions/{session_id}")
def get_session(session_id: int):
    return sessions.get(session_id)


@app.post("/sessions/{session_id}/stop")
def stop_session(session_id: int):
    return sessions.stop(session_id)


@app.post("/sessions/stop")
def stop_active():
    return sessions.stop(None)


@app.delete("/sessions/{session_id}")
def delete_session(session_id: int):
    return sessions.delete(session_id)


@app.get("/capture/status")
def capture_status():
    return capture.status()


@app.post("/capture/start")
def capture_start(body: SessionBody | None = None):
    body = body or SessionBody(duration=None)
    sessions.start(body.duration, label=body.label, interval_secs=body.interval_secs)
    return capture.status()


@app.post("/capture/stop")
def capture_stop():
    if sessions.active_id():
        sessions.stop(None)
    return capture.status()


# Knowledge graph and context


@app.get("/graph")
def graph_export(session_id: int | None = None, limit: int = 160):
    return graph.export(session_id, limit)


@app.get("/graph/summary")
def graph_summary(session_id: int | None = None):
    return graph.summary(session_id)


@app.get("/graph/search")
def graph_search(q: str | None = None, kind: str | None = None, session_id: int | None = None, limit: int = 20):
    return {"data": graph.find(q, kind=kind, limit=limit, session_id=session_id)}


@app.get("/graph/node")
def graph_node(id: str, session_id: int | None = None, limit: int = 25):
    return graph.node(id, session_id=session_id, limit=limit)


@app.post("/graph/build")
def graph_build(body: GraphBuildBody | None = None):
    return graph.build((body or GraphBuildBody()).session_id)


@app.get("/context")
def get_context(task: str | None = None, minutes: int | None = None, session_id: int | None = None,
                max_nodes: int = 24, format: str = "json"):
    pack = context.build(task=task, minutes=minutes, session_id=session_id, max_nodes=max_nodes)
    if format == "markdown":
        return Response(pack["markdown"], media_type="text/markdown; charset=utf-8")
    return pack


# MCP


@app.get("/clients")
def list_clients():
    return {"data": clients.catalog(), "mcp_url": clients.remote_url()}


@app.get("/clients/{client_id}")
def client_config(client_id: str):
    return clients.config(client_id)


@app.post("/clients/{client_id}/install")
def client_install(client_id: str, body: InstallBody | None = None):
    return clients.install(client_id, dry_run=(body or InstallBody()).dry_run)


async def _mcp(request: Request) -> Response:
    try:
        message = json.loads(await request.body() or b"null")
    except json.JSONDecodeError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, status_code=400)
    reply = await asyncio.to_thread(mcp_server.handle, message)
    if reply is None:
        return Response(status_code=202)
    headers = {}
    if isinstance(message, dict) and message.get("method") == "initialize":
        headers["Mcp-Session-Id"] = secrets.token_hex(16)
    return JSONResponse(reply, headers=headers)


def _no_stream() -> Response:
    return Response(status_code=405, headers={"Allow": "POST"})


@app.post("/mcp")
async def mcp_post(request: Request):
    return await _mcp(request)


@app.post("/mcp/{token}")
async def mcp_post_token(token: str, request: Request):
    return await _mcp(request)


@app.get("/mcp")
@app.get("/mcp/{token}")
def mcp_get(token: str | None = None):
    return _no_stream()


@app.delete("/mcp")
@app.delete("/mcp/{token}")
def mcp_delete(token: str | None = None):
    return Response(status_code=204)


# History


@app.get("/search")
def search_content(
    q: str | None = None,
    content_type: str = "all",
    limit: int = 20,
    offset: int = 0,
    start_time: str | None = None,
    end_time: str | None = None,
    app_name: str | None = None,
    window_name: str | None = None,
    browser_url: str | None = None,
    speaker_name: str | None = None,
    min_length: int | None = None,
    max_length: int | None = None,
    focused: bool | None = None,
    tags: str | None = None,
    include_related: bool = False,
):
    tag_list = [item.strip() for item in (tags or "").split(",") if item.strip()]
    return search.query(
        q=q, content_type=content_type, limit=limit, offset=offset, start_time=start_time, end_time=end_time,
        app_name=app_name, window_name=window_name, browser_url=browser_url, speaker_name=speaker_name,
        min_length=min_length, max_length=max_length, focused=focused, tags=tag_list, include_related=include_related,
    )


@app.get("/search/keyword")
def search_keyword(q: str, limit: int = 20):
    return search.keyword(q, limit)


@app.get("/activity-summary")
def activity(start_time: str | None = None, end_time: str | None = None):
    return search.activity_summary(start_time, end_time)


@app.get("/timeline")
def timeline(date: str | None = None):
    return search.timeline(date)


@app.post("/raw_sql")
def raw_sql(body: SqlBody):
    return search.raw_sql(body.sql)


@app.post("/add")
def add(body: AddBody):
    stamp = _parse(body.timestamp) if body.timestamp else None
    kind = body.content_type.lower()
    if kind == "audio":
        return content.add_audio(
            text=body.text, timestamp=stamp, speaker_name=body.speaker_name,
            device_name=body.device_name, device_type=body.device_type, tags=body.tags,
        )
    if kind == "input":
        event_id = content.add_event(
            event_type=body.trigger or "text", app_name=body.app_name, window_name=body.window_name,
            text=body.text, timestamp=stamp, session_id=body.session_id,
        )
        return {"event_id": event_id}
    return content.add_screen(
        text=body.text, app_name=body.app_name, window_name=body.window_name, timestamp=stamp,
        browser_url=body.browser_url, trigger=body.trigger,
        text_source=body.text_source or ("ocr" if kind == "ocr" else "accessibility"),
        focused=body.focused, tags=body.tags, session_id=body.session_id,
    )


@app.get("/frames/{frame_id}")
def frame(frame_id: int):
    return search.frame_public(search.frame_record(frame_id))


@app.get("/frames/{frame_id}/context")
def frame_context(frame_id: int):
    return search.frame_bundle(frame_id)


@app.get("/frames/{frame_id}/elements")
def frame_elements(frame_id: int):
    return {"data": search.frame_bundle(frame_id)["elements"]}


@app.get("/frames/{frame_id}/image")
def frame_image(frame_id: int):
    row = search.frame_record(frame_id)
    if not row["snapshot_path"]:
        raise db.ChronicleError("frame has no image", 404)
    path = db.media_root() / row["snapshot_path"]
    if not path.exists():
        raise db.ChronicleError("image file is missing", 404)
    return FileResponse(path)


@app.get("/elements")
def elements(q: str | None = None, app_name: str | None = None, limit: int = 50, offset: int = 0):
    return search.elements(q, app_name, limit, offset)


@app.get("/meetings")
def list_meetings():
    return {"data": meetings.list_meetings()}


@app.get("/meetings/status")
def meeting_status():
    return meetings.status()


@app.post("/meetings/merge")
def merge_meetings(body: MeetingMergeBody):
    return meetings.merge(body.ids)


@app.get("/speakers")
def speakers():
    return {"data": meetings.list_speakers()}


@app.get("/speakers/unnamed")
def unnamed():
    return {"data": meetings.unnamed_speakers()}


@app.post("/speakers/update")
def update_speaker(body: SpeakerBody):
    return meetings.update_speaker(body.id, body.name)


@app.post("/speakers/merge")
def merge_speakers(body: SpeakerMergeBody):
    return meetings.merge_speakers(body.keep_id, body.drop_id)


@app.get("/memories")
def memories(limit: int = 50):
    return {"data": search.list_memories(limit)}


@app.post("/memories")
def create_memory(body: MemoryBody):
    return search.create_memory(body.content, body.source, body.frame_id)


@app.post("/tags/{content_type}/{content_id}")
def add_tags(content_type: str, content_id: int, body: TagBody):
    return search.add_tags(content_type, content_id, body.tags)


@app.delete("/tags/{content_type}/{content_id}")
def delete_tags(content_type: str, content_id: int, body: TagBody):
    return search.remove_tags(content_type, content_id, body.tags)


@app.get("/retention/status")
def retention_status():
    days = db.get_setting("retention_days", "30") or "30"
    return {"days": int(days), "forever": days == "0", "frames": db.counts()["frames"]}


@app.post("/retention/configure")
def retention_configure(body: RetentionBody):
    if body.days < 0:
        raise db.ChronicleError("days must be 0 (keep forever) or a positive number")
    db.set_setting("retention_days", str(body.days))
    return db.enforce_retention()


@app.post("/data/delete-range")
def delete_range(body: RangeBody):
    start = _parse(body.start_time)
    end = _parse(body.end_time)
    if not start or not end:
        raise db.ChronicleError("start_time and end_time are required")
    if end < start:
        raise db.ChronicleError("end_time is before start_time")
    deleted = db.delete_range(start, end)
    workflows.refresh()
    graph.forget(start, end)
    return deleted


@app.get("/pipes")
def list_pipes():
    return pipes.list_pipes()


@app.post("/pipes/install")
def install_pipe(body: PipeInstallBody):
    return pipes.install(body.model_dump())


@app.post("/pipes/{name}/run")
def run_pipe(name: str):
    return pipes.execute_sync(name)


@app.post("/pipes/{name}/enable")
def enable_pipe(name: str, body: PipeEnableBody):
    return pipes.set_enabled(name, body.enabled)


@app.post("/pipes/{name}/config")
def config_pipe(name: str, body: PipeConfigBody):
    return pipes.configure(name, body.schedule, body.prompt)


@app.get("/pipes/{name}/logs")
def pipe_logs(name: str):
    return pipes.logs(name)


@app.post("/pipes/{name}/stop")
def stop_pipe(name: str):
    with db.conn() as connection:
        connection.execute(
            "UPDATE pipe_executions SET status = 'stopped', finished_at = ? WHERE pipe_name = ? AND status = 'running'",
            (utc_iso(), name),
        )
    return pipes.logs(name)


@app.get("/workflows")
def list_workflows():
    data = workflows.list_workflows()
    if not data:
        data = workflows.refresh()
    return {"data": data}


@app.post("/workflows/refresh")
def refresh_workflows():
    return {"data": workflows.refresh()}


@app.post("/workflows/{workflow_id}/install-pipe")
def install_workflow_pipe(workflow_id: int):
    return pipes.create_from_workflow(workflow_id)


@app.post("/ask")
def ask(body: AskBody):
    return ask_mod.prepare(body.question)


@app.post("/ask/stream")
async def ask_stream(body: AskBody):
    result = await asyncio.to_thread(ask_mod.prepare, body.question)

    async def generate():
        for chunk in result["chunks"]:
            yield f"data: {json.dumps({'delta': chunk})}\n\n"
        yield f"data: {json.dumps({'done': True, 'mode': result['mode'], 'citations': result['citations'], 'answer': result['answer']})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/ai/status")
def ai_status():
    return ask_mod.ai_status()


SETTING_KEYS = [
    "onboarded", "timezone", "retention_days", "redact", "capture_interval_secs", "capture_clipboard",
    "capture_screenshots", "capture_ocr", "idle_threshold_secs", "graph_half_life_hours", "ai_base_url", "ai_model",
]
BOOL_KEYS = {"onboarded", "redact", "capture_clipboard", "capture_screenshots", "capture_ocr"}


@app.get("/settings")
def get_settings():
    payload = {key: db.get_setting(key) for key in SETTING_KEYS}
    for key in BOOL_KEYS:
        payload[key] = payload[key] == "1"
    payload["ai_api_key_set"] = bool(db.get_setting("ai_api_key", ""))
    payload["home"] = str(db.home())
    payload["capture"] = capture.status()
    return payload


@app.post("/settings")
def update_settings(body: SettingsBody):
    values = body.model_dump(exclude_none=True)
    if "capture_interval_secs" in values:
        values["capture_interval_secs"] = max(1, min(600, values["capture_interval_secs"]))
    if "idle_threshold_secs" in values:
        values["idle_threshold_secs"] = max(15, values["idle_threshold_secs"])
    for key, value in values.items():
        if isinstance(value, bool):
            value = "1" if value else "0"
        db.set_setting(key, str(value).strip())
    return get_settings()


@app.post("/demo/load")
def load_demo(replace: bool = False):
    if replace:
        if sessions.active_id():
            raise db.ChronicleError("stop the recording before replacing history with the sample", 409)
        keep = {key: db.get_setting(key) for key in ("onboarded", "timezone", "ai_base_url", "ai_model", "ai_api_key", "redact", "retention_days")}
        db.wipe()
        seed.ensure()
        for key, value in keep.items():
            if value is not None:
                db.set_setting(key, value)
    loaded = seed.load_sample()
    workflows.refresh()
    return {"loaded": loaded, "frames": db.counts()["frames"]}


db.media_root()
app.mount("/media", StaticFiles(directory=str(db.media_root())), name="media")
if WEB.exists():
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

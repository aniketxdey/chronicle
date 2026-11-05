"""Chronicle HTTP server: web UI, REST API, and the Streamable HTTP MCP endpoint at /mcp."""

from __future__ import annotations

import asyncio
import hmac
import html
import json
import os
import secrets
import shutil
import sys
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from chronicle import __version__, artifacts, brain, capture, clients, content, context, contexts, db, graph, llm, harness, mcp_server, meetings, search, seed, sessions, tuner, workflows
from chronicle import ask as ask_mod
from chronicle import clusters, itemgraph, legend, oauth
from chronicle.sources import base as sources
from chronicle.timeutil import parse_time, utc_iso

WEB = Path(__file__).parent / "web"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    seed.ensure()
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


SOURCE_SYNC_SECS = 600


async def _jobs(stop: asyncio.Event) -> None:
    next_sync = time.monotonic() + 20
    while not stop.is_set():
        try:
            pruned = await asyncio.to_thread(db.enforce_retention)
            if not pruned["skipped"] and pruned.get("cutoff"):
                await asyncio.to_thread(itemgraph.forget_range, "1970-01-01T00:00:00+00:00", pruned["cutoff"])
            if time.monotonic() >= next_sync:
                next_sync = time.monotonic() + SOURCE_SYNC_SECS
                await asyncio.to_thread(sources.sync_enabled)
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
    session_id: int | None = None
    history: list[dict] | None = None


class FieldSuggestBody(BaseModel):
    kind: str
    name: str = ""
    description: str = ""
    session_ids: list[int] = Field(default_factory=list)
    context_ids: list[int] = Field(default_factory=list)
    workflow_ids: list[int] = Field(default_factory=list)


class BrainChatBody(BaseModel):
    id: int | None = None
    scope: str = "brain"
    turns: list[dict]


class SourceBody(BaseModel):
    enabled: bool | None = None
    options: dict | None = None


class DisconnectBody(BaseModel):
    forget: bool = False


class LegendBody(BaseModel):
    categories: list[dict]


class NodeOverrideBody(BaseModel):
    id: str
    label: str | None = None
    category: str | None = None


class ContextComposeBody(BaseModel):
    name: str
    description: str = ""
    session_ids: list[int] = Field(default_factory=list)
    context_ids: list[int] = Field(default_factory=list)
    workflow_ids: list[int] = Field(default_factory=list)


class LLMProviderBody(BaseModel):
    api_key: str | None = None
    clear_key: bool = False
    model: str | None = None
    base_url: str | None = None
    activate: bool = False


class HarnessDesignBody(BaseModel):
    name: str = ""
    purpose: str = ""
    session_ids: list[int] = Field(default_factory=list)


class HarnessBody(BaseModel):
    name: str
    purpose: str = ""
    session_ids: list[int] = Field(default_factory=list)
    context_ids: list[int] = Field(default_factory=list)
    workflow_ids: list[int] = Field(default_factory=list)
    new_workflows: list[dict] = Field(default_factory=list)


class HarnessUpdateBody(BaseModel):
    name: str | None = None
    purpose: str | None = None
    session_ids: list[int] | None = None


class HarnessFileBody(BaseModel):
    path: str
    content: str


class HarnessNewFileBody(BaseModel):
    kind: str
    title: str


class HarnessChatBody(BaseModel):
    message: str


class WorkflowBody(BaseModel):
    name: str | None = None
    purpose: str | None = None
    session_ids: list[int] = Field(default_factory=list)
    context_ids: list[int] = Field(default_factory=list)


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
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None


class SessionBody(BaseModel):
    duration: str | None = "30m"
    label: str | None = None
    interval_secs: int | None = None


class GraphBuildBody(BaseModel):
    session_id: int | None = None


class GraphTuneBody(BaseModel):
    session_id: int | None = None
    rounds: int = 2
    reset: bool = False
    force: bool = False


class InstallBody(BaseModel):
    dry_run: bool = False


class ContextBody(BaseModel):
    task: str | None = None
    preset: str | None = None
    session_id: int | None = None
    minutes: int | None = None
    title: str | None = None


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


def _session_artifacts(session_id: int) -> dict:
    return {
        "contexts": [item for item in contexts.list_saved(500) if item["session_id"] == session_id],
        "workflows": [item for item in workflows.list_workflows() if session_id in item["sources"]["session_ids"]],
    }


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


@app.get("/sessions/{session_id}/stats")
def session_stats(session_id: int):
    return sessions.stats(session_id)


@app.get("/sessions/{session_id}/artifacts")
def session_artifacts(session_id: int):
    sessions.get(session_id)
    return _session_artifacts(session_id)


@app.post("/sessions/{session_id}/generate")
def session_generate(session_id: int):
    made = contexts.generate_for_recording(session_id)
    found = workflows.generate(session_ids=[session_id]) if llm.enabled() else {"created": 0, "status": "ok"}
    return {"contexts_created": len(made), "workflows_created": found["created"], "workflow_error": found.get("error"), **_session_artifacts(session_id)}


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


class ClusterNameBody(BaseModel):
    id: str
    name: str | None = None


class NodeClusterBody(BaseModel):
    id: str
    cluster: str | None = None


@app.get("/graph/clusters")
def graph_clusters():
    return clusters.overview()


@app.post("/graph/clusters/name")
def name_clusters():
    return {**clusters.name_all(), **clusters.overview()}


@app.put("/graph/clusters/name")
def rename_cluster(body: ClusterNameBody):
    return clusters.rename(body.id, body.name)


@app.put("/graph/node-cluster")
def move_node_cluster(body: NodeClusterBody):
    return clusters.move(body.id, body.cluster)


@app.get("/legend")
def get_legend():
    return legend.overview()


@app.put("/legend")
def put_legend(body: LegendBody):
    return legend.save(body.categories)


@app.put("/graph/node-override")
def put_node_override(body: NodeOverrideBody):
    return legend.set_override(body.id, body.label, body.category)


@app.get("/graph/summary")
def graph_summary(session_id: int | None = None):
    return graph.summary(session_id)


@app.get("/graph/tasks")
def graph_tasks(session_id: int | None = None, since: str | None = None):
    return {"data": graph.tasks(session_id, since=_parse(since))}


@app.get("/graph/search")
def graph_search(q: str | None = None, kind: str | None = None, session_id: int | None = None, limit: int = 20):
    return {"data": graph.find(q, kind=kind, limit=limit, session_id=session_id)}


@app.get("/graph/node")
def graph_node(id: str, session_id: int | None = None, limit: int = 25):
    return graph.node(id, session_id=session_id, limit=limit)


@app.post("/graph/build")
def graph_build(body: GraphBuildBody | None = None):
    return graph.build((body or GraphBuildBody()).session_id)


@app.post("/graph/tune")
def graph_tune(body: GraphTuneBody | None = None):
    body = body or GraphTuneBody()
    if body.reset:
        return tuner.reset(body.session_id)
    return tuner.tune(body.session_id, rounds=body.rounds, force=body.force)


@app.get("/context")
def get_context(task: str | None = None, minutes: int | None = None, session_id: int | None = None,
                max_nodes: int = 24, format: str = "json"):
    pack = context.build(task=task, minutes=minutes, session_id=session_id, max_nodes=max_nodes)
    if format == "markdown":
        return Response(pack["markdown"], media_type="text/markdown; charset=utf-8")
    return pack


@app.get("/contexts")
def list_contexts(limit: int = 100):
    return {"data": contexts.list_saved(limit), "presets": contexts.presets()}


@app.post("/contexts")
def create_context(body: ContextBody):
    return contexts.create(body.task, body.preset, body.session_id, body.minutes, body.title)


@app.post("/contexts/compose")
def compose_context(body: ContextComposeBody):
    return contexts.compose(body.name, body.description, body.session_ids, body.context_ids, body.workflow_ids)


@app.get("/contexts/presets")
def context_presets():
    return {"data": contexts.presets()}


@app.get("/contexts/detail")
def context_detail(session_id: int | None = None, start: str | None = None, end: str | None = None,
                   minutes: int | None = None):
    return contexts.detail(session_id, _parse(start), _parse(end), minutes)


@app.get("/contexts/search")
def context_search(q: str = "", session_id: int | None = None, start: str | None = None, end: str | None = None,
                   minutes: int | None = None, limit: int = 20):
    return contexts.find(q, session_id, _parse(start), _parse(end), minutes, max(1, min(limit, 100)))


@app.get("/llm/status")
def llm_status():
    return llm.status()


@app.get("/llm/models")
def llm_models(provider: str = "local"):
    return llm.list_models(provider)


@app.post("/llm/test")
def llm_test(provider: str | None = None):
    return llm.test_connection(provider)


@app.post("/llm/providers/{provider}")
def llm_save_provider(provider: str, body: LLMProviderBody):
    if provider not in llm.PROVIDERS:
        raise db.ChronicleError(f"unknown provider {provider!r}", 404)
    if body.api_key is not None and body.api_key.strip():
        db.set_setting(f"llm_api_key_{provider}", body.api_key.strip())
    if body.clear_key:
        db.set_setting(f"llm_api_key_{provider}", "")
    if body.model is not None:
        db.set_setting(f"llm_model_{provider}", body.model.strip())
    if body.base_url is not None and provider == "local":
        db.set_setting("llm_base_url", body.base_url.strip())
    if body.activate:
        db.set_setting("llm_provider", provider)
    return llm.status()


@app.get("/contexts/{context_id}")
def get_saved_context(context_id: int):
    return contexts.get(context_id)


@app.get("/contexts/{context_id}/document")
def context_document(context_id: int):
    item = contexts.get(context_id)
    return _attachment(f"{artifacts.file_stem(item)}.md", "text/markdown; charset=utf-8", artifacts.context_document(item))


@app.delete("/contexts/{context_id}")
def delete_saved_context(context_id: int):
    return contexts.delete(context_id)


# MCP


@app.get("/clients")
def list_clients():
    return {"data": clients.catalog(), "mcp_url": clients.remote_url()}


@app.get("/clients/{client_id}")
def client_config(client_id: str):
    return clients.config(client_id)


@app.post("/clients/{client_id}/uninstall")
def uninstall_client(client_id: str):
    return clients.uninstall(client_id)


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
    deleted["items"] = itemgraph.forget_range(start, end)
    graph.forget(start, end)
    return deleted


def _attachment(name: str, media_type: str, body: str | bytes) -> Response:
    return Response(content=body, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/workflows")
def list_workflows():
    return {"data": workflows.list_workflows()}


@app.post("/workflows/generate")
def generate_workflows(body: WorkflowBody):
    return workflows.generate(body.name, body.purpose, body.session_ids, body.context_ids)


@app.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: int):
    return workflows.get_workflow(workflow_id)


@app.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: int):
    return workflows.delete(workflow_id)


@app.get("/workflows/{workflow_id}/document")
def workflow_document(workflow_id: int):
    item = workflows.get_workflow(workflow_id)
    return _attachment(f"{artifacts.file_stem(item)}.md", "text/markdown; charset=utf-8", artifacts.workflow_document(item))


@app.get("/graph/download/{kind}")
@app.get("/graph/download/{kind}/{item_id}")
def graph_download(kind: str, item_id: int | None = None, format: str = "json"):
    return _attachment(*artifacts.graph_download(kind, item_id, format))


@app.get("/harnesses")
def list_harnesses():
    return {"data": harness.list_harnesses()}


@app.post("/harnesses")
def create_harness(body: HarnessBody):
    return harness.create(body.name, body.purpose, body.session_ids, body.context_ids, body.workflow_ids, body.new_workflows)


@app.post("/harnesses/suggest")
def suggest_harness(body: HarnessDesignBody):
    return harness.suggest(body.name, body.purpose, body.session_ids)


@app.get("/harnesses/{harness_id}")
def get_harness(harness_id: int):
    return harness.get(harness_id)


@app.put("/harnesses/{harness_id}")
def update_harness(harness_id: int, body: HarnessUpdateBody):
    return harness.update(harness_id, body.name, body.purpose, body.session_ids)


@app.delete("/harnesses/{harness_id}")
def delete_harness(harness_id: int):
    return harness.delete(harness_id)


@app.put("/harnesses/{harness_id}/file")
def save_harness_file(harness_id: int, body: HarnessFileBody):
    return harness.save_file(harness_id, body.path, body.content)


@app.post("/harnesses/{harness_id}/file")
def new_harness_file(harness_id: int, body: HarnessNewFileBody):
    return harness.new_file(harness_id, body.kind, body.title)


@app.delete("/harnesses/{harness_id}/file")
def delete_harness_file(harness_id: int, path: str):
    return harness.delete_file(harness_id, path)


@app.post("/harnesses/{harness_id}/chat")
def harness_chat(harness_id: int, body: HarnessChatBody):
    return harness.chat(harness_id, body.message)


@app.post("/harnesses/{harness_id}/undo")
def undo_harness(harness_id: int):
    return harness.undo(harness_id)


@app.get("/harnesses/{harness_id}/engine")
def harness_engine(harness_id: int):
    return harness.engine(harness_id)


@app.get("/harnesses/{harness_id}/engine/search")
def harness_engine_search(harness_id: int, q: str = ""):
    return harness.engine_search(harness_id, q)


@app.get("/harnesses/{harness_id}/export")
def harness_export(harness_id: int, format: str = "zip"):
    return _attachment(*harness.export(harness_id, format))


@app.post("/ask")
def ask(body: AskBody):
    return ask_mod.prepare(body.question, body.session_id, body.history)


@app.post("/brain/ask")
def brain_ask(body: AskBody):
    return brain.answer(body.question, body.history)


@app.get("/brain/chats")
def brain_chats(scope: str = "brain"):
    return {"data": brain.list_chats(scope)}


@app.post("/brain/chats")
def save_brain_chat(body: BrainChatBody):
    return brain.save_chat(body.id, body.turns, body.scope)


@app.get("/brain/chats/{chat_id}")
def get_brain_chat(chat_id: int):
    return brain.get_chat(chat_id)


@app.delete("/brain/chats/{chat_id}")
def delete_brain_chat(chat_id: int):
    return brain.delete_chat(chat_id)


@app.get("/brain/harness-ideas")
def brain_harness_ideas():
    return brain.harness_ideas()


@app.post("/brain/suggest")
def brain_suggest(body: FieldSuggestBody):
    return brain.suggest_fields(body.kind, body.name, body.description, body.session_ids, body.context_ids, body.workflow_ids)


# Privacy & sources


@app.get("/sources")
def list_sources():
    return {"data": sources.list_sources(), "privacy": _privacy()}


def _privacy() -> dict:
    with db.conn() as connection:
        items = connection.execute("SELECT COUNT(*) AS n FROM items").fetchone()["n"]
    return {"items": items, "frames": db.counts().get("frames") or 0, "retention_days": db.get_setting("retention_days", "30"),
            "redact": db.redact_enabled(), "home": str(db.home()), "llm": llm.status().get("enabled"), "model": llm.status().get("model")}


@app.post("/sources/{source_id}")
def configure_source(source_id: str, body: SourceBody):
    return sources.configure(source_id, enabled=body.enabled, options=body.options)


@app.post("/sources/{source_id}/sync")
async def sync_source(source_id: str):
    return await asyncio.to_thread(sources.run, source_id, force=True)


@app.post("/sources/{source_id}/import")
async def import_source(source_id: str, request: Request, filename: str = "upload"):
    data = await request.body()
    if not data:
        raise db.ChronicleError("send the file as the request body")
    if len(data) > 1024 * 1024 * 1024:
        raise db.ChronicleError("that file is over 1 GB", 413)
    with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix or ".bin", delete=False) as handle:
        handle.write(data)
        path = handle.name
    try:
        return await asyncio.to_thread(sources.import_file, source_id, path)
    finally:
        Path(path).unlink(missing_ok=True)


@app.post("/sources/{source_id}/connect")
def connect_source(source_id: str):
    source = sources.get(source_id)
    if not hasattr(source, "connect"):
        raise db.ChronicleError(f"{source.name} doesn't need signing in")
    return source.connect()


@app.post("/sources/{source_id}/disconnect")
def disconnect_source(source_id: str, body: DisconnectBody | None = None):
    result = sources.disconnect(source_id, forget=bool(body and body.forget))
    if getattr(sources.get(source_id), "provider", None):
        oauth.forget(source_id)
    return result


@app.get("/sources/{source_id}/choices")
def source_choices(source_id: str):
    source = sources.get(source_id)
    return {"data": source.choices() if hasattr(source, "choices") else []}


@app.get("/oauth/callback", response_class=HTMLResponse)
def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    try:
        oauth.callback(code, state, error)
        title = "Connected"
    except db.ChronicleError as exc:
        title = html.escape(exc.message)
    return f"<!doctype html><meta charset=utf-8><title>Chronicle</title><body style='background:#000;color:#fff;font-family:system-ui;display:grid;place-items:center;height:100vh'><div><h1>{title}</h1><p><a style='color:#ffd60a' href='/#sources'>Back to Chronicle</a></p></div>"


@app.post("/ask/stream")
async def ask_stream(body: AskBody):
    result = await asyncio.to_thread(ask_mod.prepare, body.question, body.session_id, body.history)

    async def generate():
        for chunk in result["chunks"]:
            yield f"data: {json.dumps({'delta': chunk})}\n\n"
        yield f"data: {json.dumps({'done': True, 'mode': result['mode'], 'citations': result['citations'], 'answer': result['answer']})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


SETTING_KEYS = [
    "onboarded", "timezone", "retention_days", "redact", "capture_interval_secs", "capture_clipboard",
    "capture_screenshots", "capture_ocr", "idle_threshold_secs", "graph_half_life_hours", "llm_provider", "llm_model", "llm_base_url",
]
BOOL_KEYS = {"onboarded", "redact", "capture_clipboard", "capture_screenshots", "capture_ocr"}


@app.get("/settings")
def get_settings():
    payload = {key: db.get_setting(key) for key in SETTING_KEYS}
    for key in BOOL_KEYS:
        payload[key] = payload[key] == "1"
    payload["ocr_available"] = shutil.which("tesseract") is not None
    payload["home"] = str(db.home())
    payload["capture"] = capture.status()
    return payload


@app.post("/settings")
def update_settings(body: SettingsBody):
    values = body.model_dump(exclude_none=True)
    key = values.pop("llm_api_key", None)
    if key is not None:
        provider = values.get("llm_provider") or db.get_setting("llm_provider", "")
        if provider not in llm.PROVIDERS:
            raise db.ChronicleError("choose a provider before saving a key")
        values[f"llm_api_key_{provider}"] = key
    if values.get("llm_model") is not None and (values.get("llm_provider") or db.get_setting("llm_provider", "")) in llm.PROVIDERS:
        values[f"llm_model_{values.get('llm_provider') or db.get_setting('llm_provider', '')}"] = values.pop("llm_model")
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
        keep = {key: db.get_setting(key) for key in ("onboarded", "timezone", "ai_base_url", "ai_model", "ai_api_key", "redact", "retention_days",
                                                  "llm_provider", "llm_model", "llm_base_url", *(f"llm_{part}_{item['id']}" for item in llm.CATALOG for part in ("api_key", "model")))}
        db.wipe()
        seed.ensure()
        for key, value in keep.items():
            if value is not None:
                db.set_setting(key, value)
    loaded = seed.load_sample()
    return {"loaded": loaded, "frames": db.counts()["frames"]}


db.media_root()
app.mount("/media", StaticFiles(directory=str(db.media_root())), name="media")
if WEB.exists():
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")

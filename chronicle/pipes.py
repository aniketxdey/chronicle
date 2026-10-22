"""Scheduled pipes. A pipe is a markdown prompt plus a local runner."""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta
from pathlib import Path

from chronicle import db, meetings, workflows
from chronicle.timeutil import utc_iso

_locks: dict[str, threading.Lock] = {}
_guard = threading.Lock()

DISCOVER = [
    {
        "name": "toggl-time",
        "schedule": "daily 18:00",
        "description": "Build a local time entry from apps you actually used. Sending it to Toggl needs an account.",
        "prompt": "Sum minutes per app for the window and format them as time entries. Do not call Toggl unless a local proxy is configured.",
    },
    {
        "name": "slack-nudge",
        "schedule": "every 2h",
        "description": "Draft a Slack note when the export ticket moves. Posting needs a Slack app.",
        "prompt": "If BILL-142 or the Acme export changed in the window, draft a short Slack message. Do not post it yourself.",
    },
    {
        "name": "focus-guard",
        "schedule": "every 30m",
        "description": "Notice long stretches away from the task that is due.",
        "prompt": "Look at the window and say whether the work still matches the Friday export, or whether it drifted.",
    },
]


def _lock_for(name: str) -> threading.Lock:
    with _guard:
        if name not in _locks:
            _locks[name] = threading.Lock()
        return _locks[name]


def parse_markdown(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip()
    meta = {}
    for line in parts[1].strip().splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, parts[2].strip()


def render_markdown(schedule: str, enabled: bool, prompt: str) -> str:
    flag = "true" if enabled else "false"
    return f"---\nschedule: {schedule}\nenabled: {flag}\n---\n\n{prompt.strip()}\n"


def write_file(name: str, schedule: str, enabled: bool, prompt: str) -> Path:
    folder = db.pipes_root() / name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "pipe.md"
    path.write_text(render_markdown(schedule, enabled, prompt))
    return path


def import_files() -> None:
    root = db.pipes_root()
    with db.conn() as connection:
        existing = {row["name"] for row in connection.execute("SELECT name FROM pipes").fetchall()}
        for path in sorted(root.glob("*/pipe.md")):
            name = path.parent.name
            if name in existing:
                continue
            meta, prompt = parse_markdown(path.read_text())
            enabled = meta.get("enabled", "false").lower() == "true"
            connection.execute(
                """
                INSERT INTO pipes(name, schedule, enabled, prompt, description, source, created_at)
                VALUES (?, ?, ?, ?, ?, 'disk', ?)
                """,
                (name, meta.get("schedule", "manual"), 1 if enabled else 0, prompt, meta.get("description", ""), utc_iso()),
            )


def list_pipes() -> dict:
    import_files()
    with db.conn() as connection:
        rows = connection.execute("SELECT * FROM pipes ORDER BY name").fetchall()
    installed = {row["name"] for row in rows}
    discover = [item for item in DISCOVER if item["name"] not in installed]
    return {"data": [dict(row) for row in rows], "discover": discover}


def get_pipe(name: str) -> dict:
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM pipes WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise db.ChronicleError("pipe not found", 404)
    return dict(row)


def install(payload: dict) -> dict:
    source = (payload.get("source") or "").strip()
    name = (payload.get("name") or "").strip()
    if source.startswith("catalog:"):
        name = source.split(":", 1)[1]
    catalog = next((item for item in DISCOVER if item["name"] == name), None)
    if catalog and not payload.get("prompt"):
        schedule = catalog["schedule"]
        prompt = catalog["prompt"]
        description = catalog["description"]
        origin = "catalog"
    else:
        if not name or not payload.get("prompt"):
            raise db.ChronicleError("name and prompt are required")
        schedule = payload.get("schedule") or "manual"
        prompt = payload["prompt"]
        description = payload.get("description") or ""
        origin = "installed"
    if not re.fullmatch(r"[a-z0-9-]{2,40}", name):
        raise db.ChronicleError("pipe name must be 2-40 lowercase letters, numbers, or hyphens")
    with db.conn() as connection:
        exists = connection.execute("SELECT 1 FROM pipes WHERE name = ?", (name,)).fetchone()
        if exists:
            raise db.ChronicleError("pipe already installed", 409)
        connection.execute(
            """
            INSERT INTO pipes(name, schedule, enabled, prompt, description, source, created_at)
            VALUES (?, ?, 0, ?, ?, ?, ?)
            """,
            (name, schedule, prompt, description, origin, utc_iso()),
        )
    write_file(name, schedule, False, prompt)
    return get_pipe(name)


def set_enabled(name: str, enabled: bool) -> dict:
    pipe = get_pipe(name)
    with db.conn() as connection:
        connection.execute("UPDATE pipes SET enabled = ? WHERE name = ?", (1 if enabled else 0, name))
    write_file(name, pipe["schedule"], enabled, pipe["prompt"])
    return get_pipe(name)


def configure(name: str, schedule: str | None = None, prompt: str | None = None) -> dict:
    pipe = get_pipe(name)
    schedule = schedule or pipe["schedule"]
    prompt = prompt or pipe["prompt"]
    with db.conn() as connection:
        connection.execute("UPDATE pipes SET schedule = ?, prompt = ? WHERE name = ?", (schedule, prompt, name))
    write_file(name, schedule, bool(pipe["enabled"]), prompt)
    return get_pipe(name)


def logs(name: str) -> dict:
    get_pipe(name)
    with db.conn() as connection:
        rows = connection.execute(
            "SELECT * FROM pipe_executions WHERE pipe_name = ? ORDER BY id DESC LIMIT 20",
            (name,),
        ).fetchall()
    return {"pipe": name, "executions": [dict(row) for row in rows]}


def due_names(now: datetime | None = None) -> list[str]:
    moment = now or datetime.now().astimezone()
    ready = []
    with db.conn() as connection:
        rows = connection.execute("SELECT * FROM pipes WHERE enabled = 1").fetchall()
    for row in rows:
        if _is_due(row, moment):
            ready.append(row["name"])
    return ready


def _is_due(row, now: datetime) -> bool:
    schedule = (row["schedule"] or "").strip()
    last = row["last_run"]
    match = re.fullmatch(r"every\s+(\d+)\s*([mhd])", schedule)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        seconds = amount * {"m": 60, "h": 3600, "d": 86400}[unit]
        if not last:
            return True
        return datetime.fromisoformat(last) + timedelta(seconds=seconds) <= now.astimezone(datetime.fromisoformat(last).tzinfo or now.tzinfo)
    match = re.fullmatch(r"daily(?:\s+(\d{1,2}):(\d{2}))?", schedule)
    if match:
        hour = int(match.group(1) or 17)
        minute = int(match.group(2) or 0)
        local = now.astimezone(db.local_zone())
        if (local.hour, local.minute) < (hour, minute):
            return False
        if not last:
            return True
        last_local = datetime.fromisoformat(last).astimezone(db.local_zone())
        return last_local.date() < local.date()
    return False


def execute_sync(name: str) -> dict:
    pipe = get_pipe(name)
    with _lock_for(name):
        started = utc_iso()
        with db.conn() as connection:
            cursor = connection.execute(
                """
                INSERT INTO pipe_executions(pipe_name, started_at, status, stdout, stderr)
                VALUES (?, ?, 'running', '', '')
                """,
                (name, started),
            )
            execution_id = int(cursor.lastrowid)
        try:
            report, output_path = _build(pipe)
            finished = utc_iso()
            with db.conn() as connection:
                connection.execute(
                    """
                    UPDATE pipe_executions
                    SET finished_at = ?, status = 'success', stdout = ?, output_path = ?
                    WHERE id = ?
                    """,
                    (finished, report[:100_000], str(output_path), execution_id),
                )
                connection.execute("UPDATE pipes SET last_run = ? WHERE name = ?", (finished, name))
        except Exception as exc:  # noqa: BLE001 — store the failure on the execution row
            finished = utc_iso()
            with db.conn() as connection:
                connection.execute(
                    """
                    UPDATE pipe_executions
                    SET finished_at = ?, status = 'error', stderr = ?
                    WHERE id = ?
                    """,
                    (finished, str(exc), execution_id),
                )
        return _execution(execution_id)


def _execution(execution_id: int) -> dict:
    with db.conn() as connection:
        row = connection.execute("SELECT * FROM pipe_executions WHERE id = ?", (execution_id,)).fetchone()
    return dict(row)


def _window_for(schedule: str) -> tuple[str, str]:
    zone = db.local_zone()
    now = datetime.now(zone)
    match = re.fullmatch(r"every\s+(\d+)\s*([mhd])", (schedule or "").strip())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
        return utc_iso(now - delta), utc_iso(now)
    if schedule.startswith("daily"):
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return utc_iso(start), utc_iso(now)
    return utc_iso(now - timedelta(hours=24)), utc_iso(now)


def _build(pipe: dict) -> tuple[str, Path]:
    start, end = _window_for(pipe["schedule"])
    zone = db.local_zone()
    widened = False
    with db.conn() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM frames WHERE timestamp >= ? AND timestamp <= ?",
            (start, end),
        ).fetchone()["n"]
        if count == 0:
            latest = connection.execute("SELECT timestamp FROM frames ORDER BY timestamp DESC LIMIT 1").fetchone()
            if latest:
                latest_local = datetime.fromisoformat(latest["timestamp"]).astimezone(zone)
                day_start = latest_local.replace(hour=0, minute=0, second=0, microsecond=0)
                start = utc_iso(day_start)
                end = utc_iso(day_start + timedelta(days=1) - timedelta(seconds=1))
                widened = True
    name = pipe["name"]
    if "standup" in name:
        body = _standup()
    elif "meeting" in name:
        body = _meetings()
    elif "workflow" in name or "focus" in name:
        body = _workflows_report()
    elif "obsidian" in name or "journal" in name:
        body = _journal()
    elif "toggl" in name:
        body = _toggl()
    elif "slack" in name:
        body = _slack_draft()
    else:
        body = _generic(start, end)
    header = [
        "# Context",
        f"- pipe: {name}",
        f"- schedule: {pipe['schedule']}",
        f"- time_range: {start} → {end}",
        f"- timezone: {db.get_setting('timezone', 'local')}",
        "- api: http://127.0.0.1:3030",
        f"- widened_to_latest_day: {'yes' if widened else 'no'}",
        "",
        "# Prompt",
        pipe["prompt"].strip(),
        "",
        "# Report",
        body.strip(),
        "",
    ]
    report = "\n".join(header)
    folder = db.pipes_root() / name / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = folder / f"{stamp}.md"
    path.write_text(report)
    if "obsidian" in name or "journal" in name:
        export = db.home() / "exports" / "obsidian"
        export.mkdir(parents=True, exist_ok=True)
        (export / f"{stamp}.md").write_text(report)
    return report, path


def _frames_text(where: str = "1=1", params: tuple = ()) -> list:
    with db.conn() as connection:
        return connection.execute(f"SELECT * FROM frames WHERE {where} ORDER BY timestamp", params).fetchall()


def _standup() -> str:
    frames = [
        frame for frame in _frames_text()
        if "standup" in (frame["window_name"] or "").lower() or frame["app_name"] == "Slack"
    ]
    lines = ["## Standup", ""]
    if not frames:
        lines.append("No Slack frames in the recorded day.")
    for frame in frames:
        lines.append(f"### {frame['timestamp']} · {frame['window_name']}")
        lines.append(frame["full_text"].strip())
        lines.append("")
    lines.append("## Due dates heard nearby")
    lines.append("")
    for meeting in meetings.list_meetings():
        for segment in meeting["segments"]:
            text = segment["transcription"]
            if any(word in text.lower() for word in ("friday", "due", "email")):
                who = segment["speaker_name"] or "Unnamed speaker"
                lines.append(f"- {who}: {text}")
    return "\n".join(lines)


def _meetings() -> str:
    found = meetings.list_meetings()
    if not found:
        return "No meetings are stored."
    lines = []
    for meeting in found:
        lines.append(f"## {meeting['title']}")
        lines.append(f"{meeting['started_at']} → {meeting['ended_at']}")
        lines.append("")
        for segment in meeting["segments"]:
            who = segment["speaker_name"] or "Unnamed speaker"
            lines.append(f"- **{who}:** {segment['transcription']}")
        lines.append("")
    lines.append("External meeting bots (Granola, Fireflies, Otter) are not connected. This note is from local transcripts only.")
    return "\n".join(lines)


def _workflows_report() -> str:
    items = workflows.list_workflows()
    if not items:
        items = workflows.refresh()
    lines = ["## Work worth automating", ""]
    for item in items:
        lines.append(f"### {item['title']}")
        lines.append(item["description"])
        lines.append(f"Evidence: {item['evidence']}")
        lines.append(f"Suggested: {item['automation']}")
        lines.append("")
    return "\n".join(lines)


def _journal() -> str:
    frames = _frames_text()
    lines = ["## Journal", ""]
    for frame in frames:
        lines.append(f"- {frame['timestamp']} · {frame['app_name']} · {frame['window_name']}")
    lines.append("")
    lines.append("Saved under this machine's export folder. Obsidian sync is a local path, not a connected account.")
    return "\n".join(lines)


def _toggl() -> str:
    frames = _frames_text()
    totals: dict[str, float] = {}
    for frame in frames:
        totals[frame["app_name"]] = totals.get(frame["app_name"], 0) + (frame["duration_secs"] or 0)
    lines = ["## Local time entries", ""]
    for app, seconds in sorted(totals.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- {app}: {int(round(seconds / 60))} min")
    lines.append("")
    lines.append("Local only. Point an agent with a Toggl MCP server at this pipe's output to send it.")
    return "\n".join(lines)


def _slack_draft() -> str:
    return "\n".join(
        [
            "## Draft, not posted",
            "",
            "BILL-142: Acme export columns stayed frozen (invoice_id, amount_usd, settled_at). PR 882 checks passed. Still due Friday.",
            "",
            "Draft only. Chronicle never posts on your behalf; hand this to an agent with a Slack MCP server.",
        ]
    )


def _generic(start: str, end: str) -> str:
    frames = _frames_text("timestamp >= ? AND timestamp <= ?", (start, end))
    lines = ["## Frames in the window", ""]
    if not frames:
        lines.append("No frames.")
    for frame in frames[:30]:
        lines.append(f"- {frame['app_name']} · {frame['window_name']}")
    return "\n".join(lines)


def create_from_workflow(workflow_id: int) -> dict:
    item = workflows.get_workflow(workflow_id)
    base = re.sub(r"[^a-z0-9]+", "-", item["title"].lower()).strip("-")[:40] or "workflow"
    name = base
    suffix = 2
    with db.conn() as connection:
        while connection.execute("SELECT 1 FROM pipes WHERE name = ?", (name,)).fetchone():
            name = f"{base[:36]}-{suffix}"
            suffix += 1
    return install(
        {
            "name": name,
            "schedule": "daily 17:30",
            "prompt": item["pipe_prompt"] or item["automation"],
            "description": item["description"],
        }
    )

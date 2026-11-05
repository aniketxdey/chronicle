"""How you use AI: chats captured by the browser extension, chat-history exports, and local logs of coding agents.

Every item carries ``props.tool`` (an ``shells.AI_TOOLS`` id) so the AI shell can say which AI you use for what.
Prompts (``from_me``) are kept in full up to a cap; replies are trimmed, since what you ask says more about you.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import quote, unquote, urlsplit

from chronicle import db
from chronicle.sources.base import Item, Source, Status, register, snapshot

HOME = Path.home()
PROMPT_CAP = 6000
REPLY_CAP = 1500


def _iso(value) -> str:
    if value is None or value == "":
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    if isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")
    text = str(value).replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _text(content) -> str:
    """Text from the many shapes chat APIs use: strings, part lists, {type: text, text}, {parts: [...]}."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(filter(None, (_text(part) for part in content)))
    if isinstance(content, dict):
        if content.get("type") in {"tool_use", "tool_result", "image", "thinking", "redacted_thinking"}:
            return ""
        for key in ("text", "parts", "content", "value"):
            if key in content:
                return _text(content[key])
    return ""


_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
_INJECTED = re.compile(r"<([a-z]+[_-][a-z_-]+)(?:\s[^>]*)?>.*?</\1>\s*", re.S)


def typed(text: str) -> str:
    """What the person actually typed, without blocks their tool injected (commands, attached files, reminders)."""
    found = _QUERY.findall(text)
    if found:
        return "\n\n".join(found).strip()
    return _INJECTED.sub("", text).strip()


def turn(tool: str, thread: str, index: int | str, role: str, text: str, ts, *, title: str = "", url: str | None = None,
         model: str | None = None, project: str | None = None) -> Item | None:
    mine = role in {"user", "human"}
    text = typed(text or "") if mine else (text or "").strip()
    if not text:
        return None
    props = {"tool": tool}
    if model:
        props["model"] = model
    if project:
        props["project"] = project
    return Item(kind="prompt" if mine else "reply", ext_id=f"{tool}:{thread}:{index}", ts=_iso(ts),
                title=(title or text.split("\n", 1)[0])[:160], text=text[: PROMPT_CAP if mine else REPLY_CAP], url=url,
                thread=str(thread), actors=[{"name": "Me", "me": True}] if mine else [], from_me=mine, props=props)


def _compact(items: Iterable[Item | None]) -> Iterator[Item]:
    return (item for item in items if item is not None)


# Pushed by the browser extension

class ExtensionSource(Source):
    id = "ai-extension"
    name = "AI chats in your browser"
    category = "ai"
    description = "Chats on ChatGPT, Claude, Gemini, Perplexity, Copilot, Grok, DeepSeek, and Le Chat."
    permissions = ["browser_extension"]
    icon = "sparkles"

    def available(self) -> Status:
        return Status("unavailable", "Needs the browser extension, which isn't in Chronicle yet.")


def from_extension(payload: dict) -> list[Item]:
    tool = str(payload.get("tool") or "chatgpt")
    thread = str(payload.get("conversation_id") or payload.get("url") or "chat")
    title = payload.get("title") or ""
    items = []
    for entry in payload.get("turns") or []:
        key = entry.get("id") or hashlib.sha1(f"{entry.get('role')}:{entry.get('text', '')[:400]}".encode()).hexdigest()[:16]
        items.append(turn(tool, thread, key, entry.get("role", "user"), entry.get("text", ""), entry.get("ts") or payload.get("ts"),
                          title=title, url=payload.get("url"), model=payload.get("model")))
    return list(_compact(items))


register(ExtensionSource())


# Exports

def _json_from(path: Path, inner: str) -> object:
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            name = next((n for n in archive.namelist() if n.endswith(inner)), None)
            if name is None:
                raise db.ChronicleError(f"{inner} not found in {path.name}")
            return json.loads(archive.read(name).decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


class ChatGPTExportSource(Source):
    id = "chatgpt-export"
    name = "ChatGPT history"
    category = "ai"
    description = "Import your data export: Settings › Data controls › Export."
    importable = True
    icon = "upload"

    def available(self) -> Status:
        return Status("connected", "your export .zip or .json")

    def import_file(self, path: Path, options: dict) -> Iterator[Item]:
        data = _json_from(path, "conversations.json")
        for conversation in data if isinstance(data, list) else []:
            thread = conversation.get("conversation_id") or conversation.get("id") or conversation.get("title")
            title = conversation.get("title") or ""
            nodes = [n["message"] for n in (conversation.get("mapping") or {}).values() if n.get("message")]
            nodes.sort(key=lambda m: m.get("create_time") or 0)
            for message in nodes:
                role = (message.get("author") or {}).get("role")
                if role not in {"user", "assistant"}:
                    continue
                item = turn("chatgpt", thread, message.get("id"), role, _text(message.get("content")),
                            message.get("create_time") or conversation.get("create_time"), title=title,
                            url=f"https://chatgpt.com/c/{thread}", model=(message.get("metadata") or {}).get("model_slug"))
                if item:
                    yield item


class ClaudeExportSource(Source):
    id = "claude-export"
    name = "Claude history"
    category = "ai"
    description = "Import your data export: Settings › Privacy › Export data."
    importable = True
    icon = "upload"

    def available(self) -> Status:
        return Status("connected", "your export .zip or .json")

    def import_file(self, path: Path, options: dict) -> Iterator[Item]:
        data = _json_from(path, "conversations.json")
        for conversation in data if isinstance(data, list) else []:
            thread = conversation.get("uuid") or conversation.get("name")
            for message in conversation.get("chat_messages") or []:
                text = message.get("text") or _text(message.get("content"))
                item = turn("claude", thread, message.get("uuid"), message.get("sender", "human"), text, message.get("created_at"),
                            title=conversation.get("name") or "", url=f"https://claude.ai/chat/{thread}")
                if item:
                    yield item


class GeminiTakeoutSource(Source):
    id = "gemini-takeout"
    name = "Gemini history"
    category = "ai"
    description = "Import Gemini Apps activity from Google Takeout, as JSON."
    importable = True
    icon = "upload"

    def available(self) -> Status:
        return Status("connected", "MyActivity.json or the Takeout .zip")

    def import_file(self, path: Path, options: dict) -> Iterator[Item]:
        data = _json_from(path, "MyActivity.json")
        for entry in data if isinstance(data, list) else []:
            title = entry.get("title") or ""
            if not title.startswith("Prompted "):
                continue
            prompt = title[len("Prompted "):]
            stamp = entry.get("time")
            item = turn("gemini", (stamp or "")[:10] or "gemini", hashlib.sha1(f"{stamp}{prompt}".encode()).hexdigest()[:16],
                        "user", prompt, stamp, title=prompt[:120], url="https://gemini.google.com/app")
            if item:
                yield item


register(ChatGPTExportSource())
register(ClaudeExportSource())
register(GeminiTakeoutSource())


# Local logs of coding agents

class _JsonlLogs(Source):
    category = "ai"
    icon = "terminal"
    roots: list[Path] = []
    pattern = "**/*.jsonl"
    syncs = True

    def available(self) -> Status:
        if not any(root.exists() for root in self.roots):
            return Status("unavailable", f"{self.name} isn't installed or hasn't been used on this Mac")
        return Status("connected")

    def files(self) -> list[Path]:
        return sorted(path for root in self.roots if root.exists() for path in root.glob(self.pattern))

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        marks = json.loads(cursor) if cursor else {}
        for path in self.files():
            key = str(path)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            offset = marks.get(key, 0)
            if size <= offset:
                continue
            with path.open("rb") as handle:
                handle.seek(offset)
                data = handle.read()
            complete = data.rfind(b"\n") + 1
            lines = data[:complete].decode("utf-8", errors="ignore").splitlines()
            state: dict = {"path": path}
            for number, line in enumerate(lines):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                item = self.parse(record, state, f"{offset}-{number}")
                if item:
                    yield item
            marks[key] = offset + complete
            yield json.dumps(marks)

    def parse(self, record: dict, state: dict, position: str) -> Item | None:
        raise NotImplementedError


class ClaudeCodeSource(_JsonlLogs):
    id = "claude-code"
    name = "Claude Code"
    description = "Your prompts, from Claude Code session logs."
    roots = [HOME / ".claude" / "projects"]

    def parse(self, record, state, position):
        if record.get("type") not in {"user", "assistant"} or record.get("isMeta") or record.get("isSidechain"):
            return None
        message = record.get("message") or {}
        text = _text(message.get("content"))
        if record.get("type") == "user" and (not text or text.startswith(("<command-", "<local-command", "Caveat:"))):
            return None
        session = record.get("sessionId") or state["path"].stem
        if record.get("type") == "user" and session not in state:
            state[session] = text.split("\n", 1)[0][:120]
        project = Path(record.get("cwd") or "").name or None
        return turn("claude-code", session, record.get("uuid") or position, message.get("role") or record["type"], text,
                    record.get("timestamp"), title=state.get(session, ""), model=message.get("model"), project=project)


class CodexSource(_JsonlLogs):
    id = "codex"
    name = "Codex"
    description = "Your prompts, from Codex session logs."
    roots = [HOME / ".codex" / "sessions"]

    def parse(self, record, state, position):
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else record
        if record.get("type") == "session_meta" or payload.get("type") == "session_meta":
            meta = payload.get("payload", payload)
            state["session"] = meta.get("id") or state["path"].stem
            state["project"] = Path(meta.get("cwd") or "").name or None
            return None
        if payload.get("type") != "message" or payload.get("role") not in {"user", "assistant"}:
            return None
        text = _text(payload.get("content"))
        if payload["role"] == "user" and (not text or text.lstrip().startswith(("<environment_context>", "<user_instructions>"))):
            return None
        session = state.get("session") or state["path"].stem
        if payload["role"] == "user" and "title" not in state:
            state["title"] = text.split("\n", 1)[0][:120]
        return turn("codex", session, position, payload["role"], text, record.get("timestamp"), title=state.get("title", ""),
                    project=state.get("project"))


class GeminiCliSource(_JsonlLogs):
    id = "gemini-cli"
    name = "Gemini CLI"
    description = "Your prompts, from Gemini CLI logs."
    roots = [HOME / ".gemini" / "tmp"]
    pattern = "*/logs.json"

    def sync(self, cursor, options):
        marks = json.loads(cursor) if cursor else {}
        for path in self.files():
            try:
                entries = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            done = marks.get(str(path), 0)
            for entry in entries[done:]:
                if entry.get("type") == "user":
                    item = turn("gemini-cli", entry.get("sessionId") or path.parent.name, entry.get("messageId", 0), "user",
                                entry.get("message", ""), entry.get("timestamp"))
                    if item:
                        yield item
            marks[str(path)] = len(entries)
            yield json.dumps(marks)


CURSOR_DB = HOME / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"


class CursorSource(Source):
    id = "cursor"
    name = "Cursor"
    category = "ai"
    description = "Your prompts, from Cursor local storage."
    icon = "terminal"
    syncs = True

    def __init__(self, path: Path | None = None):
        self.path = path or CURSOR_DB

    def available(self) -> Status:
        if not self.path.exists():
            return Status("unavailable", "Cursor isn't installed or hasn't been used on this Mac")
        return Status("connected")

    def projects(self) -> dict[str, str]:
        """composerId -> workspace folder name, from each workspace's own state database."""
        found: dict[str, str] = {}
        root = self.path.parent.parent / "workspaceStorage"
        if not root.is_dir():
            return found
        for folder in root.iterdir():
            meta, store = folder / "workspace.json", folder / "state.vscdb"
            if not meta.is_file() or not store.is_file():
                continue
            try:
                uri = json.loads(meta.read_text()).get("folder") or ""
                name = Path(unquote(urlsplit(uri).path)).name
                if not name:
                    continue
                connection = sqlite3.connect(f"file:{quote(str(store))}?mode=ro&immutable=1", uri=True, timeout=1)
                try:
                    row = connection.execute("SELECT value FROM ItemTable WHERE key = 'composer.composerData'").fetchone()
                finally:
                    connection.close()
                for composer in (json.loads(row[0]).get("allComposers") or []) if row else []:
                    if composer.get("composerId"):
                        found[composer["composerId"]] = name
            except (OSError, ValueError, sqlite3.Error):
                continue
        return found

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        since = int(cursor or 0)
        newest = since
        self._projects = self.projects()
        try:
            with snapshot(self.path) as connection:
                composers = connection.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'").fetchall()
                for row in composers:
                    try:
                        data = json.loads(row["value"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    updated = int(data.get("lastUpdatedAt") or data.get("createdAt") or 0)
                    if updated <= since:
                        continue
                    newest = max(newest, updated)
                    yield from self._composer(connection, data)
        except sqlite3.DatabaseError:
            return
        yield str(newest)

    def _composer(self, connection, data: dict) -> Iterator[Item]:
        composer = data.get("composerId") or "chat"
        title = data.get("name") or ""
        bubbles = data.get("conversation") or []
        if not bubbles and data.get("fullConversationHeadersOnly"):
            for header in data["fullConversationHeadersOnly"]:
                row = connection.execute("SELECT value FROM cursorDiskKV WHERE key = ?",
                                         (f"bubbleId:{composer}:{header.get('bubbleId')}",)).fetchone()
                if row:
                    try:
                        bubbles.append(json.loads(row["value"]))
                    except (json.JSONDecodeError, TypeError):
                        continue
        project = getattr(self, "_projects", {}).get(composer)
        for index, bubble in enumerate(bubbles):
            role = "user" if bubble.get("type") == 1 else "assistant"
            item = turn("cursor", composer, bubble.get("bubbleId") or index, role, bubble.get("text") or "",
                        bubble.get("createdAt") or data.get("createdAt"), title=title,
                        model=(bubble.get("modelInfo") or {}).get("modelName"), project=project)
            if item:
                yield item


register(ClaudeCodeSource())
register(CodexSource())
register(CursorSource())
register(GeminiCliSource())

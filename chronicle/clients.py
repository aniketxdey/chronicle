"""MCP setup for every major client and model API.

``config(client)`` returns the snippet to paste; ``install(client)`` merges it into the
client's config file (with a backup) or runs the client's own CLI.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from chronicle import db

NAME = "chronicle"


def _appdata() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))


def _vscode_user() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User"
    if sys.platform == "win32":
        return _appdata() / "Code" / "User"
    return Path.home() / ".config" / "Code" / "User"


def _claude_desktop() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if sys.platform == "win32":
        return _appdata() / "Claude" / "claude_desktop_config.json"
    return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def server_command() -> tuple[str, list[str]]:
    exe = shutil.which("chronicle")
    if exe:
        return exe, ["mcp"]
    return sys.executable, ["-m", "chronicle", "mcp"]


def server_env() -> dict:
    env = {}
    if os.environ.get("CHRONICLE_HOME"):
        env["CHRONICLE_HOME"] = str(db.home())
    return env


def remote_url() -> str:
    base = os.environ.get("CHRONICLE_PUBLIC_URL", "http://127.0.0.1:3030").rstrip("/")
    token = os.environ.get("CHRONICLE_TOKEN")
    return f"{base}/mcp/{token}" if token else f"{base}/mcp"


def _stdio_entry(typed: bool = False) -> dict:
    command, args = server_command()
    entry: dict = {"command": command, "args": args}
    if typed:
        entry = {"type": "stdio", **entry}
    env = server_env()
    if env:
        entry["env"] = env
    return entry


def _toml_block() -> str:
    command, args = server_command()
    lines = [f"[mcp_servers.{NAME}]", f"command = {json.dumps(command)}", f"args = {json.dumps(args)}"]
    env = server_env()
    if env:
        lines.append("env = { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in env.items()) + " }")
    return "\n".join(lines) + "\n"


def _claude_code_args() -> list[str]:
    command, args = server_command()
    extra = []
    for key, value in server_env().items():
        extra += ["-e", f"{key}={value}"]
    return ["claude", "mcp", "add", "--scope", "user", *extra, NAME, "--", command, *args]


CLIENTS: dict[str, dict] = {
    "claude-desktop": {"label": "Claude Desktop", "kind": "json", "path": _claude_desktop, "key": "mcpServers"},
    "claude-code": {"label": "Claude Code", "kind": "cli"},
    "cursor": {"label": "Cursor", "kind": "json", "path": lambda: Path.home() / ".cursor" / "mcp.json", "key": "mcpServers"},
    "vscode": {"label": "VS Code (Copilot agent mode)", "kind": "json", "path": lambda: _vscode_user() / "mcp.json", "key": "servers", "typed": True},
    "windsurf": {"label": "Windsurf", "kind": "json", "path": lambda: Path.home() / ".codeium" / "windsurf" / "mcp_config.json", "key": "mcpServers"},
    "codex": {"label": "OpenAI Codex CLI", "kind": "toml", "path": lambda: Path.home() / ".codex" / "config.toml"},
    "gemini-cli": {"label": "Gemini CLI", "kind": "json", "path": lambda: Path.home() / ".gemini" / "settings.json", "key": "mcpServers"},
    "cline": {
        "label": "Cline", "kind": "json", "key": "mcpServers",
        "path": lambda: _vscode_user() / "globalStorage" / "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json",
    },
    "lm-studio": {"label": "LM Studio", "kind": "json", "path": lambda: Path.home() / ".lmstudio" / "mcp.json", "key": "mcpServers"},
    "zed": {"label": "Zed", "kind": "snippet"},
    "continue": {"label": "Continue", "kind": "snippet"},
    "goose": {"label": "Goose", "kind": "snippet"},
    "chatgpt": {"label": "ChatGPT (developer mode connector)", "kind": "remote"},
    "openai-api": {"label": "OpenAI Responses API", "kind": "remote"},
    "anthropic-api": {"label": "Anthropic Messages API (MCP connector)", "kind": "remote"},
    "gemini-api": {"label": "Gemini API (google-genai)", "kind": "remote"},
    "http": {"label": "Any Streamable HTTP client", "kind": "remote"},
}


def catalog() -> list[dict]:
    items = []
    for client_id, spec in CLIENTS.items():
        path = spec["path"]() if "path" in spec else None
        items.append({
            "id": client_id,
            "label": spec["label"],
            "transport": "http" if spec["kind"] == "remote" else "stdio",
            "installable": spec["kind"] in {"json", "toml"} or (spec["kind"] == "cli" and bool(shutil.which("claude"))),
            "config_path": str(path) if path else None,
            "detected": bool(path and path.parent.exists()) or (client_id == "claude-code" and bool(shutil.which("claude"))),
        })
    return items


def config(client_id: str) -> dict:
    spec = CLIENTS.get(client_id)
    if spec is None:
        raise db.ChronicleError(f"unknown client {client_id!r}; choose from {', '.join(CLIENTS)}", 404)
    command, args = server_command()
    url = remote_url()
    token = os.environ.get("CHRONICLE_TOKEN")
    result = {"id": client_id, "label": spec["label"]}
    kind = spec["kind"]
    if kind == "json":
        body = {spec["key"]: {NAME: _stdio_entry(spec.get("typed", False))}}
        result.update(format="json", path=str(spec["path"]()), snippet=json.dumps(body, indent=2),
                      steps=[f"Merge this into {spec['path']()}, or run: chronicle mcp install {client_id}", f"Restart {spec['label']}."])
    elif kind == "toml":
        result.update(format="toml", path=str(spec["path"]()), snippet=_toml_block(),
                      steps=[f"Append to {spec['path']()}, or run: chronicle mcp install codex", "Start a new codex session; /mcp lists servers."])
    elif kind == "cli":
        line = " ".join(_quote(part) for part in _claude_code_args())
        http_line = f"claude mcp add --transport http {NAME} {url}"
        result.update(format="shell", snippet=line, steps=["Run the command, then /mcp inside Claude Code.", f"Or, against a running server: {http_line}"])
    elif client_id == "zed":
        body = {"context_servers": {NAME: {"source": "custom", "command": command, "args": args, "env": server_env()}}}
        result.update(format="json", path="~/.config/zed/settings.json", snippet=json.dumps(body, indent=2),
                      steps=["Add under context_servers in Zed settings (Zed: Open Settings)."])
    elif client_id == "continue":
        arg_lines = "\n".join(f"      - {json.dumps(arg)}" for arg in args)
        snippet = f"mcpServers:\n  - name: {NAME}\n    command: {json.dumps(command)}\n    args:\n{arg_lines}\n"
        result.update(format="yaml", path="~/.continue/config.yaml", snippet=snippet, steps=["Add to config.yaml. MCP tools work in agent mode."])
    elif client_id == "goose":
        arg_lines = "\n".join(f"      - {json.dumps(arg)}" for arg in args)
        snippet = (
            f"extensions:\n  {NAME}:\n    name: {NAME}\n    type: stdio\n    enabled: true\n    cmd: {json.dumps(command)}\n"
            f"    args:\n{arg_lines}\n    timeout: 300\n"
        )
        result.update(format="yaml", path="~/.config/goose/config.yaml", snippet=snippet, steps=["Add to config.yaml, or run goose configure > Add Extension > Command-line."])
    elif client_id == "chatgpt":
        result.update(format="text", snippet=url, steps=[
            "Run `chronicle serve --host 0.0.0.0` behind an HTTPS tunnel (cloudflared, ngrok, tailscale funnel) and set CHRONICLE_PUBLIC_URL to it.",
            "Set CHRONICLE_TOKEN so the URL carries a secret path: <public-url>/mcp/<token>.",
            "ChatGPT > Settings > Connectors > Advanced > Developer mode, then Create connector with that URL and no authentication.",
            "The search and fetch tools also make Chronicle usable for deep research.",
        ])
    elif client_id == "openai-api":
        snippet = f'''from openai import OpenAI

client = OpenAI()
response = client.responses.create(
    model="gpt-5",
    tools=[{{
        "type": "mcp",
        "server_label": "chronicle",
        "server_url": "{url}",
        "require_approval": "never",
    }}],
    input="Use get_context for the task 'file this week's expense report', then tell me the steps.",
)
print(response.output_text)
'''
        result.update(format="python", snippet=snippet, steps=["The URL must be reachable from OpenAI's servers (public HTTPS)."])
    elif client_id == "anthropic-api":
        auth = f',\n        "authorization_token": "{token}"' if token else ""
        snippet = f'''import anthropic

client = anthropic.Anthropic()
response = client.beta.messages.create(
    model="claude-sonnet-4-5",
    max_tokens=2000,
    betas=["mcp-client-2025-04-04"],
    mcp_servers=[{{
        "type": "url",
        "url": "{url}",
        "name": "chronicle"{auth}
    }}],
    messages=[{{"role": "user", "content": "Call get_context for 'reply to the Acme thread' and summarize what I need."}}],
)
print(response.content)
'''
        result.update(format="python", snippet=snippet, steps=[
            "The URL must be public HTTPS. Pair with the computer-use tool so the agent sees context before it clicks.",
        ])
    elif client_id == "gemini-api":
        snippet = f'''import asyncio
from google import genai
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

server = StdioServerParameters(command={json.dumps(command)}, args={json.dumps(args)})

async def main():
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            client = genai.Client()
            response = await client.aio.models.generate_content(
                model="gemini-2.5-flash",
                contents="Call get_context and tell me what I was doing in the last hour.",
                config=genai.types.GenerateContentConfig(tools=[session]),
            )
            print(response.text)

asyncio.run(main())
'''
        result.update(format="python", snippet=snippet, steps=["pip install google-genai mcp", "The SDK calls MCP tools automatically."])
    else:
        curl = (
            f"curl -s {url} -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' "
            "-d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'"
        )
        result.update(format="shell", snippet=curl, steps=[f"Streamable HTTP endpoint: {url}", "GET returns 405; this server does not push server-initiated messages."])
    return result


def _quote(part: str) -> str:
    return part if re.fullmatch(r"[\w@%+=:,./-]+", part) else json.dumps(part)


def install(client_id: str, path: str | Path | None = None, dry_run: bool = False) -> dict:
    spec = CLIENTS.get(client_id)
    if spec is None:
        raise db.ChronicleError(f"unknown client {client_id!r}", 404)
    kind = spec["kind"]
    if kind == "cli":
        args = _claude_code_args()
        if dry_run:
            return {"client": client_id, "would_run": args}
        if not shutil.which("claude"):
            raise db.ChronicleError("the claude CLI is not on PATH; install Claude Code first", 400)
        subprocess.run(["claude", "mcp", "remove", "--scope", "user", NAME], capture_output=True, text=True, check=False)
        done = subprocess.run(args, capture_output=True, text=True, check=False)
        if done.returncode != 0:
            raise db.ChronicleError((done.stderr or done.stdout).strip() or "claude mcp add failed", 500)
        return {"client": client_id, "ran": args, "output": done.stdout.strip()}
    if kind not in {"json", "toml"}:
        raise db.ChronicleError(f"{spec['label']} is configured by hand; run: chronicle mcp config {client_id}", 400)
    target = Path(path) if path else spec["path"]()
    if kind == "json":
        data = {}
        if target.exists() and target.read_text().strip():
            try:
                data = json.loads(target.read_text())
            except json.JSONDecodeError as exc:
                raise db.ChronicleError(f"{target} is not plain JSON ({exc}); paste the snippet by hand", 400) from exc
        servers = data.setdefault(spec["key"], {})
        servers[NAME] = _stdio_entry(spec.get("typed", False))
        text = json.dumps(data, indent=2) + "\n"
    else:
        existing = target.read_text() if target.exists() else ""
        text = _merge_toml(existing, _toml_block())
    if dry_run:
        return {"client": client_id, "path": str(target), "would_write": text}
    backup = None
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(target.name + ".chronicle-backup")
        shutil.copy2(target, backup)
    target.write_text(text)
    return {"client": client_id, "path": str(target), "backup": str(backup) if backup else None, "restart": spec["label"]}


def _merge_toml(existing: str, block: str) -> str:
    header = re.compile(rf"^\[mcp_servers\.{NAME}(\.[^\]]+)?\]\s*$")
    any_header = re.compile(r"^\[.+\]\s*$")
    out: list[str] = []
    skipping = False
    for line in existing.splitlines():
        if header.match(line.strip()):
            skipping = True
            continue
        if skipping and any_header.match(line.strip()):
            skipping = False
        if not skipping:
            out.append(line)
    body = "\n".join(out).rstrip()
    return (body + "\n\n" if body else "") + block

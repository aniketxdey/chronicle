# Chronicle

Record your computer for a set amount of time, turn the recording into a knowledge graph, and serve it as context to any computer-use agent over MCP.

```
chronicle record 30m          # frames + system events for 30 minutes
chronicle graph show          # apps, pages, files, tickets, people, projects, and how they connect
chronicle context "file the expense report"   # the exact markdown an agent receives
chronicle mcp install cursor  # or claude-desktop, codex, gemini-cli, vscode, windsurf, ...
```

Chronicle is an open-source take on the idea behind screen-memory products like Screenpipe, cut down to the part agents need: **what is on screen, what the user just did, and what all of it relates to**. Everything runs locally and lives in one folder (`~/.chronicle`). There is no account, no cloud, and no telemetry.

## How it works

1. **Record.** A recording session runs for a preset duration (`90s`, `30m`, `2h`, or until stopped). Every few seconds the recorder samples the front app and window, the browser URL and tab title, and a screenshot. It also logs system events: `app_switch`, `window_focus`, `url_change`, `clipboard`, `idle_start`/`idle_end`, and `session_start`/`session_stop`. OCR runs when `tesseract` is installed. Keystrokes are never recorded. Password managers, System Settings, and private browsing windows are skipped. Emails, API keys, JWTs, and card numbers are redacted before anything is saved.
2. **Graph.** When a session ends, Chronicle builds a knowledge graph for that session and rebuilds the one for all history. Nodes are apps, windows, sites, pages, files, tickets (`BILL-142`), PRs, code identifiers, people (from transcripts and names), projects, customers, meetings, and sessions. Edges record `has_window`, `visited`, `shows`, `co_occurs`, `switched_to` and `then` (workflow transitions), `spoke_in`, `discussed`, `copied`, and `captured`. Every node and edge keeps the time spent on it and links back to the frames it came from.
3. **Serve.** `get_context(task)` returns a compact context pack: the current screen, recent activity blocks, recent system events, habitual transitions, the subgraph relevant to the task, and cited evidence. Agents can then drill down with `query_graph`, `get_entity`, `search_history`, and `get_frame` (which can return the screenshot itself).

## Install

Requires Python 3.10+. The only dependencies are FastAPI and uvicorn.

```bash
git clone <this repo> chronicle && cd chronicle
uv venv && uv pip install -e .        # or: python -m venv .venv && .venv/bin/pip install -e .
chronicle doctor                      # checks window, URL, screenshot, clipboard, idle, OCR access
chronicle demo                        # optional: a sample workday to try everything without recording
chronicle serve                       # UI at http://127.0.0.1:3030, MCP at /mcp
```

**macOS:** grant **Screen Recording** and **Accessibility** to whatever runs Chronicle (your terminal, or the MCP host app such as Claude Desktop or Cursor) under System Settings › Privacy & Security. Browser URLs and tab titles also need a one-time Automation prompt per browser. Supported browsers are Safari, Chrome, Arc, Brave, Edge, Vivaldi, and Opera.
**Linux (best effort):** X11 with `xdotool` and `xprop`. For screenshots install one of `grim`, `gnome-screenshot`, `scrot`, or ImageMagick `import`; add `xclip`/`wl-paste` for the clipboard and `xprintidle` for idle time.
**Windows (best effort):** the window, idle time, screenshots, and clipboard are read through user32 and PowerShell.
**OCR (optional):** `brew install tesseract` / `apt install tesseract-ocr`.

## Connect an agent

`chronicle mcp clients` lists every supported client, and `chronicle mcp config <client>` prints the snippet to paste. `chronicle mcp install <client>` merges it into the client's config file and keeps a `.chronicle-backup` copy of the old one. The **Connect** page in the UI does the same.

### Local agents (stdio)

| Client | Command | Config written |
| --- | --- | --- |
| Claude Desktop | `chronicle mcp install claude-desktop` | `claude_desktop_config.json` → `mcpServers` |
| Claude Code | `chronicle mcp install claude-code` | runs `claude mcp add --scope user chronicle -- …` |
| Cursor | `chronicle mcp install cursor` | `~/.cursor/mcp.json` |
| VS Code (Copilot agent mode) | `chronicle mcp install vscode` | user `mcp.json` → `servers` |
| Windsurf | `chronicle mcp install windsurf` | `~/.codeium/windsurf/mcp_config.json` |
| OpenAI Codex CLI | `chronicle mcp install codex` | `~/.codex/config.toml` → `[mcp_servers.chronicle]` |
| Gemini CLI | `chronicle mcp install gemini-cli` | `~/.gemini/settings.json` |
| Cline, LM Studio | `chronicle mcp install cline` / `lm-studio` | their `mcpServers` JSON |
| Zed, Continue, Goose | `chronicle mcp config zed` / `continue` / `goose` | paste the snippet |

The generic stdio entry is:

```json
{ "mcpServers": { "chronicle": { "command": "chronicle", "args": ["mcp"] } } }
```

A stdio server also records: whichever Chronicle process holds `~/.chronicle/capture.lock` captures while a session is active. An agent can therefore call `start_recording` with no `chronicle serve` running. Pass `--no-record` to opt out.

### Remote agents and model APIs (Streamable HTTP)

`chronicle serve` exposes MCP at `POST /mcp` (protocol versions 2025-06-18, 2025-03-26, and 2024-11-05). Hosted agents need a public HTTPS URL, so run it behind a tunnel and set a token:

```bash
export CHRONICLE_TOKEN=$(openssl rand -hex 16)
export CHRONICLE_PUBLIC_URL=https://<your-tunnel-host>
chronicle serve &
cloudflared tunnel --url http://127.0.0.1:3030        # or ngrok, tailscale funnel
# MCP URL: $CHRONICLE_PUBLIC_URL/mcp/$CHRONICLE_TOKEN  (or /mcp with "Authorization: Bearer $CHRONICLE_TOKEN")
```

When a token is set, every route except the UI shell and `/health` requires it. Open the UI once with `?token=…` and it remembers the token. Requests with a non-local `Origin` header are rejected.

- **ChatGPT** (Settings › Connectors › Developer mode › Create): paste the `/mcp/<token>` URL with no authentication. The `search` and `fetch` tools follow the connector and deep-research result shapes.
- **OpenAI Responses API**: `tools=[{"type": "mcp", "server_label": "chronicle", "server_url": URL, "require_approval": "never"}]`. See [`examples/openai_responses.py`](examples/openai_responses.py).
- **Anthropic Messages API** (MCP connector, beta `mcp-client-2025-04-04`): `mcp_servers=[{"type": "url", "url": URL, "name": "chronicle"}]`, alongside the computer-use tool. See [`examples/anthropic_computer_use.py`](examples/anthropic_computer_use.py).
- **Gemini API** (`google-genai`): pass an MCP `ClientSession` connected to `chronicle mcp` as a tool. Run `chronicle mcp config gemini-api` for the snippet.
- **Anything else**: `chronicle mcp config http` prints a `curl` call.

The `chronicle mcp config <client>` snippets for providers are starting points. Model names and beta flags change, so check each provider's current MCP docs.

## MCP surface

| Tool | What it does |
| --- | --- |
| `get_context` | Context pack for a task: screen now, recent activity, events, transitions, relevant subgraph, evidence |
| `search_history` | Full-text search over screen text, events, transcripts, memories |
| `query_graph` / `get_entity` / `graph_summary` | Find entities, walk neighbors with evidence frames, see counts and top transitions |
| `get_frame` | One frame with nearby events and audio; `include_image=true` attaches the screenshot |
| `start_recording` / `stop_recording` / `list_sessions` | Record a demonstration for a preset duration, from the agent itself |
| `rebuild_graph`, `activity_summary`, `list_meetings` | Maintenance and summaries |
| `tune_graph` | Optional LLM pass that patches the graph with entities, typed relations, and activity summaries (see below) |
| `search` / `fetch` | ChatGPT connector and deep-research compatible |

Resources: `chronicle://context/current`, `chronicle://graph/summary`, `chronicle://sessions`, and the templates `chronicle://context/session/{id}` and `chronicle://frame/{id}`.
Prompts: `use_my_computer_context(task)` and `learn_from_recording(session_id)`. The second turns a recorded demonstration into steps an agent can repeat.

## Teach an agent by showing it

```bash
chronicle record 5m --label "file an expense report"   # do the task once while recording
chronicle context --session 1                          # see what the agent will learn
```

Then ask your agent: *"Use the learn_from_recording prompt for session 1 and do it again for this receipt."*

## CLI

```
chronicle serve [--host --port]          web UI + REST API + HTTP MCP
chronicle record <duration> [--label --interval --detach]
chronicle stop [id] · chronicle sessions
chronicle graph build|show|find|node|export [--session N]
chronicle graph tune [--session N --rounds 2 --force --reset]
chronicle context [task] [--minutes N --session N --json]
chronicle mcp [stdio|clients|config <client>|install <client> [--path --dry-run]]
chronicle search <q> · chronicle ask <question> · chronicle status · chronicle doctor · chronicle demo
```

The REST API is documented at `http://127.0.0.1:3030/docs`. Highlights: `POST /sessions {duration, label}`, `GET /graph`, `GET /graph/node?id=`, `GET /context?task=&format=markdown`, and `POST /add` to push frames, events, or transcripts from your own capture tools (for example a Whisper pipeline for audio).

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `CHRONICLE_HOME` | `~/.chronicle` | Database, screenshots, pipes, lock file |
| `CHRONICLE_TOKEN` | unset | Require this token on the HTTP API and MCP |
| `CHRONICLE_PUBLIC_URL` | `http://127.0.0.1:3030` | Base URL used in links and remote client snippets |
| `CHRONICLE_DEMO=1` | off | Load the sample workday into an empty database on start |
| `CHRONICLE_DISABLE_CAPTURE=1` | off | Never record from this process |
| `CHRONICLE_LLM` | unset (off) | `openai` turns on LLM graph tuning |
| `CHRONICLE_LLM_MODEL` | `gpt-4.1-mini` | Model used for tuning |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | unset / OpenAI | Credentials and endpoint for the `openai` provider |

Capture interval, idle threshold, screenshots, OCR, clipboard, redaction, and retention are set in the UI (Settings) or through `POST /settings`.

## LLM graph tuning (optional)

The heuristic graph knows apps, windows, pages, tickets, files, and identifiers. It does not know that "Crusoe" is a company on the Forbes AI 50 list. An optional LLM pass fixes that. It reads the graph and its evidence (window titles, URLs with their search query, redacted OCR snippets, dwell seconds) and replies with patch operations through strict function calls: `add_entity`, `retype_node`, `rename_node`, `merge_nodes`, `add_relation` (`is_a`, `part_of`, `works_on`, `competes_with`, `located_in`, `researching`, `mentions`, `listed_in`, `uses`, `related_to`), `add_trait`, `set_activity`, and `drop_node`.

It is off by default, and Chronicle runs fully offline without it. To turn it on:

```bash
uv pip install -e ".[llm]"          # openai SDK + python-dotenv; both optional, urllib is the fallback
cat >> .env <<'EOF'
CHRONICLE_LLM=openai
OPENAI_API_KEY=sk-...
# CHRONICLE_LLM_MODEL=gpt-4.1-mini   # default
# OPENAI_BASE_URL=https://api.openai.com/v1
EOF
chronicle graph tune --session 1    # or wait: every recording is tuned in the background when it ends
chronicle graph show --session 1    # activities and typed relations are listed first
```

`.env` is read from `CHRONICLE_HOME` and from the current directory, and it never overrides variables that are already set. `CHRONICLE_LLM` and `CHRONICLE_LLM_MODEL` can also be stored as the `llm_provider` and `llm_model` settings (`POST /settings`).

How it behaves:
- Each run makes up to `--rounds` passes (default 2) over batches of the heaviest nodes. A pass ends early when the model proposes nothing new. Every op is checked against the schema and against the node ids that exist at that point. Rejected ops are fed back to the model in the next round.
- Accepted ops are stored in `kg_llm_patches` and replayed in order after every `graph build`, so tuning survives rebuilds. Session patches are also replayed into the `all` scope. `chronicle graph tune --reset` (or `tune_graph` with `reset=true`) removes them.
- LLM-made nodes and edges carry `source: "llm"`, and relations carry a `confidence` and an evidence quote in `props`. `graph_summary` returns them under `llm`, and `get_context` shows activities first, then relations like `Crusoe —is_a→ company`.
- Responses are cached by a content hash of the prompt and model. A scope whose graph has not changed since its last tune is skipped unless you pass `--force`.
- Rate limits, timeouts, and API errors are retried, then reported in the result and in the `last_llm_error` setting. They never stop a recording.
- Other providers plug in through `chronicle.llm.register(name, factory)`. A provider only needs `call_tools(system, user, tools) -> [{"name", "arguments"}]`.

## Development

```bash
uv pip install -e ".[dev]"
pytest
python examples/stdio_client.py "finish the Acme export"
npx @modelcontextprotocol/inspector --cli chronicle mcp --method tools/list
```

Layout: `chronicle/probe.py` reads the machine, `capture.py` runs the recorder, and `sessions.py` manages timed recordings. `graph.py` builds the knowledge graph, `tuner.py` runs the optional LLM patch pass over it through the providers in `llm.py`, and `context.py` builds the context packs. `mcp_server.py` holds the JSON-RPC core and the stdio transport, `clients.py` holds per-client setup, `main.py` serves HTTP, and `web/` holds the UI.

## License

MIT

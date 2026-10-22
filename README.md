# Chronicle

<!-- mcp-name: io.github.aniketxdey/chronicle -->

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
2. **Graph.** Each session gets its own graph shard, extended incrementally while recording and rebuilt once when it ends; frames captured outside a session go into hourly shards. The graph for all history is a SQL aggregation of the shards in which older shards count less (a 72-hour half-life by default), so it tracks current work and deleting a session just drops its shard. Nodes are apps, windows, sites, pages, search queries, topics (keyphrases such as "Forbes AI 50", scored across a session's titles and screen text by frequency, spread, casing, position, and dwell time), files, tickets (`BILL-142`), PRs, code identifiers, people (from transcripts and names), projects, customers, meetings, and sessions. Edges record `has_window`, `visited`, `shows`, `searched`, `about`, `co_occurs`, `switched_to` and `then` (workflow transitions), `spoke_in`, `discussed`, `copied`, and `captured`. Every node and edge keeps the time spent on it and links back to the frames it came from.
3. **Serve.** `get_context(task)` returns a compact context pack: the current screen, recent activity blocks, recent system events, habitual transitions, the subgraph relevant to the task, and cited evidence. Agents can then drill down with `query_graph`, `get_entity`, `search_history`, and `get_frame` (which can return the screenshot itself).

## Install

Requires Python 3.10+. The only required dependencies are FastAPI and uvicorn. The `graph` extra adds scikit-learn and scipy for better duplicate merging.

```bash
uv tool install "chronicle-context[graph]"   # or: pipx install "chronicle-context[graph]"
chronicle doctor                      # checks window, URL, screenshot, clipboard, idle, OCR access
chronicle demo                        # optional: a sample workday to try everything without recording
chronicle serve                       # UI at http://127.0.0.1:3030, MCP at /mcp
```

Or run it without installing: `uvx chronicle-context mcp`. To work on the code, run `git clone https://github.com/aniketxdey/chronicle && cd chronicle && uv pip install -e ".[dev,graph]"`.

**macOS:** grant **Screen Recording** and **Accessibility** to whatever runs Chronicle (your terminal, or the MCP host app such as Claude Desktop or Cursor) under System Settings › Privacy & Security. Browser URLs and tab titles also need a one-time Automation prompt per browser. Supported browsers are Safari, Chrome, Arc, Brave, Edge, Vivaldi, and Opera.
**Linux (best effort):** X11 with `xdotool` and `xprop`. For screenshots install one of `grim`, `gnome-screenshot`, `scrot`, or ImageMagick `import`; add `xclip`/`wl-paste` for the clipboard and `xprintidle` for idle time.
**Windows (best effort):** the window, idle time, screenshots, and clipboard are read through user32 and PowerShell.
**OCR (optional):** `brew install tesseract` / `apt install tesseract-ocr`.

## Connect an agent

### Packaged installs

These need [uv](https://docs.astral.sh/uv/) on the `PATH`. Each one runs `uvx chronicle-context mcp` and ships instructions telling the model to call `get_context` first.

- **Claude Code, Claude Desktop, Cowork:** `claude plugin marketplace add aniketxdey/chronicle`, then `claude plugin install chronicle@chronicle`.
- **Gemini CLI:** `gemini extensions install https://github.com/aniketxdey/chronicle`.
- **MCP Registry clients:** search for `io.github.aniketxdey/chronicle`.

[docs/PUBLISHING.md](docs/PUBLISHING.md) covers how each listing is released. [PRIVACY.md](PRIVACY.md) covers what is stored and where it goes.

### Manual setup

`chronicle mcp clients` lists every supported client, and `chronicle mcp config <client>` prints the snippet to paste. `chronicle mcp install <client>` merges it into the client's config file and keeps a `.chronicle-backup` copy of the old one. The **Connect** page in the UI does the same.

#### Local agents (stdio)

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

#### Remote agents and model APIs (Streamable HTTP)

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
| `list_tasks` | Timeline of tasks the user worked on, segmented from activity and grouped by topic |
| `get_frame` | One frame with nearby events and audio; `include_image=true` attaches the screenshot |
| `start_recording` / `stop_recording` / `list_sessions` | Record a demonstration for a preset duration, from the agent itself |
| `rebuild_graph`, `activity_summary`, `list_meetings` | Maintenance and summaries |
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
chronicle graph build|show|find|node|export [--session N]   (build --full re-extracts every shard)
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
| `CHRONICLE_HALF_LIFE_HOURS` | `72` | Half-life for ranking the all-history graph; `0` or `off` disables decay. The `graph_half_life_hours` setting overrides it |

Capture interval, idle threshold, screenshots, OCR, clipboard, redaction, and retention are set in the UI (Settings) or through `POST /settings`, which also accepts `graph_half_life_hours`.

## Development

```bash
uv pip install -e ".[dev]"
pytest
python examples/stdio_client.py "finish the Acme export"
npx @modelcontextprotocol/inspector --cli chronicle mcp --method tools/list
```

Layout: `chronicle/probe.py` reads the machine, `capture.py` runs the recorder, and `sessions.py` manages timed recordings. `graph.py` builds the knowledge graph and `context.py` builds the context packs. `mcp_server.py` holds the JSON-RPC core and the stdio transport, `clients.py` holds per-client setup, `main.py` serves HTTP, and `web/` holds the UI.

## License

MIT

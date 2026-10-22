# Publishing Chronicle

Chronicle ships as one PyPI package, `chronicle-context`, and every listing points at it through `uvx chronicle-context mcp`. The PyPI release has to go out first, then each directory is a separate submission.

| Channel | What users get | Files in this repo |
| --- | --- | --- |
| PyPI | `uvx chronicle-context mcp`, `pip install chronicle-context` | `pyproject.toml` |
| MCP Registry | Discovery in clients that read the registry (VS Code, Copilot, Windows, aggregators) | `server.json`, `mcp-name` line in `README.md` |
| Claude directory and plugin marketplace | One-click install in Claude Desktop, Cowork, and Claude Code | `.claude-plugin/marketplace.json`, `plugins/chronicle/` |
| Gemini CLI extension gallery | `gemini extensions install https://github.com/aniketxdey/chronicle` | `gemini-extension.json`, `GEMINI.md` |
| ChatGPT developer mode | A custom connector, per user | none; uses `chronicle serve` and a tunnel |
| Windows On-Device Agent Registry | Windows agents can find the server | `server.json` |

## One-time setup

1. **Create the GitHub repo** `aniketxdey/chronicle` (public), then push `main` to it:
   ```bash
   git remote add origin git@github.com:aniketxdey/chronicle.git
   git push -u origin main
   ```
2. **PyPI trusted publishing.** On pypi.org, go to Account › Publishing › Add a pending publisher. Use project `chronicle-context`, owner `aniketxdey`, repo `chronicle`, workflow `release.yml`, environment `pypi`. In the GitHub repo, go to Settings › Environments and create an environment named `pypi`. No API token is needed.
3. **MCP Registry.** No setup is needed. The release workflow logs in with GitHub OIDC, which owns the `io.github.aniketxdey/*` namespace.
4. **Repo topics.** Add `gemini-cli-extension`, `mcp`, `mcp-server`, `claude-plugin`, and `computer-use` under About › Topics. The first one is what puts Chronicle in the Gemini gallery.

## Each release

1. Set the same version in `pyproject.toml`, `chronicle/__init__.py`, `server.json` (twice), `plugins/chronicle/.claude-plugin/plugin.json`, and `gemini-extension.json`. `tests/test_packaging.py` fails if they differ.
2. Run `pytest`, commit, then tag and push:
   ```bash
   git tag v0.2.0 && git push origin v0.2.0
   ```
3. `.github/workflows/release.yml` runs the tests, builds, publishes to PyPI, waits for PyPI to serve the version, publishes `server.json` to the MCP Registry, and creates a GitHub release.
4. Check `uvx chronicle-context@0.2.0 --help` and `curl "https://registry.modelcontextprotocol.io/v0/servers?search=chronicle"`.

To publish by hand instead: run `uv build && uv publish`, then `brew install mcp-publisher && mcp-publisher login github && mcp-publisher publish`.

## Claude

Anthropic lists local servers as plugins. Users can install the plugin today without any review:

```bash
claude plugin marketplace add aniketxdey/chronicle
claude plugin install chronicle@chronicle
```

In Claude Desktop, add the marketplace `aniketxdey/chronicle` from the plugins settings.

To get into the official directory, submit at [claude.ai/directory/manage](https://claude.ai/directory/manage) (Team and Enterprise owners can use [claude.ai/admin-settings/directory/submit](https://claude.ai/admin-settings/directory/submit)). The form asks for:

- the repo URL and plugin path (`plugins/chronicle`);
- a short description and use cases. Use the README opening and the three examples under "Connect an agent";
- the privacy policy: `https://github.com/aniketxdey/chronicle/blob/main/PRIVACY.md`;
- an icon: `docs/assets/icon.png` (512×512);
- a support contact: GitHub issues;
- test instructions. Reviewers can run `chronicle demo` to load a sample workday, so they don't need to record anything.

Review checks that every tool has a `title` and a `readOnlyHint` or `destructiveHint`. `tests/test_packaging.py` enforces this.

Run `claude plugin validate .` before submitting.

## Gemini CLI

Once the repo is public with the `gemini-cli-extension` topic, the gallery crawler picks it up within about a day. Users can install it directly at any time:

```bash
gemini extensions install https://github.com/aniketxdey/chronicle
```

## ChatGPT

OpenAI's Plugins Directory only accepts apps that run as a public HTTPS server. They also need a verified developer identity, a CSP, domain verification at `/.well-known/openai-apps-challenge`, and reviewer test credentials. Chronicle's data lives on the user's own machine, so there is no single server to submit. The supported route today is developer mode, which each user sets up for themselves:

1. `export CHRONICLE_TOKEN=$(openssl rand -hex 16)`, then `chronicle serve`.
2. `cloudflared tunnel --url http://127.0.0.1:3030`.
3. In ChatGPT settings, turn on developer mode and create a connector. Paste `https://<tunnel>/mcp/<token>` with no authentication.

A directory listing would need a hosted relay. That means a small public service with OAuth, where each user's machine keeps an outbound connection and ChatGPT reaches it through the relay. This is a product decision (it means running a service that sees user traffic) and is not built.

## Windows

Windows agents read the On-Device Agent Registry, which is managed with `odr.exe`. Once the server is in the MCP Registry, register it with `odr.exe` using `server.json`, following Microsoft's current "MCP on Windows" docs because the tool is still in preview. Capture on Windows is best effort, so test `chronicle doctor` there first.

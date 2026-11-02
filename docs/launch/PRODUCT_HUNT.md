# Product Hunt launch kit

Everything the Product Hunt form asks for, drafted from the README. Launch only after the release is live, because the listing's install commands need `chronicle-context` on PyPI and the site needs to be deployed. Check field limits against the form on the day, since Product Hunt changes them occasionally.

## Listing

| Field | Value |
| --- | --- |
| Name | Chronicle |
| Tagline (54/60) | Your screen history as a knowledge graph for AI agents |
| Website | https://aniketxdey.github.io/chronicle/ |
| GitHub | https://github.com/aniketxdey/chronicle |
| Pricing | Free (open source, MIT) |
| Topics | Developer Tools, Artificial Intelligence, Open Source, Productivity, Mac |
| Thumbnail | `docs/assets/icon.png` (512×512) |
| Gallery | `docs/launch/gallery/1-graph.png` to `5-integrations.png` (1270×760, in that order) |

**Description (253/260)**

> Chronicle records your screen and system events locally, builds a knowledge graph of the apps, pages, files, tickets, and people you work with, and serves it over MCP to Claude, ChatGPT, Cursor, Gemini, and others. Open source, no account, no telemetry.

Gallery captions, if the form asks for them:

1. The knowledge graph for a sample workday, with every node linked to the frames it came from.
2. Context files for eight kinds of work, ready to hand to any agent or project.
3. A timeline of frames, system events, and tasks segmented by topic.
4. Loops that repeat across recordings, which an agent can learn and replay.
5. One-command setup for each MCP client.

Every image shows the synthetic sample workday from `chronicle demo`. To regenerate them after a UI change, refresh the screenshots (see `docs/assets/README.md`) and run `python3 docs/launch/make_gallery.py`.

## Maker comment

Post this as the first comment when the launch goes live.

> Hi Product Hunt! Agents start every task knowing nothing about your computer. They don't know which ticket you were on, which file you had open, or what you looked up ten minutes ago, so you end up pasting that context in by hand. I built Chronicle to keep that memory locally and let the agent ask for it.
>
> Chronicle runs on your machine. It samples the front window, the browser URL, screen text, and a screenshot every few seconds, plus app switches and clipboard copies, and turns that into a knowledge graph of what you worked on. Agents connect over MCP and call `get_context` with the task, so they see the file you had open, the PR waiting on review, and the page you read an hour ago, each with the frame it came from.
>
> A few things I cared about:
> - It's local. Everything lives in `~/.chronicle`, with no account and no telemetry. Keystrokes are never recorded, password managers and private windows are skipped, and keys, emails, and card numbers are redacted before they're written.
> - It works with the agent you already use: Claude Code and Desktop, ChatGPT, Cursor, VS Code, Codex, Gemini CLI, Windsurf, and others.
> - You can teach by showing. Record a task once and the agent gets the steps in order.
>
> Try it without recording anything: `uv tool install "chronicle-context[graph]" && chronicle demo`.
>
> macOS is the main platform; Linux and Windows capture are best effort. I'd love to hear what you'd want an agent to remember.

A short, true story of why you built it usually works better than the general framing in the first paragraph, if you have one.

## Before launch day

- [ ] `v0.2.0` (or later) is on PyPI and the MCP Registry, and `uvx chronicle-context@0.2.0 --help` works on a clean machine.
- [ ] The Pages site is live and every link on it resolves.
- [ ] The Claude plugin and Gemini extension install from the commands on the site.
- [ ] Schedule the launch in the Product Hunt form for 12:01 AM Pacific, so it gets the full day. Tuesday to Thursday has the most traffic and the most competition.
- [ ] Optional: a 30 to 60 second video of `chronicle demo`, `chronicle context`, and an agent calling `get_context`. Product Hunt takes a YouTube link.
- [ ] Have someone else run the quick start on a fresh Mac the day before.

## On the day

- [ ] Post the maker comment as soon as the listing is live.
- [ ] Answer every comment, especially privacy questions; [PRIVACY.md](../../PRIVACY.md) has the specifics.
- [ ] Share the listing link (not a request for upvotes, which Product Hunt penalizes) on X, LinkedIn, Hacker News (Show HN, linking the GitHub repo), and relevant subreddits such as r/ClaudeAI and r/LocalLLaMA.

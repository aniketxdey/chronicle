# Privacy policy

Last updated: October 21, 2026

Chronicle is open-source software that runs on your computer. Nobody operates a Chronicle service, so there is no company or server that receives your data. This policy describes what the software does with data on your machine and where data goes when you connect it to an agent.

## What Chronicle collects

Only while a recording session is active, which you or an agent you have connected start explicitly for a set duration, Chronicle samples every few seconds:

- the name of the frontmost app and the title of its focused window;
- the URL and tab title of the active browser tab, for supported browsers;
- a screenshot, and the text on screen, read from the macOS Accessibility tree or by OCR;
- system events: app switches, window focus changes, URL changes, clipboard changes, idle start and end, and session start and stop.

Chronicle never records keystrokes. It skips password managers, System Settings, and private or incognito browser windows. Emails, API keys, JWTs, and card numbers are redacted before anything is saved. You can turn screenshots, OCR, the clipboard, and redaction rules on or off in Settings.

## Where it is stored

Everything is stored in one folder on your computer, `~/.chronicle` by default (set `CHRONICLE_HOME` to change it): a SQLite database, screenshots, and a lock file. Data older than the retention period (30 days by default, set in Settings) is pruned automatically. Deleting a session in the UI or API removes its frames, events, and graph shard. Deleting the folder removes everything.

## Where it goes

Chronicle sends no telemetry, analytics, or crash reports, and needs no account.

Data leaves the folder only through tools you connect:

- **MCP clients.** An agent you connect (Claude, ChatGPT, Gemini, Cursor, or another client) receives the tool results it asks for, such as a context pack, search results, or a screenshot. That client and its model provider then handle the data under their own privacy policies. Chronicle has no control over what they keep.
- **The HTTP server.** `chronicle serve` listens on `127.0.0.1` unless you pass another `--host`. If you expose it through a tunnel, anyone with the URL and your token can read your data. Set `CHRONICLE_TOKEN` before doing so.
- **Optional model passes.** Features that send window titles or screen text to a language model are off by default and run only against a provider and API key you configure yourself.

## Children

Chronicle is not directed at children and collects nothing about anyone other than the person running it, except what appears on their screen.

## Changes and contact

Changes to this policy are made in this file, and its history is public in the repository. Questions and reports go to [GitHub issues](https://github.com/aniketxdey/chronicle/issues).

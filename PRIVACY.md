# Privacy policy

Last updated: November 5, 2026

Chronicle is open-source software that runs on your computer. Nobody operates a Chronicle service, so no Chronicle company or server receives your data. This policy describes what the software stores on your computer and when data leaves it.

## Summary

- Chronicle stores everything in one folder on your computer.
- Chronicle sends no telemetry, analytics, or crash reports, and needs no account.
- Data leaves that folder only through the agents, models, tunnels, and cloud sources that you set up.

## Data Chronicle collects

### During a recording

Chronicle records only during a session. You start a session, or an agent that you connected starts one with `start_recording`. A session runs for a set time or until you stop it.

During a session, Chronicle collects:

- The name of the front app and the title of its focused window
- The URL and tab title of the active browser tab, in supported browsers
- A screenshot of the main display
- The text on screen, from the macOS Accessibility tree or from OCR
- System events: app switches, window focus changes, URL changes, idle start and end, and session start and stop
- Clipboard changes, up to 2,000 characters each

Chronicle never records keystrokes. Chronicle skips these apps and windows:

- Password managers: 1Password, Bitwarden, Dashlane, KeePassXC, LastPass, and Keychain Access
- System Settings, the login window, and the screen saver
- Windows with `password`, `private browsing`, `incognito`, or `inprivate` in the title

### From sources you turn on

**Privacy & Sources** controls what Chronicle reads besides recordings. Chronicle reads nothing from a source until you turn the source on.

| Source | What Chronicle reads |
| --- | --- |
| Apple Mail, Windows Mail import | Senders, subjects, and previews |
| Apple Calendar, Google Calendar, Outlook calendar | Events and guests |
| Outlook mail | Mail that you can read in Outlook |
| iMessage, WhatsApp | Messages in the chats you choose |
| Slack, Notion | Content that you can read with your sign-in |
| Folders | Files in the folders you add |
| Browser history | Page titles and addresses |
| Claude Code, Codex, Cursor, Gemini CLI | Your conversations in their local logs |
| ChatGPT, Claude, and Gemini exports | The data exports that you import |

Cloud sources use read-only access where the provider offers it, and they need your sign-in.

## Limits of redaction

Redaction is on by default, and Settings can turn it off. Before Chronicle saves text, redaction replaces email addresses, JWTs, and numbers of 13 to 19 digits. It also replaces these API key formats: `sk-`, `ghp_`, `github_pat_`, `xox`, and `AKIA`.

**Redaction applies to text only.** Screenshots keep whatever was on screen, including anything that redaction removes from text. Redaction also misses a secret in any other format. Settings can turn off screenshots, OCR, and the clipboard.

## Where Chronicle stores data

Chronicle stores data in `~/.chronicle` by default. `CHRONICLE_HOME` changes that folder. The folder holds a SQLite database, screenshots, a lock file, and your chats with Chronicle. Chronicle doesn't encrypt the folder, so protect it as you protect your other files.

Chronicle stores API keys that you add in Settings, and sign-in tokens for cloud sources, in the macOS Keychain. On other systems, these secrets are in `secrets.json` in the same folder, readable only by your user account. A key in a `.env` file stays in that file.

## How long Chronicle keeps data

Chronicle deletes frames, events, and source items older than the retention period. The default is 30 days. In Settings, 0 keeps everything.

| Action | Result |
| --- | --- |
| Delete a recording | Removes its frames, events, graph, and context files |
| **Forget** on a source | Removes everything that source read |
| Delete a harness or workflow | Removes its text. Deleting a recording doesn't, because each keeps its own copy |
| Delete the folder | Removes everything in it, except secrets in the macOS Keychain |

These deletions are permanent, and Chronicle has no undo. Data that already left your computer stays with its recipient.

## When data leaves your computer

**MCP clients.** An agent that you connect receives the tool results it requests, such as a context pack, search results, or a screenshot. The client and its model provider handle that data under their own privacy policies. A connected client can read all your harnesses. Chronicle has no control over what a client keeps.

**Models.** A model that you set up receives window titles, URLs, system events, and redacted screen text. Models write the graph refinements, context files, workflows, and chat answers. The Knowledge chat also sends matching items from sources that you turned on.

| Your setup | Where the data goes |
| --- | --- |
| A key for OpenAI, Anthropic, Google Gemini, or OpenRouter | That provider, under its own privacy policy |
| A local model | Stays on your computer, unless your endpoint is remote |
| No model, or `CHRONICLE_LLM=off` | Nowhere |

**The HTTP server.** `chronicle serve` listens on `127.0.0.1` unless you pass another `--host`. If you expose the server through a tunnel, anyone with the URL and your token can read your data. Set `CHRONICLE_TOKEN` first.

**Cloud sources.** A cloud source talks directly to its provider, which sees the requests that Chronicle makes.

## Other people's data

Chronicle targets only the person who runs it. It can still collect other people's data from that person's screen and sources. That data can include messages, names, and email addresses. You are responsible for following the laws and rules that apply where you record.

## Children

Chronicle isn't directed at children.

## Changes and contact

This file is the policy, and its history is public in the repository. Send questions and reports to [GitHub issues](https://github.com/aniketxdey/chronicle/issues).

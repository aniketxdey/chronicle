"""Load a full recorded workday so the app is usable before live capture."""

from __future__ import annotations

import os
from datetime import datetime, timedelta

from chronicle import content, db
from chronicle.timeutil import utc_iso

APP_COLOR = {
    "Slack": "#e01e5a",
    "Zoom": "#2d8cff",
    "Linear": "#5e6ad2",
    "Cursor": "#8a5a44",
    "Chrome": "#c47b2b",
    "Figma": "#f24e1e",
    "Gmail": "#c23b22",
    "Notion": "#44403c",
    "Terminal": "#3f6b4a",
}


def _day():
    zone = db.local_zone()
    now = datetime.now(zone)
    if now.hour >= 18:
        return now.date()
    return (now - timedelta(days=1)).date()


def clock(hour: int, minute: int) -> str:
    zone = db.local_zone()
    day = _day()
    target = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    return utc_iso(target)


def _svg(app: str, title: str, url: str | None, lines: list[str]) -> str:
    def esc(value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    accent = APP_COLOR.get(app, "#44403c")
    rendered = []
    y = 156
    for line in lines[:9]:
        rendered.append(
            f'<text x="64" y="{y}" fill="#241c16" font-size="22" font-family="Georgia, serif">{esc(line)}</text>'
        )
        y += 36
    url_node = ""
    if url:
        url_node = (
            f'<text x="64" y="118" fill="#6b6256" font-size="14" font-family="ui-monospace, monospace">{esc(url[:88])}</text>'
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800" viewBox="0 0 1280 800">
  <rect width="1280" height="800" fill="#ebe4d6"/>
  <rect width="1280" height="84" fill="{accent}"/>
  <circle cx="36" cy="42" r="7" fill="#fffaf3"/>
  <text x="56" y="50" fill="#fffaf3" font-size="28" font-family="Georgia, serif">{esc(app)}</text>
  <text x="280" y="50" fill="#fffaf3" font-size="20" font-family="Georgia, serif">{esc(title)}</text>
  {url_node}
  <rect x="32" y="132" width="1216" height="636" rx="14" fill="#f6f1e6"/>
  {"".join(rendered)}
</svg>
"""


def _elements(pairs: list[tuple[str, str]]) -> list[dict]:
    elements = []
    y = 150
    for role, text in pairs:
        elements.append({"role": role, "text": text, "bounds": {"x": 64, "y": y, "width": 640, "height": 28}})
        y += 36
    return elements


SCENES = [
    {
        "h": 9, "m": 2, "dur": 14, "app": "Slack", "window": "#eng-standup",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing", "person:luis", "person:priya"],
        "lines": ["#eng-standup", "Maya: BILL-142 CSV columns for Acme", "Luis: invoice_id, amount_usd, settled_at", "Priya: no customer emails in the export"],
        "elements": [("list", "#eng-standup"), ("text", "Maya Chen"), ("text", "Luis Ortega"), ("button", "Reply")],
        "text": """Slack #eng-standup
Maya Chen 9:02 AM
Yesterday I closed the export pagination bug. Today I am on BILL-142, the CSV columns for Acme.
Luis Ortega 9:04 AM
Finance wants invoice_id, amount_usd, and settled_at. Due Friday.
Priya Shah 9:06 AM
I will review PR 882 after standup. Do not include customer emails in the export.
Priya pasted jamie.ross@acme.example and then deleted it.""",
    },
    {
        "h": 9, "m": 18, "dur": 32, "app": "Zoom", "window": "Sprint planning",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["Sprint planning", "Maya, Luis, Priya", "Topic: Acme billing export", "Decision: freeze three columns"],
        "elements": [("button", "Mute"), ("button", "Share screen"), ("text", "Sprint planning")],
        "text": """Zoom Sprint planning
Participants: Maya Chen, Luis Ortega, Priya Shah
Agenda: Acme billing export, BILL-142, PR 882.
Maya shared the column list. The group agreed to freeze the Acme export columns today.
No customer emails in the file. Pagination test stays in the PR.""",
    },
    {
        "h": 9, "m": 52, "dur": 18, "app": "Linear", "window": "BILL-142 CSV export for Acme",
        "trigger": "window_focus", "source": "accessibility",
        "tags": ["project:billing", "customer:acme"],
        "lines": ["BILL-142", "CSV export for Acme", "Due Friday", "Columns: invoice_id, amount_usd, settled_at"],
        "elements": [("heading", "BILL-142"), ("text", "CSV export for Acme"), ("button", "Change status")],
        "text": """Linear BILL-142 CSV export for Acme
Status: In Progress. Assignee: Maya Chen. Due Friday.
Description: Acme export columns are invoice_id, amount_usd, and settled_at.
Acceptance: file has those three headers, no email column, pagination test passes.
Project: Billing.""",
    },
    {
        "h": 10, "m": 12, "dur": 36, "app": "Cursor", "window": "export.ts — billing",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["export.ts", "const COLUMNS = [", "  'invoice_id',", "  'amount_usd',", "  'settled_at',", "]"],
        "elements": [("text", "export.ts"), ("text", "COLUMNS"), ("button", "Run test")],
        "text": """Cursor export.ts — billing
Writing the Acme CSV exporter.
const COLUMNS = ['invoice_id', 'amount_usd', 'settled_at']
Commented out customer_email so it cannot ship in the file.
Function buildExportRow maps a ledger entry onto those three fields.""",
    },
    {
        "h": 10, "m": 50, "dur": 22, "app": "Chrome", "window": "PR 882 · northwind/billing",
        "url": "https://github.com/northwind/billing/pull/882",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing", "person:priya"],
        "lines": ["northwind/billing  PR 882", "Review: keep the pagination test", "Priya: drop the email field", "Checks: unit tests queued"],
        "elements": [("link", "Files changed"), ("text", "Priya Shah reviewed"), ("button", "Comment")],
        "text": """Chrome GitHub pull request 882 northwind/billing
Title: Acme CSV export columns.
Priya Shah left a review: keep the pagination test, and drop any customer email field.
Maya replied that invoice_id, amount_usd, and settled_at are the frozen Acme export columns.
Checks have not finished.""",
    },
    {
        "h": 11, "m": 14, "dur": 28, "app": "Cursor", "window": "export.ts — review comments",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["export.ts", "Removed customer_email", "Pagination test still runs", "Ready for another look"],
        "elements": [("text", "export.ts"), ("text", "diff")],
        "text": """Cursor export.ts — review comments
Applied Priya's review on PR 882.
Deleted the customer_email mapping. Pagination test still asserts 500 rows per page.
Acme export columns remain invoice_id, amount_usd, and settled_at.""",
    },
    {
        "h": 11, "m": 44, "dur": 16, "app": "Figma", "window": "Billing settings",
        "trigger": "app_switch", "source": "ocr",
        "tags": ["project:billing"],
        "lines": ["Billing settings", "Export CSV", "Columns preview", "invoice_id   amount_usd   settled_at"],
        "elements": [("button", "Export CSV"), ("text", "Columns preview")],
        "text": """Figma Billing settings. The canvas is mostly drawn type, so this frame is OCR fallback.
Visible labels: Export CSV, Columns preview, invoice_id, amount_usd, settled_at.
A crossed-out chip reads customer email.""",
    },
    {
        "h": 12, "m": 2, "dur": 12, "app": "Gmail", "window": "Finance — export columns",
        "trigger": "window_focus", "source": "accessibility",
        "tags": ["project:billing", "person:luis"],
        "lines": ["Finance — export columns", "Luis Ortega", "Three fields only", "Due Friday for Acme"],
        "elements": [("text", "Inbox"), ("button", "Reply")],
        "text": """Gmail from Luis Ortega. Subject: Finance — export columns.
Body: Please freeze the Acme export at invoice_id, amount_usd, and settled_at.
Due Friday. Do not add customer emails. Reply when PR 882 is merged.""",
    },
    {
        "h": 13, "m": 10, "dur": 40, "app": "Cursor", "window": "export.test.ts",
        "trigger": "typing_pause", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["export.test.ts", "it('writes the frozen columns')", "expect(headers).toEqual([...])", "pagination: 500 rows"],
        "elements": [("text", "export.test.ts"), ("button", "Run")],
        "text": """Cursor export.test.ts
Test writes the frozen Acme export columns and expects invoice_id, amount_usd, settled_at.
A second test checks pagination of 500 rows. Both passed locally.""",
    },
    {
        "h": 13, "m": 52, "dur": 18, "app": "Chrome", "window": "Stripe docs — CSV exports",
        "url": "https://docs.stripe.com/payments",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["Stripe docs", "Payments", "Comparing our column names", "settled_at is our name, not theirs"],
        "elements": [("link", "Payments"), ("text", "API reference")],
        "text": """Chrome Stripe documentation, payments section.
Maya is checking whether Stripe's balance transaction fields map onto our export.
Note in the page: settled_at is our column name. Stripe calls the equivalent available_on.
This was reading public docs, not opening a Stripe account.""",
    },
    {
        "h": 14, "m": 12, "dur": 24, "app": "Cursor", "window": "export.ts — billing",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["export.ts", "Map available_on to settled_at", "Comment cites the docs page", "Tests still green"],
        "elements": [("text", "export.ts")],
        "text": """Cursor export.ts — billing
Mapped the ledger's available_on value into settled_at for the Acme export.
Left a comment pointing at the docs page. Tests still expect invoice_id, amount_usd, and settled_at.""",
    },
    {
        "h": 14, "m": 38, "dur": 12, "app": "Slack", "window": "DM Luis Ortega",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing", "person:luis"],
        "lines": ["DM Luis Ortega", "Columns are frozen", "PR 882 updated", "Merging after checks"],
        "elements": [("text", "Luis Ortega"), ("textbox", "Message")],
        "text": """Slack direct message with Luis Ortega.
Maya: Acme export columns are frozen as invoice_id, amount_usd, and settled_at.
PR 882 is updated. I will merge after checks. Still due Friday.""",
    },
    {
        "h": 14, "m": 55, "dur": 36, "app": "Zoom", "window": "Acme weekly",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing", "customer:acme"],
        "lines": ["Acme weekly", "Jordan Hale", "Friday production export", "Use the secure link"],
        "elements": [("button", "Mute"), ("text", "Acme weekly"), ("button", "Chat")],
        "text": """Zoom Acme weekly
Customer call with Jordan Hale about the billing export.
They want the file in production by Friday.
SOC 2 came up only as a next-quarter conversation, not this week's work.
Send the file through the secure link, not a shared inbox.""",
    },
    {
        "h": 15, "m": 34, "dur": 22, "app": "Notion", "window": "Export spec",
        "trigger": "window_focus", "source": "accessibility",
        "tags": ["project:billing", "customer:acme"],
        "lines": ["Export spec", "Frozen columns", "invoice_id", "amount_usd", "settled_at", "Out of scope: SOC 2, emails"],
        "elements": [("heading", "Export spec"), ("text", "Frozen columns")],
        "text": """Notion Export spec
Frozen Acme export columns: invoice_id, amount_usd, settled_at.
Out of scope this week: customer emails, SOC 2 questionnaire, extra currencies.
Delivery: production by Friday via the secure link. Owner: Maya Chen.""",
    },
    {
        "h": 15, "m": 58, "dur": 14, "app": "Terminal", "window": "billing — deploy preview",
        "trigger": "typing_pause", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["npm test  passed", "preview deploy  billing-pr-882", "health  ok"],
        "elements": [("text", "zsh")],
        "text": """Terminal billing — deploy preview
npm test passed. Preview deploy for billing-pr-882 finished.
Health check ok. No production cut yet. Friday is still the Acme date.""",
    },
    {
        "h": 16, "m": 14, "dur": 10, "app": "Slack", "window": "#eng-standup",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["#eng-standup", "Wrap: PR 882 ready", "Acme export Friday", "No email column"],
        "elements": [("list", "#eng-standup"), ("textbox", "Message")],
        "text": """Slack #eng-standup wrap
Maya: PR 882 is ready for a last look. Acme export columns stayed frozen.
Shipping Friday. No customer emails. SOC 2 is not this ticket.""",
    },
    {
        "h": 16, "m": 28, "dur": 20, "app": "Cursor", "window": "export.ts — billing",
        "trigger": "click", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["export.ts", "Final pass on headers", "invoice_id, amount_usd, settled_at"],
        "elements": [("text", "export.ts")],
        "text": """Cursor export.ts — billing
Final pass before asking Priya to re-review PR 882.
Headers are invoice_id, amount_usd, and settled_at.""",
    },
    {
        "h": 16, "m": 50, "dur": 12, "app": "Chrome", "window": "Checks · PR 882",
        "url": "https://github.com/northwind/billing/actions",
        "trigger": "app_switch", "source": "accessibility",
        "tags": ["project:billing"],
        "lines": ["GitHub Actions", "billing-pr-882", "unit  passed", "preview  passed"],
        "elements": [("link", "Actions"), ("text", "unit passed")],
        "text": """Chrome GitHub Actions for PR 882.
Unit and preview checks passed. Merge is waiting on Priya's second review.
Acme export is still due Friday.""",
    },
]


AUDIO = [
    {"h": 9, "m": 20, "meeting": "Sprint planning", "speaker": "Maya Chen", "device": "input",
     "text": "Today I am on BILL-142. We should freeze the Acme export columns today: invoice_id, amount_usd, and settled_at."},
    {"h": 9, "m": 22, "meeting": "Sprint planning", "speaker": "Luis Ortega", "device": "output",
     "text": "Finance signed off on those three columns. They do not want customer emails in the file. This is due Friday."},
    {"h": 9, "m": 24, "meeting": "Sprint planning", "speaker": "Priya Shah", "device": "output",
     "text": "I will review PR 882 before lunch. Please leave the pagination test in."},
    {"h": 14, "m": 58, "meeting": "Acme weekly", "speaker": "Jordan Hale", "device": "output",
     "text": "We need the Acme export in production by Friday. Our auditor asked about SOC 2, but that is a next-quarter conversation."},
    {"h": 15, "m": 2, "meeting": "Acme weekly", "speaker": "Maya Chen", "device": "input",
     "text": "Friday is possible because the Acme export columns are already frozen: invoice_id, amount_usd, and settled_at."},
    {"h": 15, "m": 6, "meeting": "Acme weekly", "speaker": None, "device": "output",
     "text": "Please do not send the file to the shared inbox. Use the secure link."},
]


EVENTS = [
    {"h": 9, "m": 2, "type": "app_switch", "app": "Slack", "window": "#eng-standup", "text": "Switched to Slack"},
    {"h": 9, "m": 21, "type": "clipboard", "app": "Zoom", "window": "Sprint planning", "text": "invoice_id, amount_usd, settled_at"},
    {"h": 10, "m": 12, "type": "app_switch", "app": "Cursor", "window": "export.ts — billing", "text": "Switched to Cursor"},
    {"h": 10, "m": 51, "type": "click", "app": "Chrome", "window": "PR 882 · northwind/billing", "text": "Opened review comment"},
    {"h": 11, "m": 20, "type": "text", "app": "Cursor", "window": "export.ts — review comments", "text": "removed customer_email from buildExportRow"},
    {"h": 14, "m": 40, "type": "clipboard", "app": "Slack", "window": "DM Luis Ortega", "text": "Acme export columns frozen"},
    {"h": 15, "m": 59, "type": "text", "app": "Terminal", "window": "billing — deploy preview", "text": "npm test"},
]


PIPES = [
    {
        "name": "daily-standup",
        "schedule": "every 1h",
        "enabled": True,
        "description": "Turn Slack standup and nearby decisions into notes.",
        "prompt": """Write standup notes from screen text and meeting audio in the scheduled window.
Group by person when a speaker name is known.
End with open questions and due dates.
Do not include passwords, tokens, or customer email addresses.""",
    },
    {
        "name": "meeting-notes",
        "schedule": "daily 17:00",
        "enabled": True,
        "description": "Summarize the latest meeting and the follow-ups that were actually said.",
        "prompt": """Summarize meetings in the window.
List decisions, owners, and due dates.
Quote the speaker when the line came from audio.
If the window has no meeting, say so instead of inventing one.""",
    },
    {
        "name": "workflow-digest",
        "schedule": "every 1d",
        "enabled": False,
        "description": "List repeated work that is worth turning into a pipe.",
        "prompt": """Read the detected workflows and write a short digest of work worth automating.
Prefer loops that already repeat. Do not propose an automation you cannot ground in a frame or a meeting.""",
    },
    {
        "name": "obsidian-journal",
        "schedule": "daily 18:00",
        "enabled": False,
        "description": "Write a local markdown journal. A vault sync app is not connected.",
        "prompt": """Write a daily journal from today's screen and audio.
Save it as markdown. Keep it concrete: apps, decisions, and open loops.""",
    },
]


def _pipe_md(schedule: str, enabled: bool, prompt: str) -> str:
    flag = "true" if enabled else "false"
    return f"---\nschedule: {schedule}\nenabled: {flag}\n---\n\n{prompt.strip()}\n"


def _load() -> None:
    for scene in SCENES:
        stamp = clock(scene["h"], scene["m"])
        lines = scene["lines"]
        content.add_screen(
            text=scene["text"],
            app_name=scene["app"],
            window_name=scene["window"],
            timestamp=stamp,
            browser_url=scene.get("url"),
            trigger=scene["trigger"],
            text_source=scene["source"],
            duration_secs=scene["dur"] * 60,
            tags=scene["tags"],
            elements=_elements(scene["elements"]),
            svg=_svg(scene["app"], scene["window"], scene.get("url"), lines),
            dedupe=False,
        )
    grouped: dict[str, list[dict]] = {}
    for item in AUDIO:
        grouped.setdefault(item["meeting"], []).append(item)
    for title, lines in grouped.items():
        start = clock(lines[0]["h"], lines[0]["m"])
        end_clock = clock(lines[-1]["h"], lines[-1]["m"])
        end = utc_iso(datetime.fromisoformat(end_clock) + timedelta(minutes=4))
        meeting_id = content.ensure_meeting(title=title, started_at=start, ended_at=end, app_name="zoom.us")
        for line in lines:
            saved = content.add_audio(
                text=line["text"],
                timestamp=clock(line["h"], line["m"]),
                speaker_name=line["speaker"],
                device_name="MacBook Microphone" if line["device"] == "input" else "System Audio",
                device_type=line["device"],
                meeting_id=meeting_id,
                duration_secs=30,
                tags=["project:billing"],
            )
            content.attach_meeting(saved["transcription_id"], meeting_id)
    for event in EVENTS:
        content.add_event(
            event_type=event["type"],
            app_name=event["app"],
            window_name=event["window"],
            text=event["text"],
            timestamp=clock(event["h"], event["m"]),
        )
    memory = content_memory()
    with db.conn() as connection:
        connection.execute(
            "INSERT INTO tags(content_type, content_id, tag) VALUES ('memory', ?, 'project:billing')",
            (memory["id"],),
        )
        now = utc_iso()
        for pipe in PIPES:
            folder = db.pipes_root() / pipe["name"]
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "pipe.md").write_text(_pipe_md(pipe["schedule"], pipe["enabled"], pipe["prompt"]))
            connection.execute(
                """
                INSERT INTO pipes(name, schedule, enabled, prompt, description, source, last_run, created_at)
                VALUES (?, ?, ?, ?, ?, 'builtin', NULL, ?)
                """,
                (
                    pipe["name"],
                    pipe["schedule"],
                    1 if pipe["enabled"] else 0,
                    pipe["prompt"],
                    pipe["description"],
                    now,
                ),
            )


def content_memory() -> dict:
    return content_add_memory()


def content_add_memory() -> dict:
    from chronicle import search

    return search.create_memory(
        "Acme export columns are frozen: invoice_id, amount_usd, and settled_at. No customer emails. Due Friday.",
        source="sample-day",
    )


def ensure() -> None:
    db.init()
    if os.environ.get("CHRONICLE_DEMO") == "1":
        load_sample()
    import_new_pipe_files()


def load_sample() -> bool:
    """Load the sample workday into an empty database. Returns True if it loaded."""
    db.init()
    with db.conn() as connection:
        count = connection.execute("SELECT COUNT(*) AS n FROM frames").fetchone()["n"]
    if count:
        return False
    _load()
    from chronicle import graph

    graph.build()
    return True


def import_new_pipe_files() -> None:
    from chronicle.pipes import import_files

    import_files()

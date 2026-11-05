"""The seven shells of the atom and how technical node kinds fold into them."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

SHELLS = [
    {"id": "people", "label": "People", "color": "#FF7A8A"},
    {"id": "projects", "label": "Projects", "color": "#FFB547"},
    {"id": "apps", "label": "Apps & sites", "color": "#4CB8FF"},
    {"id": "ai", "label": "AI", "color": "#B26BFF"},
    {"id": "topics", "label": "Topics", "color": "#2BD4A4"},
    {"id": "places", "label": "Places & events", "color": "#FF9E6B"},
    {"id": "habits", "label": "Habits", "color": "#F472D0"},
]
SHELL_IDS = [shell["id"] for shell in SHELLS]

KIND_SHELL = {
    "person": "people", "contact": "people",
    "project": "projects", "customer": "projects", "company": "projects", "organization": "projects",
    "deal": "projects", "document": "projects", "activity": "projects", "task": "projects",
    "ticket": "projects", "pr": "projects", "file": "projects", "thread": "projects",
    "work": "projects", "topic_cluster": "projects",
    "app": "apps", "site": "apps", "page": "apps", "window": "apps",
    "ai_tool": "ai", "ai_chat": "ai", "ai_habit": "ai", "model": "ai",
    "topic": "topics", "concept": "topics", "product": "topics", "category": "topics", "query": "topics",
    "identifier": "topics", "list": "topics",
    "place": "places", "event": "places", "meeting": "places",
    "habit": "habits", "workflow": "habits",
}
# Shown only in the detail view; the simple view keeps the atom readable for everyone.
DETAIL_KINDS = {"page", "window", "ticket", "pr", "file", "query", "identifier", "list", "thread"}
# Recording sessions stay internal. Named work sessions (`task`) and work-area hubs show in Projects.
SKIP_KINDS = {"session"}
NOISY_KINDS = {"window", "page", "query", "identifier", "file"}
BROWSERS = {
    "arc", "safari", "google chrome", "chrome", "firefox", "brave browser", "microsoft edge", "orion", "vivaldi",
    "opera", "dia", "zen", "chromium",
}
# Host → the name a person would use. Unknown hosts fall back to a title-cased domain.
PLATFORMS = {
    "github.com": "GitHub", "gitlab.com": "GitLab", "bitbucket.org": "Bitbucket",
    "mail.google.com": "Gmail", "docs.google.com": "Google Docs", "sheets.google.com": "Google Sheets",
    "drive.google.com": "Google Drive", "calendar.google.com": "Google Calendar", "meet.google.com": "Google Meet",
    "colab.research.google.com": "Colab", "console.cloud.google.com": "Google Cloud",
    "notion.so": "Notion", "linear.app": "Linear", "figma.com": "Figma",
    "slack.com": "Slack", "app.slack.com": "Slack",
    "twitter.com": "X", "x.com": "X", "linkedin.com": "LinkedIn", "youtube.com": "YouTube",
    "stackoverflow.com": "Stack Overflow", "news.ycombinator.com": "Hacker News",
    "reddit.com": "Reddit", "wikipedia.org": "Wikipedia", "medium.com": "Medium", "substack.com": "Substack",
    "arxiv.org": "arXiv", "huggingface.co": "Hugging Face", "kaggle.com": "Kaggle",
    "npmjs.com": "npm", "pypi.org": "PyPI", "vercel.com": "Vercel", "fly.io": "Fly.io",
    "dropbox.com": "Dropbox", "zoom.us": "Zoom", "teams.microsoft.com": "Teams",
    "outlook.office.com": "Outlook", "outlook.live.com": "Outlook", "web.whatsapp.com": "WhatsApp",
    "discord.com": "Discord", "trello.com": "Trello", "asana.com": "Asana",
    "expensify.com": "Expensify", "stripe.com": "Stripe", "dashboard.stripe.com": "Stripe",
    "forbes.com": "Forbes", "chatgpt.com": "ChatGPT", "claude.ai": "Claude", "perplexity.ai": "Perplexity",
}

AI_TOOLS = [
    {"id": "chatgpt", "label": "ChatGPT", "apps": ["chatgpt"], "hosts": ["chatgpt.com", "chat.openai.com"]},
    {"id": "claude", "label": "Claude", "apps": ["claude"], "hosts": ["claude.ai"]},
    {"id": "gemini", "label": "Gemini", "apps": ["gemini"], "hosts": ["gemini.google.com", "aistudio.google.com"]},
    {"id": "perplexity", "label": "Perplexity", "apps": ["perplexity"], "hosts": ["perplexity.ai"]},
    {"id": "copilot", "label": "Copilot", "apps": ["copilot", "microsoft copilot"], "hosts": ["copilot.microsoft.com"]},
    {"id": "grok", "label": "Grok", "apps": ["grok"], "hosts": ["grok.com"]},
    {"id": "deepseek", "label": "DeepSeek", "apps": ["deepseek"], "hosts": ["chat.deepseek.com"]},
    {"id": "mistral", "label": "Le Chat", "apps": ["le chat"], "hosts": ["chat.mistral.ai"]},
    {"id": "poe", "label": "Poe", "apps": ["poe"], "hosts": ["poe.com"]},
    {"id": "notebooklm", "label": "NotebookLM", "apps": [], "hosts": ["notebooklm.google.com"]},
    {"id": "meta-ai", "label": "Meta AI", "apps": [], "hosts": ["meta.ai"]},
    {"id": "cursor", "label": "Cursor", "apps": ["cursor"], "hosts": []},
    {"id": "claude-code", "label": "Claude Code", "apps": [], "hosts": []},
    {"id": "codex", "label": "Codex", "apps": ["codex"], "hosts": []},
    {"id": "windsurf", "label": "Windsurf", "apps": ["windsurf"], "hosts": []},
    {"id": "gemini-cli", "label": "Gemini CLI", "apps": [], "hosts": []},
]
_BY_APP = {name: tool for tool in AI_TOOLS for name in tool["apps"]}
_BY_HOST = {host: tool for tool in AI_TOOLS for host in tool["hosts"]}


def ai_tool_for(kind: str, label: str, node_id: str | None = None) -> dict | None:
    """The AI tool an app or site node stands for, if any."""
    value = (label or "").strip().lower()
    if kind == "app":
        return _BY_APP.get(value)
    if kind == "site":
        host = ((node_id or "").split(":", 1)[-1] if node_id else value).removeprefix("www.")
        return _BY_HOST.get(host) or _BY_HOST.get(value.removeprefix("www."))
    return None


def ai_tool_for_url(url: str | None) -> dict | None:
    if not url:
        return None
    host = urlsplit(url if "://" in url else f"https://{url}").netloc.lower().removeprefix("www.")
    return _BY_HOST.get(host)


def ai_tool(tool_id: str) -> dict | None:
    return next((tool for tool in AI_TOOLS if tool["id"] == tool_id), None)


def shell_for(kind: str) -> str | None:
    if kind in SKIP_KINDS:
        return None
    return KIND_SHELL.get(kind, "topics")


def kind_of(node_id: str) -> str:
    return node_id.split(":", 1)[0]


def slug(value: str) -> str:
    return re.sub(r"\s+", "-", (value or "").strip()).lower()


def is_browser(label: str | None) -> bool:
    return (label or "").strip().lower() in BROWSERS


def platform_label(host: str | None) -> str:
    """A name a person would recognize for a site: github.com → GitHub."""
    host = (host or "").strip().lower().removeprefix("www.")
    if not host:
        return host
    if host in PLATFORMS:
        return PLATFORMS[host]
    for suffix, name in PLATFORMS.items():
        if host.endswith("." + suffix):
            return name
    parts = [part for part in host.split(".") if part not in {"com", "org", "net", "io", "ai", "app", "dev", "co", "us"}]
    word = (parts[-1] if parts else host.split(".")[0]).replace("-", " ")
    return word.title() if word.islower() or word.isupper() else word


def friendly(kind: str, label: str | None) -> str:
    """Clean the label the atom shows: hosts become product names, already-named sites stay."""
    text = (label or "").strip()
    if kind == "site" and "." in text:
        return platform_label(text) or text
    return text

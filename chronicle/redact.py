"""Local redaction for secrets that should never land in history."""

from __future__ import annotations

import re

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_KEY = re.compile(
    r"\b(?:sk-[A-Za-z0-9]{8,}|ghp_[A-Za-z0-9]{8,}|github_pat_[A-Za-z0-9_]{8,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{8,})\b"
)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_CARD = re.compile(r"\b\d{13,19}\b")


def redact(text: str | None) -> str:
    if not text:
        return ""
    cleaned = _KEY.sub("[key]", text)
    cleaned = _JWT.sub("[token]", cleaned)
    cleaned = _EMAIL.sub("[email]", cleaned)
    cleaned = _CARD.sub("[number]", cleaned)
    return cleaned

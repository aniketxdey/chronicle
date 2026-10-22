"""Answers grounded in local history. A cloud model is optional."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from chronicle import db, meetings, search, workflows

_STOP = {
    "a", "an", "the", "i", "me", "my", "we", "our", "you", "your", "it", "this", "that",
    "what", "when", "where", "who", "how", "was", "were", "is", "are", "did", "do", "doing",
    "on", "in", "for", "to", "of", "and", "or", "today", "yesterday", "just", "about", "from",
    "with", "should", "would", "could", "into", "than", "then", "them", "they", "have", "has",
    "been", "being", "over", "after", "before", "please", "summarize", "summary",
}


def keywords(question: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9_./:#@+-]{3,}", question or "")
    return [token.lower() for token in tokens if token.lower() not in _STOP]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text or "")
    return [part.strip() for part in parts if len(part.strip()) > 24]


def prepare(question: str) -> dict:
    cleaned = (question or "").strip()
    if not cleaned:
        raise db.ChronicleError("question is required")
    lowered = cleaned.lower()
    if any(word in lowered for word in ("automat", "repetit", "workflow", "worth automating")):
        answer, citations = _workflows(cleaned)
        mode = "local"
    elif any(word in lowered for word in ("meeting", "meetings", "transcript", "said", "heard", "call")):
        answer, citations = _meetings(cleaned)
        mode = "local"
    elif any(word in lowered for word in ("today", "working on", "what did i", "recap", "my day", "what was i")):
        answer, citations = _recap()
        mode = "local"
    else:
        answer, citations = _search(cleaned)
        mode = "local"
    model_text = _try_model(cleaned, answer)
    if model_text:
        answer = model_text
        mode = "model"
    chunks = _chunks(answer)
    return {"question": cleaned, "answer": answer, "chunks": chunks, "citations": citations, "mode": mode}


def _chunks(answer: str) -> list[str]:
    text = answer.strip()
    size = 180
    return [text[index : index + size] for index in range(0, len(text), size)] or [text]


def _recap() -> tuple[str, list[dict]]:
    summary = search.activity_summary()
    timeline = search.timeline(None)
    loops = _open_loops()
    lines = [summary["narrative"], ""]
    citations = []
    if timeline["blocks"]:
        lines.append("Through the day:")
        for block in timeline["blocks"]:
            stamp = _clock(block["start"])
            audio = " Audio was captured." if block["has_audio"] else ""
            lines.append(f"- {stamp} {block['app_name']} — {block['window_name']} ({block['minutes']} min).{audio}")
            citations.append(
                {
                    "type": "frame",
                    "id": block["frame_id"],
                    "label": f"{block['app_name']} · {stamp}",
                    "timestamp": block["start"],
                }
            )
    if loops:
        lines.append("")
        lines.append("Still open:")
        for loop in loops[:5]:
            lines.append(f"- {loop['text']}")
            citations.append(loop["citation"])
    return "\n".join(lines).strip(), citations


def _meetings(question: str) -> tuple[str, list[dict]]:
    found = meetings.list_meetings()
    if not found:
        return "No meetings are stored yet.", []
    tokens = keywords(question)
    lines = []
    citations = []
    for meeting in found:
        lines.append(f"{meeting['title']} ({_clock(meeting['started_at'])}).")
        chosen = meeting["segments"]
        if tokens:
            ranked = sorted(
                meeting["segments"],
                key=lambda segment: sum(token in segment["transcription"].lower() for token in tokens),
                reverse=True,
            )
            chosen = [segment for segment in ranked if sum(token in segment["transcription"].lower() for token in tokens)] or ranked[:3]
        for segment in chosen[:4]:
            who = segment["speaker_name"] or "Unnamed speaker"
            lines.append(f"{who} said: {segment['transcription']}")
            citations.append(
                {
                    "type": "audio",
                    "id": segment["id"],
                    "label": f"{who} · {meeting['title']}",
                    "timestamp": segment["timestamp"],
                    "meeting_id": meeting["id"],
                }
            )
        lines.append("")
    return "\n".join(lines).strip(), citations


def _workflows(question: str) -> tuple[str, list[dict]]:
    del question
    items = workflows.list_workflows() or workflows.refresh()
    if not items:
        return "No repeated workflow is visible in the stored history yet.", []
    lines = ["Work that already repeats in this history:", ""]
    citations = []
    for item in items:
        lines.append(f"- {item['title']}. {item['automation']}")
        citations.append({"type": "workflow", "id": item["id"], "label": item["title"], "timestamp": item["first_seen"]})
    return "\n".join(lines).strip(), citations


def _search(question: str) -> tuple[str, list[dict]]:
    found = search.query(q=question, content_type="all", limit=12)
    hits = found["data"]
    if not hits:
        return "Nothing in the stored history matches that.", []
    tokens = keywords(question)
    candidates: list[tuple[int, str, dict]] = []
    for item in hits:
        content = item["content"]
        text = content.get("text") or ""
        for sentence in _sentences(text) or [text[:240]]:
            score = sum(1 for token in tokens if token in sentence.lower())
            if score or not tokens:
                candidates.append((score, sentence, item))
    candidates.sort(key=lambda item: item[0], reverse=True)
    if not candidates:
        return "Nothing in the stored history matches that.", []
    best_score, best_sentence, best_item = candidates[0]
    lines = [best_sentence if best_score else "Closest stored lines:", ""]
    citations = []
    seen = set()
    for score, sentence, item in candidates[:5]:
        if sentence in seen:
            continue
        seen.add(sentence)
        content = item["content"]
        label = _label(item)
        if sentence != best_sentence:
            lines.append(f"- {label}: {sentence}")
        citations.append(
            {
                "type": item["type"].lower(),
                "id": content.get("frame_id") or content.get("transcription_id") or content.get("memory_id") or content.get("event_id"),
                "label": label,
                "timestamp": content.get("timestamp"),
                "frame_id": content.get("frame_id"),
            }
        )
    if best_item["type"] == "Memory" and best_sentence not in lines[0]:
        lines.insert(0, best_sentence)
    return "\n".join(lines).strip(), citations


def _label(item: dict) -> str:
    content = item["content"]
    if item["type"] == "Audio":
        return content.get("speaker_name") or "Audio"
    if item["type"] == "Memory":
        return "Memory"
    if item["type"] == "Input":
        return f"{content.get('app_name') or 'Input'} · {content.get('event_type')}"
    return f"{content.get('app_name') or 'Screen'} · {content.get('window_name') or ''}".strip(" ·")


def _open_loops() -> list[dict]:
    pattern = re.compile(r"\b(friday|due|do not|don't|need to|secure link|out of scope)\b", re.I)
    loops = []
    seen = set()
    for meeting in meetings.list_meetings():
        for segment in meeting["segments"]:
            if not pattern.search(segment["transcription"]):
                continue
            text = segment["transcription"].strip()
            if text in seen:
                continue
            seen.add(text)
            who = segment["speaker_name"] or "Unnamed speaker"
            loops.append(
                {
                    "text": f"{who}: {text}",
                    "citation": {
                        "type": "audio",
                        "id": segment["id"],
                        "label": who,
                        "timestamp": segment["timestamp"],
                    },
                }
            )
    return loops


def _clock(timestamp: str) -> str:
    moment = datetime_from(timestamp)
    local = moment.astimezone(db.local_zone())
    return local.strftime("%-I:%M %p").lower()


def datetime_from(timestamp: str):
    return datetime_parse(timestamp)


def datetime_parse(timestamp: str):
    from datetime import datetime

    return datetime.fromisoformat(timestamp)


def _try_model(question: str, local_answer: str) -> str | None:
    base = (db.get_setting("ai_base_url", "") or "").strip()
    key = (db.get_setting("ai_api_key", "") or "").strip()
    model = (db.get_setting("ai_model", "") or "").strip() or "local"
    if not base or not key:
        return None
    url = base.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Answer only from the supplied local history. If it is not in the notes, say so. "
                    "Do not invent meetings, owners, or dates."
                ),
            },
            {
                "role": "user",
                "content": f"Question: {question}\n\nLocal notes:\n{local_answer}",
            },
        ],
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            body = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError):
        return None
    try:
        return body["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, AttributeError):
        return None


def ai_status() -> dict:
    base = (db.get_setting("ai_base_url", "") or "").strip()
    key = (db.get_setting("ai_api_key", "") or "").strip()
    model = (db.get_setting("ai_model", "") or "").strip()
    if not base or not key:
        return {"mode": "local", "model": None, "configured": False}
    return {"mode": "openai_compatible", "model": model or "local", "configured": True, "base_url": base}

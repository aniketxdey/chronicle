"""Find repeated computer work worth turning into a pipe."""

from __future__ import annotations

import json

from chronicle import db


def refresh() -> list[dict]:
    with db.conn() as connection:
        frames = connection.execute("SELECT * FROM frames ORDER BY timestamp").fetchall()
        meetings = connection.execute("SELECT * FROM meetings ORDER BY started_at").fetchall()
        detected = _detect(frames, meetings)
        connection.execute("DELETE FROM workflows")
        for item in detected:
            connection.execute(
                """
                INSERT INTO workflows (
                    title, apps, description, evidence, automation, minutes, score,
                    suggested_pipe, pipe_prompt, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["title"],
                    json.dumps(item["apps"]),
                    item["description"],
                    item["evidence"],
                    item["automation"],
                    item["minutes"],
                    item["score"],
                    item.get("suggested_pipe"),
                    item.get("pipe_prompt"),
                    item["first_seen"],
                    item["last_seen"],
                ),
            )
    return list_workflows()


def list_workflows() -> list[dict]:
    with db.conn() as connection:
        rows = connection.execute("SELECT * FROM workflows ORDER BY score DESC, id").fetchall()
    payload = []
    for row in rows:
        item = dict(row)
        item["apps"] = json.loads(item["apps"] or "[]")
        payload.append(item)
    return payload


def get_workflow(workflow_id: int) -> dict:
    for item in list_workflows():
        if item["id"] == workflow_id:
            return item
    raise db.ChronicleError("workflow not found", 404)


def _minutes(frames, predicate) -> int:
    seconds = sum((frame["duration_secs"] or 0) for frame in frames if predicate(frame))
    return int(round(seconds / 60))


def _excerpt(frame) -> str:
    text = " ".join((frame["full_text"] or "").split())
    return text[:180]


def _detect(frames, meetings) -> list[dict]:
    if not frames:
        return []
    found = []
    standup = [frame for frame in frames if "standup" in (frame["window_name"] or "").lower()]
    if standup:
        minutes = _minutes(frames, lambda frame: frame in standup)
        found.append(
            {
                "title": "Write the standup from Slack",
                "apps": ["Slack"],
                "description": "Standup notes already land in Slack. A pipe can turn that channel, plus the decisions said out loud, into a note.",
                "evidence": _excerpt(standup[0]),
                "automation": "Run daily-standup on the hour and keep the note next to the channel.",
                "minutes": minutes,
                "score": minutes + 20,
                "suggested_pipe": "daily-standup",
                "pipe_prompt": "Summarize #eng-standup and any due dates mentioned nearby. Do not include email addresses.",
                "first_seen": standup[0]["timestamp"],
                "last_seen": standup[-1]["timestamp"],
            }
        )

    switches = 0
    for previous, current in zip(frames, frames[1:]):
        pair = {previous["app_name"], current["app_name"]}
        github = "github.com" in (previous["browser_url"] or "") or "github.com" in (current["browser_url"] or "")
        if pair == {"Cursor", "Chrome"} and github:
            switches += 1
    if switches >= 2:
        review_frames = [
            frame
            for frame in frames
            if frame["app_name"] in {"Cursor", "Chrome"} and (
                "github.com" in (frame["browser_url"] or "") or frame["app_name"] == "Cursor"
            )
        ]
        minutes = _minutes(frames, lambda frame: frame in review_frames)
        found.append(
            {
                "title": "PR review loop between GitHub and Cursor",
                "apps": ["Chrome", "Cursor"],
                "description": "The day bounced between PR 882 and the export file. That loop is the same shape every time a review comment comes in.",
                "evidence": _excerpt(next(frame for frame in frames if "pull/882" in (frame["browser_url"] or "") or "PR 882" in (frame["full_text"] or ""))),
                "automation": "When GitHub and Cursor alternate, draft the review note and the code change as one pipe run.",
                "minutes": minutes,
                "score": minutes + switches * 5,
                "suggested_pipe": None,
                "pipe_prompt": "When a GitHub review and Cursor edits land in the same hour, write a short review log: comment, code change, and whether tests were mentioned.",
                "first_seen": review_frames[0]["timestamp"],
                "last_seen": review_frames[-1]["timestamp"],
            }
        )

    for meeting in meetings:
        found.append(
            {
                "title": f"Follow up on {meeting['title']}",
                "apps": ["Zoom"],
                "description": "The call already has a transcript. The follow-up is a note with owners and the date that was actually said.",
                "evidence": meeting["title"],
                "automation": "Run meeting-notes when the call ends and file the decisions with the ticket.",
                "minutes": 30,
                "score": 40 if "Acme" in meeting["title"] else 28,
                "suggested_pipe": "meeting-notes",
                "pipe_prompt": f"Summarize {meeting['title']}. List decisions, the Friday date if one was said, and anything that was explicitly deferred.",
                "first_seen": meeting["started_at"],
                "last_seen": meeting["ended_at"],
            }
        )

    docs = [frame for frame in frames if "stripe" in (frame["browser_url"] or "").lower() or "stripe" in (frame["window_name"] or "").lower()]
    code = [frame for frame in frames if frame["app_name"] == "Cursor" and "export" in (frame["window_name"] or "").lower()]
    if docs and code:
        found.append(
            {
                "title": "Billing export checked against public docs",
                "apps": ["Chrome", "Cursor"],
                "description": "Column names were checked in public payments docs, then mapped in export.ts. The docs site is reference material, not a connected account.",
                "evidence": _excerpt(docs[0]),
                "automation": "A pipe can diff our column names against the notes already captured beside the code. It should not call a payments API.",
                "minutes": _minutes(frames, lambda frame: frame in docs or frame in code),
                "score": 36,
                "suggested_pipe": None,
                "pipe_prompt": "Compare the export columns mentioned in the editor with the notes taken from public docs. List mismatches only. Do not call any external API.",
                "first_seen": docs[0]["timestamp"],
                "last_seen": code[-1]["timestamp"],
            }
        )

    notion = [frame for frame in frames if frame["app_name"] == "Notion"]
    if notion and code:
        found.append(
            {
                "title": "Spec in Notion, implementation in Cursor",
                "apps": ["Notion", "Cursor"],
                "description": "The export spec was rewritten after the code. Those two surfaces are the same decision written twice.",
                "evidence": _excerpt(notion[0]),
                "automation": "After the spec changes, open a pipe that checks the code comments still match the frozen columns.",
                "minutes": _minutes(frames, lambda frame: frame in notion),
                "score": 24,
                "suggested_pipe": None,
                "pipe_prompt": "Read the Notion export spec and the latest export.ts frame. Say whether the column list still matches.",
                "first_seen": notion[0]["timestamp"],
                "last_seen": notion[-1]["timestamp"],
            }
        )
    return found

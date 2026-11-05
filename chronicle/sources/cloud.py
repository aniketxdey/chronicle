"""Cloud sources over OAuth: Google Calendar, Outlook mail and calendar, Slack, Notion.

Each reads only what its scopes allow, first syncs the last 30 days, then moves forward from a cursor.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
from typing import Iterator

from chronicle import db, net, oauth
from chronicle.sources.base import Item, Source, Status, register

FIRST_SYNC_DAYS = 30
PAGE_CAP = 25


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> str | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return _iso(moment)


class OAuthSource(Source):
    provider = ""
    scopes: list[str] = []
    syncs = True

    @property
    def oauth(self):  # type: ignore[override]
        return self.provider

    def describe(self) -> dict:
        return {**super().describe(), "connectable": oauth.configured(self.provider)}

    def available(self) -> Status:
        if not oauth.configured(self.provider):
            return Status("unavailable", f"Needs a {self.provider.title()} OAuth client ID. See Connect a cloud source in the docs.")
        if not oauth.token(self.id):
            return Status("needs_setup", "sign in to connect")
        return Status("connected", (oauth.token(self.id) or {}).get("workspace") or "")

    def connect(self) -> dict:
        return oauth.start(self.id, self.provider, self.scopes)

    def headers(self) -> dict:
        return {"Authorization": f"Bearer {oauth.access_token(self.id)}"}


# Google

class GoogleCalendarSource(OAuthSource):
    id = "google-calendar"
    name = "Google Calendar"
    category = "calendar"
    description = "Events, guests, and places. Read only."
    provider = "google"
    scopes = ["https://www.googleapis.com/auth/calendar.readonly"]
    icon = "calendar"

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        params = {"timeMin": _iso(_now() - timedelta(days=FIRST_SYNC_DAYS)), "timeMax": _iso(_now() + timedelta(days=14)),
                  "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
        page = None
        for _ in range(PAGE_CAP):
            data = net.get("https://www.googleapis.com/calendar/v3/calendars/primary/events", params={**params, "pageToken": page},
                           headers=self.headers())
            for event in data.get("items") or []:
                item = google_event(event)
                if item:
                    yield item
            page = data.get("nextPageToken")
            if not page:
                break


def google_event(event: dict) -> Item | None:
    if event.get("status") == "cancelled":
        return None
    start = (event.get("start") or {}).get("dateTime") or (event.get("start") or {}).get("date")
    end = (event.get("end") or {}).get("dateTime") or (event.get("end") or {}).get("date")
    ts = _parse(start if "T" in (start or "") else f"{start}T09:00:00+00:00")
    if not ts:
        return None
    attendees = [{"name": a.get("displayName") or (a.get("email") or "").split("@")[0], "handle": a.get("email"), "me": bool(a.get("self"))}
                 for a in event.get("attendees") or [] if a.get("email")]
    return Item(kind="event", ext_id=f"{event['id']}:{ts}", ts=ts, title=event.get("summary") or "Event",
                text=(event.get("description") or "")[:2000], url=event.get("htmlLink"), thread=event.get("id"),
                actors=attendees[:20], props={"start": ts, "end": _parse(end) if end and "T" in end else end, "location": event.get("location")})


# Microsoft

class OutlookMailSource(OAuthSource):
    id = "outlook"
    name = "Outlook mail"
    category = "email"
    description = "Senders, subjects, and previews from Outlook or Microsoft 365. Read only."
    provider = "microsoft"
    scopes = ["offline_access", "User.Read", "Mail.Read"]
    icon = "mail"

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        since = cursor or _iso(_now() - timedelta(days=FIRST_SYNC_DAYS))
        url = "https://graph.microsoft.com/v1.0/me/messages"
        params = {"$filter": f"receivedDateTime gt {since.replace('+00:00', 'Z')}", "$orderby": "receivedDateTime asc", "$top": 100,
                  "$select": "id,subject,bodyPreview,receivedDateTime,from,toRecipients,conversationId,webLink,parentFolderId,isDraft"}
        sent_folder = None
        try:
            sent_folder = net.get("https://graph.microsoft.com/v1.0/me/mailFolders/sentitems", headers=self.headers()).get("id")
        except db.ChronicleError:
            pass
        newest = since
        for _ in range(PAGE_CAP):
            data = net.get(url, params=params, headers=self.headers())
            for message in data.get("value") or []:
                if message.get("isDraft"):
                    continue
                sent = message.get("parentFolderId") == sent_folder
                sender = (message.get("from") or {}).get("emailAddress") or {}
                actors = [{"name": "Me", "me": True}] + [
                    {"name": r["emailAddress"].get("name"), "handle": r["emailAddress"].get("address")}
                    for r in message.get("toRecipients") or [] if r.get("emailAddress")
                ][:8] if sent else [{"name": sender.get("name") or sender.get("address"), "handle": sender.get("address")}]
                ts = _parse(message.get("receivedDateTime")) or _iso(_now())
                newest = max(newest, ts)
                yield Item(kind="email", ext_id=message["id"], ts=ts, title=message.get("subject") or "(no subject)",
                           text=message.get("bodyPreview") or "", url=message.get("webLink"), thread=message.get("conversationId"),
                           actors=actors, from_me=sent)
            url, params = data.get("@odata.nextLink"), None
            if not url:
                break
        yield newest


class OutlookCalendarSource(OAuthSource):
    id = "outlook-calendar"
    name = "Outlook calendar"
    category = "calendar"
    description = "Events, attendees, and places from Outlook or Microsoft 365. Read only."
    provider = "microsoft"
    scopes = ["offline_access", "User.Read", "Calendars.Read"]
    icon = "calendar"

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        url = "https://graph.microsoft.com/v1.0/me/calendarView"
        params = {"startDateTime": _iso(_now() - timedelta(days=FIRST_SYNC_DAYS)), "endDateTime": _iso(_now() + timedelta(days=14)),
                  "$top": 100, "$select": "id,subject,bodyPreview,start,end,location,attendees,webLink,isCancelled"}
        for _ in range(PAGE_CAP):
            data = net.get(url, params=params, headers={**self.headers(), "Prefer": 'outlook.timezone="UTC"'})
            for event in data.get("value") or []:
                if event.get("isCancelled"):
                    continue
                ts = _parse((event.get("start") or {}).get("dateTime", "") + "+00:00")
                if not ts:
                    continue
                attendees = [{"name": a["emailAddress"].get("name"), "handle": a["emailAddress"].get("address")}
                             for a in event.get("attendees") or [] if a.get("emailAddress")]
                yield Item(kind="event", ext_id=f"{event['id']}:{ts}", ts=ts, title=event.get("subject") or "Event",
                           text=event.get("bodyPreview") or "", url=event.get("webLink"), thread=event["id"], actors=attendees[:20],
                           props={"start": ts, "end": _parse((event.get("end") or {}).get("dateTime", "") + "+00:00"),
                                  "location": (event.get("location") or {}).get("displayName") or None})
            url, params = data.get("@odata.nextLink"), None
            if not url:
                break


# Slack

class SlackSource(OAuthSource):
    id = "slack"
    name = "Slack"
    category = "messages"
    description = "Messages in channels and DMs you're in. Read only."
    provider = "slack"
    scopes = ["channels:history", "groups:history", "im:history", "mpim:history", "channels:read", "groups:read", "im:read",
              "mpim:read", "users:read"]
    icon = "hash"
    API = "https://slack.com/api"

    def _call(self, method: str, **params) -> dict:
        data = net.get(f"{self.API}/{method}", params=params, headers=self.headers())
        if not data.get("ok"):
            raise db.ChronicleError(f"Slack {method}: {data.get('error')}", 502)
        return data

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        marks = json.loads(cursor) if cursor else {}
        me = (oauth.token(self.id) or {}).get("account")
        oldest_default = str((_now() - timedelta(days=FIRST_SYNC_DAYS)).timestamp())
        names: dict[str, str] = {}
        chosen = set(options.get("channels") or [])
        channels, page = [], None
        for _ in range(10):
            data = self._call("users.conversations", types="public_channel,private_channel,im,mpim", limit=200, cursor=page,
                              exclude_archived="true")
            channels.extend(data.get("channels") or [])
            page = (data.get("response_metadata") or {}).get("next_cursor")
            if not page:
                break
        for channel in channels:
            if chosen and channel["id"] not in chosen:
                continue
            oldest = marks.get(channel["id"], oldest_default)
            latest = oldest
            data = self._call("conversations.history", channel=channel["id"], oldest=oldest, limit=200)
            title = channel.get("name") and f"#{channel['name']}" or self._name(channel.get("user"), names) or "Direct message"
            for message in reversed(data.get("messages") or []):
                if message.get("subtype") in {"channel_join", "channel_leave", "bot_message"} or not message.get("text"):
                    continue
                user = message.get("user")
                mine = bool(me) and user == me
                actor = {"name": "Me", "me": True} if mine else {"name": self._name(user, names) or "Someone", "handle": user}
                latest = max(latest, message["ts"], key=float)
                yield Item(kind="message", ext_id=f"{channel['id']}:{message['ts']}",
                           ts=_iso(datetime.fromtimestamp(float(message["ts"]), timezone.utc)), title=title,
                           text=re.sub(r"<@(\w+)>", lambda m: "@" + (self._name(m.group(1), names) or "someone"), message["text"]),
                           thread=message.get("thread_ts") or channel["id"], actors=[actor], from_me=mine,
                           props={"channel": channel["id"], "dm": bool(channel.get("is_im")),
                                  "mentions_me": bool(me) and f"<@{me}>" in message["text"]})
            marks[channel["id"]] = latest
            yield json.dumps(marks)

    def _name(self, user: str | None, cache: dict[str, str]) -> str:
        if not user:
            return ""
        if user not in cache:
            try:
                profile = self._call("users.info", user=user).get("user") or {}
                cache[user] = (profile.get("profile") or {}).get("real_name") or profile.get("real_name") or profile.get("name") or user
            except db.ChronicleError:
                cache[user] = user
        return cache[user]


# Notion

class NotionSource(OAuthSource):
    id = "notion"
    name = "Notion"
    category = "files"
    description = "Pages you share when you connect Notion. Read only."
    provider = "notion"
    scopes: list[str] = []
    icon = "book-open"
    API = "https://api.notion.com/v1"

    def headers(self) -> dict:
        return {**super().headers(), "Notion-Version": "2022-06-28"}

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        since = cursor or _iso(_now() - timedelta(days=FIRST_SYNC_DAYS * 6))
        start, newest = None, since
        for _ in range(PAGE_CAP):
            body = {"filter": {"property": "object", "value": "page"}, "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                    "page_size": 50}
            if start:
                body["start_cursor"] = start
            data = net.post(f"{self.API}/search", json_body=body, headers=self.headers())
            stop = False
            for page in data.get("results") or []:
                edited = _parse(page.get("last_edited_time")) or _iso(_now())
                if edited <= since:
                    stop = True
                    break
                newest = max(newest, edited)
                yield Item(kind="document", ext_id=f"{page['id']}:{edited}", ts=edited, title=notion_title(page),
                           text=self._text(page["id"]), url=page.get("url"), thread=page["id"])
            start = data.get("next_cursor")
            if stop or not data.get("has_more"):
                break
        yield newest

    def _text(self, page_id: str) -> str:
        try:
            data = net.get(f"{self.API}/blocks/{page_id}/children", params={"page_size": 100}, headers=self.headers())
        except db.ChronicleError:
            return ""
        parts = []
        for block in data.get("results") or []:
            content = block.get(block.get("type") or "", {})
            parts.append("".join(span.get("plain_text", "") for span in (content.get("rich_text") or [])))
        return "\n".join(p for p in parts if p)[:20000]


def notion_title(page: dict) -> str:
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return "".join(span.get("plain_text", "") for span in prop.get("title") or []) or "Untitled"
    return "Untitled"


for _source in (GoogleCalendarSource(), OutlookMailSource(), OutlookCalendarSource(),
                SlackSource(), NotionSource()):
    register(_source)


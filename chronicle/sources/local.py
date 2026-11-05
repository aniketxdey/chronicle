"""Sources that read data already on this Mac: screen, browser history, Apple Mail, Calendar, iMessage, WhatsApp, folders.

Apple's databases are protected by macOS, so most of these need Full Disk Access granted to Chronicle. They're
read from a copy, never written. Schemas differ between macOS versions, so columns are checked before use.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import json
import mailbox
import os
import re
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from chronicle import db
from chronicle.sources.base import Item, Source, Status, readable, register, snapshot
from chronicle.timeutil import utc_iso

HOME = Path.home()
APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)
CHROME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
FIRST_SYNC_DAYS = 30


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def apple_time(value) -> str | None:
    """Apple Core Data times: seconds since 2001, or nanoseconds in newer Messages databases."""
    if value is None:
        return None
    value = float(value)
    if value > 1e12:
        value /= 1e9
    return _iso(APPLE_EPOCH + timedelta(seconds=value))


def _apple_seconds(iso: str) -> float:
    return (datetime.fromisoformat(iso) - APPLE_EPOCH).total_seconds()


def _since(cursor: str | None, days: int = FIRST_SYNC_DAYS) -> str:
    return cursor or _iso(datetime.now(timezone.utc) - timedelta(days=days))


def _stamp_unit(sample) -> str:
    """Apple databases mix unix seconds, Apple-epoch seconds, and nanoseconds."""
    value = float(sample or 0)
    if value > 1e12:
        return "apple_ns"
    if value > 1e9:
        return "unix"
    return "apple"


def _to_db_stamp(iso: str, unit: str) -> float:
    if unit == "unix":
        return datetime.fromisoformat(iso).timestamp()
    apple = _apple_seconds(iso)
    return apple * 1e9 if unit == "apple_ns" else apple


def _from_db_stamp(value, unit: str) -> str | None:
    if value is None:
        return None
    value = float(value)
    if unit == "unix":
        return _iso(datetime.fromtimestamp(value, timezone.utc))
    if unit == "apple_ns":
        value /= 1e9
    return apple_time(value)


def _window_unit(connection, table: str, column: str, start_iso: str, end_iso: str) -> str:
    """Pick the timestamp unit that actually hits rows in the requested window.

    A single far-future event can make MAX(date) look like unix seconds even when the
    rest of the table is Apple epoch, which then matches nothing.
    """
    sample = connection.execute(f"SELECT MAX({column}) AS d FROM {table}").fetchone()["d"]
    guessed = _stamp_unit(sample)
    best, best_n = guessed, -1
    for unit in ("apple", "unix", "apple_ns"):
        start = _to_db_stamp(start_iso, unit)
        end = _to_db_stamp(end_iso, unit)
        n = connection.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {column} BETWEEN ? AND ?", (start, end)).fetchone()["n"]
        if n > best_n or (n == best_n and unit == guessed):
            best, best_n = unit, n
    return best


def _columns(connection, table: str) -> set[str]:
    try:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


# Contacts, so phone numbers and addresses become names

def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")[-10:]


def contacts() -> dict[str, str]:
    """handle (last 10 phone digits or lowercase email) -> display name, from the macOS address book."""
    names: dict[str, str] = {}
    root = HOME / "Library" / "Application Support" / "AddressBook"
    for path in [*root.glob("Sources/*/AddressBook-v22.abcddb"), root / "AddressBook-v22.abcddb"]:
        if not path.exists():
            continue
        try:
            with snapshot(path) as connection:
                people = {
                    row["Z_PK"]: " ".join(filter(None, [row["ZFIRSTNAME"], row["ZLASTNAME"]])) or (row["ZORGANIZATION"] or "")
                    for row in connection.execute("SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZORGANIZATION FROM ZABCDRECORD")
                }
                for row in connection.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                    if people.get(row["ZOWNER"]) and _digits(row["ZFULLNUMBER"]):
                        names.setdefault(_digits(row["ZFULLNUMBER"]), people[row["ZOWNER"]])
                for row in connection.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                    if people.get(row["ZOWNER"]) and row["ZADDRESS"]:
                        names.setdefault(row["ZADDRESS"].lower(), people[row["ZOWNER"]])
        except (OSError, sqlite3.DatabaseError):
            continue
    return names


def name_for(handle: str | None, book: dict[str, str]) -> str:
    if not handle:
        return ""
    if "@" in handle:
        return book.get(handle.lower(), handle.split("@")[0].replace(".", " ").title())
    return book.get(_digits(handle), handle)


# Screen

class ScreenSource(Source):
    id = "screen"
    name = "Screen"
    category = "screen"
    description = "The front app, window, page, and visible text while you record. Never keystrokes or passwords."
    permissions = ["screen_recording", "accessibility"]
    default_enabled = True
    icon = "monitor"

    managed = True

    def available(self) -> Status:
        frames = db.counts().get("frames") or 0
        error = db.get_setting("last_capture_error", "") or ""
        if "Screen Recording" in error:
            return Status("needs_permission", error)
        detail = f"{frames} frame{'s' if frames != 1 else ''} recorded"
        if "denied" in (db.get_setting("last_ax_status", "") or "").lower():
            detail += " · window text needs Accessibility"
        return Status("connected", detail)


register(ScreenSource())


# Browser history

BROWSERS = {
    "chrome": HOME / "Library/Application Support/Google/Chrome/Default/History",
    "arc": HOME / "Library/Application Support/Arc/User Data/Default/History",
    "brave": HOME / "Library/Application Support/BraveSoftware/Brave-Browser/Default/History",
    "edge": HOME / "Library/Application Support/Microsoft Edge/Default/History",
    "vivaldi": HOME / "Library/Application Support/Vivaldi/Default/History",
    "safari": HOME / "Library/Safari/History.db",
}
_SKIP_URL = re.compile(r"^(chrome|arc|about|edge|brave|file|data|blob|javascript):|accounts\.google\.com|/oauth|/login|/signin", re.I)


class BrowserHistorySource(Source):
    id = "browser-history"
    name = "Browser history"
    category = "browser"
    description = "Page titles and addresses from Safari, Chrome, Arc, Brave, Edge, and Vivaldi."
    permissions = ["full_disk_access"]
    icon = "globe"

    def __init__(self, browsers: dict[str, Path] | None = None):
        self.browsers = browsers or BROWSERS

    def available(self) -> Status:
        found = [name for name, path in self.browsers.items() if path.exists()]
        if not found:
            return Status("unavailable", "no supported browser found")
        ready = [name for name in found if readable(self.browsers[name]).state == "connected"]
        blocked = [name for name in found if name not in ready]
        if not ready:
            return Status("needs_permission", "needs Full Disk Access to read browser history")
        detail = ", ".join(name.title() for name in ready)
        if blocked:
            names = ", ".join(name.title() for name in blocked)
            detail += f" · {names} need{'s' if len(blocked) == 1 else ''} Full Disk Access"
        return Status("connected", detail)

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        marks = json.loads(cursor) if cursor else {}
        allowed = set(options.get("browsers") or self.browsers)
        for browser, path in self.browsers.items():
            if browser not in allowed or readable(path).state != "connected":
                continue
            since = marks.get(browser) or _since(None)
            newest = since
            try:
                rows = self._safari(path, since) if browser == "safari" else self._chromium(path, since)
                for ts, visit_id, url, title in rows:
                    if not url or _SKIP_URL.search(url):
                        continue
                    newest = max(newest, ts)
                    yield Item(kind="visit", ext_id=f"{browser}:{visit_id}", ts=ts, title=title or url, url=url,
                               props={"browser": browser})
            except (OSError, sqlite3.DatabaseError):
                continue
            marks[browser] = newest
            yield json.dumps(marks)

    def _chromium(self, path: Path, since: str):
        start = int((datetime.fromisoformat(since) - CHROME_EPOCH).total_seconds() * 1_000_000)
        with snapshot(path) as connection:
            for row in connection.execute(
                """
                SELECT v.id, v.visit_time, u.url, u.title FROM visits v JOIN urls u ON u.id = v.url
                WHERE v.visit_time > ? ORDER BY v.visit_time LIMIT 20000
                """,
                (start,),
            ):
                yield _iso(CHROME_EPOCH + timedelta(microseconds=row["visit_time"])), row["id"], row["url"], row["title"]

    def _safari(self, path: Path, since: str):
        with snapshot(path) as connection:
            for row in connection.execute(
                """
                SELECT v.id, v.visit_time, i.url, v.title FROM history_visits v JOIN history_items i ON i.id = v.history_item
                WHERE v.visit_time > ? ORDER BY v.visit_time LIMIT 20000
                """,
                (_apple_seconds(since),),
            ):
                yield apple_time(row["visit_time"]), row["id"], row["url"], row["title"]


register(BrowserHistorySource())


def visit_from_extension(payload: dict) -> Item | None:
    """A live tab the paired extension saw. Title and URL only, never page contents."""
    url = str(payload.get("url") or "").strip()
    if not url or _SKIP_URL.search(url):
        return None
    ts = payload.get("ts") or utc_iso()
    title = str(payload.get("title") or url)[:500]
    dwell = max(0, int(payload.get("dwell_ms") or 0))
    key = hashlib.sha1(f"{url}|{str(ts)[:13]}".encode()).hexdigest()[:20]
    return Item(kind="visit", ext_id=f"live:{key}", ts=ts, title=title, url=url, text="",
                props={"browser": "extension", "dwell_ms": dwell})


# Apple Mail

def _mail_index() -> Path | None:
    versions = sorted((HOME / "Library" / "Mail").glob("V*/MailData/Envelope Index"), reverse=True)
    return versions[0] if versions else None


class AppleMailSource(Source):
    id = "apple-mail"
    name = "Apple Mail"
    category = "email"
    description = "Senders, subjects, and previews from every account in the Mail app."
    permissions = ["full_disk_access"]
    icon = "mail"

    def __init__(self, index: Path | None = None):
        self.index = index

    def _path(self) -> Path | None:
        return self.index or _mail_index()

    def available(self) -> Status:
        path = self._path()
        if path is None:
            if (HOME / "Library" / "Mail").exists() and readable(HOME / "Library" / "Mail").state == "needs_permission":
                return Status("needs_permission", "needs Full Disk Access to read Mail")
            return Status("unavailable", "the Mail app has no accounts on this Mac")
        return readable(path)

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        path = self._path()
        if path is None:
            return
        book = contacts()
        newest = cursor
        with snapshot(path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "messages" not in tables:
                return
            columns = _columns(connection, "messages")
            summary = "(SELECT summary FROM summaries s WHERE s.ROWID = m.summary)" if "summary" in columns and "summaries" in tables else "''"
            conversation = "m.conversation_id" if "conversation_id" in columns else "m.ROWID"
            date_col = "date_received" if "date_received" in columns else "date_sent"
            sample = connection.execute(f"SELECT MAX({date_col}) AS d FROM messages").fetchone()["d"]
            unit = _stamp_unit(sample)
            select = f"""
                SELECT m.ROWID AS id, m.{date_col} AS date, {conversation} AS thread, a.address, a.comment AS sender_name,
                       sj.subject, {summary} AS summary, mb.url AS mailbox
                FROM messages m
                LEFT JOIN addresses a ON a.ROWID = m.sender
                LEFT JOIN subjects sj ON sj.ROWID = m.subject
                LEFT JOIN mailboxes mb ON mb.ROWID = m.mailbox
            """
            if cursor:
                rows = connection.execute(select + f" WHERE m.{date_col} > ? ORDER BY m.{date_col} LIMIT 20000",
                                          (_to_db_stamp(_since(cursor), unit),)).fetchall()
            else:
                rows = list(reversed(connection.execute(select + f" ORDER BY m.{date_col} DESC LIMIT 20000").fetchall()))
            recipients: dict[int, list[tuple[str, str]]] = {}
            if _columns(connection, "recipients"):
                ids = [row["id"] for row in rows]
                for start in range(0, len(ids), 500):
                    batch = ids[start:start + 500]
                    marks = ",".join("?" for _ in batch)
                    for row in connection.execute(
                        f"""
                        SELECT r.message, a.address, a.comment FROM recipients r JOIN addresses a ON a.ROWID = r.address
                        WHERE r.message IN ({marks})
                        """,
                        batch,
                    ):
                        recipients.setdefault(row["message"], []).append((row["address"], row["comment"]))
        for row in rows:
            mailbox = (row["mailbox"] or "").lower()
            if any(word in mailbox for word in ("junk", "spam", "trash", "deleted")):
                continue
            sent = "sent" in mailbox
            ts = _from_db_stamp(row["date"], unit)
            actors = [] if sent else [{"name": row["sender_name"] or name_for(row["address"], book), "handle": row["address"]}]
            if sent:
                actors.append({"name": "Me", "me": True})
                actors.extend({"name": comment or name_for(address, book), "handle": address}
                              for address, comment in recipients.get(row["id"], [])[:6])
            newest = ts
            yield Item(kind="email", ext_id=str(row["id"]), ts=ts, title=row["subject"] or "(no subject)",
                       text=row["summary"] or "", thread=str(row["thread"]), actors=actors, from_me=sent,
                       props={"mailbox": row["mailbox"]})
        if newest:
            yield newest


register(AppleMailSource())


# Apple Calendar

CALENDAR_DB = HOME / "Library/Group Containers/group.com.apple.calendar/Calendar.sqlitedb"


class AppleCalendarSource(Source):
    id = "apple-calendar"
    name = "Apple Calendar"
    category = "calendar"
    description = "Events, guests, and places from the Calendar app."
    permissions = ["full_disk_access"]
    icon = "calendar"

    def __init__(self, path: Path | None = None):
        self.path = path or CALENDAR_DB

    def available(self) -> Status:
        return readable(self.path)

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        # Events change after they're created, so every sync re-reads a rolling window and upserts by id+start.
        start_iso = _iso(datetime.now(timezone.utc) - timedelta(days=365))
        end_iso = _iso(datetime.now(timezone.utc) + timedelta(days=14))
        with snapshot(self.path) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            table = "CalendarItem" if "CalendarItem" in tables else ("ZCALENDARITEM" if "ZCALENDARITEM" in tables else None)
            if table is None:
                return
            columns = _columns(connection, table)
            start_col = "start_date" if "start_date" in columns else ("ZSTARTDATE" if "ZSTARTDATE" in columns else None)
            if not start_col:
                return
            occ_cols = _columns(connection, "OccurrenceCache")
            occ_start = next((name for name in ("occurrence_date", "occurrence_start_date", "start_date") if name in occ_cols), None)
            occ_item = next((name for name in ("item_id", "owner_id", "calendar_item_id", "event_id") if name in occ_cols), None)
            use_occ = bool(occ_start and occ_item)
            date_table, date_col = ("OccurrenceCache", occ_start) if use_occ else (table, start_col)
            unit = _window_unit(connection, date_table, date_col, start_iso, end_iso)
            start = _to_db_stamp(start_iso, unit)
            end = _to_db_stamp(end_iso, unit)
            location = "(SELECT title FROM Location l WHERE l.ROWID = c.location_id)" if "location_id" in columns else "NULL"
            description = "c.description" if "description" in columns else ("c.ZNOTES" if "ZNOTES" in columns else "''")
            summary = "c.summary" if "summary" in columns else ("c.ZTITLE" if "ZTITLE" in columns else "''")
            end_col = "end_date" if "end_date" in columns else ("ZENDDATE" if "ZENDDATE" in columns else start_col)
            pk = "c.ROWID" if "ROWID" in {c.upper() for c in columns} or table == "CalendarItem" else "c.Z_PK"
            hidden = "AND (c.hidden = 0 OR c.hidden IS NULL)" if "hidden" in columns else ""
            if use_occ:
                occ_end = "o.occurrence_end_date" if "occurrence_end_date" in occ_cols else f"c.{end_col}"
                rows = connection.execute(
                    f"""
                    SELECT {pk} AS id, {summary} AS summary, o.{occ_start} AS start_date, {occ_end} AS end_date,
                           {location} AS location, {description} AS description
                    FROM OccurrenceCache o JOIN {table} c ON c.ROWID = o.{occ_item}
                    WHERE o.{occ_start} BETWEEN ? AND ? {hidden} ORDER BY o.{occ_start} LIMIT 20000
                    """,
                    (start, end),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"""
                    SELECT {pk} AS id, {summary} AS summary, c.{start_col} AS start_date, c.{end_col} AS end_date,
                           {location} AS location, {description} AS description
                    FROM {table} c WHERE c.{start_col} BETWEEN ? AND ? {hidden} ORDER BY c.{start_col} LIMIT 20000
                    """,
                    (start, end),
                ).fetchall()
            people: dict[int, list[dict]] = {}
            if _columns(connection, "Participant"):
                participant = _columns(connection, "Participant")
                identity = "(SELECT display_name FROM Identity i WHERE i.ROWID = p.identity_id)" if "identity_id" in participant and _columns(connection, "Identity") else "NULL"
                for row in connection.execute(f"SELECT p.owner_id, p.email, {identity} AS name FROM Participant p"):
                    people.setdefault(row["owner_id"], []).append({"name": row["name"] or name_for(row["email"], {}), "handle": row["email"]})
        for row in rows:
            ts = _from_db_stamp(row["start_date"], unit)
            stamp = int(float(row["start_date"] or 0))
            yield Item(kind="event", ext_id=f"{row['id']}:{stamp}", ts=ts, title=row["summary"] or "Event",
                       text=(row["description"] or "")[:2000], thread=str(row["id"]), actors=people.get(row["id"], [])[:20],
                       props={"start": ts, "end": _from_db_stamp(row["end_date"], unit), "location": row["location"]})


register(AppleCalendarSource())


# iMessage

MESSAGES_DB = HOME / "Library/Messages/chat.db"


def attributed_text(blob: bytes | None) -> str:
    """Message text from ``attributedBody`` (newer macOS leaves ``text`` empty and stores a typedstream)."""
    if not blob:
        return ""
    index = blob.find(b"NSString")
    if index < 0:
        return ""
    rest = blob[index + len(b"NSString"):]
    plus = rest.find(b"+")
    if plus < 0:
        return ""
    rest = rest[plus + 1:]
    if not rest:
        return ""
    length, offset = rest[0], 1
    if length == 0x81:
        length, offset = int.from_bytes(rest[1:3], "little"), 3
    elif length == 0x82:
        length, offset = int.from_bytes(rest[1:5], "little"), 5
    return rest[offset:offset + length].decode("utf-8", errors="ignore")


class IMessageSource(Source):
    id = "imessage"
    name = "iMessage"
    category = "messages"
    description = "Messages in the chats you pick. Nothing else is read."
    permissions = ["full_disk_access", "contacts"]
    icon = "message-circle"

    def __init__(self, path: Path | None = None):
        self.path = path or MESSAGES_DB

    def available(self) -> Status:
        return readable(self.path)

    def choices(self) -> list[dict]:
        book = contacts()
        with snapshot(self.path) as connection:
            rows = connection.execute(
                """
                SELECT c.ROWID AS id, c.chat_identifier, c.display_name, MAX(m.date) AS last, COUNT(*) AS n
                FROM chat c JOIN chat_message_join j ON j.chat_id = c.ROWID JOIN message m ON m.ROWID = j.message_id
                GROUP BY c.ROWID ORDER BY last DESC LIMIT 200
                """
            ).fetchall()
        return [{"id": str(row["id"]), "label": row["display_name"] or name_for(row["chat_identifier"], book),
                 "count": row["n"], "last": apple_time(row["last"])} for row in rows]

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        chats = {str(c) for c in options.get("chats") or []}
        everything = bool(options.get("all_chats"))
        if not chats and not everything:
            return
        since = _apple_seconds(_since(cursor))
        book = contacts()
        with snapshot(self.path) as connection:
            nanos = connection.execute("SELECT MAX(date) AS d FROM message").fetchone()["d"] or 0
            start = since * 1e9 if nanos > 1e12 else since
            rows = connection.execute(
                """
                SELECT m.ROWID AS id, m.text, m.attributedBody, m.date, m.is_from_me, h.id AS handle,
                       c.ROWID AS chat, c.display_name, c.chat_identifier
                FROM message m
                JOIN chat_message_join j ON j.message_id = m.ROWID JOIN chat c ON c.ROWID = j.chat_id
                LEFT JOIN handle h ON h.ROWID = m.handle_id
                WHERE m.date > ? ORDER BY m.date LIMIT 50000
                """,
                (start,),
            ).fetchall()
        newest = cursor
        for row in rows:
            if not everything and str(row["chat"]) not in chats:
                continue
            text = row["text"] or attributed_text(row["attributedBody"])
            if not text.strip():
                continue
            ts = apple_time(row["date"])
            title = row["display_name"] or name_for(row["chat_identifier"], book)
            actor = {"name": "Me", "me": True} if row["is_from_me"] else {"name": name_for(row["handle"], book), "handle": row["handle"]}
            newest = ts
            yield Item(kind="message", ext_id=str(row["id"]), ts=ts, title=title, text=text, thread=str(row["chat"]),
                       actors=[actor], from_me=bool(row["is_from_me"]))
        if newest:
            yield newest


register(IMessageSource())


# WhatsApp (Mac app)

WHATSAPP_DB = HOME / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ChatStorage.sqlite"


class WhatsAppSource(Source):
    id = "whatsapp"
    name = "WhatsApp"
    category = "messages"
    description = "Messages in the chats you pick, from the WhatsApp Mac app."
    permissions = ["full_disk_access"]
    icon = "message-square"

    def __init__(self, path: Path | None = None):
        self.path = path or WHATSAPP_DB

    def available(self) -> Status:
        if not self.path.exists():
            return Status("unavailable", "install WhatsApp for Mac and sign in")
        return readable(self.path)

    def choices(self) -> list[dict]:
        with snapshot(self.path) as connection:
            rows = connection.execute(
                """
                SELECT s.Z_PK AS id, s.ZPARTNERNAME AS name, COUNT(m.Z_PK) AS n, MAX(m.ZMESSAGEDATE) AS last
                FROM ZWACHATSESSION s LEFT JOIN ZWAMESSAGE m ON m.ZCHATSESSION = s.Z_PK
                GROUP BY s.Z_PK ORDER BY last DESC LIMIT 200
                """
            ).fetchall()
        return [{"id": str(row["id"]), "label": row["name"] or "Chat", "count": row["n"], "last": apple_time(row["last"])} for row in rows]

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        chats = {str(c) for c in options.get("chats") or []}
        everything = bool(options.get("all_chats"))
        if not chats and not everything:
            return
        since = _apple_seconds(_since(cursor))
        with snapshot(self.path) as connection:
            columns = _columns(connection, "ZWAMESSAGE")
            push = "m.ZPUSHNAME" if "ZPUSHNAME" in columns else "NULL"
            rows = connection.execute(
                f"""
                SELECT m.Z_PK AS id, m.ZTEXT AS text, m.ZMESSAGEDATE AS date, m.ZISFROMME AS mine, m.ZFROMJID AS jid,
                       {push} AS push, s.Z_PK AS chat, s.ZPARTNERNAME AS chat_name
                FROM ZWAMESSAGE m JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
                WHERE m.ZMESSAGEDATE > ? AND m.ZTEXT IS NOT NULL ORDER BY m.ZMESSAGEDATE LIMIT 50000
                """,
                (since,),
            ).fetchall()
        newest = cursor
        for row in rows:
            if not everything and str(row["chat"]) not in chats:
                continue
            ts = apple_time(row["date"])
            group = "@g.us" in (row["jid"] or "")
            name = row["push"] or (row["chat_name"] if not group else (row["jid"] or "").split("@")[0])
            actor = {"name": "Me", "me": True} if row["mine"] else {"name": name or "Someone", "handle": row["jid"]}
            newest = ts
            yield Item(kind="message", ext_id=str(row["id"]), ts=ts, title=row["chat_name"] or "WhatsApp chat", text=row["text"],
                       thread=str(row["chat"]), actors=[actor], from_me=bool(row["mine"]))
        if newest:
            yield newest


register(WhatsAppSource())


# Folders

TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm", ".rtf", ".org", ".tex", ".yaml", ".yml"}
DOC_SUFFIXES = {".docx", ".pptx", ".xlsx", ".pdf", ".pages"}
SKIP_DIRS = {"node_modules", ".git", "Library", ".venv", "venv", "__pycache__", ".cache", "build", "dist"}
MAX_BYTES = 2_000_000


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n in ("word/document.xml",) or re.match(r"ppt/slides/slide\d+\.xml", n)]
            xml = " ".join(archive.read(name).decode("utf-8", errors="ignore") for name in names[:60])
    except (zipfile.BadZipFile, OSError, KeyError):
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", xml)).strip()


def _pdf_text(path: Path) -> str:
    import shutil
    import subprocess

    tool = shutil.which("pdftotext")
    if tool:
        try:
            return subprocess.run([tool, "-l", "20", str(path), "-"], capture_output=True, text=True, timeout=30).stdout
        except (subprocess.SubprocessError, OSError):
            return ""
    try:
        from pypdf import PdfReader  # type: ignore

        return " ".join((page.extract_text() or "") for page in PdfReader(str(path)).pages[:20])
    except Exception:  # noqa: BLE001
        return ""


def file_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if suffix in {".html", ".htm"}:
            text = re.sub(r"<[^>]+>", " ", text)
        return text
    if suffix in {".docx", ".pptx"}:
        return _docx_text(path)
    if suffix == ".pdf":
        return _pdf_text(path)
    return ""


class FoldersSource(Source):
    id = "local-folders"
    name = "Folders"
    category = "files"
    description = "Notes, Word, PowerPoint, PDF, and text files in folders you add."
    permissions = ["folder_picker"]
    icon = "folder"

    def available(self) -> Status:
        folders = self._folders(state_options())
        if not folders:
            return Status("needs_setup", "choose one or more folders")
        return Status("connected", ", ".join(str(f) for f in folders[:3]))

    @staticmethod
    def _folders(options: dict) -> list[Path]:
        return [Path(p).expanduser() for p in options.get("folders") or [] if Path(p).expanduser().is_dir()]

    def sync(self, cursor: str | None, options: dict) -> Iterator[Item | str]:
        since = float(cursor or 0)
        newest = since
        for folder in self._folders(options):
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
                for name in files:
                    path = Path(root) / name
                    if path.suffix.lower() not in TEXT_SUFFIXES | DOC_SUFFIXES:
                        continue
                    try:
                        stat = path.stat()
                    except OSError:
                        continue
                    if stat.st_mtime <= since or stat.st_size > MAX_BYTES * 10:
                        continue
                    try:
                        text = file_text(path)[:MAX_BYTES]
                    except OSError:
                        continue
                    newest = max(newest, stat.st_mtime)
                    yield Item(kind="document", ext_id=f"{path}:{int(stat.st_mtime)}",
                               ts=_iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc)), title=path.stem, text=text,
                               url=path.as_uri(), thread=str(path), props={"path": str(path), "suffix": path.suffix.lower()})
        yield str(newest)


def state_options() -> dict:
    from chronicle.sources import base

    return base.state("local-folders")["options"]


register(FoldersSource())


# Mail files, for Windows Mail and anything else that exports .eml or .mbox

def _mail_time(message) -> str:
    try:
        moment = email.utils.parsedate_to_datetime(message.get("Date") or "")
    except (TypeError, ValueError):
        return utc_iso()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return _iso(moment)


def _mail_text(message) -> str:
    part = message.get_body(preferencelist=("plain", "html")) if hasattr(message, "get_body") else message
    if part is None:
        return ""
    try:
        text = part.get_content()
    except (LookupError, KeyError, ValueError):
        return ""
    return re.sub(r"<[^>]+>", " ", text) if part.get_content_type() == "text/html" else text


def _mail_item(raw: bytes) -> Item | None:
    message = email.message_from_bytes(raw, policy=email.policy.default)
    if not (message.get("From") or message.get("Subject")):
        return None
    people = email.utils.getaddresses([str(message.get(key) or "") for key in ("From", "To", "Cc")])
    actors = [{"name": name or address, "handle": address} for name, address in people if name or address]
    key = str(message.get("Message-ID") or "") or hashlib.sha1(raw[:4000]).hexdigest()
    return Item(kind="email", ext_id=key, ts=_mail_time(message), title=str(message.get("Subject") or ""),
                text=_mail_text(message)[:2000], thread=str(message.get("In-Reply-To") or key), actors=actors)


class MailFilesSource(Source):
    id = "windows-mail"
    name = "Windows Mail"
    category = "email"
    description = "Import mail you saved as .eml or .mbox, or a .zip of them. Also works with Outlook and Thunderbird exports."
    importable = True
    icon = "mail"

    def import_file(self, path: Path, options: dict) -> Iterator[Item]:
        for raw in _mail_files(path):
            item = _mail_item(raw)
            if item:
                yield item


def _mail_files(path: Path) -> Iterator[bytes]:
    suffix = path.suffix.lower()
    if suffix == ".eml":
        yield path.read_bytes()
    elif suffix == ".mbox":
        box = mailbox.mbox(path, create=False)
        try:
            yield from (box.get_bytes(key) for key in box.keys())
        finally:
            box.close()
    elif suffix == ".zip":
        with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory(prefix="chronicle-mail-") as folder:
            for info in archive.infolist():
                name = Path(info.filename).name
                if not info.is_dir() and name.lower().endswith(".eml"):
                    yield archive.read(info)
                elif not info.is_dir() and name.lower().endswith(".mbox"):
                    target = Path(folder) / "mail.mbox"
                    target.write_bytes(archive.read(info))
                    yield from _mail_files(target)
    else:
        raise db.ChronicleError("choose a .eml, .mbox, or .zip file", 400)


register(MailFilesSource())

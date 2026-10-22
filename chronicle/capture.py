"""The recorder: while a session is active, capture frames and system events of this machine.

Only one process records at a time, enforced by a lock file in CHRONICLE_HOME, so
the HTTP server and any number of stdio MCP servers can run side by side. Keystrokes
are never recorded.
"""

from __future__ import annotations

import hashlib
import os
import sys
import threading
import time
from pathlib import Path

from chronicle import content, db, probe, sessions
from chronicle.timeutil import utc_iso

DENY_APPS = {
    "1Password", "1Password 7", "Bitwarden", "Dashlane", "KeePassXC", "LastPass", "Keychain Access",
    "System Settings", "System Preferences", "SecurityAgent", "loginwindow", "ScreenSaverEngine",
}
DENY_WINDOW_WORDS = ("password", "private browsing", "incognito", "inprivate")
CLIPBOARD_LIMIT = 2000
GRAPH_REFRESH_SECS = 30


class CaptureLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle = None

    def acquire(self) -> bool:
        if self.handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+")
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        handle.write(f"{os.getpid()} {' '.join(sys.argv[:2])}\n")
        handle.flush()
        self.handle = handle
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None

    def owner(self) -> str | None:
        try:
            return self.path.read_text().strip() or None
        except OSError:
            return None


class Recorder:
    def __init__(self):
        self.session_id: int | None = None
        self.app = ""
        self.window = ""
        self.url: str | None = None
        self.idle = False
        self.clip_hash: str | None = None
        self.last_graph = 0.0
        self.last_tick = 0.0

    def reset(self, session_id: int | None) -> None:
        self.__init__()
        self.session_id = session_id

    def tick(self, session: dict) -> dict | None:
        if session["id"] != self.session_id:
            self.reset(session["id"])
        sid = session["id"]
        interval = float(session.get("interval_secs") or 5)
        now = time.monotonic()
        elapsed = min(now - self.last_tick, interval * 3) if self.last_tick else interval
        self.last_tick = now
        try:
            front = probe.frontmost()
        except probe.ProbeError as exc:
            db.set_setting("last_capture_error", str(exc)[:500])
            return None
        except Exception as exc:  # noqa: BLE001
            db.set_setting("last_capture_error", f"window probe failed: {exc}"[:500])
            return None
        if not front.app:
            return None

        idle = probe.idle_seconds()
        threshold = float(db.get_setting("idle_threshold_secs", "120") or 120)
        if idle is not None and idle >= threshold:
            if not self.idle:
                self.idle = True
                content.add_event(event_type="idle_start", app_name=front.app, window_name=front.window,
                                  text=f"No input for {int(idle)}s", session_id=sid)
            return None
        if self.idle:
            self.idle = False
            content.add_event(event_type="idle_end", app_name=front.app, window_name=front.window,
                              text="Input resumed", session_id=sid)

        private = front.app in DENY_APPS or any(word in front.window.lower() for word in DENY_WINDOW_WORDS)
        if private:
            if self.app != front.app:
                content.add_event(event_type="app_switch", app_name=front.app, window_name="",
                                  text=f"Switched to {front.app} (not captured)", session_id=sid)
                self.app, self.window = front.app, ""
            return None

        events: list[tuple[str, str]] = []
        trigger = "interval"
        if front.app != self.app:
            trigger = "app_switch"
            events.append(("app_switch", f"Switched to {front.app}" + (f" from {self.app}" if self.app else "")))
        elif front.window != self.window:
            trigger = "window_focus"
            events.append(("window_focus", f"Focused {front.window}"))
        if front.url and front.url != self.url:
            if trigger == "interval":
                trigger = "url_change"
            events.append(("url_change", front.url))
        if db.get_bool("capture_clipboard", True):
            clip = probe.clipboard()
            if clip is not None:
                digest = hashlib.sha256(clip.encode()).hexdigest()
                if self.clip_hash is not None and digest != self.clip_hash and clip.strip():
                    events.append(("clipboard", clip[:CLIPBOARD_LIMIT]))
                    if trigger == "interval":
                        trigger = "clipboard"
                self.clip_hash = digest
        self.app, self.window, self.url = front.app, front.window, front.url

        shot_dir = db.media_root() / "frames"
        pending = shot_dir / f"pending-{os.getpid()}.jpg"
        has_image = db.get_bool("capture_screenshots", True) and probe.screenshot(pending)
        ocr_text = probe.ocr(pending) if has_image and db.get_bool("capture_ocr", True) else None
        text = "\n".join(part for part in (front.window, front.url or "", ocr_text or "") if part)
        saved = content.add_screen(
            text=text or front.app,
            app_name=front.app,
            window_name=front.window,
            browser_url=front.url,
            trigger=trigger,
            text_source="ocr" if ocr_text else "accessibility",
            duration_secs=round(elapsed, 1),
            device_name="Main display",
            ocr_text=ocr_text,
            dedupe=True,
            session_id=sid,
        )
        frame_id = saved["frame_id"]
        if has_image:
            relative = f"frames/{frame_id}.jpg"
            pending.replace(db.media_root() / relative)
            with db.conn() as connection:
                connection.execute("UPDATE frames SET snapshot_path = ? WHERE id = ?", (relative, frame_id))
        for event_type, text_value in events:
            content.add_event(event_type=event_type, app_name=front.app, window_name=front.window,
                              text=text_value, frame_id=frame_id, session_id=sid)
        sessions.refresh_counts(sid)
        db.set_setting("last_capture_at", utc_iso())
        db.set_setting("last_capture_error", "")
        if time.monotonic() - self.last_graph > GRAPH_REFRESH_SECS:
            self.last_graph = time.monotonic()
            from chronicle import graph

            graph.refresh(sid)
        return saved


_lock: CaptureLock | None = None


def lock() -> CaptureLock:
    global _lock
    if _lock is None:
        _lock = CaptureLock(db.home() / "capture.lock")
    return _lock


def run(stop: threading.Event) -> None:
    """Record whenever a session is active. Safe to run in several processes; one wins the lock."""
    recorder = Recorder()
    while not stop.is_set():
        wait = 2.0
        try:
            sessions.finalize_due()
            session = sessions.active()
            if session is None:
                recorder.reset(None)
                lock().release()
            elif lock().acquire():
                began = time.monotonic()
                recorder.tick(session)
                wait = max(0.2, float(session.get("interval_secs") or 5) - (time.monotonic() - began))
        except Exception as exc:  # noqa: BLE001
            db.set_setting("last_capture_error", f"{type(exc).__name__}: {exc}"[:500])
        stop.wait(wait)
    lock().release()


def start_worker() -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    thread = threading.Thread(target=run, args=(stop,), name="chronicle-recorder", daemon=True)
    thread.start()
    return thread, stop


def status() -> dict:
    session = sessions.active()
    holder = lock()
    return {
        "recording": session is not None,
        "session": session,
        "this_process_records": holder.handle is not None,
        "recorder": holder.owner() if session else None,
        "last_error": db.get_setting("last_capture_error", "") or "",
        "last_capture_at": db.get_setting("last_capture_at", "") or "",
        "interval_secs": int(db.get_setting("capture_interval_secs", "5") or "5"),
        "idle_threshold_secs": int(db.get_setting("idle_threshold_secs", "120") or "120"),
        "clipboard": db.get_bool("capture_clipboard", True),
        "screenshots": db.get_bool("capture_screenshots", True),
        "denied_apps": sorted(DENY_APPS),
        "capabilities": probe.capabilities(),
        "keystrokes": "never recorded",
    }

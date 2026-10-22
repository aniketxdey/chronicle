"""Read the state of this machine: front window, browser URL, clipboard, idle time, screenshots, OCR.

macOS is the primary target. Linux (X11 via xdotool, Wayland screenshots via grim)
and Windows (user32 via ctypes) are best-effort. Every probe returns None or an
empty value instead of raising, so the recorder can keep going on partial support.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from chronicle.redact import redact

BROWSERS_CHROMIUM = {
    "Google Chrome", "Google Chrome Canary", "Chromium", "Arc", "Brave Browser", "Microsoft Edge", "Vivaldi", "Opera",
}
BROWSERS_SAFARI = {"Safari", "Safari Technology Preview", "Orion"}
HELPERS = Path(__file__).parent / "helpers"
AX_HINT = (
    "grant Accessibility to the app that runs Chronicle (your terminal, or the MCP host such as Cursor or "
    "Claude Desktop) in System Settings > Privacy & Security > Accessibility, then restart it"
)

FRONT_SCRIPT = r'''
tell application "System Events"
  set frontApp to first application process whose frontmost is true
  set appName to name of frontApp
  set winName to ""
  try
    set winName to name of front window of frontApp
  end try
  return appName & linefeed & winName
end tell
'''


@dataclass
class Front:
    app: str = ""
    window: str = ""
    url: str | None = None
    extra: dict = field(default_factory=dict)


def _run(args: list[str], timeout: float = 3, text: bool = True) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(args, check=False, capture_output=True, text=text, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None


class ProbeError(RuntimeError):
    pass


def frontmost() -> Front:
    if sys.platform == "darwin":
        return _mac_front()
    if sys.platform.startswith("linux"):
        return _linux_front()
    if sys.platform == "win32":
        return _windows_front()
    raise ProbeError(f"window probing is not implemented for {sys.platform}")


def _mac_front() -> Front:
    done = _run(["osascript", "-e", FRONT_SCRIPT])
    if done is None:
        raise ProbeError("osascript timed out reading the front window")
    if done.returncode != 0:
        raise ProbeError(
            (done.stderr or "").strip()
            or "Accessibility permission is needed: System Settings > Privacy & Security > Accessibility"
        )
    lines = [line.strip() for line in (done.stdout or "").splitlines()]
    front = Front(app=lines[0] if lines else "", window=lines[1] if len(lines) > 1 else "")
    url, title = _mac_browser_tab(front.app)
    front.url = url
    if title and not front.window:
        front.window = title
    return front


def _mac_browser_tab(app: str) -> tuple[str | None, str | None]:
    if app in BROWSERS_CHROMIUM:
        script = f'tell application "{app}" to return (URL of active tab of front window) & linefeed & (title of active tab of front window)'
    elif app in BROWSERS_SAFARI:
        script = f'tell application "{app}" to return (URL of front document) & linefeed & (name of front document)'
    else:
        return None, None
    done = _run(["osascript", "-e", script], timeout=2)
    if done is None or done.returncode != 0:
        return None, None
    parts = (done.stdout or "").rstrip("\n").split("\n", 1)
    url = parts[0].strip()
    title = parts[1].strip() if len(parts) > 1 else None
    if not url.startswith(("http://", "https://", "file://")):
        url = None
    return url, title or None


def _linux_front() -> Front:
    if not shutil.which("xdotool"):
        raise ProbeError("install xdotool to read the active window on Linux (X11)")
    window_id = _run(["xdotool", "getactivewindow"])
    if window_id is None or window_id.returncode != 0:
        raise ProbeError("xdotool could not read the active window (Wayland sessions are not supported)")
    wid = window_id.stdout.strip()
    title = _run(["xdotool", "getwindowname", wid])
    app = ""
    if shutil.which("xprop"):
        klass = _run(["xprop", "-id", wid, "WM_CLASS"])
        if klass and klass.returncode == 0 and "=" in klass.stdout:
            parts = [part.strip().strip('"') for part in klass.stdout.split("=", 1)[1].split(",")]
            app = parts[-1] if parts else ""
    return Front(app=app or "Unknown", window=(title.stdout.strip() if title else ""))


def _windows_front() -> Front:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = "Unknown"
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if handle:
        size = wintypes.DWORD(1024)
        path = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
            app = Path(path.value).stem
        kernel32.CloseHandle(handle)
    return Front(app=app, window=buffer.value)


def idle_seconds() -> float | None:
    if sys.platform == "darwin":
        done = _run(["ioreg", "-c", "IOHIDSystem", "-d", "4"], timeout=3)
        if done is None or done.returncode != 0:
            return None
        for line in done.stdout.splitlines():
            if "HIDIdleTime" in line:
                try:
                    return int(line.rsplit("=", 1)[1].strip()) / 1_000_000_000
                except ValueError:
                    return None
        return None
    if sys.platform.startswith("linux") and shutil.which("xprintidle"):
        done = _run(["xprintidle"])
        if done and done.returncode == 0 and done.stdout.strip().isdigit():
            return int(done.stdout.strip()) / 1000
        return None
    if sys.platform == "win32":
        import ctypes

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LastInput()
        info.cbSize = ctypes.sizeof(LastInput)
        if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):  # type: ignore[attr-defined]
            return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000  # type: ignore[attr-defined]
    return None


def clipboard() -> str | None:
    if sys.platform == "darwin":
        done = _run(["pbpaste"], timeout=2)
    elif sys.platform.startswith("linux"):
        if shutil.which("wl-paste"):
            done = _run(["wl-paste", "--no-newline"], timeout=2)
        elif shutil.which("xclip"):
            done = _run(["xclip", "-o", "-selection", "clipboard"], timeout=2)
        else:
            return None
    elif sys.platform == "win32":
        done = _run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], timeout=4)
    else:
        return None
    if done is None or done.returncode != 0:
        return None
    return done.stdout


def screenshot(target: Path, max_width: int = 1600) -> bool:
    """Write a JPEG of the main display to target. Returns False when no tool is available."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        done = _run(["screencapture", "-x", "-m", "-t", "jpg", str(target)], timeout=8)
        if done is None or done.returncode != 0 or not target.exists():
            return False
        if shutil.which("sips"):
            _run(["sips", "-Z", str(max_width), "-s", "formatOptions", "70", str(target)], timeout=8)
        return target.stat().st_size > 64
    if sys.platform.startswith("linux"):
        for args in (
            ["grim", "-t", "jpeg", str(target)],
            ["gnome-screenshot", "-f", str(target)],
            ["scrot", "-o", str(target)],
            ["import", "-window", "root", str(target)],
        ):
            if shutil.which(args[0]):
                done = _run(args, timeout=8)
                if done and done.returncode == 0 and target.exists():
                    return True
        return False
    if sys.platform == "win32":
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
            "$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($i);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            f"$i.Save('{target}',[System.Drawing.Imaging.ImageFormat]::Jpeg)"
        )
        done = _run(["powershell", "-NoProfile", "-Command", script], timeout=10)
        return bool(done and done.returncode == 0 and target.exists())
    return False


def ocr(image: Path) -> str | None:
    if not shutil.which("tesseract"):
        return None
    done = _run(["tesseract", str(image), "-", "--psm", "3"], timeout=30)
    if done is None or done.returncode != 0:
        return None
    lines = [" ".join(line.split()) for line in done.stdout.splitlines()]
    return "\n".join(line for line in lines if len(line) > 1)[:20000] or None


_ax_trust: tuple[float, bool | None] = (0.0, None)
_ax_compiling: threading.Thread | None = None
_ax_compile_error: str | None = None


def _swift_paths() -> tuple[Path, Path, str]:
    from chronicle import db

    binary = db.home() / "bin" / "axdump"
    digest = hashlib.sha256((HELPERS / "axdump.swift").read_bytes()).hexdigest()
    return binary, binary.with_name("axdump.sha256"), digest


def _swift_fresh() -> bool:
    binary, stamp, digest = _swift_paths()
    try:
        return binary.exists() and stamp.read_text().strip() == digest
    except OSError:
        return False


def _can_compile() -> bool:
    # /usr/bin/swiftc is a shim that pops an install dialog when the Command Line Tools are missing.
    if _ax_compile_error or not shutil.which("swiftc"):
        return False
    done = _run(["xcode-select", "-p"], timeout=3)
    return bool(done and done.returncode == 0 and Path(done.stdout.strip()).exists())


def compile_ax_helper() -> dict:
    """Build axdump.swift into CHRONICLE_HOME/bin/axdump. Safe to race: each build renames into place."""
    global _ax_compile_error
    binary, stamp, digest = _swift_paths()
    binary.parent.mkdir(parents=True, exist_ok=True)
    partial = binary.with_name(f"axdump.{os.getpid()}.{threading.get_ident()}.tmp")
    began = time.monotonic()
    done = _run(["swiftc", "-O", "-o", str(partial), str(HELPERS / "axdump.swift")], timeout=300)
    ms = round((time.monotonic() - began) * 1000)
    if done is None or done.returncode != 0 or not partial.exists():
        partial.unlink(missing_ok=True)
        _ax_compile_error = ((done.stderr if done else "") or "swiftc timed out").strip()[-500:] or "swiftc failed"
        return {"compiled": False, "compile_ms": ms, "compile_error": _ax_compile_error}
    partial.replace(binary)
    stamp.write_text(digest)
    return {"compiled": True, "compile_ms": ms}


def ax_helper(build: str = "background") -> tuple[list[str], dict] | None:
    """Command for the AX helper. build is never|background|wait; JXA serves while Swift builds or when it can't."""
    global _ax_compiling
    if sys.platform != "darwin":
        return None
    info: dict = {}
    if not _swift_fresh() and build != "never" and _can_compile():
        if build == "wait":
            info = compile_ax_helper()
        elif _ax_compiling is None or not _ax_compiling.is_alive():
            _ax_compiling = threading.Thread(target=compile_ax_helper, name="chronicle-axdump-build", daemon=True)
            _ax_compiling.start()
    if _swift_fresh():
        return [str(_swift_paths()[0])], {"helper": "swift", **info}
    if shutil.which("osascript"):
        return ["osascript", "-l", "JavaScript", str(HELPERS / "axdump.js")], {"helper": "jxa", **info}
    return None


def _ax_role(role: str) -> str:
    if role == "AXStaticText":
        return "text"
    return role[2:].lower() if role.startswith("AX") else (role.lower() or "text")


def parse_ax(output: str, max_chars: int = 8000) -> tuple[str, dict] | None:
    """Turn axdump JSON lines into (redacted text, meta). meta["elements"] keeps reading order."""
    meta: dict | None = None
    elements: list[dict] = []
    seen: set[str] = set()
    total = 0
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("meta"), dict):
            meta = item["meta"]
            continue
        text = redact(str(item.get("text") or "")).strip()[: max(0, max_chars - total)]
        if not text or text in seen:
            continue
        seen.add(text)
        total += len(text) + 1
        elements.append({"role": _ax_role(str(item.get("role") or "")), "text": text, "depth": int(item.get("depth") or 0)})
    if meta is None:
        return None
    meta["elements"] = elements
    return "\n".join(element["text"] for element in elements), meta


def ax_text(
    pid: int | None = None,
    *,
    max_nodes: int = 2000,
    max_chars: int = 8000,
    budget: float = 1.0,
    unlock: bool = True,
    build: str = "background",
) -> tuple[str, dict] | None:
    """Text of the focused window of pid (default: frontmost app) via Accessibility. None when unavailable.

    meta["status"] is ok|denied|no_app|no_window; meta["elapsed_ms"] includes subprocess startup.
    """
    helper = ax_helper(build)
    if helper is None:
        return None
    command, info = helper
    args = [str(max_nodes), str(max_chars), str(budget), "1" if unlock else "0"] + ([str(pid)] if pid else [])
    began = time.monotonic()
    # The helper polices its own budget; this is the backstop for a wedged AX server or a slow osascript start.
    done = _run(command + args, timeout=budget + 2)
    parsed = parse_ax(done.stdout if done is not None and done.returncode == 0 else "", max_chars)
    if parsed is None and info["helper"] == "swift" and shutil.which("osascript"):
        info["helper"] = "jxa"
        info["swift_failed"] = True
        done = _run(["osascript", "-l", "JavaScript", str(HELPERS / "axdump.js")] + args, timeout=budget + 2)
        parsed = parse_ax(done.stdout if done is not None and done.returncode == 0 else "", max_chars)
    if parsed is None:
        return None
    text, meta = parsed
    meta.update(info)
    meta["elapsed_ms"] = round((time.monotonic() - began) * 1000)
    return text, meta


def ax_trusted() -> bool | None:
    """Whether processes we spawn may use Accessibility. Cached briefly; None off macOS or if unknown."""
    global _ax_trust
    if sys.platform != "darwin":
        return None
    checked, value = _ax_trust
    if checked and time.monotonic() - checked < 30:
        return value
    done = _run(["osascript", "-l", "JavaScript", "-e", "ObjC.import('ApplicationServices'); $.AXIsProcessTrusted()"])
    value = None if done is None or done.returncode != 0 else done.stdout.strip() == "true"
    _ax_trust = (time.monotonic(), value)
    return value


def capabilities() -> dict:
    mac = sys.platform == "darwin"
    linux = sys.platform.startswith("linux")
    helper = ax_helper("never") if mac else None
    return {
        "platform": sys.platform,
        "window": mac or sys.platform == "win32" or (linux and bool(shutil.which("xdotool"))),
        "browser_url": mac,
        "screenshot": (mac and bool(shutil.which("screencapture")))
        or sys.platform == "win32"
        or (linux and any(shutil.which(tool) for tool in ("grim", "gnome-screenshot", "scrot", "import"))),
        "clipboard": mac or sys.platform == "win32" or (linux and bool(shutil.which("wl-paste") or shutil.which("xclip"))),
        "idle": mac or sys.platform == "win32" or (linux and bool(shutil.which("xprintidle"))),
        "ocr": bool(shutil.which("tesseract")),
        "accessibility": bool(ax_trusted()),
        "ax_helper": None if helper is None else helper[1]["helper"],
    }

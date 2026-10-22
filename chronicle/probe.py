"""Read the state of this machine: front window, browser URL, clipboard, idle time, screenshots, OCR.

macOS is the primary target. Linux (X11 via xdotool, Wayland screenshots via grim)
and Windows (user32 via ctypes) are best-effort. Every probe returns None or an
empty value instead of raising, so the recorder can keep going on partial support.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

BROWSERS_CHROMIUM = {
    "Google Chrome", "Google Chrome Canary", "Chromium", "Arc", "Brave Browser", "Microsoft Edge", "Vivaldi", "Opera",
}
BROWSERS_SAFARI = {"Safari", "Safari Technology Preview", "Orion"}

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


def capabilities() -> dict:
    mac = sys.platform == "darwin"
    linux = sys.platform.startswith("linux")
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
    }

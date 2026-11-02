"""Read what Chronicle would capture from an OSWorld observation: app, window, URL, and visible text."""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET

STATE = "{https://accessibility.ubuntu.example.org/ns/state}"
TEXT_LIMIT = 6000
ADDRESS_BARS = ("address and search bar", "address bar", "location")


def _true(node: ET.Element, state: str) -> bool:
    return node.get(f"{STATE}{state}", "false") == "true"


def _active_window(root: ET.Element) -> tuple[str, ET.Element | None]:
    for app in root.iter("application"):
        for window in app:
            if _true(window, "active"):
                return app.get("name", ""), window
    return "", None


def from_a11y(tree: str) -> dict | None:
    """Return the active app, window title, address-bar URL, and showing text, or None if the tree can't be read."""
    try:
        root = ET.fromstring(tree)
    except ET.ParseError:
        return None
    app, window = _active_window(root)
    if window is None:
        return None
    url = None
    lines: list[str] = []
    seen: set[str] = set()
    size = 0
    for node in window.iter():
        if not (_true(node, "showing") and _true(node, "visible")):
            continue
        name = (node.get("name") or "").strip()
        text = (node.text or "").strip()
        if url is None and node.tag == "entry" and name.lower() in ADDRESS_BARS and text:
            url = text if "://" in text else f"https://{text}"
        for value in (name, text):
            if value and value not in seen and size < TEXT_LIMIT:
                seen.add(value)
                lines.append(value)
                size += len(value) + 1
    return {"app": app or "Desktop", "window": window.get("name", ""), "url": url, "text": "\n".join(lines), "source": "accessibility"}


def from_screenshot(png: bytes | None) -> dict | None:
    if not png or not shutil.which("tesseract"):
        return None
    try:
        done = subprocess.run(["tesseract", "stdin", "-", "--psm", "3"], input=png, capture_output=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        return None
    text = done.stdout.decode("utf-8", "replace").strip()
    if done.returncode != 0 or not text:
        return None
    return {"app": "Desktop", "window": "", "url": None, "text": text[:TEXT_LIMIT], "source": "ocr"}


def read(obs: dict, fetch_tree=None) -> dict | None:
    """Prefer the accessibility tree (from the observation, else ``fetch_tree()``), then OCR on the screenshot."""
    tree = obs.get("accessibility_tree")
    if not tree and fetch_tree is not None:
        try:
            tree = fetch_tree()
        except Exception:
            tree = None
    frame = from_a11y(tree) if tree else None
    return frame or from_screenshot(obs.get("screenshot"))

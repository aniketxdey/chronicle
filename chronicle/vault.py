"""Secrets (API keys, OAuth tokens) live in the macOS Keychain, never in the database or a dotfile.

Elsewhere, or with ``CHRONICLE_SECRETS=file`` (tests), they go to ``secrets.json`` under CHRONICLE_HOME with mode 600.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from chronicle import db

SERVICE = "app.chronicle.mac"


def _backend() -> str:
    choice = os.environ.get("CHRONICLE_SECRETS", "").lower()
    if choice in {"file", "keychain"}:
        return choice
    return "keychain" if sys.platform == "darwin" else "file"


def _file():
    return db.home() / "secrets.json"


def _read_file() -> dict:
    path = _file()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_file(data: dict) -> None:
    path = _file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    os.chmod(path, 0o600)


def get(key: str) -> str | None:
    if _backend() == "file":
        return _read_file().get(key)
    result = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", key, "-w"],
                            capture_output=True, text=True)
    return result.stdout.rstrip("\n") if result.returncode == 0 else None


def set(key: str, value: str) -> None:  # noqa: A001
    if _backend() == "file":
        data = _read_file()
        data[key] = value
        _write_file(data)
        return
    result = subprocess.run(["security", "add-generic-password", "-U", "-s", SERVICE, "-a", key, "-l", f"Chronicle: {key}", "-w", value],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise db.ChronicleError(f"couldn't save to the Keychain: {result.stderr.strip()}", 500)


def delete(key: str) -> None:
    if _backend() == "file":
        data = _read_file()
        data.pop(key, None)
        _write_file(data)
        return
    subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", key], capture_output=True)


def get_json(key: str) -> dict | None:
    raw = get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def set_json(key: str, value: dict) -> None:
    set(key, json.dumps(value))

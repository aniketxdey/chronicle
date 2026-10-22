import json
import re
from pathlib import Path

import pytest

import chronicle
from chronicle import mcp_server

tomllib = pytest.importorskip("tomllib")

ROOT = Path(__file__).resolve().parent.parent


def _json(path):
    return json.loads((ROOT / path).read_text())


def test_versions_match():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    server = _json("server.json")
    versions = {
        "pyproject.toml": project["version"],
        "chronicle/__init__.py": chronicle.__version__,
        "server.json": server["version"],
        "server.json package": server["packages"][0]["version"],
        "plugin.json": _json("plugins/chronicle/.claude-plugin/plugin.json")["version"],
        "gemini-extension.json": _json("gemini-extension.json")["version"],
    }
    assert len(set(versions.values())) == 1, versions


def test_registry_entry_matches_package():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    server = _json("server.json")
    package = server["packages"][0]
    assert package["identifier"] == project["name"]
    assert project["name"] in project["scripts"]
    assert len(server["description"]) <= 100
    assert f"mcp-name: {server['name']}" in (ROOT / "README.md").read_text()


def test_launchers_use_published_package():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    for path in ("plugins/chronicle/.mcp.json", "gemini-extension.json"):
        entry = _json(path)["mcpServers"]["chronicle"]
        assert entry["command"] == "uvx"
        assert any(re.match(rf"{project['name']}(\[|$)", arg) for arg in entry["args"])
        assert entry["args"][-1] == "mcp"


def test_tools_have_directory_annotations():
    for tool in mcp_server.TOOLS:
        hints = tool.get("annotations", {})
        assert tool.get("title"), tool["name"]
        assert hints.get("readOnlyHint") or "destructiveHint" in hints, tool["name"]

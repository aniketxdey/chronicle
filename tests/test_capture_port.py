"""Capture must not fake a screenshot without permission, and should not re-probe an unchanged window."""

from __future__ import annotations

import sys

import pytest

from chronicle import probe


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS only")
def test_no_screenshot_without_screen_recording(monkeypatch, tmp_path):
    monkeypatch.setattr(probe, "screen_capture_allowed", lambda: False)
    calls = []
    monkeypatch.setattr(probe, "_run", lambda *args, **kwargs: calls.append(args))
    assert probe.screenshot(tmp_path / "shot.jpg") is False
    assert not calls

"""Wrap an OSWorld agent so it records each step for Chronicle and starts each task with its arm's context.

The wrapper only sees what OSWorld passes to ``predict`` (plus the accessibility tree of the same screen); it never
sees rewards, evaluator output, or task configuration, so nothing about scoring can reach the memory.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from chronicle_bench import memory, screen

_OWN = {"inner", "env", "arm", "spool_dir", "budget", "_task_id", "_instruction", "_augmented", "_last_step"}
_usage_target: list[Path | None] = [None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _append(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(record) + "\n")


def record_usage(usage) -> None:
    """Append one API call's token usage to the current task's usage file."""
    target = _usage_target[0]
    if target is None or usage is None:
        return
    fields = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    _append(target, {"ts": _now(), **{name: getattr(usage, name, None) or 0 for name in fields}})


class MemoryAgent:
    def __init__(self, inner, env, arm: str, spool_dir: str | Path, budget: int = memory.BUDGET_CHARS):
        if arm not in memory.ARMS:
            raise ValueError(f"arm must be one of {memory.ARMS}")
        self.inner = inner
        self.env = env
        self.arm = arm
        self.spool_dir = Path(spool_dir)
        self.budget = budget
        self._task_id = None
        self._instruction = None
        self._augmented = None
        self._last_step = None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def __setattr__(self, name, value):
        if name in _OWN:
            object.__setattr__(self, name, value)
        else:
            setattr(self.inner, name, value)

    def _current_task_id(self) -> str:
        return str(getattr(self.env, "task_id", None) or "unknown")

    def reset(self, *args, **kwargs):
        self._instruction = None
        self._augmented = None
        self._last_step = None
        return self.inner.reset(*args, **kwargs)

    def _start(self, instruction: str) -> None:
        self._task_id = self._current_task_id()
        self._instruction = instruction
        spool = self.spool_dir / f"{self._task_id}.jsonl"
        _usage_target[0] = self.spool_dir / f"{self._task_id}.usage.jsonl"
        started = time.monotonic()
        block = memory.context_for(self.arm, instruction, self.budget)
        self._augmented = memory.augment(instruction, block)
        _append(spool, {"type": "task_start", "ts": _now(), "arm": self.arm, "instruction": instruction,
                        "context_chars": len(block), "context_secs": round(time.monotonic() - started, 3)})

    def _record(self, obs: dict) -> None:
        controller = getattr(self.env, "controller", None)
        fetch = getattr(controller, "get_accessibility_tree", None)
        frame = screen.read(obs or {}, fetch)
        now = time.monotonic()
        seconds = round(now - self._last_step, 2) if self._last_step else None
        self._last_step = now
        if frame is None:
            return
        _append(self.spool_dir / f"{self._task_id}.jsonl", {"type": "step", "ts": _now(), "seconds": seconds, **frame})

    def predict(self, instruction, obs=None, *args, **kwargs):
        if instruction != self._instruction:
            self._start(instruction)
        self._record(obs)
        if isinstance(obs, dict) and "instruction" in obs:
            obs = {**obs, "instruction": self._augmented}
        return self.inner.predict(self._augmented, obs, *args, **kwargs)

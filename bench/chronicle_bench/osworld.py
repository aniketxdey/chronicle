"""Swap OSWorld's AnthropicAgent for a MemoryAgent-wrapped one, and count Anthropic API token usage.

Loaded from bench/sitecustomize.py, so it applies in OSWorld's worker processes too. Configured by:

    CHRONICLE_BENCH_ARM     raw | search | chronicle
    CHRONICLE_BENCH_HOME    this arm's Chronicle home (read during a wave, written only between waves)
    CHRONICLE_BENCH_SPOOL   where step recordings and token usage are written
    CHRONICLE_BENCH_BUDGET  context characters (default 8000)
"""

from __future__ import annotations

import functools
import os
import sys

from chronicle_bench import agent, memory


def _wrap_create(create):
    @functools.wraps(create)
    def wrapped(*args, **kwargs):
        response = create(*args, **kwargs)
        agent.record_usage(getattr(response, "usage", None))
        return response

    return wrapped


def _patch_usage() -> None:
    try:
        from anthropic.resources.beta.messages import Messages
    except ImportError:
        print("chronicle_bench: anthropic SDK not found; token usage will not be recorded", file=sys.stderr)
        return

    if not getattr(Messages.create, "_chronicle_bench", False):
        Messages.create = _wrap_create(Messages.create)
        Messages.create._chronicle_bench = True


def install() -> None:
    arm = os.environ["CHRONICLE_BENCH_ARM"]
    spool = os.environ["CHRONICLE_BENCH_SPOOL"]
    budget = int(os.environ.get("CHRONICLE_BENCH_BUDGET") or memory.BUDGET_CHARS)
    memory.use_home(os.environ["CHRONICLE_BENCH_HOME"])

    try:
        import mm_agents.anthropic as package
    except ImportError:
        return

    original = package.AnthropicAgent
    if getattr(original, "_chronicle_bench", False):
        return

    def factory(*args, **kwargs):
        return agent.MemoryAgent(original(*args, **kwargs), env=kwargs.get("env"), arm=arm, spool_dir=spool, budget=budget)

    factory._chronicle_bench = True
    package.AnthropicAgent = factory
    _patch_usage()

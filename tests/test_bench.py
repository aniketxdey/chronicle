from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BENCH = REPO / "bench"
sys.path.insert(0, str(BENCH))

from chronicle_bench import agent, memory, screen  # noqa: E402

import analyze  # noqa: E402
import run_pilot  # noqa: E402

ST = "https://accessibility.ubuntu.example.org/ns/state"
TREE = f"""<desktop-frame xmlns:st="{ST}">
  <application name="Thunderbird">
    <frame name="Inbox - Thunderbird" st:active="false"><label name="old mail" st:showing="true" st:visible="true"/></frame>
  </application>
  <application name="Google Chrome">
    <frame name="Expense policy - Google Chrome" st:active="true">
      <entry name="Address and search bar" st:showing="true" st:visible="true">intranet.example.com/policy</entry>
      <heading name="Travel reimbursement" st:showing="true" st:visible="true"/>
      <paragraph st:showing="true" st:visible="true">Employee ID E-2291 submitted report R-77 on 2026-03-02.</paragraph>
      <label name="hidden" st:showing="false" st:visible="true"/>
    </frame>
  </application>
</desktop-frame>"""


def _spool(path: Path, instruction: str, frames: list[dict]) -> Path:
    lines = [{"type": "task_start", "ts": "2026-11-01T10:00:00+00:00", "arm": "chronicle", "instruction": instruction}]
    for index, frame in enumerate(frames):
        lines.append({"type": "step", "ts": f"2026-11-01T10:00:{10 + index * 5:02d}+00:00", "seconds": 5,
                      "source": "accessibility", "url": None, **frame})
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


@pytest.fixture
def bench_home(tmp_path):
    saved = {key: os.environ.get(key) for key in ("CHRONICLE_HOME", "CHRONICLE_HALF_LIFE_HOURS", "CHRONICLE_DEMO")}
    os.environ.pop("CHRONICLE_DEMO", None)
    memory.use_home(tmp_path / "home")
    yield tmp_path
    from chronicle import db

    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    db.close()


def test_a11y_reads_active_window_only():
    frame = screen.from_a11y(TREE)
    assert frame["app"] == "Google Chrome"
    assert frame["window"] == "Expense policy - Google Chrome"
    assert frame["url"] == "https://intranet.example.com/policy"
    assert "Employee ID E-2291" in frame["text"]
    assert "old mail" not in frame["text"] and "hidden" not in frame["text"]
    assert screen.from_a11y("<not xml") is None


def test_memory_arms_share_budget_and_raw_adds_nothing(bench_home):
    assert memory.context_for("chronicle", "anything") == ""
    spool = _spool(bench_home / "001.jsonl", "File the travel reimbursement for report R-77", [
        {"app": "Google Chrome", "window": "Expense policy - Google Chrome", "text": "Employee ID E-2291 submitted report R-77"},
        {"app": "Thunderbird", "window": "Inbox - Thunderbird", "text": "Reimbursement approved for E-2291 by Dana Ortiz"},
    ])
    assert memory.ingest_task(spool) is not None
    task = "Find the employee ID for report R-77"
    assert memory.context_for("raw", task) == ""
    found = memory.context_for("search", task)
    pack = memory.context_for("chronicle", task)
    assert "E-2291" in found and "E-2291" in pack
    assert "On screen now" not in pack and memory.EMPTY_NOTE not in pack
    for block in (found, pack):
        assert block.startswith("<earlier_sessions>") and memory.PREAMBLE in block
    small = 300
    for arm in ("search", "chronicle"):
        body = memory.context_for(arm, task, budget=small)
        overhead = len("<earlier_sessions>\n") + len(memory.PREAMBLE) + 2 + len("\n</earlier_sessions>")
        assert len(body) <= overhead + small


def test_tight_budget_keeps_evidence_and_graph():
    markdown = "# Computer context\nTask: t\n\n" + "".join(
        f"## {title}\n" + "x" * 400 + "\n\n"
        for title in ("What you were doing", "Time by app", "Recent activity (oldest first)", "System events",
                      "Habitual transitions", "Topics and searches", "Knowledge graph (all)", "Evidence for the task")
    )
    fitted = memory.fit_sections(markdown, 1000)
    assert "## Evidence for the task" in fitted and "## Knowledge graph (all)" in fitted
    assert "## Time by app" not in fitted and len(fitted) <= 1000
    tight = memory.fit_sections(markdown, 700)
    assert "## Evidence for the task\n" + "x" * 400 in tight and len(tight) <= 700


class FakeInner:
    def __init__(self):
        self.seen = []
        self.resets = 0

    def reset(self, *args, **kwargs):
        self.resets += 1

    def predict(self, instruction, obs):
        self.seen.append((instruction, obs.get("instruction")))
        return "ok", ["pyautogui.click(1, 1)"]


class FakeEnv:
    task_id = "007"

    class controller:  # noqa: N801
        @staticmethod
        def get_accessibility_tree():
            return TREE


def test_wrapper_injects_context_records_steps_and_forwards_attributes(bench_home):
    _spool_path = _spool(bench_home / "001.jsonl", "Open the expense policy", [
        {"app": "Google Chrome", "window": "Expense policy - Google Chrome", "text": "Employee ID E-2291 report R-77"},
    ])
    memory.ingest_task(_spool_path)
    inner = FakeInner()
    wrapped = agent.MemoryAgent(inner, FakeEnv(), arm="chronicle", spool_dir=bench_home / "spool")
    wrapped.task_current_date = "2026-03-05"
    assert inner.task_current_date == "2026-03-05"
    wrapped.reset()
    obs = {"screenshot": b"", "accessibility_tree": None, "instruction": "Find report R-77"}
    for _ in range(2):
        assert wrapped.predict("Find report R-77", obs) == ("ok", ["pyautogui.click(1, 1)"])
    sent, sent_obs = inner.seen[0]
    assert sent.startswith("Find report R-77\n\n<earlier_sessions>") and sent_obs == sent
    assert inner.seen[1][0] == sent
    records = memory.read_spool(bench_home / "spool" / "007.jsonl")
    assert [r["type"] for r in records] == ["task_start", "step", "step"]
    assert records[0]["arm"] == "chronicle" and records[0]["context_chars"] > 0
    assert records[1]["app"] == "Google Chrome"

    raw = FakeInner()
    agent.MemoryAgent(raw, FakeEnv(), arm="raw", spool_dir=bench_home / "raw").predict("Find report R-77", dict(obs))
    assert raw.seen[0][0] == "Find report R-77"


def test_select_is_stratified_and_deterministic():
    meta = {"chrome": [f"c{i}" for i in range(30)], "gimp": [f"g{i}" for i in range(10)]}
    first = run_pilot.select(meta, None, 8, seed=0)
    assert first == run_pilot.select(meta, None, 8, seed=0)
    assert sum(d == "chrome" for d, _ in first) == 6 and sum(d == "gimp" for d, _ in first) == 2
    assert run_pilot.select(meta, {"c1", "g3", "zz"}, None, seed=1) in ([["chrome", "c1"], ["gimp", "g3"]], [["gimp", "g3"], ["chrome", "c1"]])


def test_mcnemar_and_paired_analysis(tmp_path):
    assert analyze.mcnemar_exact(0, 6) == pytest.approx(2 / 64)
    assert analyze.mcnemar_exact(3, 3) == 1.0
    tasks = [["tasks", f"{i:03d}"] for i in range(6)]
    (tmp_path / "plan.json").write_text(json.dumps({
        "created": "x", "osworld_commit": None, "chronicle_commit": None, "meta": "m", "only": None, "seed": 0,
        "wave_size": 2, "runner_args": [], "arms": ["raw", "chronicle"], "tasks": tasks,
    }))
    scores = {"raw": [0, 0, 1, 0.5, 0, 0], "chronicle": [1, 0, 1, 0.75, 1, None]}
    for arm, values in scores.items():
        for (_, task), value in zip(tasks, values):
            if value is None:
                continue
            result = tmp_path / arm / "results" / "claude" / "tasks" / task / "result.txt"
            result.parent.mkdir(parents=True)
            result.write_text(f"{value}\n")
    report = analyze.analyze(tmp_path)
    comparison = report["comparisons"][0]
    assert comparison["arms"] == ["chronicle", "raw"] and comparison["tasks"] == 5
    assert comparison["mean_diff"] == pytest.approx((1 + 0 + 0 + 0.25 + 1) / 5)
    assert (comparison["only_a_success"], comparison["only_b_success"]) == (2, 0)
    assert "chronicle vs raw (all)" in analyze.markdown(report, "2026-11-02")


def test_sitecustomize_wraps_agent_in_a_fresh_interpreter(tmp_path):
    fake = tmp_path / "osworld" / "mm_agents" / "anthropic"
    fake.mkdir(parents=True)
    (fake.parent / "__init__.py").write_text("")
    (fake / "__init__.py").write_text(
        "class AnthropicAgent:\n"
        "    def __init__(self, env=None, **kw):\n        self.env = env\n"
        "    def reset(self, *a, **k):\n        pass\n"
        "    def predict(self, instruction, obs=None):\n        return instruction, []\n"
    )
    script = (
        "from mm_agents.anthropic import AnthropicAgent\n"
        "class Env:\n    task_id = 't1'\n    controller = None\n"
        "agent = AnthropicAgent(env=Env())\n"
        "print(type(agent).__name__)\n"
        "print(agent.predict('do it', {'screenshot': b''})[0])\n"
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(BENCH), str(REPO), str(tmp_path / "osworld")]),
               CHRONICLE_BENCH_ARM="search", CHRONICLE_BENCH_HOME=str(tmp_path / "home"),
               CHRONICLE_BENCH_SPOOL=str(tmp_path / "spool"))
    done = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True)
    assert done.stdout.splitlines() == ["MemoryAgent", "do it"]
    records = memory.read_spool(tmp_path / "spool" / "t1.jsonl")
    assert records[0]["type"] == "task_start" and records[0]["arm"] == "search"


def test_dry_run_writes_plan(tmp_path):
    osworld = tmp_path / "osworld"
    (osworld / "evaluation_examples").mkdir(parents=True)
    (osworld / "evaluation_examples" / "test.json").write_text(json.dumps({"tasks": [f"{i:03d}" for i in range(10)]}))
    (osworld / "evaluation_examples" / "only.json").write_text(json.dumps({"x": ["001", "002", "003", "004", "005"]}))
    out = tmp_path / "run"
    run_pilot.main(["--osworld", str(osworld), "--meta", "evaluation_examples/test.json", "--only",
                    "evaluation_examples/only.json", "--select", "4", "--out", str(out), "--dry-run"])
    plan = json.loads((out / "plan.json").read_text())
    assert len(plan["tasks"]) == 4 and {t for _, t in plan["tasks"]} <= {"001", "002", "003", "004", "005"}
    assert [len(w) for w in plan["waves"]] == [4]
    assert (out / "chronicle" / "waves" / "wave_000.json").exists()

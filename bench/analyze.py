"""Score a benchmark run: per-arm results, paired differences, McNemar tests, order split, tokens, and cost.

    python bench/analyze.py bench/runs/v2-pilot --price-in 3 --price-out 15 --price-date 2026-11-02 [--json out.json]

Scores come from each task's result.txt. A score of 1 counts as binary success; the score itself is the
partial score. Check that reading against the benchmark's own scorer on the first run (see PROTOCOL.md).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(BENCH))

from run_pilot import result_path  # noqa: E402

BOOTSTRAP = 10_000


def read_score(path: Path | None) -> float | None:
    if path is None:
        return None
    try:
        return float(path.read_text().strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


def task_record(out: Path, arm: str, task: str) -> dict:
    path = result_path(out, arm, task)
    score = read_score(path)
    steps = 0
    if path is not None and (path.parent / "traj.jsonl").exists():
        steps = sum(1 for line in (path.parent / "traj.jsonl").read_text().splitlines() if '"step_num"' in line)
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    usage_file = out / arm / "spool" / f"{task}.usage.jsonl"
    if usage_file.exists():
        for line in usage_file.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                for key in usage:
                    usage[key] += int(row.get(key) or 0)
    return {"score": score, "success": None if score is None else score >= 1 - 1e-9, "steps": steps, **usage}


def bootstrap_ci(diffs: list[float], seed: int = 0) -> tuple[float, float]:
    if not diffs:
        return (math.nan, math.nan)
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(BOOTSTRAP))
    return means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pair counts."""
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def compare(records: dict, tasks: list[str], a: str, b: str) -> dict:
    """Paired comparison of arm a against arm b over tasks scored in both."""
    both = [t for t in tasks if records[a][t]["score"] is not None and records[b][t]["score"] is not None]
    diffs = [records[a][t]["score"] - records[b][t]["score"] for t in both]
    only_a = sum(1 for t in both if records[a][t]["success"] and not records[b][t]["success"])
    only_b = sum(1 for t in both if records[b][t]["success"] and not records[a][t]["success"])
    low, high = bootstrap_ci(diffs)
    return {
        "arms": [a, b], "tasks": len(both), "mean_diff": sum(diffs) / len(diffs) if diffs else math.nan,
        "ci95": [low, high], "only_a_success": only_a, "only_b_success": only_b,
        "mcnemar_p": mcnemar_exact(only_a, only_b),
    }


def summarize(records: dict, tasks: list[str], arm: str, prices: dict | None) -> dict:
    rows = [records[arm][t] for t in tasks if records[arm][t]["score"] is not None]
    n = len(rows)
    tokens_in = sum(r["input_tokens"] + r["cache_read_input_tokens"] + r["cache_creation_input_tokens"] for r in rows)
    tokens_out = sum(r["output_tokens"] for r in rows)
    cost = None
    if prices and n:
        cost = sum(
            r["input_tokens"] * prices["input"] + r["cache_read_input_tokens"] * prices["cache_read"]
            + r["cache_creation_input_tokens"] * prices["cache_write"] + r["output_tokens"] * prices["output"]
            for r in rows
        ) / 1e6 / n
    return {
        "arm": arm, "tasks": n,
        "mean_score": sum(r["score"] for r in rows) / n if n else math.nan,
        "success_rate": sum(bool(r["success"]) for r in rows) / n if n else math.nan,
        "steps_per_task": sum(r["steps"] for r in rows) / n if n else math.nan,
        "input_tokens_per_task": tokens_in / n if n else math.nan,
        "output_tokens_per_task": tokens_out / n if n else math.nan,
        "cost_per_task_usd": cost,
    }


def analyze(out: Path, prices: dict | None = None) -> dict:
    plan = json.loads((out / "plan.json").read_text())
    arms = plan["arms"]
    order = [task for _, task in plan["tasks"]]
    records = {arm: {task: task_record(out, arm, task) for task in order} for arm in arms}
    dropped = [t for t in order if all(records[arm][t]["score"] is None for arm in arms)]
    tasks = [t for t in order if t not in dropped]
    half = len(tasks) // 2
    pairs = [(a, "raw") for a in ("search", "chronicle") if a in arms and "raw" in arms]
    if "search" in arms and "chronicle" in arms:
        pairs.append(("chronicle", "search"))
    return {
        "plan": {k: plan[k] for k in ("created", "osworld_commit", "chronicle_commit", "meta", "only", "seed", "wave_size", "runner_args")},
        "dropped": dropped,
        "arms": [summarize(records, tasks, arm, prices) for arm in arms],
        "comparisons": [compare(records, tasks, a, b) for a, b in pairs],
        "by_order": {
            "first_half": [compare(records, tasks[:half], a, b) for a, b in pairs],
            "second_half": [compare(records, tasks[half:], a, b) for a, b in pairs],
        },
        "tasks": {t: {arm: records[arm][t] for arm in arms} for t in order},
    }


def _fmt(value, digits: int = 3) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def markdown(report: dict, price_date: str | None) -> str:
    lines = ["| Arm | Tasks | Mean score | Success | Steps | Input tok | Output tok | $/task |", "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in report["arms"]:
        lines.append(
            f"| {row['arm']} | {row['tasks']} | {_fmt(row['mean_score'])} | {_fmt(row['success_rate'])} | {_fmt(row['steps_per_task'], 1)} "
            f"| {_fmt(row['input_tokens_per_task'], 0)} | {_fmt(row['output_tokens_per_task'], 0)} | {_fmt(row['cost_per_task_usd'], 2)} |"
        )
    lines += ["", "| Comparison | Tasks | Mean diff | 95% CI | Success only in first / second | McNemar p |", "| --- | --- | --- | --- | --- | --- |"]
    for label, rows in (("all", report["comparisons"]), ("first half", report["by_order"]["first_half"]),
                        ("second half", report["by_order"]["second_half"])):
        for row in rows:
            a, b = row["arms"]
            lines.append(
                f"| {a} vs {b} ({label}) | {row['tasks']} | {_fmt(row['mean_diff'])} | [{_fmt(row['ci95'][0])}, {_fmt(row['ci95'][1])}] "
                f"| {row['only_a_success']} / {row['only_b_success']} | {_fmt(row['mcnemar_p'])} |"
            )
    if report["dropped"]:
        lines += ["", f"Dropped for infrastructure failures in every arm: {', '.join(report['dropped'])}"]
    if price_date:
        lines += ["", f"Costs use list prices taken on {price_date}."]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("--price-in", type=float, help="USD per million input tokens")
    parser.add_argument("--price-out", type=float, help="USD per million output tokens")
    parser.add_argument("--price-cache-read", type=float, help="USD per million cache-read tokens (default: --price-in)")
    parser.add_argument("--price-cache-write", type=float, help="USD per million cache-write tokens (default: --price-in)")
    parser.add_argument("--price-date")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    prices = None
    if args.price_in is not None and args.price_out is not None:
        prices = {
            "input": args.price_in, "output": args.price_out,
            "cache_read": args.price_in if args.price_cache_read is None else args.price_cache_read,
            "cache_write": args.price_in if args.price_cache_write is None else args.price_cache_write,
        }
    report = analyze(args.out, prices)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n")
    print(markdown(report, args.price_date))


if __name__ == "__main__":
    main()

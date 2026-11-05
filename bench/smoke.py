"""Smoke test of Chronicle's memory path, with no agent and no VM. See "Smoke test" in bench/PROTOCOL.md.

    python bench/smoke.py --data bench/runs/datasets --out bench/runs/smoke [--lme 60] [--seed 0]

Two parts:

Retrieval. LoCoMo and a stratified LongMemEval subset are turned into the same spool files the OSWorld recorder
writes, one dialogue turn per frame, and ingested through chronicle_bench.memory into a fresh Chronicle home per
conversation or question. Each question then goes through three arms at the same character budget: chronicle and
search (the OSWorld arms) and recency (newest frames, a floor). The metric is evidence recall: did the annotated
evidence turn reach the text the agent would be given? This measures retrieval on conversational data. It says
nothing about whether an agent completes more tasks.

Harness. run_pilot.py --dry-run plans both OSWorld benchmarks, then real OSWorld-Verified instructions with
synthetic screen text go through ingest and context_for, checking the budget and that nothing outside the
recorded fields can reach the context. It produces no scores.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import platform
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BENCH = Path(__file__).resolve().parent
REPO = BENCH.parent
sys.path[:0] = [str(BENCH), str(REPO)]

from analyze import BOOTSTRAP, bootstrap_ci, mcnemar_exact  # noqa: E402
from chronicle_bench import memory  # noqa: E402

ARMS = ("chronicle", "search", "recency")
PAIRS = (("chronicle", "search"), ("chronicle", "recency"), ("search", "recency"))
SHINGLE = 6
LOCOMO_CATEGORIES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop", 5: "adversarial"}
CANARY = "ZZCANARYEVALUATORLEAK"
BLOCK_OVERHEAD = len("<earlier_sessions>\n") + len(memory.PREAMBLE) + len("\n\n") + len("\n</earlier_sessions>")


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def normalized(text: str) -> str:
    return f" {' '.join(tokens(text))} "


def evidence_found(evidence: str, output_normalized: str, size: int = SHINGLE) -> bool:
    """True if any run of ``size`` consecutive words of the evidence (or all of it, if shorter) is in the output."""
    words = tokens(evidence)
    if not words:
        return False
    windows = [words] if len(words) <= size else (words[i:i + size] for i in range(len(words) - size + 1))
    return any(f" {' '.join(window)} " in output_normalized for window in windows)


def cluster_ci(values_by_cluster: list[list[float]], seed: int = 0, resamples: int = BOOTSTRAP) -> tuple[float, float]:
    """95% bootstrap interval of the mean, resampling whole clusters (questions about one conversation aren't independent)."""
    clusters = [values for values in values_by_cluster if values]
    if not clusters:
        return (float("nan"), float("nan"))
    totals = [(sum(values), len(values)) for values in clusters]
    rng = random.Random(seed)
    means = []
    for _ in range(resamples):
        picked = rng.choices(totals, k=len(totals))
        means.append(sum(item[0] for item in picked) / sum(item[1] for item in picked))
    means.sort()
    return means[int(0.025 * resamples)], means[int(0.975 * resamples) - 1]


# Dataset conversion: every item is {id, sessions: [{label, ts, turns: [{key, speaker, text}]}], questions: [...]}.


def _locomo_time(text: str) -> datetime:
    return datetime.strptime(text, "%I:%M %p on %d %B, %Y").replace(tzinfo=timezone.utc)


def load_locomo(path: Path) -> list[dict]:
    items = []
    for sample in json.loads(path.read_text()):
        conversation = sample["conversation"]
        sessions = []
        numbers = sorted(int(k.split("_")[1]) for k in conversation if re.fullmatch(r"session_\d+", k))
        for number in numbers:
            turns = []
            for turn in conversation[f"session_{number}"]:
                text = turn["text"]
                if turn.get("blip_caption"):
                    text += f" [shared an image: {turn['blip_caption']}]"
                turns.append({"key": turn["dia_id"], "speaker": turn["speaker"], "text": text, "evidence_text": turn["text"]})
            sessions.append({"label": f"session {number}", "ts": _locomo_time(conversation[f"session_{number}_date_time"]), "turns": turns})
        lookup = {turn["key"]: turn["evidence_text"] for session in sessions for turn in session["turns"]}
        questions = []
        for index, qa in enumerate(sample["qa"]):
            keys = re.findall(r"D\d+:\d+", " ".join(qa.get("evidence") or []))
            found = [lookup[key] for key in dict.fromkeys(keys) if key in lookup]
            questions.append({"id": f"{sample['sample_id']}/{index}", "question": qa["question"],
                              "category": LOCOMO_CATEGORIES[qa["category"]], "evidence": found})
        items.append({"id": str(sample["sample_id"]), "sessions": sessions, "questions": questions})
    return items


def _lme_time(text: str) -> datetime:
    return datetime.strptime(re.sub(r" \(\w+\)", "", text), "%Y/%m/%d %H:%M").replace(tzinfo=timezone.utc)


def load_longmemeval(path: Path, count: int, seed: int) -> list[dict]:
    """A subset stratified by question type (largest remainder). Abstention questions have no evidence to find."""
    rows = [row for row in json.loads(path.read_text()) if not row["question_id"].endswith("_abs")]
    pools: dict[str, list[dict]] = {}
    for row in rows:
        pools.setdefault(row["question_type"], []).append(row)
    count = min(count, len(rows))
    quotas = {kind: count * len(pool) / len(rows) for kind, pool in pools.items()}
    taken = {kind: int(quota) for kind, quota in quotas.items()}
    for kind in sorted(quotas, key=lambda k: (taken[k] - quotas[k], k))[: count - sum(taken.values())]:
        taken[kind] += 1
    rng = random.Random(seed)
    items = []
    for kind in sorted(pools):
        for row in rng.sample(sorted(pools[kind], key=lambda r: r["question_id"]), taken[kind]):
            sessions, evidence = [], []
            for number, (stamp, session) in enumerate(zip(row["haystack_dates"], row["haystack_sessions"])):
                turns = []
                for index, turn in enumerate(session):
                    turns.append({"key": f"{number}:{index}", "speaker": turn["role"], "text": turn["content"]})
                    if turn.get("has_answer"):
                        evidence.append(turn["content"])
                sessions.append({"label": f"session {number}", "ts": _lme_time(stamp), "turns": turns})
            items.append({"id": row["question_id"], "sessions": sessions, "questions": [
                {"id": row["question_id"], "question": row["question"], "category": kind, "evidence": evidence}]})
    return items


def write_spool(path: Path, app: str, session: dict) -> None:
    from chronicle.timeutil import utc_iso

    records = [{"type": "task_start", "ts": utc_iso(session["ts"]), "instruction": session["label"], "arm": "smoke"}]
    for index, turn in enumerate(session["turns"]):
        records.append({"type": "step", "ts": utc_iso(session["ts"] + timedelta(seconds=30 * (index + 1))), "app": app,
                        "window": session["label"], "text": f"{turn['speaker']}: {turn['text']}", "source": "accessibility", "seconds": 30})
    path.write_text("".join(json.dumps(record) + "\n" for record in records))


def _replay_stop(session_id: int | None, session: dict) -> None:
    """Move the stop to when the history ended. sessions.stop stamps the wall clock, which for years-old data would
    put every stop event ahead of every frame in recency-ordered lists, a problem a live recording doesn't have."""
    from chronicle import db
    from chronicle.timeutil import utc_iso

    if session_id is None:
        return
    stamp = utc_iso(session["ts"] + timedelta(seconds=30 * (len(session["turns"]) + 1)))
    with db.conn() as connection:
        connection.execute("UPDATE ui_events SET timestamp = ? WHERE session_id = ? AND event_type = 'session_stop'", (stamp, session_id))
        connection.execute("UPDATE sessions SET stopped_at = ? WHERE id = ?", (stamp, session_id))


def recency_text(budget: int) -> str:
    """Newest frames first, whole, until the budget is spent. No query: this is what history alone is worth."""
    from chronicle import db

    lines = ["# Most recent activity (newest first)", ""]
    used = sum(len(line) + 1 for line in lines)
    with db.conn() as connection:
        rows = connection.execute("SELECT timestamp, app_name, window_name, full_text FROM frames ORDER BY timestamp DESC, id DESC").fetchall()
    for row in rows:
        line = f"- {row['timestamp']} {row['app_name']} · {row['window_name']}: {' '.join((row['full_text'] or '').split())}"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
    return f"<earlier_sessions>\n{memory.PREAMBLE}\n\n" + "\n".join(lines) + "\n</earlier_sessions>"


def run_item(job: tuple[dict, str]) -> dict:
    """Runs in its own process, because Chronicle's home is per process."""
    item, app = job
    home = Path(tempfile.mkdtemp(prefix="chronicle-smoke-"))
    os.environ["CHRONICLE_DISABLE_JOBS"] = "1"
    try:
        memory.use_home(home / "home")
        started = time.perf_counter()
        frames = 0
        for index, session in enumerate(item["sessions"]):
            spool = home / f"{index:04d}.jsonl"
            write_spool(spool, app, session)
            _replay_stop(memory.ingest_task(spool), session)
            frames += len(session["turns"])
        ingest_seconds = time.perf_counter() - started
        rows = []
        for question in item["questions"]:
            if not question["evidence"]:
                continue
            row = {"id": question["id"], "item": item["id"], "category": question["category"], "evidence": len(question["evidence"])}
            for arm in ARMS:
                begun = time.perf_counter()
                text = recency_text(memory.BUDGET_CHARS) if arm == "recency" else memory.context_for(arm, question["question"], memory.BUDGET_CHARS)
                seconds = time.perf_counter() - begun
                output = normalized(text)
                found = sum(evidence_found(evidence, output) for evidence in question["evidence"])
                row[arm] = {"recall": found / len(question["evidence"]), "all": found == len(question["evidence"]),
                            "chars": len(text), "seconds": seconds}
            rows.append(row)
        dropped = sum(1 for question in item["questions"] if not question["evidence"])
        return {"id": item["id"], "frames": frames, "sessions": len(item["sessions"]), "ingest_seconds": ingest_seconds, "rows": rows, "dropped": dropped}
    finally:
        shutil.rmtree(home, ignore_errors=True)


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def score(results: list[dict]) -> dict:
    rows = [row for result in results for row in result["rows"]]
    by_item: dict[str, list[dict]] = {}
    for row in rows:
        by_item.setdefault(row["item"], []).append(row)
    arms = {}
    for arm in ARMS:
        seconds = [row[arm]["seconds"] for row in rows]
        chars = [row[arm]["chars"] for row in rows]
        arms[arm] = {
            "recall": statistics.fmean(row[arm]["recall"] for row in rows),
            "all_evidence": statistics.fmean(row[arm]["all"] for row in rows),
            "p50_ms": percentile(seconds, 0.5) * 1000, "p95_ms": percentile(seconds, 0.95) * 1000,
            "mean_chars": statistics.fmean(chars), "max_chars": max(chars),
            "over_budget": sum(c > memory.BUDGET_CHARS + BLOCK_OVERHEAD for c in chars),
        }
    comparisons = []
    for a, b in PAIRS:
        diffs = [row[a]["recall"] - row[b]["recall"] for row in rows]
        only_a = sum(row[a]["all"] and not row[b]["all"] for row in rows)
        only_b = sum(row[b]["all"] and not row[a]["all"] for row in rows)
        comparisons.append({
            "arms": [a, b], "mean_diff": statistics.fmean(diffs), "ci95_question": list(bootstrap_ci(diffs)),
            "ci95_cluster": list(cluster_ci([[row[a]["recall"] - row[b]["recall"] for row in group] for group in by_item.values()])),
            "only_a": only_a, "only_b": only_b, "mcnemar_p": mcnemar_exact(only_a, only_b),
        })
    categories: dict[str, dict] = {}
    for name in sorted({row["category"] for row in rows}):
        chosen = [row for row in rows if row["category"] == name]
        categories[name] = {"questions": len(chosen), **{arm: statistics.fmean(row[arm]["recall"] for row in chosen) for arm in ARMS}}
    ingest = [result["ingest_seconds"] for result in results]
    frames = [result["frames"] for result in results]
    return {
        "items": len(results), "questions": len(rows), "dropped_no_evidence": sum(result["dropped"] for result in results),
        "evidence_turns": sum(row["evidence"] for row in rows), "frames": sum(frames),
        "ingest_seconds_total": sum(ingest), "ms_per_frame": 1000 * sum(ingest) / max(1, sum(frames)),
        "arms": arms, "comparisons": comparisons, "categories": categories,
    }


# Harness validation.


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for child in value.values() for text in _strings(child)]
    if isinstance(value, list):
        return [text for child in value for text in _strings(child)]
    return []


def harness_context(job: tuple[list[dict], int]) -> dict:
    """Own process: ingest tasks in waves with synthetic screen text and canaries in every field ingest should ignore."""
    tasks, wave = job
    home = Path(tempfile.mkdtemp(prefix="chronicle-smoke-harness-"))
    os.environ["CHRONICLE_DISABLE_JOBS"] = "1"
    try:
        memory.use_home(home / "home")
        leaks, over, empty, longest, checked = [], 0, 0, 0, 0
        forbidden = [CANARY]
        # Node ids are slugs, so "Google Chrome" in an instruction shows up as google-chrome: compare word by word.
        allowed = normalized(" ".join(task["instruction"] for task in tasks))
        for task in tasks:
            forbidden += [text for text in task["evaluator_strings"] if len(text) >= 12 and normalized(text) not in allowed]
        forbidden = list(dict.fromkeys(forbidden))
        for start in range(0, len(tasks), wave):
            for task in tasks[start:start + wave]:
                if start:
                    for arm in ("search", "chronicle"):
                        text = memory.context_for(arm, task["instruction"], memory.BUDGET_CHARS)
                        checked += 1
                        empty += not text
                        longest = max(longest, len(text))
                        over += len(text) > memory.BUDGET_CHARS + BLOCK_OVERHEAD
                        leaks += [{"task": task["id"], "arm": arm, "string": s[:60]} for s in forbidden if s in text]
            for task in tasks[start:start + wave]:
                spool = home / f"{task['id']}.jsonl"
                stamp = "2026-01-01T00:00:00.000Z"
                records = [{"type": "task_start", "ts": stamp, "instruction": task["instruction"], "arm": "smoke", "config": CANARY}]
                for step in range(4):
                    records.append({"type": "step", "ts": stamp, "app": task["domain"], "window": f"synthetic window {step}",
                                    "text": f"[synthetic screen text, step {step}] {task['instruction']}", "source": "accessibility",
                                    "seconds": 5, "reward": CANARY, "info": CANARY, "evaluator": CANARY})
                records.append({"type": "result", "ts": stamp, "score": CANARY, "text": CANARY})
                spool.write_text("".join(json.dumps(record) + "\n" for record in records))
                memory.ingest_task(spool)
        return {"contexts_checked": checked, "over_budget": over, "empty": empty, "longest_chars": longest,
                "forbidden_strings": len(forbidden), "leaks": leaks}
    finally:
        shutil.rmtree(home, ignore_errors=True)


def _commit(path: Path) -> str | None:
    done = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False)
    return done.stdout.strip() or None


def _dry_run(osworld: Path, out: Path, meta: str, only: str | None, select: int) -> dict:
    command = [sys.executable, str(BENCH / "run_pilot.py"), "--osworld", str(osworld), "--meta", meta, "--select", str(select),
               "--seed", "0", "--out", str(out), "--dry-run"]
    if only:
        command += ["--only", only]
    shutil.rmtree(out, ignore_errors=True)
    subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads((out / "plan.json").read_text())


def validate_harness(data: Path, out: Path, pool_size: int) -> dict:
    report: dict = {}
    v2, verified = data / "OSWorld-V2", data / "OSWorld"
    examples = v2 / "evaluation_examples"
    v2_tasks = sum(len(ids) for ids in json.loads((examples / "test_v2.json").read_text()).values())
    implicit = sum(len(ids) for ids in json.loads((examples / "implicit_state_inference.json").read_text()).values())
    plan_v2 = _dry_run(v2, out / "plan-v2", "evaluation_examples/test_v2.json", "evaluation_examples/implicit_state_inference.json", 20)
    report["osworld_v2"] = {"commit": _commit(v2), "tasks": v2_tasks, "implicit_state": implicit,
                            "planned": len(plan_v2["tasks"]), "waves": len(plan_v2["waves"])}
    listing = json.loads((verified / "evaluation_examples/test_nogdrive.json").read_text())
    plan_verified = _dry_run(verified, out / "plan-verified", "evaluation_examples/test_nogdrive.json", None, 60)
    report["osworld_verified"] = {"commit": _commit(verified), "tasks": sum(map(len, listing.values())), "domains": len(listing),
                                  "planned": len(plan_verified["tasks"]), "waves": len(plan_verified["waves"])}
    tasks = []
    for domain, task_id in plan_verified["tasks"]:
        config = json.loads((verified / "evaluation_examples/examples" / domain / f"{task_id}.json").read_text())
        tasks.append({"id": task_id, "domain": domain, "instruction": config["instruction"],
                      "evaluator_strings": _strings(config.get("evaluator"))})
    with multiprocessing.get_context("spawn").Pool(1) as pool:
        report["context_path"] = pool.apply(harness_context, ((tasks, plan_verified["wave_size"]),))
    return report


def _pct(value: float) -> str:
    return f"{100 * value:.1f}%"


def markdown(report: dict) -> str:
    lines = []
    for name, part in report["datasets"].items():
        lines += [f"## {name}: {part['questions']} questions over {part['items']} histories, {part['frames']} frames", "",
                  "| Arm | Evidence recall | All evidence found | p50 ms | p95 ms | Mean chars | Over budget |", "| --- | --- | --- | --- | --- | --- | --- |"]
        for arm, row in part["arms"].items():
            lines.append(f"| {arm} | {_pct(row['recall'])} | {_pct(row['all_evidence'])} | {row['p50_ms']:.1f} | {row['p95_ms']:.1f} | {row['mean_chars']:.0f} | {row['over_budget']} |")
        lines += ["", "| Comparison | Recall diff (points) | 95% CI, question | 95% CI, history | Only first / only second | McNemar p |", "| --- | --- | --- | --- | --- | --- |"]
        for row in part["comparisons"]:
            a, b = row["arms"]
            ci, cluster = row["ci95_question"], row["ci95_cluster"]
            lines.append(f"| {a} vs {b} | {100 * row['mean_diff']:+.1f} | [{100 * ci[0]:+.1f}, {100 * ci[1]:+.1f}] | [{100 * cluster[0]:+.1f}, {100 * cluster[1]:+.1f}] | {row['only_a']} / {row['only_b']} | {row['mcnemar_p']:.3g} |")
        lines += ["", "| Category | Questions | chronicle | search | recency |", "| --- | --- | --- | --- | --- |"]
        for category, row in part["categories"].items():
            lines.append(f"| {category} | {row['questions']} | {_pct(row['chronicle'])} | {_pct(row['search'])} | {_pct(row['recency'])} |")
        lines += ["", f"Ingest and graph build: {part['ingest_seconds_total']:.1f}s total, {part['ms_per_frame']:.1f} ms per frame.", ""]
    if report.get("harness"):
        lines += ["## OSWorld harness", "", "```json", json.dumps(report["harness"], indent=2), "```"]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=BENCH / "runs" / "datasets")
    parser.add_argument("--out", type=Path, default=BENCH / "runs" / "smoke")
    parser.add_argument("--lme", type=int, default=60, help="LongMemEval questions to sample; each needs its own graph")
    parser.add_argument("--locomo-conversations", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--no-harness", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    report: dict = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "chronicle_commit": _commit(REPO),
        "python": platform.python_version(), "machine": platform.machine(), "workers": args.workers, "seed": args.seed,
        "budget_chars": memory.BUDGET_CHARS, "shingle_words": SHINGLE, "llm": "off", "datasets": {},
    }
    sources = {
        "LoCoMo": (load_locomo(args.data / "locomo/data/locomo10.json")[: args.locomo_conversations], "Messages"),
        "LongMemEval": (load_longmemeval(args.data / "longmemeval/longmemeval_s", args.lme, args.seed), "Assistant"),
    }
    rows_file = (args.out / "rows.jsonl").open("w")
    for name, (items, app) in sources.items():
        if not items:
            continue
        started = time.perf_counter()
        with multiprocessing.get_context("spawn").Pool(args.workers, maxtasksperchild=1) as pool:
            results = []
            for result in pool.imap_unordered(run_item, [(item, app) for item in items]):
                results.append(result)
                print(f"[{name}] {len(results)}/{len(items)} {result['id']}: {len(result['rows'])} questions, {result['ingest_seconds']:.1f}s ingest", flush=True)
        for result in results:
            for row in result["rows"]:
                rows_file.write(json.dumps({"dataset": name, **row}) + "\n")
        report["datasets"][name] = {**score(results), "wall_seconds": time.perf_counter() - started}
    rows_file.close()
    if not args.no_harness:
        report["harness"] = validate_harness(args.data.resolve(), args.out.resolve() / "harness", args.workers)
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    text = markdown(report)
    (args.out / "report.md").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()

"""Run the Chronicle benchmark pilot on OSWorld in waves (see bench/PROTOCOL.md).

    python bench/run_pilot.py --osworld ~/OSWorld-V2 --meta evaluation_examples/test_v2.json \
        --only evaluation_examples/implicit_state_inference.json --select 20 --seed 0 \
        --out bench/runs/v2-pilot --dry-run
    python bench/run_pilot.py --osworld ~/OSWorld-V2 --out bench/runs/v2-pilot -- --provider_name aws --max_steps 150

The first call writes plan.json (task selection, order, waves). Later calls reuse it and skip finished waves.
Arguments after ``--`` are passed to the OSWorld runner unchanged and must stay the same for the whole run.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BENCH = Path(__file__).resolve().parent
REPO = BENCH.parent
sys.path.insert(0, str(BENCH))

from chronicle_bench.memory import ARMS, read_spool  # noqa: E402


def _git(path: Path) -> str | None:
    done = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=False)
    return done.stdout.strip() or None


def select(meta: dict, only: set[str] | None, count: int | None, seed: int) -> list[list[str]]:
    """Stratified sample by domain (largest remainder), then one shuffled order shared by every arm."""
    rng = random.Random(seed)
    pools = {domain: sorted(i for i in ids if only is None or i in only) for domain, ids in meta.items()}
    pools = {domain: ids for domain, ids in pools.items() if ids}
    total = sum(len(ids) for ids in pools.values())
    count = min(count or total, total)
    quotas = {domain: count * len(ids) / total for domain, ids in pools.items()}
    taken = {domain: int(q) for domain, q in quotas.items()}
    for domain in sorted(quotas, key=lambda d: (taken[d] - quotas[d], d))[: count - sum(taken.values())]:
        taken[domain] += 1
    chosen = [[domain, task] for domain, ids in sorted(pools.items()) for task in rng.sample(ids, taken[domain])]
    rng.shuffle(chosen)
    return chosen


def load_plan(args) -> dict:
    path = args.out / "plan.json"
    if path.exists():
        plan = json.loads(path.read_text())
    else:
        if not args.meta:
            raise SystemExit("--meta is required for a new run")
        meta = json.loads((args.osworld / args.meta).read_text())
        only = None
        if args.only:
            only = {task for ids in json.loads((args.osworld / args.only).read_text()).values() for task in ids}
        tasks = select(meta, only, args.select, args.seed)
        plan = {
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "osworld": str(args.osworld), "osworld_commit": _git(args.osworld), "chronicle_commit": _git(REPO),
            "meta": args.meta, "only": args.only, "seed": args.seed, "select": args.select,
            "wave_size": args.wave_size, "arms": args.arms, "runner": args.runner, "runner_args": None,
            "tasks": tasks,
            "waves": [tasks[i:i + args.wave_size] for i in range(0, len(tasks), args.wave_size)],
        }
    if args.runner_args:
        if plan["runner_args"] not in (None, args.runner_args):
            raise SystemExit(f"runner arguments differ from this run's plan.json: {plan['runner_args']}")
        plan["runner_args"] = args.runner_args
    args.out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, indent=2) + "\n")
    return plan


def _env(plan: dict, arm: str, out: Path) -> dict:
    env = dict(os.environ)
    paths = [str(BENCH), str(REPO), plan["osworld"], env.get("PYTHONPATH", "")]
    env.update(
        PYTHONPATH=os.pathsep.join(p for p in paths if p),
        CHRONICLE_BENCH_ARM=arm,
        CHRONICLE_BENCH_HOME=str(out / arm / "home"),
        CHRONICLE_BENCH_SPOOL=str(out / arm / "spool"),
    )
    return env


def _command(plan: dict, wave_meta: Path, results: Path, size: int, uv: str) -> list[str]:
    return [*shlex.split(uv), "python", plan["runner"], "--test_all_meta_path", str(wave_meta),
            "--result_dir", str(results), "--num_envs", str(size), *(plan["runner_args"] or [])]


def result_path(out: Path, arm: str, task: str) -> Path | None:
    found = sorted((out / arm / "results").glob(f"**/{task}/result.txt"))
    return found[0] if found else None


def run_wave(plan: dict, out: Path, index: int, tasks: list[list[str]], arms: list[str], uv: str, dry_run: bool) -> None:
    procs = []
    for arm in arms:
        meta: dict[str, list[str]] = {}
        for domain, task in tasks:
            meta.setdefault(domain, []).append(task)
        wave_meta = out / arm / "waves" / f"wave_{index:03d}.json"
        wave_meta.parent.mkdir(parents=True, exist_ok=True)
        wave_meta.write_text(json.dumps(meta, indent=2) + "\n")
        command = _command(plan, wave_meta.resolve(), (out / arm / "results").resolve(), len(tasks), uv)
        print(f"[{arm}] wave {index}: {shlex.join(command)}")
        if dry_run:
            continue
        log = out / arm / "logs" / f"wave_{index:03d}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        handle = log.open("a")
        procs.append((arm, subprocess.Popen(command, cwd=plan["osworld"], env=_env(plan, arm, out.resolve()),
                                            stdout=handle, stderr=subprocess.STDOUT), handle))
    for arm, proc, handle in procs:
        proc.wait()
        handle.close()
        if proc.returncode:
            print(f"[{arm}] wave {index}: runner exited with {proc.returncode}; see logs/wave_{index:03d}.log")


def check_spools(out: Path, arm: str, tasks: list[list[str]]) -> list[Path]:
    spools = []
    for _, task in tasks:
        spool = out / arm / "spool" / f"{task}.jsonl"
        if not spool.exists():
            raise SystemExit(f"[{arm}] no recording for task {task}; is the wrapper installed? (see {spool.parent})")
        arms = {r.get("arm") for r in read_spool(spool) if r["type"] == "task_start"}
        if arms != {arm}:
            raise SystemExit(f"[{arm}] {spool} was recorded by arm(s) {sorted(map(str, arms))}")
        spools.append(spool)
    return spools


def ingest(out: Path, arm: str, spools: list[Path]) -> None:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(BENCH), str(REPO), os.environ.get("PYTHONPATH", "")]))
    subprocess.run([sys.executable, "-m", "chronicle_bench.memory", "ingest", "--home", str(out / arm / "home"),
                    *map(str, spools)], env=env, check=True)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    runner_args = argv[argv.index("--") + 1:] if "--" in argv else []
    argv = argv[: argv.index("--")] if "--" in argv else argv
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--osworld", type=Path, required=True)
    parser.add_argument("--meta", help="task list relative to the OSWorld checkout, {domain: [ids]}")
    parser.add_argument("--only", help="keep only ids listed in this file, e.g. a phenomenon list")
    parser.add_argument("--select", type=int)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--wave-size", type=int, default=4)
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--runner", default="scripts/python/run_multienv_claude.py")
    parser.add_argument("--uv", default="uv run", help="how to start Python in the OSWorld project")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    args.osworld = args.osworld.expanduser().resolve()
    args.arms = [arm for arm in args.arms.split(",") if arm]
    if set(args.arms) - set(ARMS):
        raise SystemExit(f"--arms must be drawn from {ARMS}")
    args.runner_args = runner_args or None
    plan = load_plan(args)
    out = args.out
    print(f"{len(plan['tasks'])} tasks in {len(plan['waves'])} waves; arms {plan['arms']}; plan at {out / 'plan.json'}")
    if not args.dry_run and plan["runner_args"] is None:
        raise SystemExit("pass the OSWorld runner arguments after -- on the first real run")

    for index, wave in enumerate(plan["waves"]):
        done = {arm: out / arm / "waves" / f"wave_{index:03d}.done" for arm in plan["arms"]}
        pending = [arm for arm in plan["arms"] if not done[arm].exists()]
        if not pending:
            continue
        run_wave(plan, out, index, wave, pending, args.uv, args.dry_run)
        if args.dry_run:
            continue
        for arm in pending:
            missing = [[d, t] for d, t in wave if result_path(out, arm, t) is None]
            if missing:
                print(f"[{arm}] wave {index}: retrying {len(missing)} task(s) without a result once")
                for _, task in missing:
                    (out / arm / "spool" / f"{task}.jsonl").unlink(missing_ok=True)
                    (out / arm / "spool" / f"{task}.usage.jsonl").unlink(missing_ok=True)
                run_wave(plan, out, index, missing, [arm], args.uv, False)
            scored = [[d, t] for d, t in wave if result_path(out, arm, t) is not None]
            spools = check_spools(out, arm, scored)
            if arm != "raw" and spools:
                ingest(out, arm, spools)
            done[arm].write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n")


if __name__ == "__main__":
    main()

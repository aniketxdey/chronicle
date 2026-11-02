# Benchmark protocol: Chronicle memory on OSWorld

Status: pilot designed, not yet run. This file is written before any results exist, and changes after the first scored run will be listed under [Changes](#changes).

## Question

Does a computer-use agent complete more of a benchmark's tasks when it starts each task with Chronicle's context pack, built from recordings of its own earlier tasks, than the same agent without it? And does any gain come from the knowledge graph, or would plain search over the same recordings do as well?

## Arms

All three arms use the same model, harness, prompts, step budget, task order, and recorder. They differ only in the text added to the task instruction at the start of each task (and each phase of a multi-phase task).

| Arm | Added to the instruction |
| --- | --- |
| `raw` | Nothing |
| `search` | The 40 recorded frames that best match the instruction, ranked by BM25 over Chronicle's frame text index (an OR query over every distinct instruction term), each with its app, window, URL, and matching snippet |
| `chronicle` | The `get_context` pack for the instruction over the arm's own recordings |

Both memory arms get the same preamble and the same character budget (8,000 characters, about 2,000 tokens). The preamble says the notes come from earlier, different tasks and may be irrelevant. Because the budget is equal, a difference between `search` and `chronicle` reflects what is selected and how it's organized, not how much text the agent gets.

The `search` arm is ranked by relevance rather than going through `search_history`, which orders matches by recency. A recency-ordered list would be a weak baseline, and the point of this arm is to be the strongest simple alternative to the graph. Its results are cut at a line boundary, so the weakest matches go first.

The `chronicle` pack omits its "On screen now" section, because in this setting the newest frame belongs to the previous task's VM, and the closing line that lists MCP tools, because the agent can't call them here. When the pack is over budget, whole sections are removed in this order: time by app, system events, habitual transitions, recent activity, topics and searches, what you were doing. If that isn't enough, the knowledge graph section is shortened. "Evidence for the task" is kept whole.

Context is injected once per task or phase rather than offered as MCP tools. That keeps the comparison model-agnostic and the cost overhead fixed. Tool access, where the agent decides when to query, is a separate later experiment.

## How memory is built

Each memory arm learns online from its own runs, following the online setting of Agent Workflow Memory (Wang et al., ICML 2025). Tasks run in waves of a fixed size, and after each wave the arm's recordings of those tasks are ingested into its store and the graph is rebuilt. A task can only see recordings of tasks from earlier waves.

**What is recorded.** At every step, a recorder wrapped around the agent reads what Chronicle reads on a real Linux machine: the active application and window title from the accessibility tree, the browser address bar when one is visible, and the visible text of the active window. If the accessibility tree is unavailable, the recorder runs `tesseract` OCR on the screenshot. The task instruction is stored as the recording's label. The agent's actions and reasoning are not recorded, matching Chronicle, which never records keystrokes.

**What is never recorded.** Evaluator results (`result.txt`), rewards and `info` from `env.step`, task configuration and evaluator specs, reference solutions, and anything from the benchmark files other than the instruction the agent sees. Failed and successful runs are recorded alike, as they would be on a real machine. The recorder only sees what was passed to the agent's `predict`, plus the accessibility tree of the same screen.

**Chronicle settings.** LLM refinement is off (`CHRONICLE_LLM=off`), so the graph is the heuristic one and no second model touches the data. Time decay is off (`CHRONICLE_HALF_LIFE_HOURS=off`), so arms that run at different speeds aren't weighted differently.

The `raw` arm runs the same recorder, so per-step latency is the same in every arm, which matters for OSWorld 2.0's timing-sensitive tasks. The `raw` arm's recordings are never read.

## Benchmarks and pilot subset

| Benchmark | Release | Pilot tasks | Primary metric |
| --- | --- | --- | --- |
| OSWorld 2.0 | `osworld-v2.1` | 20 sampled from the 43 tagged implicit-state inference | partial score |
| OSWorld-Verified | current `xlang-ai/OSWorld` main, `test_nogdrive.json` | 60 stratified by domain | binary success |

Partial score is the primary metric for OSWorld 2.0 because binary completion there is low for every published agent (8.3% for Claude Sonnet 4.6 at 500 steps in the OSWorld 2.0 paper), so 20 tasks would have almost no binary signal.

Tasks are sampled with `bench/run_pilot.py --seed 0`, which also fixes the order. The selected ids and order are saved to `plan.json` before any task runs.

The pilot uses one model (Claude Sonnet 4.6 unless changed before the first run) with the harness's default prompts, 150 steps on OSWorld 2.0 and 50 on OSWorld-Verified, and waves of 4 tasks.

## Reporting

For each benchmark, `bench/analyze.py` reports:

- the primary metric per arm, and the paired difference of each memory arm against `raw` and of `chronicle` against `search`, with a 95% paired bootstrap interval (10,000 resamples, fixed seed);
- binary success and an exact McNemar test on discordant pairs;
- the same split by position in the task order (first half against second half), since memory can only help once it has something in it;
- steps, input and output tokens, and dollar cost per task, using prices passed on the command line and the date they were taken.

A task that fails for infrastructure reasons (VM boot, setup timeout) is re-run once. If it still fails, it's dropped from every arm and listed. Agent failures count as zero.

With 20 and 60 tasks, the pilot can only detect large effects, and its results will be labeled as a pilot. A null or negative result will be published the same way as a positive one. The full run, if any, will be specified in a new version of this file before it starts.

## Threats to validity

- **Little to remember.** If OSWorld 2.0 tasks use unrelated user profiles, recordings of earlier tasks may contain little that helps later ones. The `search` arm and the order split show whether there was anything useful to find.
- **Near-duplicate tasks.** OSWorld-Verified has families of similar tasks in the same app. Remembering how a similar task went is legitimate experience transfer, which is what the online protocol measures, but per-domain results will be reported so it's visible where gains come from.
- **Context as distraction.** Irrelevant context can hurt. That shows up as a negative difference and is reported as such.
- **One run per arm.** Model sampling varies between runs. The pilot has one run per task and arm; repeat runs are part of any full study.

## Running

Requirements: an OSWorld-V2 checkout at the release tag with its tasks and assets downloaded (the task and asset datasets on Hugging Face are gated, so request access first), the provider set up as in its `docs/PROVIDER_SETUP.md` (AWS for this pilot), `ANTHROPIC_API_KEY`, and `tesseract` on the harness host for the OCR fallback.

```bash
# from this repository's root
python bench/run_pilot.py --osworld ~/OSWorld-V2 --meta evaluation_examples/test_v2.json \
  --only evaluation_examples/implicit_state_inference.json \
  --select 20 --seed 0 --out bench/runs/v2-pilot --dry-run          # writes plan.json and prints the commands
python bench/run_pilot.py --osworld ~/OSWorld-V2 --out bench/runs/v2-pilot \
  -- --provider_name aws --model claude-sonnet-4-6 --max_steps 150   # runs every wave
python bench/analyze.py bench/runs/v2-pilot --price-in 3 --price-out 15 --price-date 2026-11-02
```

Arguments after `--` go to OSWorld's `run_multienv_claude.py` unchanged. `run_pilot.py` resumes from `plan.json` and skips waves that are already ingested.

## Changes

None yet.

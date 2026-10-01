# mini-swe-agent baseline (docs/PAPER_PLAN.md arm A)

`run.py` runs vanilla mini-swe-agent 2.4.6 on OpenRouter models over the tasks of one registered repo,
writes the ledger with the same accounting rules as every other system (SPEC §6), and saves each
trajectory where `python -m demandtest replay` looks for it.

## Setup

```bash
python -m venv .venv-mswea && . .venv-mswea/bin/activate
pip install -r baselines/mswea/requirements.txt
export OPENROUTER_API_KEY=...            # your key; never commit it
export MSWEA_GLOBAL_COST_LIMIT=20        # optional hard cap in USD for one runner process
```

Before the first run, the repository must build offline: run `mvn -q test-compile` in it once so every
dependency is in the local Maven repository. The runner adds `-o -B` to `MAVEN_ARGS` for the agent; put
anything else there yourself (for example `-Dmaven.repo.local=...`).

**The agent runs shell commands on this machine** (mini-swe-agent's `LocalEnvironment`, inside a
per-task copy of the repository). Run it in a VM or container if that is a concern; there is no
network isolation beyond Maven's offline flag.

## Run

```bash
# print the exact prompt of the first task; no API call
python -m baselines.mswea.run --db data/runs.db --repo cron-utils --index data/index/cron-utils.json \
    --model qwen/qwen3-coder-next --traj-dir data/cron-utils/traj --show-prompt

# smoke test: two tasks, one worker, provider pinned
python -m baselines.mswea.run --db data/runs.db --repo cron-utils --index data/index/cron-utils.json \
    --model qwen/qwen3-coder-next --provider <provider> --traj-dir data/cron-utils/traj \
    --limit 2 --workers 1 --keep-workdir --work-dir data/work

# Gate 1 replay over the saved trajectories
python -m demandtest replay --index data/index/cron-utils.json --tasks data/cron-utils/tasks.jsonl \
    --traj-dir data/cron-utils/traj --out data/cron-utils/replay.jsonl
```

- `--provider` pins OpenRouter providers with fallbacks disabled; `--quantizations` restricts the
  served precision. Use them for every reported run, so the model behind a number never changes.
- `--model-kwargs` takes JSON merged into each request; use the sampling values from the model card.
- The prompt and agent settings live in `config.yaml` (step limit 50, cost limit 0.5 USD per task,
  600 s per command). The file is hashed into each run's config, so editing it starts a new
  configuration in the ledger. `run` is idempotent per (task, system, model, config); `--force` re-runs.
- `--repeat k` writes trajectories to `<traj-dir>/r<k>/` and is a separate configuration.

## What is recorded

| Ledger | Source |
|---|---|
| `llm_calls` | one row per billed call, including calls whose output failed to parse; tokens from OpenRouter's `usage`, `-1` when absent; `latency_ms` = call timestamp minus the previous message (tool time excluded) |
| `file_access` | files printed (`cat`, `sed`, `head`, …) or grepped into, parsed from the bash commands (`demandtest/replay.py`) |
| `results` | `compiled` / `passed` from our S4 step 5 on a fresh copy of the original repository; `wall_ms` = agent wall-clock; `target_hit` as for every system |
| checkpoint `AGENT` | exit status, OpenRouter providers per call, cached prompt tokens, cost in USD, model time, the test source, files the agent changed outside the test root |

Leakage control (SPEC §9): each task runs on a copy without `.git` and build outputs, with the
reference test method and its attached comment removed. The original repository is never modified.

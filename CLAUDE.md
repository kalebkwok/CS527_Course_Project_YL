# DemandTest — working notes

Demand-driven static context for intention-aligned Java unit test generation (CS 527 Group 5).
Paper target: arXiv preprint → **ISSTA 2027** (abstract 2027-01-08, paper 2027-01-11). The paper's
claim, RQs, arms, gates and dates are in `docs/PAPER_PLAN.md`; `docs/SPEC.md` is the method contract
(MUST / SHOULD / OPEN). Read both before changing behavior.

## Files

```
demandtest/            Python package, standard library only
  index.py             load index.json (SPEC §4), lookups, leakage exclusion (excluded_scope)
  demand.py            S1 demand set, oracle cues, semantic gaps
  expand.py            S2 recipes (Resolver), Σ (sufficient), bounded expansion
  proximal.py trigger.py  demand-proximal tests, oracle trigger hints
  packet.py            packet rendering, final_status (sufficient / -with-gaps / budget-limited / fallback)
  generate.py refine.py   S3 one call, S5 at most one repair call
  repair.py execute.py    S4 static repair; compile/run verdict, target_hit
  llm.py db.py metrics.py accounting LLM client, SQLite ledger, report tables
  replay.py            Gate 1: Σ after every step of a mini-swe-agent trajectory
  cli.py               init-db, add-repo, import-tasks, run, report, replay
baselines/mswea/       main agent baseline: mini-swe-agent 2.4.6 on OpenRouter (run.py, config.yaml)
baselines/openhands/   OpenHands trajectory parser (runner not written)
indexer/               S0 Java indexer (JavaParser 3.26.4) -> index.json
docs/                  SPEC.md, PILOT.md (original pilot design), PAPER_PLAN.md
paper/                 one-page and full proposals (submitted; do not edit without asking)
tests/                 offline tests and fixtures (mini_repo, mini_index.json, mswea_trajectory.json)
data/                  git-ignored: repos/cron-utils, index/cron-utils.json, runs.db
```

## Commands

```bash
python -m unittest discover -t . -s tests     # 126 tests; 1 skipped without mini-swe-agent
python -m demandtest {init-db|add-repo|import-tasks|run|report|replay} --help
python -m baselines.mswea.run --help          # needs: pip install -r baselines/mswea/requirements.txt
cd indexer && mvn -B -Dmaven.repo.local=../.m2repo package   # JDK 17; then java -jar target/indexer.jar --repo R --out I
cd paper && tectonic proposal-onepage.tex     # must stay one page
```

## Rules

- `demandtest/` stays standard-library only. Third-party code (mini-swe-agent, yaml, jinja2) is used
  only in `baselines/mswea/` and imported inside the functions that need it.
- Tests run offline: no LLM, no network, no Java. Anything needing mini-swe-agent is `skipUnless`.
- Token counts come from provider usage only; `-1` when absent, never an estimate (SPEC §6).
- Leakage (SPEC §9): every system hides the reference test (`index.excluded_scope`); the agent's
  checkout has no `.git`, no build outputs, and no reference method or its attached comment. Never
  modify the original repository.
- Σ is "structural sufficiency" in code, docs and paper, never just "sufficiency". Never claim real bugs.
- Runs are idempotent per (task, system, model, config hash). Editing a prompt or `config.yaml` makes
  a new configuration.
- Agent trajectories are saved as `task_slug(ref_test_id).traj.json`; `replay` finds them by that name.
- OpenRouter runs pin the provider (`--provider`, no fallbacks). Never write `OPENROUTER_API_KEY`
  into the repo. The agent executes shell commands on the host.
- A behavior change updates SPEC (or PAPER_PLAN) and gets a README changelog line. Commit only when asked.

## Status (2026-10-01)

Done: S0 indexer (cron-utils indexed), S1–S5 pipeline, ledger, CLI, Gate 1 replay, mini-swe-agent
runner (verified with a scripted model; no real API run yet), paper plan.

**Known defect, fix first:** `expand.Resolver._with_params` keeps a recipe whose non-literal parameter
does not resolve, so Σ can hold with an unresolved leaf (contradicts SPEC §2.4.3). It biases Gate 1
toward the paper's claim.

## Must-do work, in order

1. Fix the Σ defect above, with a regression test.
2. `scripts/make_intentions.py` (SPEC §9): tasks for cron-utils and truth (yavi if truth will not
   build offline). Index truth.
3. Smoke-run the runner on 2 cron-utils tasks with Qwen3-Coder-Next; re-estimate tokens per run.
4. Gate 1 (by 2026-10-26): vanilla runs on 2 projects × 50 tasks, then `replay`. Decide per PAPER_PLAN §5.
5. Fork harness and arms C (LLM self-assessed) and D (budget-matched cap), Σ ± k. Gate 2 by 2026-11-22.
6. Evaluation: PIT mutation score, LLM judge with human κ, dynamic check that the focal method ran.
7. Full evaluation (Qwen3.6-27B and Devstral Small 2), OpenHands sanity subset, writing, submission.

Open: author order; faculty advisor; IntentionTest has 12 projects in arXiv v4 but our texts say 13.
Not now: MCP wrapper, SWE-agent and IntentionTest-style baselines, Gradle support.

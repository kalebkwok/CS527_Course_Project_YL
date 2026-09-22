# DemandTest pilot study — the experiment that decides whether the full evaluation is worth running

Version 1.0 (2026-09-22). Owner: Kaleb Guo. Referenced from SPEC §11 (go/no-go).

## 1. Question the pilot answers

Three questions, each with a decision rule. If any fails, the full six-stage evaluation is not run
as planned and the design is revised first.

| # | Question | Measured by | Go if |
|---|---|---|---|
| P1 | Do intention-specific typed demands select better context than a compact static context of the same size? | aligned success rate, DemandTest vs CSC, same generator, same repair, same token budget | DemandTest ≥ CSC + 3 points, paired bootstrap CI excluding 0 |
| P2 | Does stopping at Σ preserve aligned success while saving context? | same tasks: stop-at-Σ vs `--expand-past 2/4` vs full class | stop-at-Σ within 2 points of the best extended variant at ≥ 20 % fewer packet tokens |
| P3 | Do the savings survive honest accounting against an agent? | tokens and first-use / amortized time vs OpenHands at 10/30/60 iterations, failed attempts included | ≥ 50 % fewer tokens and ≥ 20 % less amortized time than OpenHands-30 at non-inferior aligned success (margin 5 points) |

## 2. Subjects and tasks

- **Projects (3, contrasting, all from IntentionTest's 13):** chosen by index profile, not by
  convenience: one builder/parser-heavy (cron-utils, already indexed), one fixture-heavy
  (candidate: truth), one interface/mocking-heavy (candidate: blade or spark). Confirm with the
  index statistics (`ctors`, `builders`, `fixtures`, `mocking_lib`) before locking.
- **Tasks: 150 total.** 120 reverse-engineered intentions (SPEC §9, faithful to IntentionTest) plus
  30 independently written by two people from the focal method and class only.
- **Strata recorded per task:** `clone_available` (SPEC §9), `semantic_gaps` status
  (`sufficient` / `sufficient-with-gaps` / `fallback`), oracle kind, focal LOC.
- **Leakage:** SPEC §9 rule, including `.git` removal from the agent's checkout.

## 3. Conditions

All conditions share the same open-weight model, the same S3 prompt, and the same S4/S5 repair
policy unless the row says otherwise.

| Condition | What differs | Purpose |
|---|---|---|
| **DT** | DemandTest as specified, stop at Σ | the system |
| **DT-fixed** | expansion to `B_f`, `B_t` with no stopping rule | isolates the stopping rule |
| **DT-past-k** | stop at Σ, then add k = 2, 4 more entities by the same expansion order | RQ5 same-task comparison |
| **DT −I** | demands from types only; intention text still in packet section 1 | isolates intention conditioning |
| **CSC** | focal method + signatures of directly referenced types + class fields, cut to `B_t` | the "signatures and few calls" explanation |
| **Embed** | embedding retrieval (CodeT5+) of entities/tests to the same `B_t` | similarity ranking vs obligations |
| **OH-10 / OH-30 / OH-60** | OpenHands headless, same model, iteration caps | agent at three budgets, on a 45-task subset (15 per project) |
| **IT-reimpl** | IntentionTest-style retrieve-and-edit, four rounds | the closest prior system, labeled reimplementation |

Full class in the packet is the upper end of the DT-past-k series.

## 4. Metrics (SPEC §11)

Primary: aligned success rate over all attempted tasks (`compiled ∧ passed ∧ focal_executed ∧
judge = 2`). Secondary: compile, pass, `target_hit`, `focal_executed`, packet tokens, total
tokens, calls, online files, index cost (once), first-use and amortized time, `repair_weakened`,
`unresolved_reason` distribution, mutation score on 20 tasks.

## 5. Manual analyses (fixed sample sizes, done blind to condition where possible)

1. **Missing-fact analysis, 60 tasks:** 30 `sufficient`-but-failed DT runs and 30 `fallback` runs.
   For each, one person lists the facts the reference test needed that the packet lacked, and
   classifies the run as *premature stop* (Σ held, fact missing), *binding failure* (S1 could not
   bind the intention), *generation failure* (context adequate, model wrong), or *repair failure*
   (S4/S5 broke a correct test). A second person re-classifies 20; κ reported.
2. **Judge validation, 100 tests:** stratified across conditions and projects, blinded, two raters
   on the 0–2 rubric; the judge's agreement with the human majority is reported before any
   judge-based number is used.
3. **Intention validation:** 10 % of the 120 reverse-engineered intentions, two raters, κ.

## 6. Analysis

Paired at task level. Bootstrap confidence intervals (2,000 resamples) clustered by project.
Per-project tables; pooled numbers only next to per-project ones. Report clone-available and
clone-free strata separately. Pareto figure: aligned success vs tokens with interval bars, one
point per condition and per agent budget.

## 7. Code needed before the pilot can run (in order; sizes are estimates)

| Item | Where | Size |
|---|---|---|
| `scripts/make_intentions.py` (reverse-engineer, constraints, retry, drop count) | scripts/ | 1 day |
| `focal_executed` via JaCoCo agent on the single-test Maven run, XML parse for the focal method's lines | execute.py, db.py, metrics.py | 1 day |
| `--expand-past k`, `--fixed-budget`, `--no-intention` flags; `semantic_gaps` and `unresolved_reason` in the S1/S2 checkpoints | demand.py, expand.py, cli.py | 1 day |
| CSC baseline as `--system csc` on the same generator/repair | new module, cli.py | half a day |
| `clone_available` and `repair_weakened` columns | proximal.py, cli.py, db.py, metrics.py | half a day |
| Index cost logging (S0 wall-clock, files) into the ledger | indexer, db.py | half a day |
| OpenHands headless runner with iteration cap and `.git`-stripped checkout | baselines/openhands/ | 2 days, highest risk |
| Embedding retrieval baseline | new module | 1 day, last |

## 8. Timeline

| Week of | Milestone |
|---|---|
| Sep 22 | endpoint working; 10 hand-written cron-utils tasks run end to end; first bugs fixed |
| Sep 29 | make_intentions on cron-utils; JaCoCo `focal_executed`; flags; CSC; OpenHands on 5 tasks |
| Oct 6 | 150 tasks across 3 projects; DT, DT-fixed, DT-past-k, DT −I, CSC complete |
| Oct 13 | OH-10/30/60 on 45 tasks; IT-reimpl; manual analyses; go/no-go on P1–P3 |
| Oct 20 onward | full evaluation only if go; otherwise redesign the failing component |

## 9. What the pilot deliberately does not do

No large repository, no SWE-agent, no fine-tuning, no decision-model gates. No industrial
repository is available; the independently written intentions and the blinded ratings are the
nearest proxy for developer intentions and acceptance, and the paper says so.

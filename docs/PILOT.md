# DemandTest pilot study — the experiment that decides whether the full evaluation is worth running

Version 1.1 (2026-09-22, after the second review round). Owner: Kaleb Guo. Referenced from SPEC §11.

## 1. What the pilot decides

Three questions, each with a three-way outcome: **proceed**, **redesign**, or **inconclusive**.
An inconclusive interval triggers sample planning or a narrower claim, never an automatic
redesign. After the pilot the design is frozen; confirmatory evaluation uses held-out tasks and
projects.

| # | Question | Proceed | Redesign | Inconclusive |
|---|---|---|---|---|
| P1 selection | In the controlled selector comparison, is the obligation selector practically promising? | point estimate ≥ +3 points aligned success over the best other selector at the primary allowance, and the paired interval excludes 0 | point estimate ≤ 0 with an interval that excludes +3 | anything else: plan the sample from the observed disagreement rate |
| P2 stopping | Are packets nested, are tokens saved, and are losses attributable to premature stops? | Σ within 2 points of Σ+4 (preselected comparator) at ≥ 20 % fewer packet tokens, and premature stops ≤ 25 % of classified Σ failures | premature stops are the dominant failure class (> 50 %) | anything else |
| P3 agent efficiency | Is the harness credible and the cost advantage promising enough to justify a larger quality comparison? | ledger reconciles for both systems, DT ≤ 50 % of OH-30 tokens and ≤ 80 % amortized time, and the 45-task paired interval on `d = p_DT − p_OH30` does not exclude −0.05 from above | DT's lower bound on `d` is below −0.15 at these savings | anything else |

Sample-size honesty: 150 tasks is enough for feasibility, failure analysis, and detecting large
effects. It is not a confirmatory sample for three-point or five-point margins; that is why the
decision table has an inconclusive column and why the full evaluation's sample is planned from the
pilot's observed paired disagreement `q` (SPEC §11).

## 2. Subjects and tasks

- **Projects (3, fixed case studies, all from IntentionTest's 13):** one builder/parser-heavy
  (cron-utils, already indexed), one fixture-heavy (candidate: truth), one interface/mocking-heavy
  (candidate: blade or spark). Lock the choice from index statistics (`ctors`, `builders`,
  `fixtures`, `mocking_lib`) before any generation run. Results are reported per project; no
  pooled inference across three projects.
- **Tasks: 150.** 120 reverse-engineered intentions (SPEC §9, faithful to IntentionTest §5.1.1)
  plus 30 written independently by two people from the focal method and class only.
- **Units:** tasks sharing a focal method are one unit for pairing and resampling.
- **Strata recorded per task:** `clone_available` (§9), `packet_status` (§2.4.6), oracle kind,
  focal LOC, `semantic_gaps`, `unresolved_reason`.
- **Leakage:** SPEC §9, including `.git` removal from the agent's checkout.

## 3. Experiment E1 — selection (P1)

Everything is held constant except the selector.

| Held constant | Value |
|---|---|
| Candidate pool | every entity reachable by DemandTest's expansion from the focal method, every observable of the demand types, every visible test |
| Rendering | the packet template, including a HOW TO OBTAIN VALUES section |
| Auxiliary policy | idiom example and trigger hints **on for every arm**, and a second sweep with both off |
| Stopping | fill to the allowance |
| Allowances | 4, 8, 12 entities, counted with all variable context; 8 is the primary allowance |
| Generator and repair | one S3 call, S4 static repair, ≤ 1 S5 round |

| Arm | Selector |
|---|---|
| `obligation` | DemandTest's demand-driven resolution and expansion order |
| `similarity` | rank the same pool by embedding (CodeT5+) or BM25 similarity to intention + focal signature |
| `dependency` | rank the same pool by reference distance from the focal method (fixed expansion) |

Whole-system comparison, kept but interpreted as such: **DT vs CSC** (focal method + signatures of
directly referenced types + class fields under the same HOW TO OBTAIN heading, cut to `B_t`). A DT
win here may come from recipes, fixture initializers, trigger hints, the example, or fallback
tests; E1 is what separates them.

**Intention sensitivity** (reported with E1): fraction of tasks where replacing the intention
(objective-only, or the paired independently written one) changes the entity set the obligation
selector picks.

## 4. Experiment E2 — stopping (P2)

Selector fixed to `obligation`. Stopping policy varies over **nested** packets from SPEC §2.5's
continuation rule: `Σ` (stop at the predicate), `Σ+2`, `Σ+4`, `fixed` (fill to `B_t`). **Σ+4 is
the preselected primary comparator.** Packet nesting `ctx(Σ) ⊆ ctx(Σ+2) ⊆ ctx(Σ+4)` is asserted
from the S2 checkpoints, not assumed. Full-class replacement is a separate representation
ablation reported next to E2, never as its top.

Manual missing-fact analysis, blind to condition, fixed before the runs: 30 `sufficient`-but-failed,
30 `sufficient-with-gaps`-or-`budget-limited`-but-failed, 30 `fallback`. Each failed run is
classified as premature stop, intention-binding failure, generation failure with adequate context,
or repair failure. A second person re-classifies 30; κ reported.

## 5. Experiment E3 — agent efficiency (P3)

OpenHands headless, same model, at 10 / 30 / 60 iterations on a **45-task paired subset** (15 per
project, stratified by oracle kind and clone availability), after a five-task tool-competence
check. The simplified IntentionTest reimplementation runs on the same subset. Accounting per SPEC
§11: four buckets, first-use and amortized time, failed attempts included. Only these 45 pairs
carry evidence about DT vs OpenHands; the other 105 tasks do not.

## 6. Metrics and analysis

Primary: aligned success rate over all attempted tasks (`compiled ∧ passed ∧ focal_executed ∧
judge = 2`), with `focal_executed` from JaCoCo. Secondary: SPEC §11 list. Every metric by
`packet_status` and by clone stratum.

Analysis: paired at the unit level; per-project tables; bootstrap intervals **within** a project
over paired units (2,000 resamples), no cluster bootstrap across three projects; the observed
paired disagreement `q` per comparison is reported for planning the full evaluation.

## 7. Human evaluation

- Intention validation: 10 % of the 120 reverse-engineered intentions, two raters, κ.
- Judge validation: 100 generated tests, two raters, **third rater adjudicates disagreements**,
  κ reported; the sample prioritizes paired DT/CSC outputs on the same tasks; the judge's
  agreement with the adjudicated label is reported before any judge-based number is used, and
  system-dependent judging errors are inspected on the pairs.

## 8. Code needed before the pilot can run (in order; sizes are estimates)

| Item | Where | Size | Status |
|---|---|---|---|
| Final-packet status, semantic gaps, unresolved reasons, accessibility, `--expand-past k` | demand.py, expand.py, packet.py, cli.py, db.py, metrics.py | done in 0.2.4 | ✓ |
| `scripts/make_intentions.py` (reverse-engineer, ≤ 5 code tokens, retry cap, drop count) | scripts/ | 1 day | |
| `focal_executed` via JaCoCo agent on the single-test Maven run, XML parse for the focal method's lines | execute.py, db.py, metrics.py | 1 day | |
| Selector interface for E1: `obligation` / `similarity` / `dependency` over one candidate pool, fixed allowances, common rendering | new module `select.py`, expand.py, cli.py | 1.5 days | |
| CSC baseline as `--system csc` rendering signatures under the HOW TO OBTAIN heading | new module, cli.py | half a day | |
| `--fixed-budget`, `--no-intention`, hints/example on-off flags | cli.py, packet.py, expand.py | half a day | |
| `clone_available`, `repair_weakened`, intention-sensitivity script | proximal.py, cli.py, db.py, scripts/ | half a day | |
| Index cost logging (S0 wall-clock, files) into the ledger; estimator error vs provider usage | indexer, db.py | half a day | |
| OpenHands headless runner, iteration cap, `.git`-stripped checkout | baselines/openhands/ | 2 days, highest risk | |
| Embedding/BM25 similarity selector | select.py | 1 day, last | |

## 9. Timeline

| Week of | Milestone |
|---|---|
| Sep 22 | endpoint working; 10 hand-written cron-utils tasks run end to end; first bugs fixed |
| Sep 29 | make_intentions on cron-utils; JaCoCo `focal_executed`; selector interface; CSC; OpenHands on 5 tasks |
| Oct 6 | 150 tasks across 3 projects; E1 and E2 complete |
| Oct 13 | E3 on 45 tasks; simplified IntentionTest; manual analyses; P1–P3 decisions |
| Oct 20 onward | full evaluation if *proceed*; sample planning if *inconclusive*; component redesign if *redesign* |

## 10. What the pilot deliberately does not do

No large repository, no SWE-agent, no fine-tuning, no decision-model gates, no pooled inference
across projects. No industrial repository is available; the independently written intentions and
the blinded ratings are the nearest proxy for developer intentions and acceptance, and the paper
says so.

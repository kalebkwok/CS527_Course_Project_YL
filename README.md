# DemandTest

Demand-driven static discovery of project-specific knowledge for efficient,
intention-aligned unit test generation.

Target: ICSE 2027 Industry Challenge #8, *Efficient Project-Specific Test
Generation with Validation Intentions*. Course: CS527 (UIUC), Fall 2026.

**Thesis.** Given a focal method, a Java repository, and a natural-language
*validation intention*, most of the tokens spent by repository-level code
agents go into *finding* project-specific knowledge (how to construct inputs,
which fixtures and mocks the project uses, what is observable for the oracle)
by reading files one at a time. DemandTest computes that knowledge statically:
it derives a typed **demand set** from the focal method's signature and the
intention, resolves each demand against a one-time project index using
**construction recipes**, expands context only along edges that satisfy an
unresolved demand, and stops when a deterministic **sufficiency predicate**
holds. The LLM then sees one compact knowledge packet and generates the test in
a single call; mechanical failures are repaired statically, and at most one LLM
repair round is allowed.

## Status

**Python pipeline implemented (2026-09-14).** The S1–S5 package, the SQLite
ledger, the accounting LLM client, the CLI, the OpenHands trajectory parser,
the offline fixtures and the Python acceptance tests of
[`docs/SPEC.md`](docs/SPEC.md) §12 exist and pass offline (no LLM, no Java
toolchain). The CS527 proposal is in [`paper/`](paper/).

Still to build: `scripts/make_intentions.py` (task construction, §9), the OpenHands runner
(the trajectory parser exists, the headless launcher does not), and the evaluation harness
(PIT mutation, LLM-judge alignment, amortized S0 cost reporting).

**0.2.4 (2026-09-22), second review round; code and documentation.** Σ is claimed only for
evidence the model receives: every run carries one of four statuses computed on the *rendered*
packet (`sufficient`, `sufficient-with-gaps`, `budget-limited`, `fallback`), stored in
`results.packet_status` and reported by `report`. Oracle cues follow one rule (expected results,
else objective; negated cues removed). S1 records semantic-gap flags; S2 records an unresolved
reason per need and honors package-private accessibility. `--expand-past k` applies a
deterministic continuation rule so Σ ⊆ Σ+2 ⊆ Σ+4 are nested. SPEC §11 and `docs/PILOT.md` now
separate the selection experiment from the stopping experiment, give the non-inferiority margin
a rationale, treat the pilot's three projects as fixed case studies, and decide proceed /
redesign / inconclusive. 100 offline tests.

**0.2.3 (2026-09-22), after external review; documentation only.** Σ is now the *structural*
context-sufficiency predicate, with its boundary, supported intention forms, evidence rules,
recipe objective, and index scope written down (SPEC §2.4). Leakage control, the clone-available
split, and independently written intentions are in §9. §11 makes aligned success rate over all
tasks the primary metric, requires dynamic focal execution, splits accounting into index / online /
generation / execution, adds agent budget sweeps and a compact-static-context baseline, and
redesigns RQ5 as a same-task stopped-vs-extended comparison. The pilot that decides whether the
full evaluation runs is [`docs/PILOT.md`](docs/PILOT.md).

**0.2.2 (2026-09-22), after TestTailor (Zhou et al., FSE 2026).** Their ablation says the closest
existing test plus the point where it diverges is the dominant lever, so: the fallback and the
idiom example now pick existing tests by *demand overlap* (`proximal.py`, SPEC §2.5.1) instead of
"first test calling `m`", and every related test is headed by its *demand diff* (what it already
obtains, what it does not assert). Oracle lines carry a syntactic *trigger hint* (`trigger.py`:
"triggered when: n < 0"). A static `target_hit` (calls `m` and shows every oracle) is stored per
run so the report separates *passes* from *passes and checks the intention*; `report` also prints
usable tests per LLM call and a budget curve. `--repeat K` supports the variance protocol and
`--refine 2` the repair-cap pilot (SPEC §11). Still 0 LLM calls before S3; Σ and the prompts are unchanged.

**S0 indexer implemented (2026-09-14).** `indexer/` (Java 17, JavaParser 3.26.4 symbol solver,
Gson streaming writer) builds `indexer/target/indexer.jar` and `index.json` per §4. The §12
acceptance test is green on the 3-file sample project, and `indexer/verify_sample.sh`
additionally loads the produced index with the real Python consumer and re-derives the facts
of the hand-written fixture from `tests/fixtures/mini_repo`. First real index: cron-utils at
`bac6e86` (213 files → 91 types, 718 tests, 1.9 MB, 1.8 s, 0 unresolved parameter types with
`--classpath-file`). See [`indexer/README.md`](indexer/README.md) for decisions and limitations.

```
python -m demandtest init-db      --db data/runs.db
python -m demandtest add-repo     --db data/runs.db --name mini --path tests/fixtures/mini_repo
python -m demandtest import-tasks --db data/runs.db --repo mini --tasks tests/fixtures/mini_tasks.jsonl
python -m demandtest run          --db data/runs.db --repo mini --model <name> \
                                  --index tests/fixtures/mini_index.json --dry-run
python -m demandtest report       --db data/runs.db
python -m unittest discover -t . -s tests        # §12 acceptance tests, offline
```

## Planned layout

```
demandtest/          Python pipeline (S1-S5), SQLite ledger, accounting LLM client, CLI   [implemented]
indexer/             Java project-index builder (JavaParser + symbol solver) -> index.json [implemented]
baselines/openhands/ Repository-agent baseline: trajectory parser [implemented], runner [pending]
scripts/             Task construction (intentions from existing tests), repo selection     [pending]
docs/SPEC.md         Design and implementation specification
paper/               CS527 proposal (ACM sigconf)
tests/               Offline acceptance tests (no LLM, no Java toolchain)                   [implemented]
data/                Local data (git-ignored): repos, index.json, tasks.jsonl, runs.db
```

## Pipeline

| Stage | Name | LLM calls | Output |
|---|---|---|---|
| S0 | Project index (one-time per repo) | 0 | `index.json` |
| S1 | Demand set | 0 | typed needs |
| S2 | Bounded expansion + sufficiency predicate | 0 | knowledge packet, inspected files |
| S3 | Generation | 1 | test class |
| S4 | Static repair (imports, package, throws, idiom) + compile/run | 0 | verdict |
| S5 | Semantic repair (only on failure) | <=1 | final test |

## Ground truth and leakage

Intentions are reverse-engineered from existing tests (as in IntentionTest).
When generating for a task, the reference test is removed from the index, the
test corpus, and the baseline's checkout, so no system sees the answer. We
never claim to find real bugs; correctness is measured by compilation,
execution against the real code, mutation score, and semantic alignment with
the intention.

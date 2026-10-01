# Paper plan: arXiv preprint → ISSTA 2027

Version 1 (2026-10-01). Owner: Kaleb Guo. This file records the paper-level decisions; the method itself
stays in [`SPEC.md`](SPEC.md), and the original 150-task pilot design in [`PILOT.md`](PILOT.md).

| Item | Value |
|---|---|
| Target | ISSTA 2027 research papers. Abstract **2027-01-08**, full paper **2027-01-11**, final notification 2027-06-17 (there is a major-revision round) |
| Preprint | arXiv, by default right after submission. Check the ISSTA 2027 CFP preprint and double-anonymous rules once it is published (not out as of 2026-10-01) |
| Fallback venues | ICSE 2027 AGENT workshop (2026-11-27, 5-page short paper, single-anonymous); ICSE 2027 Industry Challenge (2026-11-13). A peer-reviewed workshop paper may count as prior publication for ISSTA, so use a fallback only if ISSTA is abandoned |
| Contribution type | Insight first (a phenomenon), then the method that exploits it, then the evidence (the "Insight / Performance / Capability" split of hzwer/WritingAIPaper) |

## 1. Core claim

> Test-generating agents keep exploring after the repository context they have read is already
> structurally sufficient for the test. A deterministic predicate Σ, derived from the focal method's
> types and the validation intention, locates that point; stopping there costs fewer tokens than
> LLM self-assessed sufficiency or a turn cap at the same budget, without lowering test quality.

Three contributions:

1. **Phenomenon.** Replaying vanilla agent trajectories with Σ evaluated after every step: how many
   tokens are spent after Σ already holds, and whether that exploration raises success.
2. **Method.** Σ as a stop signal for agents, exposed as a tool (CLI first, MCP wrapper later).
3. **Evidence.** Σ against LLM self-assessed sufficiency (TestAgent-style) and against a
   budget-matched turn cap, on the same trajectories.

**Σ is always "structural sufficiency"** (SPEC §2.4). The paper never says Σ guarantees enough
context.

## 2. Research questions

- **RQ1 (phenomenon).** How much of a vanilla agent's token spend falls after Σ first holds, and
  does post-Σ exploration change aligned success?
- **RQ2 (stopping point).** Forking the same trajectory at Σ − k, Σ, Σ + k and at the end: is Σ at
  the knee of the success-versus-tokens curve?
- **RQ3 (against other stopping rules).** At equal or lower tokens, does stopping at Σ match or
  beat (a) LLM self-assessed sufficiency and (b) a budget-matched turn cap?
- **RQ4 (Σ as a predictor).** Using the empirical ground truth of §4, what are Σ's precision and
  recall for "the context is already enough"?
- **RQ5 (agent or no agent).** Where do the one-shot pipeline (S0–S5), agent + Σ, and the vanilla
  agent sit on the cost/quality curve? This answers the Agentless question for test generation.

## 3. Arms

| Arm | What runs | Cost |
|---|---|---|
| A. vanilla | mini-swe-agent, default prompt + our task prompt, local model | one full run per task |
| Fork@end | prefix = whole exploration, then one forced "write the test now" call + S4 + ≤ 1 repair | 1–2 calls |
| Fork@Σ, Σ ± k | prefix cut at the step where Σ first holds (and k steps earlier/later) | 1–2 calls each |
| C. LLM self-assessed | ask "is the context sufficient?" every few steps along the prefix; fork at the first yes | short calls + 1–2 |
| D. budget-matched cap | fork at a fixed step equal to the mean Σ step of the project | 1–2 calls |
| One-shot pipeline | existing S0–S5 | 1–2 calls |
| Real agent + Σ (subset) | 20–30 tasks where the harness enforces the Σ stop live | full runs |
| OpenHands (subset) | 20–30 tasks, vanilla, to show mini-swe-agent is a comparable baseline | full runs |

Forks share one exploration prefix, so differences between them come only from the stopping point.
The limitation (a real agent that knows about Σ might explore differently) is answered by the
real-run subset and stated in the threats to validity.

## 4. Empirical ground truth for "enough context" (RQ4)

A step k is **empirically sufficient** for a task when Fork@k is at least as successful as Fork@end
(same verdict on compile, pass, target hit, and alignment). Σ's precision is the share of Σ-holds
steps that are empirically sufficient; recall is the share of empirically sufficient first steps
that Σ also flags. With repeats, "at least as successful" is a success rate over the repeats.

## 5. Gates

| Date | Gate | Proceed if | Otherwise |
|---|---|---|---|
| **2026-10-26** | G1: phenomenon | On 2 projects × 50 tasks, Σ holds in a clear majority of vanilla runs and a substantial share of tokens falls after it | **Plan B1** (Σ rarely holds): paper becomes "what static analysis can and cannot supply to a test-generation agent", built on the `unresolved_reason` distribution. **Plan B2** (agents already stop at Σ): paper becomes the one-shot-versus-agent comparison (RQ5) |
| **2026-11-22** | G2: stopping rules | Fork@Σ beats both C and D at equal or lower tokens | Reframe as an empirical study of stopping rules for test-generation agents (what each rule gets wrong) |

The thresholds for G1 are set after the first 20 replayed runs and written here before the
remaining 80 are replayed.

## 6. Validity decisions

| Decision | Choice | Why |
|---|---|---|
| Primary metric | Objective first: compiles, passes, executes the focal method, kills the mutants the held-out reference test kills. LLM-judge alignment is secondary | The judge is the weakest link |
| Judge validation | Both authors label ~100 outputs independently; report Cohen's κ | Reviewers will ask |
| Contamination | Add 2–3 repositories created after the models' training cutoff, as CAT (arXiv 2604.22046) did | IntentionTest projects are old and public |
| Models | At least two local models of different strength | Tool and stopping effects depend on model strength (arXiv 2608.13568) |
| Statistics | Paired design: McNemar for success, Wilcoxon for tokens, bootstrap CIs; repeats on a subset | ~9% outcome variance at temperature 0 (arXiv 2607.09691) |
| Sample size | Several hundred tasks in the full evaluation | A 5-point paired difference needs it |
| Cost | Tokens, wall-clock and GPU time | Token reduction is not cost reduction (arXiv 2607.12161) |
| Σ correctness | Fix the partial-recipe gap before G1: today a recipe with an unresolved parameter still satisfies Σ, which contradicts SPEC §2.4.3 | Σ is the paper |
| File reads in replay | Σ uses files the agent printed (`cat`, `sed`, `head`, …); grep hits are a sensitivity analysis. Partial reads count as whole-file reads, which favors the agent | Stated in threats |

## 7. Related work to position against

| Group | Works | What we add |
|---|---|---|
| Static context for LLM test generation | KTester, CATGen, CAT, TestTailor, HITS, RATester | A decidable stop rule instead of a fixed context pattern |
| Program-analysis tools for agents | AutoCodeRover, CodexGraph, RepoGraph, LocAgent, PatchAgent, TestAgent (arXiv 2607.09101), LSP token study (arXiv 2608.13568) | Demand-level answers and a stop signal, not symbol lookups |
| Agent efficiency and stopping | SWE-Pruner, FastContext, Agent Retrieval Bench, SCATE | Deterministic stopping derived from the task, not learned or self-assessed |
| Same task | IntentionTest (arXiv 2507.20619) | Agent setting and cost accounting |
| Pipelines versus agents | Agentless | The same question for test generation (RQ5) |

TestAgent decides sufficiency by asking the LLM, asks explicitly after 30 turns and forces
generation after 40. That is arm C.

## 8. Writing

- Page-one figure: a vanilla trajectory's cumulative tokens with the step where Σ first holds
  marked and the post-Σ tokens shaded.
- Introduction: why industrial test generation matters → agents do not know when to stop →
  question and findings → value.
- Every table readable without the text.

## 9. Decisions and open items

Decided 2026-10-01:

| Item | Decision |
|---|---|
| Serving | OpenRouter API, providers pinned with fallbacks off (`baselines/mswea/run.py --provider`). The weights are public, so local deployment stays possible; a short self-hosted run (one rented H100) later measures time and backs the deployability claim |
| Models | Strong: **Qwen3.6-27B** (dense, 77.2% SWE-bench Verified on its card, 2026-04). Medium, different family: **Devstral Small 2** (24B, 68.0%, 2025-12). Development and debugging: **Qwen3-Coder-Next** (80B total / 3B active, 70.6%, cheapest). Card scores use different scaffolds and are not comparable |
| Thinking | Qwen3.6 thinks by default. Decide on/off from a 5-task smoke test, then keep it fixed across arms; report reasoning tokens separately |
| G1 projects | **cron-utils** (indexed) and **truth** (fixture-heavy); **yavi** if truth does not build offline. Not Spark (about 51 tests) |
| Full evaluation | Every IntentionTest project that builds, plus a post-release split: tests (and their focal methods) added after 2026-04-21 in active Maven projects |
| Time metric | Tokens are primary. Time is split into model time (per-call latency) and tool time; the comparative time claim comes from the self-hosted run |

Budget estimate (to revise after the first 20 runs measure tokens per run): about 0.7M tokens per
vanilla run (25 steps, mostly re-sent history); G1 under 50 USD; the full evaluation (about 600
tasks × 2 models, repeats, forks, subsets; 2–3B tokens) about 300–700 USD depending on cache hits and
thinking length.

Open:

1. Author order; agree now.
2. Faculty advisor or co-author. Ask the course instructor or another faculty member; also ask about
   university compute (NCSA Delta, NSF ACCESS).
3. IntentionTest's project count: the arXiv v4 paper says 12 projects and 3,680 tests; the proposal and
   README say 13. Check the artifact and correct the text before writing.

## 10. Timeline

| Weeks | Work |
|---|---|
| 10/1–10/26 | Fix the Σ partial-recipe gap; mini-swe-agent runner (done: `baselines/mswea/run.py`); task construction; vanilla runs on 2 × 50 tasks; replay (`python -m demandtest replay`); **G1** |
| 10/27–11/22 | Fork harness; arms C and D; judge validation; **G2** |
| 11/23–12/20 | All projects, second model, repeats, real-run subsets |
| 12/21–1/8 | Writing and internal review; abstract on 1/8 |
| 1/11 | Submit; post to arXiv |

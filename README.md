# DemandTest

Demand-driven static discovery of project-specific knowledge for efficient,
intention-aligned unit test generation.

- **Course:** CS 527 (UIUC), Fall 2026, Group 5
- **Team:** Yunqi Li (NetID: yunqili4), Jinjie Guo (NetID: jinjieg2)
- **Problem:** ICSE 2027 Industry Challenge #8, *Efficient Project-Specific Test Generation with
  Validation Intentions*
- **Paper:** arXiv preprint, then ISSTA 2027 (submission January 2027). See the [Roadmap](#roadmap).

> 中文说明见 [简体中文说明](#简体中文说明)。

## The idea

Given a focal method, a Java repository, and a natural-language *validation intention*, a
repository agent spends most of its tokens *finding* project-specific knowledge: how to construct
inputs, which fixtures and mocks the project uses, and what is observable for the assertion.
DemandTest computes that knowledge statically. It derives a typed **demand set** from the focal
method's signature and the intention, resolves each demand against a one-time project index using
**construction recipes**, expands context only along unresolved demands, and stops when a
deterministic **structural sufficiency predicate Σ** holds. The LLM then sees one compact knowledge
packet and generates the test in a single call; mechanical failures are repaired statically, and at
most one LLM repair round is allowed.

| Stage | Name | LLM calls | Output |
|---|---|---|---|
| S0 | Project index (one-time per repo) | 0 | `index.json` |
| S1 | Demand set | 0 | typed needs |
| S2 | Bounded expansion + structural sufficiency predicate Σ | 0 | knowledge packet, inspected files |
| S3 | Generation | 1 | test class |
| S4 | Static repair (imports, package, throws, idiom) + compile/run | 0 | verdict |
| S5 | Semantic repair (only on failure) | ≤ 1 | final test |

**Research question.** Test-generating agents may keep exploring after the context they have read is
already structurally sufficient. We measure that on recorded agent trajectories (Σ evaluated after
every step), then test Σ as a stop signal for agents against two simpler rules: the LLM judging its
own context, and a turn cap at the same budget. The one-shot pipeline above is the cheapest point on
the same cost/quality curve. Claim, research questions, and arms: [`docs/PAPER_PLAN.md`](docs/PAPER_PLAN.md).

**Baselines.** The main agent baseline is vanilla **mini-swe-agent** (2.4.6) on open-weight models
served through OpenRouter with pinned providers ([`baselines/mswea/`](baselines/mswea/README.md)).
Vanilla OpenHands runs on a small subset as a sanity check that mini-swe-agent is a comparable
baseline.

## Repository layout

```
demandtest/          Python pipeline (S1-S5), SQLite ledger, accounting LLM client, CLI, replay [implemented]
indexer/             Java project-index builder (JavaParser + symbol solver) -> index.json   [implemented]
baselines/mswea/     Main agent baseline: mini-swe-agent on OpenRouter, runner + ledger       [implemented]
baselines/openhands/ Sanity-subset baseline: trajectory parser [implemented], runner [pending]
scripts/             Task construction (intentions from existing tests)                       [pending]
docs/SPEC.md         Design and implementation specification (the contract)
docs/PAPER_PLAN.md   Paper plan: claim, RQs, arms, gates, budget, timeline
docs/PILOT.md        Original 150-task pilot design (superseded in scope by PAPER_PLAN)
paper/               Proposal documents (see below)
tests/               Offline tests (no LLM, no network, no Java toolchain)                    [implemented]
CLAUDE.md            Working notes for coding agents: files, commands, rules, must-do work
data/                Local data (git-ignored): repos, index.json, tasks.jsonl, runs.db, trajectories
```

## Quick start

Python ≥ 3.10, standard library only (`tiktoken` is optional for token estimates). Java 17 and
Maven are needed only to build the S0 indexer ([`indexer/README.md`](indexer/README.md)) and to
compile generated tests.

```bash
python -m unittest discover -t . -s tests        # 126 offline tests (1 skipped without mini-swe-agent)

# one-shot pipeline on the bundled fixture, no model call
python -m demandtest init-db      --db data/runs.db
python -m demandtest add-repo     --db data/runs.db --name mini --path tests/fixtures/mini_repo
python -m demandtest import-tasks --db data/runs.db --repo mini --tasks tests/fixtures/mini_tasks.jsonl
python -m demandtest run          --db data/runs.db --repo mini --model <name> \
                                  --index tests/fixtures/mini_index.json --dry-run
python -m demandtest report       --db data/runs.db
```

Agent baseline and Gate 1 replay (setup and options in [`baselines/mswea/README.md`](baselines/mswea/README.md)):

```bash
pip install -r baselines/mswea/requirements.txt  # mini-swe-agent; the demandtest package stays stdlib-only
export OPENROUTER_API_KEY=...                    # your own key; never commit it
python -m baselines.mswea.run --db data/runs.db --repo <repo> --index data/index/<repo>.json \
    --model qwen/qwen3-coder-next --provider <provider> --traj-dir data/<repo>/traj --limit 2
python -m demandtest replay --index data/index/<repo>.json --tasks data/<repo>/tasks.jsonl \
    --traj-dir data/<repo>/traj --out data/<repo>/replay.jsonl
```

## Roadmap

Dates are deadlines. Each gate has a written fallback in [`docs/PAPER_PLAN.md`](docs/PAPER_PLAN.md) §5.

| Phase | When | Work | Status |
|---|---|---|---|
| 0. Pipeline | Sep 2026 | S0 indexer (first index: cron-utils), S1–S5 pipeline, ledger, CLI, offline tests | ✅ done |
| 1. Agent harness | Oct 1 | mini-swe-agent runner on OpenRouter; `replay` (Σ after every agent step) | ✅ done |
| 2. **Gate 1: does the phenomenon exist?** | **Oct 26** | see checklist below | 🔄 in progress |
| 3. **Gate 2: stopping rules** | **Nov 22** | fork harness (one trajectory, stopped at Σ − k, Σ, Σ + k, end); arm C: LLM self-assessed sufficiency; arm D: turn cap at the same budget; LLM-judge validation with human labels | ⬜ |
| 4. Full evaluation | Dec 20 | all buildable IntentionTest projects plus tests added after the models' release; Qwen3.6-27B and Devstral Small 2; repeats; real agent + Σ subset; OpenHands sanity subset; PIT mutation score; check that the focal method ran | ⬜ |
| 5. Paper | Jan 11, 2027 | writing and internal review (abstract Jan 8), submission, arXiv preprint, artifact | ⬜ |

**Gate 1 checklist**

- [ ] Fix Σ: a recipe whose parameter does not resolve must not satisfy Σ (SPEC §2.4.3); add a regression test
- [ ] `scripts/make_intentions.py`: tasks reverse-engineered from existing tests (SPEC §9)
- [ ] Index truth (or yavi if truth does not build offline); build tasks for cron-utils and truth
- [ ] Smoke run: 2 cron-utils tasks with Qwen3-Coder-Next; re-estimate tokens and cost per run
- [ ] Vanilla agent runs on 2 projects × 50 tasks; `replay`; record the Gate 1 decision

**After submission:** expose the analysis as tools any agent can call (MCP server), and support Gradle.

## Proposal documents (`paper/`)

| File | What it is |
|---|---|
| `proposal-onepage.pdf` | **One-page proposal submitted to the course** (ACM two-column). Source: `proposal-onepage.tex` |
| `proposal.pdf` | Full-length proposal. Source: `proposal.tex` |
| `proposal.bib`, `acmart.cls`, `ACM-Reference-Format.bst` | Shared bibliography and ACM class/style for both `.tex` files |

Build from `paper/` with [Tectonic](https://tectonic-typesetting.github.io/)
(`brew install tectonic`) or `latexmk -pdf`, or upload the `.tex` plus the three shared files
to Overleaf:

```bash
cd paper
tectonic proposal-onepage.tex        # must stay one page: pdfinfo proposal-onepage.pdf | grep Pages
```

## Status

The S1–S5 package, the SQLite ledger, the accounting LLM client, the CLI, the Gate 1 replay, the
mini-swe-agent runner, the OpenHands trajectory parser, and the offline fixtures are implemented;
all 126 offline tests pass (the 100 acceptance tests of [`docs/SPEC.md`](docs/SPEC.md) §12, 17 for
the replay, 9 for the runner, whose end-to-end test drives the real agent with a scripted model and
is skipped when mini-swe-agent is not installed). The S0 indexer (Java 17, JavaParser 3.26.4) builds
`indexer/target/indexer.jar`; first real index: cron-utils at `bac6e86` (213 files → 91 types,
718 tests, 1.9 MB, 1.8 s). No real agent run has been made yet. Known defect: the Σ item at the top
of the Gate 1 checklist.

### Changelog

- **0.2.5 (2026-10-01), paper plan.** Paper target: arXiv preprint, then ISSTA 2027
  ([`docs/PAPER_PLAN.md`](docs/PAPER_PLAN.md)). The main baseline becomes mini-swe-agent 2.4.6 on
  OpenRouter (`baselines/mswea/run.py`), with OpenHands on a subset. New `replay` command for Gate 1:
  Σ evaluated after every step of a recorded agent trajectory, over only the files the agent has read.
- **0.2.4 (2026-09-22), second review round.** Σ is claimed only for evidence the model receives:
  every run carries one of four statuses computed on the rendered packet (`sufficient`,
  `sufficient-with-gaps`, `budget-limited`, `fallback`), stored in `results.packet_status`. One
  oracle-cue rule with negation handling; semantic-gap flags in S1; an unresolved reason per need
  in S2; `--expand-past k` produces nested packets for the stopping experiment. SPEC §11 and
  `docs/PILOT.md` separate the selection and stopping experiments and define the proceed /
  redesign / inconclusive decision.
- **0.2.3 (2026-09-22), after external review; documentation only.** Σ became the *structural*
  context-sufficiency predicate (SPEC §2.4). Leakage control, the clone-available split, and
  independently written intentions (§9). Aligned success rate over all tasks is the primary metric;
  accounting is split into index / online / generation / execution; agent budget sweeps and a
  compact static-context baseline were added (§11).
- **0.2.2 (2026-09-22), after TestTailor (FSE 2026).** The fallback and the idiom example pick
  existing tests by *demand overlap* (`proximal.py`), each headed by its *demand diff*; oracle lines
  carry a *trigger hint* (`trigger.py`); a static `target_hit` separates "passes" from "passes and
  checks the intention"; `--repeat K` and `--refine 2` support the variance and repair-cap pilots.
- **2026-09-14.** Python pipeline and S0 indexer implemented.

## Ground truth and leakage

Intentions are reverse-engineered from existing tests (as in IntentionTest). When generating for a
task, the reference test is hidden from the index and the test corpus; agents work on a copy of the
repository without `.git` and build outputs, with the reference test method and its attached comment
removed. No system sees the answer. We never claim to find real bugs; correctness is measured by
compilation, execution against the real code, mutation score, and semantic alignment with the
intention.

---

## 简体中文说明

### 这个项目是做什么的

DemandTest 是我们 CS 527 的课程项目（第 5 组：Yunqi Li、Jinjie Guo），目标是先挂 arXiv，再投 ISSTA 2027。

- **任务：** 给定一个 Java 方法和一句自然语言的“测试意图”（这个测试要检查什么），自动生成一个
  能编译、能运行、并且真正检查这个意图的单元测试。
- **难点：** 写测试需要项目内部的知识，比如怎么构造合法的输入、用哪些 mock、断言什么。
  agent 要一个一个地读文件去找，很慢，也很费 token。
- **我们的做法：** 先用静态分析算出这个测试“需要什么”，只取这些信息，够了就停。
- **研究问题：** agent 是否在上下文“结构上已经够了”之后还在继续探索？用 Σ 作为停止信号，
  能不能比“让 LLM 自己判断”和“同预算的轮数上限”更省、质量不降？
- **对比对象：** 主基线是原版（vanilla）mini-swe-agent，通过 OpenRouter 调用开源权重模型；
  OpenHands 只在小子集上跑，用来证明基线够强。

### 文件在哪里

| 位置 | 内容 |
|---|---|
| `paper/proposal-onepage.pdf` | 交给课程的**一页提案**（ACM 双栏格式） |
| `paper/proposal.pdf` | 完整版提案 |
| `demandtest/` | Python 主流程（S1–S5）和回放分析 `replay` |
| `indexer/` | Java 索引器（S0） |
| `baselines/mswea/` | 主基线：mini-swe-agent（OpenRouter API） |
| `baselines/openhands/` | OpenHands 基线（只在小子集上跑） |
| `docs/SPEC.md` | 详细设计文档 |
| `docs/PAPER_PLAN.md` | 论文计划：主张、RQ、实验组、检查点、预算、时间线 |
| `docs/PILOT.md` | 最初的 150 个任务试点设计（范围已被论文计划取代） |
| `CLAUDE.md` | 给编程 agent 的工作说明：文件、命令、规则、必做工作 |
| `tests/` | 离线测试 |

### 常用操作

**1. 修改一页提案并重新生成 PDF**

编辑 `paper/proposal-onepage.tex`，然后运行：

```bash
cd paper
tectonic proposal-onepage.tex
```

第一次运行会自动下载需要的 LaTeX 包。没有 `tectonic` 的话先装：`brew install tectonic`。
也可以把 `proposal-onepage.tex`、`proposal.bib`、`acmart.cls`、`ACM-Reference-Format.bst`
这四个文件上传到 Overleaf 编译。改完后记得确认 PDF 还是只有一页：

```bash
pdfinfo proposal-onepage.pdf | grep Pages
```

**2. 运行测试**

在项目根目录运行：

```bash
python -m unittest discover -t . -s tests
```

最后显示 `OK`（126 个测试，没装 mini-swe-agent 时有 1 个跳过）就说明代码正常。不需要大模型，也不需要 Java。

**3. 试跑整个流程（不调用模型）**

用上面 [Quick start](#quick-start) 里的命令，`run` 带 `--dry-run`，只走流程、不真正调用大模型。

**4. 跑 agent 基线**

先装依赖、设置自己的 `OPENROUTER_API_KEY`，用法见 [`baselines/mswea/README.md`](baselines/mswea/README.md)。
注意 agent 会在本机执行 shell 命令。

**5. 构建 Java 索引器**

需要 JDK 17 和 Maven，步骤见 [`indexer/README.md`](indexer/README.md)。

### 路线图

完整表格见上面的 [Roadmap](#roadmap)。

| 阶段 | 截止 | 内容 | 状态 |
|---|---|---|---|
| 0. 主流程 | 9 月 | 索引器、S1–S5、ledger、CLI、离线测试 | ✅ |
| 1. agent 基础设施 | 10/1 | mini-swe-agent 运行器、回放分析 `replay` | ✅ |
| 2. **检查点 1：现象是否存在** | **10/26** | 修 Σ；任务构造脚本；索引 truth；试跑 2 个任务；2 个项目 × 50 个任务跑 vanilla 并回放 | 🔄 |
| 3. **检查点 2：停止规则对比** | **11/22** | 分叉实验；LLM 自评组；同预算轮数上限组；LLM 评审的人工校验 | ⬜ |
| 4. 完整实验 | 12/20 | 全部可构建的项目、模型发布后新增的测试、两个模型、重复运行、小规模真实运行、OpenHands 子集、变异测试 | ⬜ |
| 5. 论文 | 2027/1/11 | 写作、内部审稿（1/8 交摘要）、投稿、挂 arXiv、整理 artifact | ⬜ |

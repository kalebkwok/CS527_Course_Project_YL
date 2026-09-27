# DemandTest

Demand-driven static discovery of project-specific knowledge for efficient,
intention-aligned unit test generation.

- **Course:** CS 527 (UIUC), Fall 2026, Group 5
- **Team:** Yunqi Li (NetID: yunqili4), Jinjie Guo (NetID: jinjieg2)
- **Target:** ICSE 2027 Industry Challenge #8, *Efficient Project-Specific Test Generation with
  Validation Intentions*

> 中文说明见 [简体中文说明](#简体中文说明)。

## The idea

Given a focal method, a Java repository, and a natural-language *validation intention*, a
repository agent spends most of its tokens *finding* project-specific knowledge: how to construct
inputs, which fixtures and mocks the project uses, and what is observable for the assertion.
DemandTest computes that knowledge statically. It derives a typed **demand set** from the focal
method's signature and the intention, resolves each demand against a one-time project index using
**construction recipes**, expands context only along unresolved demands, and stops when a
deterministic **sufficiency predicate Σ** holds. The LLM then sees one compact knowledge packet
and generates the test in a single call; mechanical failures are repaired statically, and at most
one LLM repair round is allowed.

| Stage | Name | LLM calls | Output |
|---|---|---|---|
| S0 | Project index (one-time per repo) | 0 | `index.json` |
| S1 | Demand set | 0 | typed needs |
| S2 | Bounded expansion + sufficiency predicate Σ | 0 | knowledge packet, inspected files |
| S3 | Generation | 1 | test class |
| S4 | Static repair (imports, package, throws, idiom) + compile/run | 0 | verdict |
| S5 | Semantic repair (only on failure) | ≤ 1 | final test |

The main baseline is **vanilla OpenHands**: its default agent, prompts, tools, and iteration
limit, with the same model and only our task prompt. Runs capped at 10, 30, and 60 iterations
are extra points on the quality-versus-tokens plot.

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

## Repository layout

```
demandtest/          Python pipeline (S1-S5), SQLite ledger, accounting LLM client, CLI   [implemented]
indexer/             Java project-index builder (JavaParser + symbol solver) -> index.json [implemented]
baselines/openhands/ Repository-agent baseline: trajectory parser [implemented], runner [pending]
scripts/             Task construction (intentions from existing tests), repo selection     [pending]
docs/SPEC.md         Design and implementation specification
docs/PILOT.md        150-task pilot that decides whether the full evaluation runs
paper/               Proposal documents (see above)
tests/               Offline acceptance tests (no LLM, no Java toolchain)                   [implemented]
data/                Local data (git-ignored): repos, index.json, tasks.jsonl, runs.db
```

## Quick start

Python ≥ 3.10, standard library only (`tiktoken` is optional for token estimates). Java 17 and
Maven are needed only to build the S0 indexer; see [`indexer/README.md`](indexer/README.md).

```bash
python -m unittest discover -t . -s tests        # 100 offline acceptance tests (SPEC §12)

python -m demandtest init-db      --db data/runs.db
python -m demandtest add-repo     --db data/runs.db --name mini --path tests/fixtures/mini_repo
python -m demandtest import-tasks --db data/runs.db --repo mini --tasks tests/fixtures/mini_tasks.jsonl
python -m demandtest run          --db data/runs.db --repo mini --model <name> \
                                  --index tests/fixtures/mini_index.json --dry-run
python -m demandtest report       --db data/runs.db
```

## Status

**Implemented.** The S1–S5 package, the SQLite ledger, the accounting LLM client, the CLI, the
OpenHands trajectory parser, and the offline fixtures; all 100 acceptance tests of
[`docs/SPEC.md`](docs/SPEC.md) §12 pass offline. The S0 indexer (Java 17, JavaParser 3.26.4)
builds `indexer/target/indexer.jar`; first real index: cron-utils at `bac6e86` (213 files → 91
types, 718 tests, 1.9 MB, 1.8 s).

**Still to build.**
- `scripts/make_intentions.py`: task construction (SPEC §9).
- The OpenHands runner: vanilla OpenHands as the main baseline, plus runs capped at 10 / 30 / 60
  iterations. SPEC §10.1 still describes only the capped runs and needs the vanilla setup (pinned
  version and its default iteration limit).
- The evaluation harness: PIT mutation score, LLM-judge alignment, amortized S0 cost reporting.

### Changelog

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
task, the reference test is removed from the index, the test corpus, and the baseline's checkout,
so no system sees the answer. We never claim to find real bugs; correctness is measured by
compilation, execution against the real code, mutation score, and semantic alignment with the
intention.

---

## 简体中文说明

### 这个项目是做什么的

DemandTest 是我们 CS 527 的课程项目（第 5 组：Yunqi Li、Jinjie Guo）。

- **任务：** 给定一个 Java 方法和一句自然语言的“测试意图”（这个测试要检查什么），自动生成一个
  能编译、能运行、并且真正检查这个意图的单元测试。
- **难点：** 写测试需要项目内部的知识，比如怎么构造合法的输入、用哪些 mock、断言什么。
  现有的智能体（如 OpenHands）要一个一个地读文件去找，很慢，也很费 token。
- **我们的做法：** 先用静态分析算出这个测试“需要什么”，只取这些信息，够了就停，
  然后只调用一次本地大模型。
- **对比对象：** 原版（vanilla，即默认配置）OpenHands。目标是质量相当，但 token 少一半以上、
  时间少 20% 以上。

### 文件在哪里

| 位置 | 内容 |
|---|---|
| `paper/proposal-onepage.pdf` | 交给课程的**一页提案**（ACM 双栏格式） |
| `paper/proposal.pdf` | 完整版提案 |
| `demandtest/` | Python 主流程（S1–S5） |
| `indexer/` | Java 索引器（S0） |
| `baselines/openhands/` | OpenHands 基线 |
| `docs/SPEC.md` | 详细设计文档 |
| `docs/PILOT.md` | 150 个任务的试点实验计划 |
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

最后显示 `OK`（100 个测试）就说明代码正常。不需要大模型，也不需要 Java。

**3. 试跑整个流程（不调用模型）**

用上面 [Quick start](#quick-start) 里的命令，最后一步 `run` 带 `--dry-run`，
只走流程、不真正调用大模型。

**4. 构建 Java 索引器**

需要 JDK 17 和 Maven，步骤见 [`indexer/README.md`](indexer/README.md)。

### 下一步要做的

1. 写任务构造脚本 `scripts/make_intentions.py`。
2. 实现 OpenHands 运行器：原版（默认配置）作为主基线，另外跑 10 / 30 / 60 步上限的版本。
3. 评估工具：PIT 变异测试、LLM 评审、成本统计。
4. 先做 150 个任务的试点实验（见 `docs/PILOT.md`），再决定是否跑完整实验。

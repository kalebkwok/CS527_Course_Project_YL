# DemandTest — Design and Implementation Specification

Version 0.2.1 (2026-09-14). Status: S0–S5 implemented against this spec (§12 indexer row green
on the 3-file sample; first real index: cron-utils `bac6e86`); baselines/eval pending. Owner: Kaleb Guo.
Audience: whoever (human or model) implements the code. Nothing in this repo
is implemented yet; this document is the contract. Sections marked **MUST**
are requirements; **SHOULD** are defaults that may change after the first
ablation; **OPEN** items are decided by the owner.

Contents

1. Problem statement and non-goals
2. Formal objects (index, needs, recipes, sufficiency, expansion, packet)
3. Repository layout and languages
4. S0 — Indexer (Java) and the `index.json` schema
5. S1–S5 — Python pipeline: module contracts
6. Ledger schema (SQLite) and accounting rules
7. CLI
8. Prompts
9. Task construction (validation intentions from existing tests)
10. Baselines (OpenHands, IntentionTest-style)
11. Evaluation protocol and metrics
12. Acceptance tests (definition of done per module)
13. Work split and milestones
14. Open questions

---

## 1. Problem statement and non-goals

**Input.** A Java repository `R` (Maven; Gradle later), a focal method `m`
in class `C`, and a validation intention
`I = (objective, preconditions?, expected_results?)` in natural language.

**Output.** One JUnit test class `T` that compiles against `R`, executes, and
whose assertions validate `I` on `m`.

**Objective.** Subject to test quality comparable to a repository-level agent
baseline, minimize (a) LLM prompt+completion tokens, (b) number of LLM calls,
(c) wall-clock time, (d) number of repository files inspected per task.
These are the metrics of ICSE 2027 Industry Challenge #8 (R3: ≥ 50 % fewer
tokens and ≥ 20 % less wall-clock than a strong repository-agent baseline;
R5: compilation rate, execution rate, semantic alignment, token cost,
response time, number of inspected files).

**Thesis.** Agents spend most tokens *finding* project-specific knowledge by
reading files. That knowledge — how to construct inputs, which fixtures and
mocks the project uses, what is observable for an oracle — can be computed
statically from the focal signature, the intention, and a one-time project
index, with an explicit, decidable test of whether the collected knowledge is
*sufficient*. The LLM then needs one call.

**Non-goals (MUST NOT be built).** Program repair; vulnerability detection;
coverage-maximizing generation; a general agent loop; any orchestration
platform; claims of finding real bugs.

---

## 2. Formal objects

### 2.1 Project index

Built once per repository by S0. Every entity carries `file` (path relative
to the repository root) so inspected-file accounting is exact. Full JSON
schema in §4.

### 2.2 Demand set `D(m, I)` (S1, no LLM)

A **need** is a typed requirement the generated test must satisfy.

```
Need := Receiver(C)                       -- unless m is static or a constructor
      | Arg(i, tau_i, constraint?)        -- one per parameter; constraint = sentence of
                                          --   I.preconditions mentioning the parameter name
      | Setup(field, tau)                 -- one per field of C that m reads (cap 5, most-used first)
      | Oracle(kind, tau)                 -- kind ∈ {return, exception, state, interaction}
      | Idiom(test_framework, assertion_lib, mocking_lib)
```

Oracle rules (regex over `I.expected_results`, case-insensitive):

| pattern in expected_results | needs added |
|---|---|
| `throw|throws|thrown|exception|error|reject|invalid|illegal` | `Oracle(exception, tau)` for each `tau ∈ m.throws`, else `RuntimeException` |
| otherwise, `m.returns ≠ void` | `Oracle(return, m.returns)` |
| otherwise or `state|updated|modif|mutat|stored|set to|becomes|register|added to|remove` | `Oracle(state, C)` |
| `call|invoke|delegate|notif|forward|dispatch|publish` | `Oracle(interaction, tau)` for each Setup field type |

Needs are de-duplicated by `(kind, tau, detail)`, order preserved.

### 2.3 Recipes

A **recipe** produces a value of type `tau`:

```
Recipe := Literal(tau)              -- JDK/primitive types the LLM can write inline (§4.4 list)
        | Fixture(test_id, field)   -- an existing test's fixture field of type tau
        | Helper(test_id, method)   -- a test-utility method returning tau
        | Ctor(c)                   -- public or package-private constructor
        | Factory(f)                -- static method returning tau or a subtype
        | Builder(b)                -- method returning *Builder whose build() returns tau
        | Singleton(s)              -- static field of type tau, incl. enum constants
        | Mock(tau)                 -- only if mocking_lib ≠ none and tau is interface/abstract
        | SubtypeCtor(c of S <: tau) -- when tau is interface/abstract
```

`cost(r)` = number of parameters of `r` that are not `Literal`-resolvable,
summed transitively over chosen sub-recipes; `Literal`/`Singleton`/`Fixture`
cost 0, `Mock` costs 1, `Builder` costs 1. Tie-break order:
`Fixture < Helper < Ctor < Factory < Builder < Singleton < Mock < SubtypeCtor`
("prefer how the project already does it").

`resolve(tau, depth)` returns the cheapest recipe whose parameter types
resolve recursively with `depth < d_max` (default 3); memoized on
`(erase(tau), depth)`; cycle-guarded (a type currently being resolved
resolves to ⊥). `erase` strips generics and array brackets (OPEN 1).

### 2.4 Sufficiency predicate `Σ(D, ctx)` (S2, no LLM)

`ctx` is the current context: a set of visible type FQNs, a list of entities
(methods/fields/fixtures) whose signatures the packet may show, the set of
files those entities live in, and a map `need → recipe`.

```
Σ(D, ctx) ⇔ ∀ n ∈ D:
  n = Receiver|Arg|Setup      ⇒ ctx.recipes[n] defined (all sub-recipes' entities ∈ ctx)
  n = Oracle(return, tau)     ⇒ tau is JDK/primitive ∨ ∃ observable o of tau in ctx.entities
  n = Oracle(exception, tau)  ⇒ tau is JDK ∨ tau ∈ ctx.types
  n = Oracle(state, C)        ⇒ ∃ observable o of C in ctx.entities
  n = Oracle(interaction,tau) ⇒ mocking_lib ≠ none
  n = Idiom(...)              ⇒ project.test_framework defined
```

`Σ` is computed from the index only. It MUST NOT call the LLM.

### 2.5 Bounded expansion (S2)

```
ctx := {types: {C}, entities: [], files: {file(C)}, recipes: {}}
unresolvable := {}
loop while ¬Σ(D, ctx) ∧ |ctx.files| < B_files ∧ est_tokens(ctx) < B_tokens:
    progressed := false
    for n in unresolved(D, ctx) \ unresolvable:          -- round-robin, stable order
        if n ∈ {Receiver, Arg, Setup}:
            r := resolve(n.tau, 0)                        -- over the WHOLE index
            if r = ⊥: unresolvable += n; continue
            ctx.recipes[n] := r; ctx.entities += entities(r); ctx.files += files(entities(r))
            ctx.types += {n.tau} ∪ owners(entities(r)); progressed := true
        elif n = Oracle(_, tau):
            obs := observables(tau)[:6]
            if obs = ∅: unresolvable += n
            else: ctx.entities += obs; ctx.files += files(obs); ctx.types += {tau}; progressed := true
        if |ctx.files| ≥ B_files: break
    if ¬progressed: break
status := "sufficient"  if Σ(D, ctx) ∧ unresolvable = ∅
          "fallback"    otherwise
if status = "fallback":                                   -- IntentionTest-style backstop
    ref := tests_calling(m)[:2]  ∨  tests_of_class(C)[:2]  (excluding the task's reference test)
    ctx.files += files(ref); packet gains their source (≤ 40 lines each)
trace := one line per step: "<need> <- <recipe kind>:<rendered> (+files=[...])"
```

Defaults (SHOULD): `B_files = 12`, `B_tokens = 2000` (packet size excluding the
focal body), `d_max = 3`. All three are CLI knobs and ablation axes.

### 2.6 Knowledge packet (S2 → S3)

Rendered deterministically, in this order, truncating whole sections from the
tail once `B_tokens` is exceeded (sections 1–3 and 5 are never dropped; §12 is authoritative):

1. `INTENTION` — objective, preconditions, expected results, verbatim.
2. `FOCAL` — the focal method source with the class header line and the
   focal file's imports (≤ 20), prefixed with `// <focal_file>`.
3. `HOW TO OBTAIN VALUES (from this project)` — one line per resolved need:
   `- <role> (<SimpleType>): <rendered recipe>  // <constraint>` followed by
   the signatures of the recipe's entities, one per line, each suffixed with
   `// <OwnerSimpleName>`. Fixture recipes render as
   `// see <TestClass>: field <name> of type <T>` plus the fixture's
   initializer lines lifted from the test source (≤ 5 lines).
4. `OBSERVABLE FOR ASSERTIONS` — ≤ 8 observable signatures for oracle needs.
5. `PROJECT FACTS` — test framework, assertion lib, mocking lib, package of
   `C`, test root.
6. `ASSERTION STYLE IN THIS PROJECT` — the first ≤ 25 lines of one existing
   test calling `m` (else any test), labeled "example, do not copy blindly".
7. `RELATED EXISTING TEST …` — only in `fallback` status, ≤ 40 lines each.

Rendered recipes: `new Foo(<literal int>, <literal String>)`,
`Foo.of(...)`, `Foo.builder()...build()`, `Foo.INSTANCE`, `mock(Foo.class)`.
Token estimate: `tiktoken` `cl100k_base` if available, else `len(text)/3.6`.
Never used for accounting — only for the budget check.

### 2.7 Generation (S3)

Exactly one chat completion, `temperature 0`, system prompt §8.1, user
message = packet. The reply MUST contain one ```` ```java ```` block with one
complete class. If parsing fails the run is recorded as failed
(`notes = parse-error`) — **no retry**, so accounting stays honest.

### 2.8 Static repair (S4, no LLM)

Passes, in order, implemented with JavaParser (Track A) — a regex first cut
is acceptable for the pilot:

1. **Package**: set the package declaration to `C`'s package; the file is
   written to `<test_root>/<pkg path>/<ClassName>.java`. This also grants
   package-private access.
2. **Imports**: for every unresolved simple type name, look it up in the
   index. Unique FQN → add import. Ambiguous → prefer (a) `C`'s package,
   (b) the FQN most imported by existing tests, (c) JDK. Add framework
   imports implied by used annotations/assertions (`@Test`, `assertEquals`,
   `assertThat`, `mock(`, `when(`, `verify(`, `List.of`, …).
3. **Checked exceptions**: if `m` declares a checked exception and a test
   method lacks a `throws` clause, add `throws Exception`.
4. **Idiom**: rewrite JUnit 4 ↔ 5 annotations/imports to the project's
   framework.
5. **Compile and run only this class**:
   `mvn -q -B -o -Dtest=<Class> -DfailIfNoTests=false -Dsurefire.failIfNoSpecifiedTests=false test`
   (add `-pl <module> -am` for multi-module repos), timeout 900 s, `MAVEN_OPTS=-Xmx2g`.
   `compiled` ⇔ no `COMPILATION ERROR` / `cannot find symbol` and no timeout;
   `passed` ⇔ exit 0 ∧ `Tests run: k, Failures: 0, Errors: 0`.
6. **Diagnostics trimming**: first javac error block (8 lines) or first
   failure line + ≤ 5 `at …` frames. This string is the only failure
   information S5 may see.

The generated test file is removed after the run unless `--keep`.

### 2.9 Semantic repair (S5)

At most **one** LLM call (prompt §8.2), only if S4 compiled-but-failed or
javac diagnostics survived S4. Input: the test, the trimmed diagnostics,
and the packet's sections 1, 3, 4. **No new files are opened**; the
inspected-file count does not change in S5. S4 passes run again on the
output, then compile-and-run once more.

---

## 3. Repository layout and languages

```
demandtest/            Python 3.10+ package: S1–S5, ledger, LLM client, CLI. Stdlib only;
                       `tiktoken` optional.
indexer/               Java 17 Maven project (JavaParser 3.26 + symbol solver, Gson) -> index.json
baselines/openhands/   run script + trajectory parser (Python)
scripts/               make_intentions.py, count_source_files.sh, repos.yaml
docs/SPEC.md           this file
paper/                 proposal (ACM sigconf)
tests/                 offline tests (§12); no LLM, no Java toolchain needed
data/                  git-ignored: repos/, index/, tasks/, trajectories/, runs.db
```

The Python package MUST run with `python -m demandtest` and MUST NOT
require network access except in `llm.py`.

---

## 4. S0 — Indexer and `index.json`

### 4.1 Invocation

```
java -jar indexer/target/indexer.jar --repo <path> --out <index.json> [--src-roots a,b] [--test-roots c,d]
```

Auto-detect roots from `pom.xml` / `build.gradle` (`src/main/java`,
`src/test/java`, multi-module: every module). Parse every `.java` file with
JavaParser and a `CombinedTypeSolver` (reflection + every source root +
`target/classes` and dependency jars from `mvn dependency:build-classpath`
when present). Unresolvable symbols are recorded by simple name with
`"resolved": false` — never dropped silently.

### 4.2 Schema

```jsonc
{
  "project": {
    "name": "cron-utils", "commit": "<sha>", "build_tool": "maven", "java_version": "17",
    "src_roots": ["src/main/java"], "test_roots": ["src/test/java"],
    "n_source_files": 123, "n_test_files": 45,
    "test_framework": "junit5" | "junit4",
    "assertion_lib": "junit" | "assertj" | "hamcrest" | "truth",
    "mocking_lib": "mockito" | "none"
  },
  "types": {
    "<FQN>": {
      "kind": "class|interface|enum|record|abstract", "file": "src/main/java/...", "package": "...",
      "line": 12, "supertypes": ["<FQN>"], "type_params": ["T"],
      "ctors":       [ Method ],        // name "<init>"; public and package-private only
      "factories":   [ Method ],        // static, returns this type or a subtype
      "builders":    [ Method ],        // returns a type named *Builder that has build(): this type
      "singletons":  [ Field ],         // static fields of this type, enum constants
      "observables": [ Method ],        // public getters (no params, non-void), equals, hashCode,
                                        // toString, size/isEmpty/contains, public final fields (as Field)
      "methods":     [ Method ],        // all declared methods
      "fields":      [ Field ]
    }
  },
  "tests": [
    {
      "id": "<TestFQN>#<method>", "file": "src/test/java/...", "class": "<FQN>", "method": "name",
      "line_start": 40, "line_end": 61,
      "framework": "junit5", "assertion_lib": "assertj", "mocking": "mockito",
      "fixtures": [ Field ],            // instance fields of the test class (+ their initializer text)
      "callees":  ["<FQN>#<name>(<erased param types>)"],   // resolved calls into src roots
      "helpers":  [{"id": "<TestFQN>#<name>(...)", "returns": "<FQN>|void"}],  // calls into test utilities, WITH return type
                                        // (the pipeline builds Helper recipes from these; a top-level
                                        //  "test_helpers": [{"id", "returns", "file"}] registry is equivalent)
      "source":   "<method source text>"
    }
  ]
}

Method := { "name": "...", "params": [{"name": "x", "type": "<FQN or primitive>", "resolved": true}],
            "returns": "<FQN>|void", "throws": ["<FQN>"], "static": false,
            "visibility": "public|package|protected|private", "file": "...", "line": 10, "line_end": 25,
            "body_reads_fields": ["fieldName"],   // fields of the owner read in the body (flow-insensitive)
            "body_writes_fields": ["fieldName"] }
Field  := { "name": "...", "type": "<FQN>", "static": false, "visibility": "...", "file": "...", "line": 5,
            "initializer": "new Foo(1)" | null }
```

`callees` resolution uses the symbol solver; when resolution fails, record
`"<unresolved>#<simpleName>(…)"` so the failure rate can be measured.

### 4.3 Sizes

Elasticsearch-class repositories yield tens of thousands of types; the file
may be several hundred MB. Write with a streaming JSON writer; the Python
side may load lazily (OPEN 6).

### 4.4 Literal-resolvable types

Primitives and their boxes, `String`, `Object`, `List/Map/Set/Collection`
(`List.of` etc.), `Optional`, `LocalDate`, `LocalDateTime`, `Duration`,
`File`, `Path`, `BigDecimal`, `BigInteger`, arrays of the above. Everything
else under `java.*`/`javax.*` is "JDK, not literal": resolved via its own
constructors through reflection in the type solver, cost as usual.

---

## 5. S1–S5 — Python module contracts

Types below are Python dataclasses; JSON-serializable via `__dict__`.

```
index.py
  load(path) -> Index
  Index.type(fqn) -> TypeInfo | None            # erases generics/arrays before lookup
  Index.by_simple_name(simple) -> list[TypeInfo]
  Index.subtypes(fqn) -> list[TypeInfo]
  Index.observables(fqn) -> list[Method]
  Index.tests_calling(sig) -> list[TestInfo]     # honors excluded_tests
  Index.fixtures_of_type(fqn) -> list[(TestInfo, Field)]
  Index.files(entities) -> set[str]
  Index.exclude_test(test_id)                    # leakage control, §9
  erase(t: str) -> str ; is_jdk(t) -> bool ; is_literal(t) -> bool

demand.py
  @dataclass(frozen) Need(kind, type, detail="", constraint="")  ; Need.key() -> "kind:type:detail"
  @dataclass Task(repo, focal_class, focal_method, focal_sig, focal_file, intention: dict, ref_test_id, id=None)
  find_focal(index, task) -> (TypeInfo, Method)  # KeyError if absent
  compute_demands(index, task) -> list[Need]     # §2.2

expand.py
  @dataclass Recipe(kind, type, cost, entity=None, sub=[]) ; .entities() ; .render()
  @dataclass Context(types:set, entities:list, files:set, recipes:dict)
  class Resolver(index, d_max=3): candidates(fqn) -> list[Recipe] ; resolve(fqn, depth=0) -> Recipe | None
  sufficient(index, demands, ctx) -> (bool, unresolved: list[Need])   # §2.4
  expand(index, demands, task, budget_files=12, budget_tokens=2000, d_max=3, est_tokens=callable)
      -> ExpansionResult(ctx, status: "sufficient"|"fallback", unresolved, trace: list[str], referable_tests)

packet.py
  est_tokens(text) -> int
  render(index, task, demands, expansion, focal_source, budget_tokens) -> Packet(text, est_tokens, sections: dict)

llm.py
  class LLMClient(model, conn, run_id, base_url=$DEMANDTEST_BASE_URL, api_key=$DEMANDTEST_API_KEY,
                  timeout_s=180, max_retries=3)
      .chat(messages, stage, max_tokens=1024, temperature=0.0, **extra) -> str
      # OpenAI-compatible /chat/completions via stdlib urllib; retries transport errors with
      # exponential backoff; on success inserts one llm_calls row with the provider's usage
      # (prompt_tokens, completion_tokens; -1 if absent), latency_ms, seq, sha256[:16] of prompt & reply.
  class DryRunClient(LLMClient): canned answers per stage; logs stage+"-dry" with estimated tokens.

generate.py
  SYSTEM_PROMPT (§8.1) ; extract_java(text) -> str (ParseError) ; generate(client, packet, max_tokens=1200) -> str

repair.py
  static_repair(index, task, src) -> (src', fixes: list[str])   # §2.8 passes 1–4

execute.py
  write_test(repo, task, src, test_root) -> Path
  compile_and_run(repo, task, src, test_root="src/test/java", timeout_s=900, module=None)
      -> Verdict(compiled, passed, wall_ms, diagnostics, n_asserts, test_path, raw_tail)
  remove_test(repo, test_path)

refine.py
  refine_once(client, index, task, src, verdict, packet, max_tokens=1200) -> str   # §2.9, prompt §8.2

metrics.py
  summarize(conn) -> rows (per system×model: n, compile_rate, pass_rate, mutation, alignment,
                            tokens_per_task, calls_per_task, wall_s, files_per_task)
  pareto(conn)    -> rows (demandtest configs: pass_rate vs tokens_per_task)
  format_table(rows) -> markdown

cli.py   see §7.  run_one(conn, repo_row, task, args): S1→S5 with a checkpoint after each stage;
         any exception is caught, recorded in results.notes, status="error"; never aborts the batch.
```

Every stage reads its input from the previous stage's checkpoint and writes
its own; `run` is idempotent per `(task, system, model, config_hash)`.

---

## 6. Ledger schema and accounting rules

```sql
CREATE TABLE repos(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
  commit_sha TEXT, build_tool TEXT, n_source_files INTEGER, n_test_files INTEGER);
CREATE TABLE tasks(id INTEGER PRIMARY KEY, repo_id INTEGER NOT NULL REFERENCES repos(id),
  focal_class TEXT NOT NULL, focal_method TEXT NOT NULL, focal_sig TEXT NOT NULL, focal_file TEXT NOT NULL,
  intention_json TEXT NOT NULL, ref_test_id TEXT NOT NULL, UNIQUE(repo_id, focal_sig, ref_test_id));
CREATE TABLE runs(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
  system TEXT NOT NULL, model TEXT NOT NULL, config_hash TEXT NOT NULL, config_json TEXT NOT NULL,
  started_at REAL, finished_at REAL, status TEXT NOT NULL DEFAULT 'pending',   -- pending|running|done|error
  UNIQUE(task_id, system, model, config_hash));
CREATE TABLE checkpoints(run_id INTEGER NOT NULL REFERENCES runs(id), stage TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(run_id, stage));
CREATE TABLE llm_calls(id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
  stage TEXT NOT NULL, model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL, prompt_sha TEXT NOT NULL, response_sha TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE file_access(run_id INTEGER NOT NULL REFERENCES runs(id), path TEXT NOT NULL, stage TEXT NOT NULL,
  PRIMARY KEY(run_id, path));
CREATE TABLE results(run_id INTEGER PRIMARY KEY REFERENCES runs(id), compiled INTEGER, passed INTEGER,
  n_asserts INTEGER, mutation_score REAL, alignment_score REAL, wall_ms INTEGER, inspected_files INTEGER,
  prompt_tokens INTEGER, completion_tokens INTEGER, n_llm_calls INTEGER, test_path TEXT, notes TEXT);
```

`config_hash` = sha256 of the canonical JSON of the run config, 12 hex chars.
`system ∈ {demandtest, openhands, sweagent, intentiontest}`. `results`
token/call/file columns are **derived** from `llm_calls`/`file_access` at
`finish_run`, never written by hand.

**Accounting rules (MUST).**
1. Tokens come from the provider's `usage` object for every system. Estimates
   are never written to `llm_calls`.
2. DemandTest `inspected_files` = `|ctx.files|` ∪ focal file (S2), unchanged
   by S3–S5. The one-time S0 index is reported separately as an amortized
   cost: index build time and files scanned ÷ number of tasks on that repo.
   Both numbers appear in the paper.
3. Agent baseline: a file counts as inspected when any tool call reads,
   views, greps into, lists (with content), or opens it; each distinct path
   once per run. Directory listings without content do not count.
4. Wall-clock = from task start to final verdict, including compilation and
   test execution, for every system.

---

## 7. CLI

```
demandtest init-db      --db data/runs.db
demandtest add-repo     --db … --name cron-utils --path data/repos/cron-utils [--build-tool maven] [--commit SHA]
demandtest import-tasks --db … --repo cron-utils --tasks data/tasks/cron-utils.jsonl
demandtest run          --db … --repo cron-utils --system demandtest --model <name> [--index path]
                        [--budget-files 12] [--budget-tokens 2000] [--d-max 3] [--refine 1]
                        [--limit N] [--force] [--dry-run] [--keep]
demandtest report       --db …            # prints summarize() and pareto() as markdown
```

`tasks.jsonl` lines: `{"focal_class", "focal_method", "focal_sig", "focal_file", "intention": {"objective", "preconditions", "expected_results"}, "ref_test_id"}`.
Model endpoint via `DEMANDTEST_BASE_URL` / `DEMANDTEST_API_KEY`
(OpenAI-compatible; vLLM, DeepSeek, Together, Fireworks).

---

## 8. Prompts (fixed text; changes are a config change and get a new `config_hash`)

### 8.1 S3 system prompt

```
You write one JUnit test class for a Java project.
Rules:
- Validate exactly the stated OBJECTIVE / EXPECTED RESULTS with meaningful assertions; do not chase coverage.
- Obtain every value the way the HOW TO OBTAIN VALUES section shows; do not invent constructors, factories or APIs.
- Use the project's test framework, assertion library and mocking library as given in PROJECT FACTS.
- Output a single ```java fenced block containing one complete, compilable test class with imports and package. Nothing else.
```

User message: the packet text (§2.6).

### 8.2 S5 user prompt

```
The test below was generated for this validation intention but failed. Fix it with the smallest change.
Do not add new dependencies or invent APIs; use only what appears in the packet excerpt. Return one ```java block with the full class.

INTENTION AND KNOWN RECIPES:
{packet sections 1, 3, 4}

CURRENT TEST:
```java
{test}
```

FAILURE:
{trimmed diagnostics}
```

### 8.3 Intention synthesis prompt (§9)

```
You are given a Java unit test and the focal method it exercises. Write the validation intention a
developer would have stated BEFORE writing this test, as JSON with keys:
"objective" (≤ 50 words: what requirement scenario is validated),
"preconditions" (optional: state/inputs required before invoking the focal method),
"expected_results" (optional: verifiable behavior). preconditions + expected_results ≤ 200 words.
Use natural language; do not quote code (fewer than 5% of tokens may be identifiers).
```

---

## 9. Task construction

`scripts/make_intentions.py --repo --index --model --out tasks.jsonl`:

1. For each `TestInfo` with **exactly one** callee into the source roots
   whose owner is a non-test type: that callee is the focal method.
2. Drop focal methods that are trivial (getters/setters, ≤ 3 LOC), abstract,
   or private.
3. Ask the LLM (prompt §8.3) once per test; reject outputs violating the
   length or code-token constraints and re-ask at most once.
4. Write one task line per test; `ref_test_id` = the test's id.
5. Sample 10 % for manual review by two people; disagreements refine the
   rubric; report agreement.

**Leakage control (MUST).** When running a task, `index.exclude_test(ref_test_id)`
hides the reference test from fixtures, helpers, referable-test retrieval,
and the idiom example. The generator never sees the answer. The baseline
agent is run on a checkout where the reference test method has been removed
from its file (a script produces this checkout per task; the file is
restored afterwards).

---

## 10. Baselines

### 10.1 OpenHands (primary; required by R3)

- Headless mode, same model endpoint, `max_iterations = 30`, no internet,
  sandbox mounted on the per-task checkout (§9).
- Task prompt: the intention verbatim + "Write a JUnit test for
  `<C>#<m>` in `<test_root>` that validates this intention, compiles, and
  passes. Run it to confirm." Prompt text is fixed and hashed into
  `config_json`.
- Three runs per task; report mean and spread.
- `parse_trajectory.py` maps the saved trajectory/event log into the ledger:
  each LLM completion → one `llm_calls` row (tokens from the event's usage
  metadata); each file-reading tool action (read/view/cat/grep/sed/head/
  tail/less/open, including files matched by grep) → `file_access` rows;
  `wall_ms` from first to last event; the final test file is compiled and
  run by *our* S4 step 5 to produce `compiled`/`passed` with the same
  verdict rules. **The exact event schema must be checked against the
  pinned OpenHands version and documented in `baselines/openhands/README.md`.**

### 10.2 SWE-agent (secondary, time permitting): same protocol.

### 10.3 IntentionTest-style (re-implemented on the harness)

Embedding retrieval (CodeT5+ or any local embedding model) of the top-1
referable test by intention+signature similarity; prompt = full focal class
+ full referable test file + intention; up to 4 LLM refinement rounds each
fed the full test and the full failure output. Same ledger. This isolates
"demand-driven packet + static repair" from "retrieve-and-edit".

---

## 11. Evaluation protocol and metrics

Subjects: the 13 Java projects of IntentionTest (comparability) plus 1–2
repositories with > 10,000 source files (candidates: Elasticsearch, Apache
Flink, Spring Framework; decide after `scripts/count_source_files.sh` and a
build attempt). No industrial repository is available; stated as a threat.

Metrics per task (from the ledger): `compiled`, `passed`, `n_asserts`,
`mutation_score` (PIT on a sampled subset, restricted to the focal class),
`alignment_score` (LLM-judge with a 0–2 rubric — does the test's assertion
check the expected result? — calibrated on the human 10 % sample; report
Cohen's κ), tokens, calls, `wall_ms`, `inspected_files`, cost per passing
test (provider price × tokens).

RQ1 quality parity with the agent baseline; RQ2 efficiency deltas vs. the
R3 thresholds (≥ 50 % tokens, ≥ 20 % wall-clock) plus inspected files;
RQ3 ablations (packet vs. full-class context vs. embedding retrieval; static
repair vs. LLM repair; `B_files`, `B_tokens`, `d_max`); RQ4 sensitivity to
model size and to objective-only intentions; RQ5 predicate accuracy — when
`status = sufficient`, how often S3 passes without S5, vs. `fallback`.

---

## 12. Acceptance tests (definition of done)

All tests run offline, no LLM, no Java toolchain, on a hand-written fixture
`tests/fixtures/mini_index.json` (≈ 6 types, 3 tests, one project block).

| Module | Test |
|---|---|
| `db` | `init_schema` twice is idempotent; `start_run` twice returns the same id; `finish_run` sums tokens/calls/files from the ledger. |
| `llm` | `DryRunClient.chat` writes one `llm_calls` row per call with increasing `seq`; `LLMClient` retries exactly `max_retries` times on a refused connection then raises `LLMError`. |
| `index` | `load` round-trips the fixture; `exclude_test` hides that test from `tests_calling` and `fixtures_of_type`. |
| `demand` | For a fixture focal method `Foo#process(Bar, int)` with expected_results mentioning "throws", `compute_demands` returns Receiver(Foo), Arg(0,Bar), Arg(1,int), Setup for each read field (≤5), Oracle(exception, …), Idiom; keys are unique. |
| `expand` | With `Bar` constructible only via `Bar.of(String)`: `resolve("Bar")` = Factory with cost 0; `sufficient` is false on an empty ctx and true after one expansion; `expand` with `budget_files=1` returns `fallback` and lists referable tests; the trace has one line per step. |
| `packet` | Sections appear in the specified order; a `budget_tokens` small enough drops section 6 but never sections 1–3, 5. |
| `repair` | A class without a package gets `C`'s package; `@Test` with JUnit-5 project adds the jupiter import; an ambiguous simple name resolves to `C`'s package. |
| `execute` | `write_test` places the file under the focal package path; `_trim_failure` extracts the first javac error block from a captured Maven log fixture. |
| `cli` | `run --dry-run` on the fixture repo produces one `runs` row with `status='done'` and checkpoints S1–S4; re-running is a no-op; `report` prints a table. |
| `indexer` (Java) | On a 3-file sample project: every constructor/factory/builder/singleton in the sample appears; `callees` of the sample test resolve to the focal method; `body_reads_fields` lists the read field. |
| `parse_trajectory` | A recorded OpenHands trajectory fixture yields the expected number of `llm_calls` and the expected distinct `file_access` paths. |

---

## 13. Work split and milestones

| Track | Scope | Week-1 deliverable | Week-3 deliverable |
|---|---|---|---|
| A. Indexer + static analysis | `indexer/`, `index.py`, `demand.py`, `expand.py`, `repair.py` | `index.json` for 2 pilot projects; §12 tests for index/demand/expand | JavaParser-based repair; Σ accuracy report on pilots |
| B. Harness + generation + execution | `db.py`, `llm.py`, `packet.py`, `generate.py`, `execute.py`, `refine.py`, `cli.py` | `run --dry-run` end-to-end; ledger frozen | real end-to-end on one pilot with an open-weight model |
| C. Baselines + tasks + evaluation | `baselines/`, `scripts/make_intentions.py`, `metrics.py`, PIT, LLM-judge, human review | OpenHands runs on 10 tasks with parsed ledger rows | tasks for all pilots; baseline on pilots; first Pareto plot |

The ledger schema (§6) is frozen at the end of week 1 so tracks proceed
independently. Weekly sync; every number in a table must be reproducible by
`demandtest report` on a committed `runs.db`.

---

## 14. Open questions

1. Generics/wildcards in `Arg` needs: erase (default) or track type arguments?
2. `Setup` over-approximation on large classes: cap at 5 most-read fields (default) — measure.
3. LLM-judge alignment trustworthiness: calibrate on the 10 % human sample before reporting.
4. Gradle: `--tests` filtering and classpath extraction differ; Maven first.
5. May S2 consult the intention text for recipe choice (e.g., "empty list" → `List.of()`)? Cheap; decide after the first ablation.
6. Index size on Elasticsearch-class repos: streaming writer + lazy loader, or shard by package?
7. Whether to count the focal file itself in `inspected_files` (currently yes, for every system).

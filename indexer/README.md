# S0 — DemandTest project indexer

Turns a Java repository into the `index.json` of **SPEC §4** so the S1–S5 pipeline
(`demandtest/`) has a project to plan *demand* from. Java 17, JavaParser 3.26.4 + symbol
solver, Gson. The shaded jar has no runtime dependencies.

## Build

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17      # any JDK 17+
export PATH="$JAVA_HOME/bin:/opt/homebrew/opt/maven/bin:$PATH"
cd demandtest/indexer
mvn -B -Dmaven.repo.local=../.m2repo package   # -> target/indexer.jar, runs the §12 acceptance test
./verify_sample.sh                             # + Python cross-load + mini_repo generalization check
```

`-Dmaven.repo.local=../.m2repo` is only for agent runs inside the workspace-only sandbox, where
`~/.m2` is not writable; in a normal shell with a populated `~/.m2` plain `mvn package` is right.

## Run

```bash
java -jar target/indexer.jar --repo <repo-dir> --out <index.json> \
     [--src-roots a,b] [--test-roots c,d] [--classpath-file <path>] [--maven-classpath]
```

| flag | meaning |
| --- | --- |
| `--repo <path>` | existing repository directory (required) |
| `--out <path>` | output path, parent directories are created (required) |
| `--src-roots a,b` | main source roots, repo-relative; repeatable, comma-separated |
| `--test-roots c,d` | test source roots, repo-relative; repeatable, comma-separated |
| `--classpath-file f` | dependency classpath for the symbol solver: one entry per line or `:`-separated |
| `--maven-classpath` | run `mvn -o -q dependency:build-classpath` first (slow, off by default) |
| `-h`, `--help` | usage on stdout, exit 0 |

Unknown/missing arguments print usage on **stderr** and exit **2**; success returns normally
(exit 0) — never `System.exit(0)`, the acceptance test calls `Main.main` in-process.

```bash
java -jar target/indexer.jar --repo src/test/resources/sample3 --out /tmp/sample3.json
./verify_sample.sh          # indexes sample3 + tests/fixtures/mini_repo and cross-loads them
```

Diagnostics go to stderr (`N unresolved symbol(s), e.g. [...]`); stdout stays clean and the
output file is byte-stable: sorted file order, no timestamps, no absolute paths except the
stderr summary.

## Output

`index.json` per SPEC §4.2: `project`, `types` (main sources only, nested types dotted),
`tests`, and `test_helpers` — a top-level **object** keyed by test-source FQN, because
`demandtest/index.py:305` parses those entries as `Method`. Each entry is streamed with Gson
`JsonWriter` as it is produced, so a large repository never needs the whole document in memory
(SPEC §4.3).

## How it works

* **`RepoLayout`** detects roots (explicit flags → Maven `<modules>` recursion → Gradle →
  conventional `src/main/java` + `src/test/java`) and returns the sorted, deduplicated file
  list.
* **`Pom`** reads the root pom with a namespace-unaware DOM parse (`artifactId`,
  `java_version`, `<modules>`, JUnit/Mockito dependency flags); any failure degrades to
  `build_tool: "unknown"` instead of aborting.
* **`Classpath`** wires `ReflectionTypeSolver` + one `JavaParserTypeSolver` per root (plus
  optional jar classpath and `<module>/target/classes`).
* **`Extractor`** runs three passes — all main-source types, then all test-source types, then
  the test methods — so a call inside a test can be classified as a *callee* (resolves into a
  main root) or a *helper* (resolves into a test root) even when the target file sorts after
  the caller.
* **`Names`** keeps two erasures: `eraseGenerics` (keeps `[]`, used for
  `params[].type`/`returns`/fields/supertypes) and `signatureErase` (mirrors
  `demandtest/index.py:erase` — `...`→`[]`, drop `<...>`, drop `[]` — used for `callees` and
  `helpers[].id`, which `packet.py` looks up via `index.tests_calling(fqn#sig)`).
* One unparsable file never aborts a run: it is reported on stderr (with the recovered parse
  problems when JavaParser could still build a partial AST) and the run continues. Unresolved
  symbols are counted and sampled (`ProblemLog`), never dropped silently.

No sample-specific logic: `src/main` contains no `Widget`/`sample3`/`com.sample`/`mini`
literal. Verified on sample3, `tests/fixtures/mini_repo` and a synthetic multi-module torture
repository (records + compact constructors, interfaces with default methods and constants,
abstract classes with protected constructors, enums, nested classes, nested-enum constants,
generics/arrays/varargs, implicit and package-private constructors, a file with a syntax error,
unresolvable supertypes/parameter types, JUnit 4 + JUnit 5 + AssertJ + Mockito).

## Decisions

1. **`callees` = every `MethodCallExpr` in the test body** (lambdas included) that resolves
   into a main source root, in first-appearance order, deduped, as
   `<owner-fqn>#<name>(<erased params>)`. Constructor calls are not callees
   (`ObjectCreationExpr`/`ExplicitConstructorInvocationStmt` are not `MethodCallExpr`). This is
   a **superset** of the hand-written `tests/fixtures/mini_index.json`, which lists one callee
   per test; `verify_sample.sh` compares by containment.
2. **Unresolved calls** are recorded as `<unresolved>#<name>(<args>)` (argument types from the
   resolved or written type, `_` when unknown) only when the receiver looks like a *project*
   type — a resolved scope's simple name, or a local name/`new X()`, matching an indexed main
   type. This keeps `assertEquals(...)`/`mock(...)`/`verify(...)` out of `callees` while still
   recording project calls whose overload resolution failed.
3. **`builders`** = instance or static methods whose declared return type is a type whose
   simple name ends in `Builder` *and* which itself declares a no-arg `build()` returning the
   indexed type. `factories` = static methods returning the type or a subtype. Both are
   non-private and non-void. (`WidgetBuilder` therefore has neither — its `build()` returns
   `Widget`.)
4. **`observables`** are emitted as Method objects: (a) public no-arg non-void methods,
   (b) `equals`/`hashCode`/`toString` when non-private, (c) `size`/`isEmpty`/`contains` when
   non-private, (d) public final fields — the latter in the Method shape *plus* `type` and
   `initializer`, so `index.py` can parse the whole array as `Method` and `expand.py` still
   gets `Field` objects for public final fields from `fields`/`singletons`.
5. **`test_helpers`** holds test-source types with at least one non-void, non-private declared
   method; per test, `helpers` lists the test-root methods the test calls as
   `{"id": "<fqn>#<name>(<erased params>)", "returns": "<fqn>|void"}`.
6. **`types` excludes test roots**; test classes live only in `tests`/`test_helpers`
   (`verify_sample.sh` asserts `ix.type("com.sample.WidgetTest") is None`).
7. **`source`** is `MethodDeclaration.toString()` (JavaParser's pretty printer), which matches
   the hand fixture byte for byte, and `line_start`/`line_end` span the whole declaration
   including its annotations.
8. **Idioms** are per class from imports (assertj > truth > hamcrest > junit) with the pom as
   the fallback signal for Jupiter/JUnit 4/Mockito; `framework` prefers JUnit 5.
9. **`commit`** comes from `git -C <repo> rev-parse HEAD` (10 s timeout, `null` on any
   failure — as for `tests/fixtures/mini_repo`, which is not a git repository).
10. **Gradle** support is conventional-roots only: no `settings.gradle` include walking, and
    `java_version` stays `null` (a Groovy/Kotlin DSL is not parsed).

## Known limitations

* Calls on a fully unresolved receiver that is not recognisable as a project name (e.g. a call
  on a method-call result) are skipped rather than guessed; the count is reported on stderr.
* `body_reads_fields` treats `values.put(...)` as a read of `values`, while
  `mini_index.json` lists it under `body_writes_fields` for `Store#put`; the brief restricts
  writes to assignment/unary targets, and `verify_sample.sh` does not compare that field.
* Hand fixture drift (not reproduced, not compared): `FooTest`'s `line_start`/`line_end`
  (12/20 vs the file's real 10/15), `BarTest.mocking` (`none` vs the pom-level `mockito`), and
  the one-callee-per-test lists (see decision 1).
* Generic type arguments are erased in signatures, so two overloads differing only in type
  arguments collapse — the same behaviour as `demandtest/index.py:erase`, which is what
  `packet.py` looks up.
* `java_version` is `null` when the pom expresses it only through a property indirection that
  cannot be resolved (`${...}` values are skipped rather than guessed).

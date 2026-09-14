# S0 indexer — implementation brief

Audience: the model (or human) implementing S0. Read `docs/SPEC.md` §4 and §12 first; this brief
fixes the points §4 leaves open, states the acceptance contract, and records the decisions that the
Python side already depends on. Where this brief and a guess disagree, the brief (and the code in
`demandtest/index.py`) wins.

**Deliverable.** A Java 17 Maven project in `indexer/` that builds `indexer/target/indexer.jar` and
turns a Java repository into `index.json` per §4.2.

## 1. Files you own, files you must not touch

Create / own:

- `indexer/src/main/java/com/demandtest/indexer/**` — `Main` plus whatever classes you need.
- `indexer/README.md` — build & run commands, the decisions taken, known limitations.

Do **not** modify — a failure in these is the bug to fix, not the test:

- `indexer/pom.xml` (deps and plugins are fixed: JavaParser 3.26.4 symbol solver, Gson 2.11,
  JUnit 5, shade → `target/indexer.jar` with `Main-Class: com.demandtest.indexer.Main`).
- `indexer/src/test/java/com/demandtest/indexer/Sample3AcceptanceTest.java` — the §12 acceptance test.
- `indexer/src/test/resources/sample3/**` — the 3-file sample project.
- `indexer/verify_sample.sh` — end-to-end check (Java test + Python consumer + generalization).

Adding a dependency to `pom.xml` is allowed only if it is genuinely required; report it explicitly.

## 2. CLI (§4.1)

```
java -jar indexer/target/indexer.jar --repo <path> --out <index.json> [--src-roots a,b] [--test-roots c,d]
```

- `--repo` (required) existing directory; `--out` (required) file path, parent directories created.
- `--src-roots` / `--test-roots`: comma-separated, repeatable, relative to `--repo` (absolute also
  accepted).
- `--classpath-file <path>`: extra symbol-solver classpath (dependency jars), one entry per line or
  `:`-separated. Optional.
- `--maven-classpath`: opt-in; run
  `mvn -o -q dependency:build-classpath -Dmdep.outputFile=<tmp>` (≤ 120 s, all failures ignored) and
  add the jars to the solver. **Off by default** so the acceptance test stays hermetic and fast; the
  pilot invocation passes `--classpath-file` produced once by `mvn dependency:build-classpath`.
- Unknown/missing arguments: usage on stderr, exit code 2.
- **`Main.main` must return normally on success — never `System.exit(0)`**: the acceptance test calls
  it in-process and an exit would take the test JVM with it. Fatal errors may `System.exit(2)`.

## 3. Roots and the `project` block

Root detection, in order:

1. explicit `--src-roots` / `--test-roots`;
2. else if `<repo>/pom.xml` exists: `build_tool = "maven"`; walk `<modules>` recursively and add
   `<module>/src/main/java` (src root) and `<module>/src/test/java` (test root) for every directory
   that exists, root module included;
3. else if `<repo>/build.gradle` or `build.gradle.kts` exists: `build_tool = "gradle"`, conventional
   roots;
4. else `src/main/java`, `src/test/java` when they exist; `build_tool = "unknown"`.

`project` fields:

| field | rule |
|---|---|
| `name` | root pom `<artifactId>`, else the repository directory name |
| `commit` | `git -C <repo> rev-parse HEAD` (ProcessBuilder, 10 s); `null` when unavailable |
| `build_tool` | `maven` \| `gradle` \| `unknown` |
| `java_version` | root pom `maven.compiler.release`/`source`/`target` or `java.version` property, else `null` |
| `src_roots`, `test_roots` | repo-relative, forward slashes, no trailing slash, discovery order |
| `n_source_files`, `n_test_files` | count of `.java` files under the respective roots |
| `test_framework` | `junit5` if any test-root file imports `org.junit.jupiter.*` (or uses its `@Test`); else `junit4` for `org.junit.*`; else from pom deps (`junit-jupiter` → junit5, `junit` → junit4); else `null` |
| `assertion_lib` | priority `assertj` > `truth` > `hamcrest` > `junit`, from test-root imports (`org.assertj.core.api`, `com.google.common.truth`, `org.hamcrest`, `org.junit.jupiter.api.Assertions`/`org.junit.Assert`); else `null` |
| `mocking_lib` | `mockito` if any test-root file imports `org.mockito.*` or the pom declares mockito; else `none` |

Detection uses **AST/import text**, not symbol resolution (JUnit and Mockito are usually not on the
sample/repo classpath).

## 4. `types` — main source roots only

One entry per declared type (top-level **and nested**; nested FQNs are dotted:
`com.sample.Widget.Color`). Types declared under test roots are **not** in `types` — they appear in
`tests[].class` and `test_helpers`. This matches the hand-written reference fixture
`tests/fixtures/mini_index.json` (7 main types, no test classes) and `demandtest/index.py`.

| key | rule |
|---|---|
| `kind` | `class` \| `interface` \| `enum` \| `record` \| `abstract` (abstract class → `abstract`; annotation → `interface`) |
| `file` | repo-relative, forward slashes |
| `package`, `line`, `type_params` | as written / 1-based declaration line |
| `supertypes` | resolved FQNs of `extends` + `implements`, erased, declaration order |
| `ctors` | declared **public and package-private** constructors in declaration order; `name = "<init>"`, `returns = "void"`. A class with no declared constructor gets its implicit default constructor (public if the class is public, else package). Private constructors are **never** indexed. |
| `factories` | methods **declared in this type**, `static`, whose resolved return type erases to this type or a subtype of it |
| `builders` | methods returning a type whose simple name ends in `Builder` **and** which declares `build()` returning this type |
| `singletons` | `static` fields whose erased type is this type, plus enum constants of this type (Field entries, declaration order) |
| `observables` | (a) public methods with zero parameters and non-void return (no `get`/`is` prefix required), (b) `equals`/`hashCode`/`toString`, (c) `size`/`isEmpty`/`contains`, (d) public final fields |
| `methods` | **all** declared methods including private ones, constructors excluded |
| `fields` | all declared fields |

`observables` may mix methods and fields. Field-shaped observables must also carry `returns`
(equal to the field type), `params: []` and `visibility`, because `demandtest/index.py` parses every
`observables` entry as a `Method` (it has no field variant). Document this in `README.md`.

Every Method / Field entry carries `file` and `line` (methods also `line_end`); §6 accounts inspected
files per entity, so an entry without a file path is a defect. Enum constants are Field entries with
the constant's file and line.

Parameter and type names (§4.4 / `erase`):

- resolved type → fully-qualified name, **type arguments erased** (`Map<String, Integer>` →
  `java.util.Map`, as in `mini_index.json`), array brackets kept (`java.lang.String[]`), primitives
  as-is (`int`);
- unresolved type → the text as written, with `"resolved": false` on the parameter — never dropped;
- `throws` → resolved FQNs;
- `body_reads_fields` / `body_writes_fields` → names of fields **declared in the owner** that the body
  reads / writes, flow-insensitive, declaration order, deduplicated. Compound assignment
  (`counter = counter + 1`) counts as both a read and a write.

## 5. `tests` and `test_helpers`

One entry per test method in a test root, where "test" means annotated `@Test`, `@ParameterizedTest`,
`@RepeatedTest` or `@TestFactory`.

- `id` = `<TestFQN>#<method>`; `class` = declaring type FQN; `line_start`/`line_end` = the method's
  range; `source` = the method source text verbatim.
- `framework`, `assertion_lib`, `mocking` are computed per test class as in §3.
- `fixtures` = instance fields of the test class, Field entries with `initializer` = the initializer's
  source text (`null` when absent).
- `callees` = method invocations **inside the test method body** (lambdas included) that resolve into
  a **main** source root, formatted `<FQN>#<name>(<erased param types>)`, first-appearance order,
  deduplicated. **Constructor calls are excluded**, and JDK/external calls (`assertEquals`, `mock`,
  `verify`, `List.of`, …) are not callees. When the solver cannot resolve a call whose scope looks
  like a project type, record `"<unresolved>#<name>(…)"` so the failure rate stays measurable — that
  string should be rare.
  *Rationale*: §9 identifies a task by "exactly one callee into the source roots". Recording every
  call (fixtures, factories, assertions) would make that rule select nothing.
- `helpers` (per test) = `[{"id": "<FQN>#<name>(...)", "returns": "<FQN>|void"}]` for calls inside the
  test method that resolve into a **test** root.
- `test_helpers` (top-level) = a JSON **object** mapping test-source type FQN → array of Method
  objects for that type's non-void, non-private declared methods (static and instance).
  `demandtest/index.py:305` and `expand.py:120` consume exactly this shape; emitting a JSON array
  instead breaks the loader. This is the registry Helper recipes are built from, so it lists
  *available* utility methods, not only the ones some test happened to call.

## 6. Output

- Stream with Gson `JsonWriter` (§4.3): `new JsonWriter(Writer)` + `setIndent("  ")`, UTF-8, parent
  directories of `--out` created. Do not assemble the whole document as one in-memory tree.
- Top-level key order: `project`, `types`, `tests`, `test_helpers`.
- Deterministic output: files visited in sorted order, no timestamps.
- Exit 0 even when many symbols are unresolved (record them; never drop silently).

## 7. Symbol solver wiring

- `CombinedTypeSolver`: `ReflectionTypeSolver` (so `java.lang.String`, `java.nio.file.Path`,
  `java.util.*` resolve) + one `JavaParserTypeSolver` per source/test root + jar/dir solvers from
  `--classpath-file` / `--maven-classpath` + `<module>/target/classes` directories when present.
- `ParserConfiguration`: `setLanguageLevel(JAVA_17)`, `setSymbolResolver(...)`, `setCharacterEncoding(UTF_8)`.
- One unparsable file must not abort the run: report it on stderr and continue.

## 8. Acceptance (definition of done)

1. `cd indexer && mvn -B package` is green and produces `target/indexer.jar`.
2. `indexer/verify_sample.sh` is green. It runs the Java acceptance test, then loads the produced
   `index.json` with the **real Python consumer** (`demandtest.index.load`: lookups, `tests_calling`,
   `fixtures_of_type`, `exclude_test`, `test_helpers`), then indexes `tests/fixtures/mini_repo` and
   compares the derived facts (factories, builders, singletons, public/package constructors, per-test
   callees, `body_reads_fields`) against the hand-written `tests/fixtures/mini_index.json`.
3. No sample-specific hardcoding in `src/main`: no literal `Widget`, `sample3`, `com.sample` or
   `mini`. The indexer must work on any Maven Java repository.
4. The Python package's own suite stays green: `python -m unittest discover -t . -s tests`.

Report back with: commands run, observed output, decisions/deviations from this brief, and known
limitations.

## 9. Environment

```bash
export JAVA_HOME=/opt/homebrew/opt/openjdk@17      # Homebrew keg-only
export PATH="$JAVA_HOME/bin:/opt/homebrew/opt/maven/bin:$PATH"
cd indexer
mvn -B -Dmaven.repo.local=../.m2repo package     # see the note below
./verify_sample.sh
```

**Maven local repository.** In this session the process sandbox allows writes only inside the
workspace, so `~/.m2` is *not* writable (`mkdir ~/.m2` → `Operation not permitted`). Every Maven
invocation run by an agent must therefore pass
`-Dmaven.repo.local=/Users/kalebguo/Desktop/CS527/demandtest/.m2repo` (repo-relative: `../.m2repo`
from `indexer/`). The first such invocation downloads JavaParser, Gson and JUnit 5 (network required
once). `verify_sample.sh` picks this directory up automatically when `../.m2repo` exists, or uses
`$MAVEN_REPO_LOCAL` when set; in a normal (unsandboxed) shell with a populated `~/.m2` it just uses
`~/.m2`. Do not put `-Dmaven.repo.local` into the pilot commands of the final workflow.

`Sample3AcceptanceTest` runs in `mvn test`; `verify_sample.sh` additionally needs the `.venv` Python
with `demandtest` importable (`pip install -e .` in the repository root).

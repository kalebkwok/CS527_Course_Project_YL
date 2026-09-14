#!/usr/bin/env bash
# SPEC §12 — S0 indexer acceptance check, end to end.
#
#   1. mvn -B package    -> target/indexer.jar (also runs Sample3AcceptanceTest, the §12 test)
#   2. run the jar on the 3-file sample project (indexer/src/test/resources/sample3)
#   3. load the produced index.json with the real Python consumer (demandtest.index)
#   4. generalization smoke test: index tests/fixtures/mini_repo and compare the derived facts
#      against the hand-written tests/fixtures/mini_index.json
#
# Usage: demandtest/indexer/verify_sample.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkg_root="$(cd "$here/.." && pwd)"

# Toolchain: Homebrew's openjdk@17 is keg-only, so it is not on PATH by default.
if [[ -z "${JAVA_HOME:-}" && -d /opt/homebrew/opt/openjdk@17 ]]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk@17
fi
if [[ -n "${JAVA_HOME:-}" ]]; then
  export PATH="$JAVA_HOME/bin:$PATH"
fi
if ! command -v mvn >/dev/null 2>&1 && [[ -d /opt/homebrew/opt/maven/bin ]]; then
  export PATH="/opt/homebrew/opt/maven/bin:$PATH"
fi

python_bin="${PYTHON:-}"
if [[ -z "$python_bin" ]]; then
  if [[ -x "$pkg_root/../.venv/bin/python" ]]; then
    python_bin="$pkg_root/../.venv/bin/python"
  else
    python_bin="$(command -v python3)"
  fi
fi

echo "== 1/4 build (mvn -B package, runs Sample3AcceptanceTest) =="
# Maven local repository: agents run inside a workspace-only sandbox, where ~/.m2 is not writable.
# Use $MAVEN_REPO_LOCAL when set, else the in-workspace .m2repo cache when it exists, else ~/.m2.
mvn_args=(-B -q)
if [[ -n "${MAVEN_REPO_LOCAL:-}" ]]; then
  mvn_args+=("-Dmaven.repo.local=$MAVEN_REPO_LOCAL")
elif [[ -d "$pkg_root/.m2repo" ]]; then
  mvn_args+=("-Dmaven.repo.local=$pkg_root/.m2repo")
fi
(cd "$here" && mvn "${mvn_args[@]}" package)
jar="$here/target/indexer.jar"
if [[ ! -f "$jar" ]]; then
  echo "FAIL: $jar was not built" >&2
  exit 1
fi

work="$here/build"
rm -rf "$work"
mkdir -p "$work"

echo "== 2/4 index the 3-file sample project =="
cp -R "$here/src/test/resources/sample3" "$work/sample3"
java -jar "$jar" --repo "$work/sample3" --out "$work/sample3.index.json"

echo "== 3/4 load it with the Python consumer (demandtest.index) =="
SAMPLE_INDEX="$work/sample3.index.json" PYTHONPATH="$pkg_root${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" - <<'PY'
import os

from demandtest import index as index_mod

ix = index_mod.load(os.environ["SAMPLE_INDEX"])
assert ix.project["test_framework"] == "junit5", ix.project
assert ix.project["assertion_lib"] == "junit", ix.project
assert ix.project["mocking_lib"] == "none", ix.project

widget = ix.type("com.sample.Widget")
assert widget is not None, "com.sample.Widget missing"
assert [c.name for c in widget.ctors] == ["<init>", "<init>"], [c.name for c in widget.ctors]
assert [f.name for f in widget.factories] == ["of"], [f.name for f in widget.factories]
assert [b.name for b in widget.builders] == ["builder"], [b.name for b in widget.builders]
assert [s.name for s in widget.singletons] == ["DEFAULT"], [s.name for s in widget.singletons]
assert widget.observables and widget.observables[0].owner == "com.sample.Widget"

sig = "com.sample.Widget#grow(int)"
hits = ix.tests_calling(sig)
assert len(hits) == 2, [t.id for t in hits]
assert ix.fixtures_of_type("com.sample.Widget"), "the `widget` fixture field was not indexed"

# Leakage control (§9) must work on a real index: hiding the reference test hides it everywhere.
ix.exclude_test("com.sample.WidgetTest#growAddsDeltaToSize")
assert [t.id for t in ix.tests_calling(sig)] == ["com.sample.WidgetTest#growRejectsNegativeDelta"]

helpers = ix.test_helpers.get("com.sample.WidgetTest", [])
widget_named = [h for h in helpers if h.name == "widgetNamed"]
assert widget_named, [h.name for h in helpers]
assert widget_named[0].returns == "com.sample.Widget", widget_named[0].returns

# Test-source types live in `tests` / `test_helpers`, not in `types` (matches mini_index.json).
assert ix.type("com.sample.WidgetTest") is None
print("   python cross-load: OK")
PY

echo "== 4/4 generalization check on tests/fixtures/mini_repo =="
java -jar "$jar" --repo "$pkg_root/tests/fixtures/mini_repo" --out "$work/mini.index.json"
MINI_INDEX="$work/mini.index.json" HAND_FIXTURE="$pkg_root/tests/fixtures/mini_index.json" \
  PYTHONPATH="$pkg_root${PYTHONPATH:+:$PYTHONPATH}" "$python_bin" - <<'PY'
import json
import os
import sys

from demandtest import index as index_mod

gen = index_mod.load(os.environ["MINI_INDEX"])
with open(os.environ["HAND_FIXTURE"], encoding="utf-8") as fh:
    hand = json.load(fh)

problems = []
for fqn, want_type in hand["types"].items():
    got_type = gen.type(fqn)
    if got_type is None:
        problems.append(f"missing type {fqn}")
        continue
    for key in ("factories", "builders", "singletons"):
        want = {e["name"] for e in want_type.get(key, [])}
        got = {e.name for e in getattr(got_type, key)}
        if not want <= got:
            problems.append(f"{fqn}.{key}: {sorted(want)} is not contained in {sorted(got)}")
    # SPEC §4.2 indexes public and package-private constructors only.
    want_ctors = {
        tuple(p["type"] for p in ctor["params"])
        for ctor in want_type.get("ctors", [])
        if ctor.get("visibility") != "private"
    }
    got_ctors = {tuple(p.type for p in ctor.params) for ctor in got_type.ctors}
    if not want_ctors <= got_ctors:
        problems.append(f"{fqn}.ctors: {sorted(want_ctors)} is not contained in {sorted(got_ctors)}")

for want_test in hand["tests"]:
    got_test = next((t for t in gen.tests if t.id == want_test["id"]), None)
    if got_test is None:
        problems.append(f"missing test {want_test['id']}")
        continue
    missing = set(want_test["callees"]) - set(got_test.callees)
    if missing:
        problems.append(f"{want_test['id']}.callees misses {sorted(missing)} (got {got_test.callees})")

foo = gen.type("com.mini.Foo")
process = next(m for m in foo.methods if m.name == "process")
if "store" not in process.body_reads_fields:
    problems.append(f"com.mini.Foo#process.body_reads_fields = {process.body_reads_fields}, expected `store`")

if problems:
    print("   generalization check FAILED:", file=sys.stderr)
    for problem in problems:
        print("     -", problem, file=sys.stderr)
    sys.exit(1)
print("   generalization check against mini_repo/mini_index.json: OK")
PY

echo
echo "OK: S0 indexer acceptance check passed (SPEC §12, indexer row)"

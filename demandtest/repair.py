"""S4 passes 1–4 — static repair, no LLM (SPEC §2.8).

Regex first cut, explicitly acceptable for the pilot (§2.8 intro). Pass 5
(compile/run) and pass 6 (diagnostics trimming) live in execute.py.

Material assumption: §2.8 pass 2 tie-break (b) "the FQN most imported by
existing tests" is approximated by counting `import <fqn>;` in the focal file
source when provided (the index stores test method bodies, not test-file
imports); otherwise it falls through to (c) JDK, then lexicographic order for
determinism.
"""
from __future__ import annotations

import re

from .demand import Task, find_focal
from .index import Index, JAVA_LANG_SIMPLES, erase, simple

JUnit4_TO_5_IMPORTS = [
    ("import org.junit.Test;", "import org.junit.jupiter.api.Test;"),
    ("import org.junit.Before;", "import org.junit.jupiter.api.BeforeEach;"),
    ("import org.junit.After;", "import org.junit.jupiter.api.AfterEach;"),
    ("import org.junit.BeforeClass;", "import org.junit.jupiter.api.BeforeAll;"),
    ("import org.junit.AfterClass;", "import org.junit.jupiter.api.AfterAll;"),
    ("import org.junit.Ignore;", "import org.junit.jupiter.api.Disabled;"),
    ("import org.junit.Assert;", "import org.junit.jupiter.api.Assertions;"),
    ("import static org.junit.Assert.", "import static org.junit.jupiter.api.Assertions."),
]
JUnit4_TO_5_TOKENS = [
    ("@BeforeClass", "@BeforeAll"), ("@AfterClass", "@AfterAll"),
    ("@Before", "@BeforeEach"), ("@After", "@AfterEach"), ("@Ignore", "@Disabled"),
    ("Assert.", "Assertions."),
]
JUnit5_TO_4_IMPORTS = [(b, a) for a, b in JUnit4_TO_5_IMPORTS]
JUnit5_TO_4_TOKENS = [("@BeforeEach", "@Before"), ("@AfterEach", "@After"),
                      ("@BeforeAll", "@BeforeClass"), ("@AfterAll", "@AfterClass"),
                      ("@Disabled", "@Ignore"), ("Assertions.", "Assert.")]

# (library, token in source) -> import line (§2.8 pass 2, framework imports)
FRAMEWORK_IMPORTS = {
    ("junit5", "@Test"): "import org.junit.jupiter.api.Test;",
    ("junit5", "@BeforeEach"): "import org.junit.jupiter.api.BeforeEach;",
    ("junit5", "@AfterEach"): "import org.junit.jupiter.api.AfterEach;",
    ("junit5", "assertEquals("): "import static org.junit.jupiter.api.Assertions.assertEquals;",
    ("junit5", "assertTrue("): "import static org.junit.jupiter.api.Assertions.assertTrue;",
    ("junit5", "assertFalse("): "import static org.junit.jupiter.api.Assertions.assertFalse;",
    ("junit5", "assertNull("): "import static org.junit.jupiter.api.Assertions.assertNull;",
    ("junit5", "assertNotNull("): "import static org.junit.jupiter.api.Assertions.assertNotNull;",
    ("junit5", "assertThrows("): "import static org.junit.jupiter.api.Assertions.assertThrows;",
    ("junit5", "Assertions."): "import org.junit.jupiter.api.Assertions;",
    ("junit4", "@Test"): "import org.junit.Test;",
    ("junit4", "assertEquals("): "import static org.junit.Assert.assertEquals;",
    ("junit4", "assertTrue("): "import static org.junit.Assert.assertTrue;",
    ("junit4", "assertFalse("): "import static org.junit.Assert.assertFalse;",
    ("junit4", "Assert."): "import org.junit.Assert;",
    ("mockito", "mock("): "import static org.mockito.Mockito.mock;",
    ("mockito", "when("): "import static org.mockito.Mockito.when;",
    ("mockito", "verify("): "import static org.mockito.Mockito.verify;",
    ("java", "List.of("): "import java.util.List;",
    ("java", "Map.of("): "import java.util.Map;",
    ("java", "Set.of("): "import java.util.Set;",
}


def _fix_package(src: str, pkg: str, fixes: list[str]) -> str:
    m = re.search(r"(?m)^[\t ]*package\s+([\w.]+)\s*;", src)
    if m:
        if m.group(1) != pkg:
            fixes.append(f"package {m.group(1)} -> {pkg}")
            src = src[:m.start()] + f"package {pkg};" + src[m.end():]
        return src
    lines = src.split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or
                              lines[i].lstrip().startswith(("/*", "*", "//", "*/"))):
        i += 1
    lines.insert(i, f"package {pkg};")
    fixes.append(f"package {pkg}")
    return "\n".join(lines)


def _existing_import_simples(src: str) -> set[str]:
    out = set()
    for m in re.finditer(r"(?m)^[\t ]*import\s+(?:static\s+)?([\w.]+)\s*;", src):
        out.add(m.group(1).rsplit(".", 1)[-1])
        if m.group(1).startswith("java.lang."):
            out.add(m.group(1).split(".")[2])
    return out


def _declared_names(src: str) -> set[str]:
    return set(re.findall(r"\b(?:class|interface|enum|record)\s+(\w+)", src))


def _resolve_ambiguous(name: str, candidates: list, c_pkg: str, focal_source: str | None) -> str | None:
    fqns = [t.fqn for t in candidates]
    for fqn in fqns:  # (a) C's package
        if erase(fqn).rsplit(".", 1)[0] == c_pkg:
            return fqn
    if focal_source:  # (b) most imported by existing sources we can see
        best, best_n = None, 0
        for fqn in fqns:
            n = len(re.findall(rf"(?m)^[\t ]*import\s+(?:static\s+)?{re.escape(erase(fqn))}\s*;", focal_source))
            if n > best_n:
                best, best_n = fqn, n
        if best:
            return best
    for fqn in sorted(fqns):  # (c) JDK, then lexicographic for determinism
        if erase(fqn).startswith(("java.", "javax.")):
            return fqn
    return sorted(fqns)[0]


def _fix_imports(index: Index, task: Task, src: str, focal_source: str | None, fixes: list[str]) -> str:
    C, _ = find_focal(index, task)
    pkg = C.package
    have = _existing_import_simples(src)
    declared = _declared_names(src)
    used = set()
    for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]*)\b", src):
        prev = src[m.start() - 1] if m.start() > 0 else ""
        if prev not in ("@", "."):  # skip annotations and fully-qualified segments
            used.add(m.group(1))
    add: set[str] = set()  # full import lines
    for name in sorted(used):
        if name in JAVA_LANG_SIMPLES or name in have or name in declared:
            continue
        candidates = index.by_simple_name(name)
        if not candidates:
            continue
        if len(candidates) == 1:
            fqn = candidates[0].fqn
        else:
            fqn = _resolve_ambiguous(name, candidates, pkg, focal_source)
        add.add(f"import {erase(fqn)};")
        fixes.append(f"import {erase(fqn)}")
    # NB: `add` holds complete import statements including the trailing semicolon.
    proj = index.project
    libs = (proj.get("test_framework", ""), proj.get("mocking_lib", "none"), "java")
    for (lib, token), imp in FRAMEWORK_IMPORTS.items():
        if lib not in libs or token not in src:
            continue
        path = imp[len("import "):].rstrip(";")
        if path.removeprefix("static ").rsplit(".", 1)[-1] not in have and imp not in add:
            add.add(imp)
            fixes.append(imp.rstrip(";"))
    if proj.get("assertion_lib") == "assertj" and "assertThat(" in src:
        imp = "import static org.assertj.core.api.Assertions.assertThat;"
        if "assertThat" not in have and imp not in add:
            add.add(imp)
            fixes.append(imp.rstrip(";"))
    if not add:
        return src
    lines = src.split("\n")
    insert_at = 0
    for i, ln in enumerate(lines):
        if ln.strip().startswith("package "):
            insert_at = i + 1
            break
    block = sorted(add)
    lines[insert_at:insert_at] = block
    return "\n".join(lines)


def _fix_throws(src: str, focal_method, fixes: list[str]) -> str:
    checked = [t for t in (focal_method.throws or [])
               if simple(t) not in ("RuntimeException", "Error")]
    if not checked:
        return src

    def repl(mo: re.Match) -> str:
        sig = mo.group(0)
        if "throws" in sig:
            return sig
        fixes.append("throws Exception")
        return sig[: mo.start(1) - mo.start(0)].rstrip() + " throws Exception " + mo.group(1)

    return re.sub(r"\b(?:public\s+|protected\s+|private\s+)?void\s+\w+\s*\([^)]*\)\s*(\{)", repl, src)


def _fix_idiom(src: str, framework: str | None, fixes: list[str]) -> str:
    if framework == "junit5" and ("org.junit." in src or "@Before" in src or "@After" in src) \
            and "jupiter" not in src:
        for old, new in JUnit4_TO_5_IMPORTS:
            if old in src:
                src = src.replace(old, new)
                fixes.append(f"idiom: {old} -> {new}")
        for old, new in JUnit4_TO_5_TOKENS:
            if old in src:
                src = re.sub(re.escape(old) + r"(?!\w)", new, src)
                fixes.append(f"idiom: {old} -> {new}")
    elif framework == "junit4" and ("jupiter" in src or "@BeforeEach" in src):
        for old, new in JUnit5_TO_4_IMPORTS:
            if old in src:
                src = src.replace(old, new)
                fixes.append(f"idiom: {old} -> {new}")
        for old, new in JUnit5_TO_4_TOKENS:
            if old in src:
                src = re.sub(re.escape(old) + r"(?!\w)", new, src)
                fixes.append(f"idiom: {old} -> {new}")
    return src


def static_repair(index: Index, task: Task, src: str, focal_source: str | None = None) -> tuple[str, list[str]]:
    """Returns (repaired source, list of fix descriptions) — §2.8 passes 1–4."""
    C, m = find_focal(index, task)
    fixes: list[str] = []
    src = _fix_package(src, C.package, fixes)
    src = _fix_imports(index, task, src, focal_source, fixes)
    src = _fix_throws(src, m, fixes)
    src = _fix_idiom(src, index.project.get("test_framework"), fixes)
    return src, fixes

"""Project index loading and queries (SPEC §4 schema, §5 index.py contract).

Loader-side enrichment beyond the JSON schema (material assumptions):
- Every Method/Field is stamped with `owner` (the FQN of its declaring type);
  §2.4 needs "an observable o of tau in ctx.entities", which requires ownership.
- Optional top-level `test_helpers` map ({TestFQN: [Method]}) feeds Helper
  recipes; the §4.2 per-test `helpers` entries are string ids without return
  types, so Helper resolution requires this registry. The Java indexer SHOULD
  emit it.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Optional

PRIMITIVE_TYPES = {"int", "long", "double", "float", "boolean", "char", "byte", "short"}

# §4.4 literal-resolvable types: primitives + boxes, String, Object, collections,
# Optional, java.time values, File/Path, BigInteger/BigDecimal, and arrays thereof.
LITERAL_SIMPLES = {
    "String", "Object", "List", "Map", "Set", "Collection", "Optional",
    "LocalDate", "LocalDateTime", "Duration", "File", "Path",
    "BigDecimal", "BigInteger",
    "Integer", "Long", "Double", "Float", "Boolean", "Character", "Byte", "Short",
}
LITERAL_FQNS = {
    "java.lang.String", "java.lang.Object", "java.util.List", "java.util.Map",
    "java.util.Set", "java.util.Collection", "java.util.Optional",
    "java.time.LocalDate", "java.time.LocalDateTime", "java.time.Duration",
    "java.io.File", "java.nio.file.Path", "java.math.BigDecimal", "java.math.BigInteger",
}

JAVA_LANG_SIMPLES = {
    "Object", "String", "Integer", "Long", "Double", "Float", "Boolean", "Character", "Byte", "Short",
    "Math", "System", "Exception", "RuntimeException", "Error", "Throwable", "AssertionError",
    "IllegalArgumentException", "IllegalStateException", "NullPointerException",
    "UnsupportedOperationException", "ArithmeticException", "IndexOutOfBoundsException",
    "NumberFormatException", "ClassCastException", "InterruptedException",
    "Override", "Deprecated", "SuppressWarnings", "FunctionalInterface", "SafeVarargs",
    "Iterable", "Comparable", "Runnable", "Thread", "Number", "Class", "Void",
    "StringBuilder", "CharSequence", "Cloneable", "AutoCloseable", "Enum", "Record",
}


def erase(t: str) -> str:
    """Strip generics (angle-balanced), array brackets and varargs (OPEN 1 default: erase)."""
    if not t:
        return t
    t = t.strip().replace("...", "[]")
    out, depth = [], 0
    for ch in t:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).replace("[]", "").strip()


def simple(t: str) -> str:
    return erase(t).rsplit(".", 1)[-1]


def is_jdk(t: str) -> bool:
    base = erase(t)
    return base.startswith("java.") or base.startswith("javax.")


def is_literal(t: str) -> bool:
    base = erase(t)
    if base in PRIMITIVE_TYPES:
        return True
    return base in LITERAL_FQNS or base in LITERAL_SIMPLES


def sig_of(owner_fqn: str, method) -> str:
    """Canonical callee signature: <FQN>#<name>(<erased param types>), as in §4.2 callees."""
    return f"{owner_fqn}#{method.name}({','.join(erase(p.type) for p in method.params)})"


def parse_sig(sig: str) -> tuple[str, str, list[str]]:
    head, _, tail = sig.partition("(")
    fqn, _, name = head.rpartition("#")
    if not fqn:
        fqn, name = head.rsplit(".", 1) if "." in head else ("", head)
    params = [p for p in tail.rstrip(")").split(",") if p]
    return fqn, name, params


@dataclass
class Param:
    name: str
    type: str
    resolved: bool = True


@dataclass
class Method:
    name: str
    params: list  # list[Param]
    returns: str = "void"
    throws: list = field(default_factory=list)
    static: bool = False
    visibility: str = "public"
    file: Optional[str] = None
    line: Optional[int] = None
    line_end: Optional[int] = None
    body_reads_fields: list = field(default_factory=list)
    body_writes_fields: list = field(default_factory=list)
    owner: Optional[str] = None  # stamped by the loader

    def erased_sig(self) -> str:
        return f"{self.name}({','.join(erase(p.type) for p in self.params)})"

    def signature(self) -> str:
        params = ", ".join(f"{p.type} {p.name}" for p in self.params)
        sig = f"{self.name}({params})"
        if self.returns and self.returns != "void":
            sig += f" -> {self.returns}"
        if self.throws:
            sig += f" throws {', '.join(self.throws)}"
        return sig


@dataclass
class Field:
    name: str
    type: str
    static: bool = False
    visibility: str = "public"
    file: Optional[str] = None
    line: Optional[int] = None
    initializer: Optional[str] = None
    owner: Optional[str] = None  # stamped by the loader


@dataclass
class TypeInfo:
    fqn: str
    kind: str = "class"
    file: Optional[str] = None
    package: str = ""
    line: Optional[int] = None
    supertypes: list = field(default_factory=list)
    type_params: list = field(default_factory=list)
    ctors: list = field(default_factory=list)
    factories: list = field(default_factory=list)
    builders: list = field(default_factory=list)
    singletons: list = field(default_factory=list)
    observables: list = field(default_factory=list)
    methods: list = field(default_factory=list)
    fields: list = field(default_factory=list)


@dataclass
class TestInfo:
    id: str
    file: Optional[str] = None
    class_: str = ""
    method: str = ""
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    framework: Optional[str] = None
    assertion_lib: Optional[str] = None
    mocking: Optional[str] = None
    fixtures: list = field(default_factory=list)
    callees: list = field(default_factory=list)
    helpers: list = field(default_factory=list)
    source: str = ""


def _method(obj: dict, owner: str) -> Method:
    return Method(
        name=obj.get("name", ""),
        params=[Param(p.get("name", "?"), p.get("type", "?"), p.get("resolved", True)) for p in obj.get("params", [])],
        returns=obj.get("returns", "void"),
        throws=list(obj.get("throws", [])),
        static=bool(obj.get("static", False)),
        visibility=obj.get("visibility", "public"),
        file=obj.get("file"),
        line=obj.get("line"),
        line_end=obj.get("line_end"),
        body_reads_fields=list(obj.get("body_reads_fields", [])),
        body_writes_fields=list(obj.get("body_writes_fields", [])),
        owner=owner,
    )


def _field(obj: dict, owner: str) -> Field:
    return Field(
        name=obj.get("name", ""), type=obj.get("type", ""), static=bool(obj.get("static", False)),
        visibility=obj.get("visibility", "public"), file=obj.get("file"), line=obj.get("line"),
        initializer=obj.get("initializer"), owner=owner,
    )


class Index:
    def __init__(self, project: dict, types: dict[str, TypeInfo], tests: list[TestInfo],
                 test_helpers: dict[str, list[Method]] | None = None):
        self.project = project
        self.types = types
        self.tests = tests
        self.test_helpers = test_helpers or {}
        self._by_erased: dict[str, TypeInfo] = {}
        self._by_simple: dict[str, list[TypeInfo]] = {}
        for fqn, t in types.items():
            key = erase(fqn)
            self._by_erased.setdefault(key, t)
            self._by_simple.setdefault(simple(fqn), []).append(t)
        self.excluded: set[str] = set()

    # ------------------------------------------------------------ lookups
    def type(self, fqn: str) -> TypeInfo | None:
        """Generics/arrays are erased before lookup (§5)."""
        return self._by_erased.get(erase(fqn))

    def by_simple_name(self, name: str) -> list[TypeInfo]:
        return list(self._by_simple.get(name, []))

    def subtypes(self, fqn: str) -> list[TypeInfo]:
        base = erase(fqn)
        found, frontier = [], [base]
        while frontier:
            cur = frontier.pop()
            for t in self.types.values():
                if any(erase(s) == cur for s in t.supertypes) and all(t.fqn != f.fqn for f in found):
                    found.append(t)
                    frontier.append(erase(t.fqn))
        return found

    def observables(self, fqn: str) -> list[Method]:
        t = self.type(fqn)
        return list(t.observables) if t else []

    # -------------------------------------------------------------- tests
    def _visible(self, t: TestInfo) -> bool:
        return t.id not in self.excluded

    def tests_calling(self, sig: str) -> list[TestInfo]:
        return [t for t in self.tests if sig in t.callees and self._visible(t)]

    def tests_of_class(self, class_fqn: str) -> list[TestInfo]:
        return [t for t in self.tests if erase(t.class_) == erase(class_fqn) and self._visible(t)]

    def fixtures_of_type(self, fqn: str) -> list[tuple[TestInfo, Field]]:
        base = erase(fqn)
        return [(t, f) for t in self.tests if self._visible(t) for f in t.fixtures if erase(f.type) == base]

    def exclude_test(self, test_id: str) -> None:
        """Leakage control (§9): hides the test from every retrieval below."""
        self.excluded.add(test_id)

    @contextmanager
    def excluded_scope(self, test_ids: set[str]):
        prev = set(self.excluded)
        self.excluded |= set(test_ids)
        try:
            yield
        finally:
            self.excluded = prev

    # ------------------------------------------------------------- files
    def files(self, entities) -> set[str]:
        out: set[str] = set()
        for e in entities:
            for part in (e if isinstance(e, tuple) else (e,)):
                f = getattr(part, "file", None)
                if f:
                    out.add(f)
        return out


def load(path: str) -> Index:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    types: dict[str, TypeInfo] = {}
    for fqn, obj in data.get("types", {}).items():
        owner = erase(fqn)
        types[fqn] = TypeInfo(
            fqn=fqn, kind=obj.get("kind", "class"), file=obj.get("file"), package=obj.get("package", ""),
            line=obj.get("line"), supertypes=list(obj.get("supertypes", [])),
            type_params=list(obj.get("type_params", [])),
            ctors=[_method(m, owner) for m in obj.get("ctors", [])],
            factories=[_method(m, owner) for m in obj.get("factories", [])],
            builders=[_method(m, owner) for m in obj.get("builders", [])],
            singletons=[_field(f, owner) for f in obj.get("singletons", [])],
            observables=[_method(m, owner) for m in obj.get("observables", [])],
            methods=[_method(m, owner) for m in obj.get("methods", [])],
            fields=[_field(f, owner) for f in obj.get("fields", [])],
        )
    tests = [
        TestInfo(
            id=obj["id"], file=obj.get("file"), class_=obj.get("class", ""), method=obj.get("method", ""),
            line_start=obj.get("line_start"), line_end=obj.get("line_end"),
            framework=obj.get("framework"), assertion_lib=obj.get("assertion_lib"), mocking=obj.get("mocking"),
            fixtures=[_field(f, obj.get("class", "")) for f in obj.get("fixtures", [])],
            callees=list(obj.get("callees", [])), helpers=list(obj.get("helpers", [])),
            source=obj.get("source", ""),
        )
        for obj in data.get("tests", [])
    ]
    helpers = {
        test_fqn: [_method(m, test_fqn) for m in methods]
        for test_fqn, methods in data.get("test_helpers", {}).items()
    }
    return Index(dict(data.get("project", {})), types, tests, helpers)

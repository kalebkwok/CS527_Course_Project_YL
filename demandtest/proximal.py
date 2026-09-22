"""Demand-proximal test selection and demand diff (SPEC §2.5.1, §2.6 sections 6–7). No LLM.

Static analog of the path-proximal test of TestTailor (Zhou, Lou, Dong, Hao; PACMSE/FSE 2026,
doi:10.1145/3797140). TestTailor ranks existing tests by Jaccard similarity between their
execution path and an uncovered target path, and tells the model where the closest test
diverges. DemandTest has no target path: its target is the demand set D(m, I). So a test is
ranked by the Jaccard overlap between D and the needs the test already satisfies, computed
from the index and the test source only, and the gap is rendered as a *demand diff*:
"this test already obtains X; it does not assert Y".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .demand import Need, find_focal
from .index import Index, TestInfo, erase, is_literal, parse_sig, simple

# Oracle evidence in a test body (§2.5.1). Shared with execute.target_hit.
_ANY_ASSERT = re.compile(r"\bassert\w*\s*\(|^\s*assert\s", re.M)
ORACLE_EVIDENCE = {
    "exception": re.compile(
        r"assertThrows(?:Exactly)?\s*\(|assertThatThrownBy\s*\(|\.isThrownBy\s*\(|expected\s*=\s*[\w.]+\.class"
        r"|@Test\s*\(\s*expected|\bcatch\s*\(|\bfail\s*\("),
    "return": _ANY_ASSERT,
    "state": _ANY_ASSERT,
    "interaction": re.compile(
        r"\bverify(?:NoMoreInteractions|NoInteractions|ZeroInteractions)?\s*\(|\bthen\s*\([^)]*\)\s*\.should"),
}


@dataclass
class DemandDiff:
    test: TestInfo
    satisfied: list = field(default_factory=list)  # Need
    missing: list = field(default_factory=list)    # Need
    calls_focal: bool = False
    score: float = 0.0


# ------------------------------------------------------------------ helpers


def role(n: Need) -> str:
    if n.kind == "arg":
        return f"arg{n.detail}"
    if n.kind == "setup":
        return f"setup {n.detail}"
    if n.kind == "oracle":
        return f"oracle/{n.detail}"
    return n.kind


def oracle_hint(detail: str) -> str:
    return {
        "exception": "assert that the documented exception is thrown",
        "return": "assert the returned value",
        "state": "assert the resulting observable state",
        "interaction": "verify the interaction through the mocking library",
    }.get(detail, "assert the documented behaviour")


def scorable(demands) -> list[Need]:
    """Needs that carry project knowledge: non-literal construction needs and oracle needs (§2.5.1)."""
    out: list[Need] = []
    for n in demands:
        if n.kind in ("receiver", "arg", "setup") and not is_literal(n.type):
            out.append(n)
        elif n.kind == "oracle":
            out.append(n)
    return out


def calls_focal(index: Index, task, test: TestInfo) -> bool:
    src = test.source or ""
    try:
        C, m = find_focal(index, task)
    except KeyError:
        return re.search(rf"\.{re.escape(task.focal_method)}\s*\(", src) is not None
    if f"{C.fqn}#{m.erased_sig()}" in test.callees:
        return True
    return re.search(rf"\.{re.escape(m.name)}\s*\(", src) is not None


def obtains(index: Index, test: TestInfo, tau: str) -> bool:
    """Does the test already hold a value of tau: fixture, resolved callee, or a construction idiom in its source."""
    base = erase(tau)
    if any(erase(f.type) == base for f in test.fixtures):
        return True
    for sig in test.callees:
        owner, name, _ = parse_sig(sig)
        if erase(owner) == base and name == "<init>":
            return True
        t = index.type(owner)
        if t is not None and any(m.name == name and erase(m.returns) == base for m in t.factories + t.builders):
            return True
        for methods in index.test_helpers.values():
            if any(hm.name == name and erase(hm.returns) == base for hm in methods):
                return True
    src = test.source or ""
    s = re.escape(simple(base))
    if re.search(rf"\bnew\s+{s}\s*[(<]", src):
        return True
    t = index.type(base)
    if t is not None:
        names = {m.name for m in t.factories + t.builders}
        if names and re.search(rf"\b{s}\s*\.\s*(?:{'|'.join(map(re.escape, sorted(names)))})\s*\(", src):
            return True
        if any(re.search(rf"\b{s}\s*\.\s*{re.escape(f.name)}\b", src) for f in t.singletons):
            return True
        if t.kind in ("interface", "abstract") and re.search(rf"\bmock\s*\(\s*{s}\s*\.\s*class", src):
            return True
    for methods in index.test_helpers.values():
        if any(erase(hm.returns) == base and re.search(rf"\b{re.escape(hm.name)}\s*\(", src) for hm in methods):
            return True
    return False


def oracle_evidence(index: Index, test: TestInfo, n: Need) -> bool:
    src = test.source or ""
    pat = ORACLE_EVIDENCE.get(n.detail)
    if pat is None or not pat.search(src):
        return False
    if n.detail == "state":  # an assertion alone is not state evidence: it must read an observable of tau
        obs = index.observables(n.type)
        return not obs or any(re.search(rf"\b{re.escape(o.name)}\s*\(", src) for o in obs)
    return True


# ---------------------------------------------------------------- ranking


def diff(index: Index, task, demands, test: TestInfo) -> DemandDiff:
    """Which needs of D the test already satisfies, and which it leaves open (§2.5.1)."""
    focal = calls_focal(index, task, test)
    universe = scorable(demands)
    sat: list[Need] = []
    miss: list[Need] = []
    for n in universe:
        if n.kind in ("receiver", "arg"):
            ok = focal or obtains(index, test, n.type)
        elif n.kind == "setup":
            ok = obtains(index, test, n.type)
        else:
            ok = oracle_evidence(index, test, n)
        (sat if ok else miss).append(n)
    score = len(sat) / len(universe) if universe else 0.0
    return DemandDiff(test=test, satisfied=sat, missing=miss, calls_focal=focal, score=score)


def candidates(index: Index, task, demands) -> list[TestInfo]:
    """Visible tests that touch the focal method, its class, or any type in the demand set."""
    types = {erase(n.type) for n in scorable(demands)}
    seen: set[str] = set()
    out: list[TestInfo] = []

    def add(t: TestInfo) -> None:
        if t.id not in seen and t.id not in index.excluded:
            seen.add(t.id)
            out.append(t)

    try:
        C, m = find_focal(index, task)
        for t in index.tests_calling(f"{C.fqn}#{m.erased_sig()}"):
            add(t)
        for t in index.tests_of_class(C.fqn):
            add(t)
        for t, _ in index.fixtures_of_type(C.fqn):
            add(t)
    except KeyError:
        pass
    simples = sorted({simple(tau) for tau in types})
    mention = re.compile(r"\b(?:" + "|".join(map(re.escape, simples)) + r")\b") if simples else None
    for t in index.tests:
        if any(erase(parse_sig(s)[0]) in types for s in t.callees):
            add(t)
        elif mention is not None and mention.search(t.source or ""):
            add(t)
    return out


def rank(index: Index, task, demands, k: int = 2, pool: list[TestInfo] | None = None) -> list[DemandDiff]:
    """Top-k demand-proximal tests: by score, then calls-m, then shortest source, then id; score 0 is dropped."""
    pool = candidates(index, task, demands) if pool is None else pool
    diffs = [diff(index, task, demands, t) for t in pool]
    diffs = [d for d in diffs if d.score > 0]
    diffs.sort(key=lambda d: (-d.score, not d.calls_focal, len(d.test.source or ""), d.test.id))
    return diffs[:k]


# --------------------------------------------------------------- rendering


def _short(n: Need, hint: bool = False) -> str:
    tau = simple(erase(n.type))
    if n.kind == "oracle" and hint:
        return f"{role(n)} ({tau}): {oracle_hint(n.detail)}"
    return f"{role(n)} ({tau})"


def render_diff(d: DemandDiff) -> str:
    sat = ", ".join(_short(n) for n in d.satisfied) or "nothing in the demand set"
    miss = "; ".join(_short(n, hint=True) for n in d.missing) or "nothing"
    return f"satisfies: {sat} | missing: {miss}"

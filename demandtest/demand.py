"""S1 — demand set D(m, I), no LLM (SPEC §2.2, §5 demand.py contract).

0.2.4: oracle cues are matched against expected_results, or the objective when expected_results is
empty; negated cues ("must not throw", "without error") are removed before matching; S1 also
records the semantic-gap flags of §2.4.1 (relational, state, unbound, same_type_args)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .index import Index, erase, is_literal, simple

NEED_KINDS = ("receiver", "arg", "setup", "oracle", "idiom")
GAP_KINDS = ("relational", "state", "unbound", "same_type_args")

_ORACLE_EXCEPTION = re.compile(r"throw|throws|thrown|exception|error|reject|invalid|illegal", re.IGNORECASE)
_ORACLE_STATE = re.compile(r"state|updated|modif|mutat|stored|set to|becomes|register|added to|remove", re.IGNORECASE)
_ORACLE_INTERACTION = re.compile(r"call|invoke|delegate|notif|forward|dispatch|publish", re.IGNORECASE)
# §2.2 negation: a negated cue is removed before the cue regexes run ("does not throw", "without error").
_ORACLE_NEGATION = re.compile(
    r"\b(?:not|never|no|without|neither|nor)\b(?:\s+\w+){0,3}?\s+(?:throw\w*|exception\w*|error\w*|fail\w*|reject\w*|raise\w*)",
    re.IGNORECASE)
_STATE_WORDS = re.compile(r"\b(?:state|initiali[sz]ed|configured|contains|already|existing|registered|empty|non-empty|loaded)\b",
                          re.IGNORECASE)

MAX_SETUP_FIELDS = 5  # §2.2 / OPEN 2 default


@dataclass(frozen=True)
class Need:
    kind: str
    type: str
    detail: str = ""
    constraint: str = ""

    def key(self) -> str:
        return f"{self.kind}:{self.type}:{self.detail}"


@dataclass
class Task:
    repo: str
    focal_class: str
    focal_method: str
    focal_sig: str
    focal_file: str
    intention: dict
    ref_test_id: str
    id: Optional[int] = None

    @classmethod
    def from_row(cls, repo_name: str, row) -> "Task":
        import json
        return cls(
            repo=repo_name, focal_class=row["focal_class"], focal_method=row["focal_method"],
            focal_sig=row["focal_sig"], focal_file=row["focal_file"],
            intention=json.loads(row["intention_json"]), ref_test_id=row["ref_test_id"], id=row["id"],
        )


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"[.;!\n]", text or "") if s.strip()]


def _mentions(sentence: str, name: str) -> bool:
    return bool(name) and re.search(rf"\b{re.escape(name)}\b", sentence) is not None


def _sig_matches(focal_sig: str, m) -> bool:
    """focal_sig format: <name>(<erased simple param types>) e.g. process(Bar,int)."""
    name, _, tail = focal_sig.partition("(")
    if name and name != m.name:
        return False
    want = [p for p in tail.rstrip(")").split(",") if p.strip()]
    if want and len(want) != len(m.params):
        return False
    for w, p in zip(want, m.params):
        if simple(w) != simple(erase(p.type)):
            return False
    return True


def find_focal(index: Index, task: Task) -> tuple:
    """(TypeInfo, Method); KeyError if absent (§5)."""
    t = index.type(task.focal_class)
    if t is None:
        raise KeyError(task.focal_class)
    for m in t.methods + t.ctors:
        if m.name == task.focal_method and (not task.focal_sig or _sig_matches(task.focal_sig, m)):
            return t, m
    matches = [m for m in t.methods if m.name == task.focal_method]
    if len(matches) == 1:
        return t, matches[0]
    raise KeyError(f"{task.focal_class}#{task.focal_sig or task.focal_method}")


def _read_fields(t: TypeInfo, m) -> list:
    names = set(m.body_reads_fields or [])
    return [f for f in t.fields if f.name in names][:MAX_SETUP_FIELDS]


def oracle_cue_text(intention: dict) -> str:
    """§2.2 authoritative rule: cues come from expected_results, else from the objective; negated cues are dropped."""
    intention = intention or {}
    text = (intention.get("expected_results") or "").strip() or (intention.get("objective") or "")
    return _ORACLE_NEGATION.sub(" ", text)


def compute_gaps(index: Index, task: Task) -> dict:
    """§2.4.1 semantic-gap flags: what S1 saw in the intention but cannot bind structurally. Never affects Σ."""
    C, m = find_focal(index, task)
    params = [p.name for p in m.params]
    fields = {f.name for f in C.fields}
    counts = {k: 0 for k in GAP_KINDS}
    relations: list[dict] = []
    for s in _sentences((task.intention or {}).get("preconditions", "")):
        mentioned = [p for p in params if _mentions(s, p)]
        if len(mentioned) >= 2:
            counts["relational"] += 1
            relations.append({"params": mentioned, "sentence": s})
        elif not mentioned:
            if any(_mentions(s, f) for f in fields) or _STATE_WORDS.search(s):
                counts["state"] += 1
            else:
                counts["unbound"] += 1
    types = [erase(p.type) for p in m.params if not is_literal(p.type)]
    counts["same_type_args"] = sum(1 for t in set(types) if types.count(t) >= 2)
    return {"counts": counts, "relations": relations, "total": sum(counts.values())}


def compute_demands(index: Index, task: Task) -> list[Need]:
    C, m = find_focal(index, task)
    needs: list[Need] = []

    def add(n: Need) -> None:
        if all(n.key() != existing.key() for existing in needs):  # de-dup by (kind, tau, detail), order kept
            needs.append(n)

    if not m.static and m.name != "<init>":  # ctors are recorded with name "<init>" (§4.2)
        add(Need("receiver", C.fqn))

    intention = task.intention or {}
    precond_sentences = _sentences(intention.get("preconditions", ""))
    for i, p in enumerate(m.params):
        constraint = next((s for s in precond_sentences if _mentions(s, p.name)), "")
        add(Need("arg", p.type, detail=str(i), constraint=constraint))

    read = _read_fields(C, m)
    for f in read:
        add(Need("setup", f.type, detail=f.name))

    expected = oracle_cue_text(intention)
    exc = _ORACLE_EXCEPTION.search(expected)
    state = _ORACLE_STATE.search(expected)
    inter = _ORACLE_INTERACTION.search(expected)
    # §2.2 cascade: exception row, otherwise return row, otherwise-or-state row, interaction row.
    if exc:
        taus = m.throws or ["java.lang.RuntimeException"]
        for tau in taus:
            add(Need("oracle", tau, detail="exception"))
    elif m.returns and m.returns != "void":
        add(Need("oracle", m.returns, detail="return"))
    if (not exc and (not m.returns or m.returns == "void")) or state:
        add(Need("oracle", C.fqn, detail="state"))
    if inter:
        for f in read:
            add(Need("oracle", f.type, detail="interaction"))

    proj = index.project
    add(Need("idiom", f"{proj.get('test_framework', '')}:{proj.get('assertion_lib', '')}:{proj.get('mocking_lib', '')}"))
    return needs

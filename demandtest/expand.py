"""S2 — recipes, sufficiency predicate, bounded expansion (SPEC §2.3–2.5). No LLM.

Material assumptions (documented deviations/refinements of the §2.5 sketch):
- The oracle expansion branch is refined per oracle kind: exception needs pull
  the exception type into ctx.types (an observables() probe on exception types
  is usually empty and would mark them unresolvable even though adding the
  type satisfies Σ); interaction needs are satisfiable only via mocking_lib.
- `progressed` requires an actual context change, so needs that cannot be
  repaired by expansion (e.g. interaction with mocking_lib=none) terminate the
  loop instead of spinning.

0.2.4 (after review): package-private constructors/factories are usable only from the focal
package (the generated test's package, §2.8.1); every unresolvable need carries an
`unresolved_reason` (§2.4.5); `expand_past=k` applies the deterministic continuation rule of
§2.5 after Σ holds, so packets Σ ⊆ Σ+2 ⊆ Σ+4 are nested by construction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import packet as packet_mod
from . import proximal
from .demand import Need, find_focal
from .index import Index, erase, is_jdk, is_literal, simple

_TYPE_VAR = re.compile(r"[A-Z][A-Z0-9]?")  # `T`, `K`, `V2`: erased generic parameters (§2.4.5 generic_erased)

DEFAULT_BUDGET_FILES = 12
DEFAULT_BUDGET_TOKENS = 2000
DEFAULT_D_MAX = 3

TIE_BREAK = {"literal": -1, "fixture": 0, "helper": 1, "ctor": 2, "factory": 3, "builder": 4, "singleton": 5,
             "mock": 6, "subtype_ctor": 7}  # §2.3 order; literal is terminal for literal-resolvable types


@dataclass
class Recipe:
    kind: str
    type: str
    cost: int
    entity: object = None
    sub: list = field(default_factory=list)
    arg_renders: list = field(default_factory=list)  # one render per parameter, aligned
    note: str = ""

    def entities(self) -> list:
        out: list = []
        seen: set[int] = set()

        def walk(r: "Recipe") -> None:
            if r.entity is not None and id(r.entity) not in seen:
                seen.add(id(r.entity))
                out.append(r.entity)
            for s in r.sub:
                walk(s)

        walk(self)
        return out

    def owner_simple(self) -> str:
        owner = getattr(self.entity, "owner", None)
        return simple(owner) if owner else "?"

    def render(self) -> str:
        base = simple(self.type)
        args = ", ".join(self.arg_renders)
        if self.kind == "literal":
            return f"<literal {base}>"
        if self.kind == "ctor":
            return f"new {base}({args})"
        if self.kind == "factory":
            return f"{self.owner_simple()}.{self.entity.name}({args})"
        if self.kind == "builder":
            return f"{self.owner_simple()}.{self.entity.name}()...build()"
        if self.kind == "singleton":
            return f"{self.owner_simple()}.{self.entity.name}"
        if self.kind == "mock":
            return f"mock({base}.class)"
        if self.kind == "subtype_ctor":
            return f"new {self.owner_simple()}({args})"
        if self.kind == "fixture":
            test, f = self.entity
            return f"{simple(test.class_)}.{f.name} (fixture)"
        if self.kind == "helper":
            return f"{simple(self.entity.owner)}.{self.entity.name}({args})"
        return base


@dataclass
class Context:
    types: set = field(default_factory=set)
    entities: list = field(default_factory=list)
    files: set = field(default_factory=set)
    recipes: dict = field(default_factory=dict)


@dataclass
class ExpansionResult:
    ctx: Context
    status: str  # "sufficient" | "fallback"
    unresolved: list
    trace: list
    referable_tests: list
    referable_diffs: dict = field(default_factory=dict)  # test id -> proximal.DemandDiff (§2.5.1)
    unresolved_reasons: dict = field(default_factory=dict)  # need key -> reason (§2.4.5)
    extended_by: int = 0  # entities added past Σ by the continuation rule (§2.5)


class Resolver:
    """resolve(tau, depth) -> cheapest Recipe (§2.3); memoized on (erase(tau), depth), cycle-guarded."""

    def __init__(self, index: Index, d_max: int = DEFAULT_D_MAX, test_package: Optional[str] = None):
        self.index = index
        self.d_max = d_max
        self.test_package = test_package  # package the generated test lives in (§2.8.1); None = no check
        self._memo: dict[tuple, Optional[Recipe]] = {}
        self._stack: set[str] = set()
        self.reasons: dict[str, str] = {}  # erased type -> §2.4.5 unresolved_reason
        self._inaccessible: set[str] = set()

    def _accessible(self, t: TypeInfo, visibility: str) -> bool:
        if visibility == "public":
            return True
        if visibility == "package" and (self.test_package is None or t.package == self.test_package):
            return True
        self._inaccessible.add(erase(t.fqn))
        return False

    def _classify(self, base: str) -> str:
        t = self.index.type(base)
        if t is None:
            if _TYPE_VAR.fullmatch(base):
                return "generic_erased"
            return "jdk_no_recipe" if is_jdk(base) else "offindex"
        return "inaccessible" if base in self._inaccessible else "no_path"

    def candidates(self, fqn: str, depth: int = 0) -> list[Recipe]:
        if depth >= self.d_max:
            return []
        base = erase(fqn)
        if is_literal(base):
            return [Recipe("literal", base, 0)]
        t = self.index.type(base)
        if t is None:
            return []  # includes JDK non-literal types not in the index: no static recipe
        out: list[Recipe] = []
        for test, f in self.index.fixtures_of_type(base):
            out.append(Recipe("fixture", base, 0, entity=(test, f)))
        for methods in self.index.test_helpers.values():
            for hm in methods:
                if erase(hm.returns) == base:
                    out.append(Recipe("helper", base, 0, entity=hm))
        for c in t.ctors:
            if c.visibility in ("public", "package") and self._accessible(t, c.visibility):
                r = self._with_params(base, "ctor", c, depth)
                if r is not None:
                    out.append(r)
        for f in t.factories:
            if f.visibility in ("public", "package") and not self._accessible(t, f.visibility):
                continue
            r = self._with_params(base, "factory", f, depth)
            if r is not None:
                out.append(r)
        for b in t.builders:
            out.append(Recipe("builder", base, 1, entity=b))
        for s in t.singletons:
            out.append(Recipe("singleton", base, 0, entity=s))
        if t.kind in ("interface", "abstract"):
            if self.index.project.get("mocking_lib", "none") != "none":
                out.append(Recipe("mock", base, 1))
            for st in self.index.subtypes(base):
                for c in st.ctors:
                    if c.visibility in ("public", "package") and self._accessible(st, c.visibility):
                        r = self._with_params(base, "subtype_ctor", c, depth,
                                              note=f"{simple(st.fqn)} <: {base}")
                        if r is not None:
                            r.entity = c
                            out.append(r)
        return out

    def _with_params(self, base: str, kind: str, method, depth: int, note: str = "") -> Optional[Recipe]:
        subs: list[Recipe] = []
        renders: list[str] = []
        cost = 0  # cost = non-Literal params + transitive sub costs (§2.3)
        for p in method.params:
            if is_literal(p.type):
                renders.append(f"<literal {simple(erase(p.type))}>")
                continue
            cost += 1
            sub = self.resolve(p.type, depth + 1)
            if sub is None:
                renders.append(f"<{simple(erase(p.type))}>")
            else:
                subs.append(sub)
                cost += sub.cost
                renders.append(sub.render())
        return Recipe(kind, base, cost, entity=method, sub=subs, arg_renders=renders, note=note)

    def resolve(self, fqn: str, depth: int = 0) -> Optional[Recipe]:
        base = erase(fqn)
        key = (base, depth)
        if key in self._memo:
            return self._memo[key]
        if base in self._stack:
            self.reasons.setdefault(base, "cycle")
            return None  # cycle guard: type currently being resolved resolves to ⊥
        if depth >= self.d_max:
            self.reasons.setdefault(base, "depth")
            return None
        self._stack.add(base)
        try:
            cands = self.candidates(base, depth)
        finally:
            self._stack.discard(base)
        if not cands:
            self.reasons[base] = self._classify(base)
            self._memo[key] = None
            return None
        self.reasons.pop(base, None)
        best = min(cands, key=lambda r: (r.cost, TIE_BREAK[r.kind], r.render()))
        self._memo[key] = best
        return best


def _is_observable_of(entity, tau: str) -> bool:
    owner = getattr(entity, "owner", None)
    if not owner:
        return False
    if erase(owner) != tau:
        return False
    from .index import Field
    if isinstance(entity, Field):
        return entity.visibility == "public"  # public final fields count as observables (§4.2)
    return True


def sufficient(index: Index, demands: list[Need], ctx: Context) -> tuple[bool, list[Need]]:
    """Σ(D, ctx) per §2.4, computed from the index only — MUST NOT call the LLM."""
    ent_ids = {id(e) for e in ctx.entities}
    unresolved: list[Need] = []
    for n in demands:
        ok = False
        if n.kind in ("receiver", "arg", "setup"):
            r = ctx.recipes.get(n.key())
            ok = r is not None and all(id(e) in ent_ids for e in r.entities())
        elif n.kind == "oracle":
            tau = erase(n.type)
            if n.detail == "return":
                ok = is_literal(tau) or is_jdk(tau) or any(_is_observable_of(e, tau) for e in ctx.entities)
            elif n.detail == "exception":
                ok = is_jdk(tau) or tau in ctx.types
            elif n.detail == "state":
                ok = any(_is_observable_of(e, tau) for e in ctx.entities)
            elif n.detail == "interaction":
                ok = index.project.get("mocking_lib", "none") != "none"
        elif n.kind == "idiom":
            ok = bool(index.project.get("test_framework"))
        if not ok:
            unresolved.append(n)
    return (not unresolved, unresolved)


def _sig_line(e) -> str:
    from .index import Field, Method, TestInfo
    if isinstance(e, tuple):  # fixture (TestInfo, Field)
        return ""
    if isinstance(e, Method):
        owner = simple(e.owner or "?")
        return f"{owner}#{e.signature()}"
    if isinstance(e, Field):
        return f"{e.type} {e.name};"
    if isinstance(e, TestInfo):
        return e.id
    return getattr(e, "name", "")


def _ctx_tokens(ctx: Context, est: Callable[[str], int]) -> int:
    lines = [f"{k} <- {r.render()}" for k, r in ctx.recipes.items()]
    lines += [line for line in (_sig_line(e) for e in ctx.entities) if line]
    return sum(est(line) for line in lines)


def expand(index: Index, demands: list[Need], task, budget_files: int = DEFAULT_BUDGET_FILES,
           budget_tokens: int = DEFAULT_BUDGET_TOKENS, d_max: int = DEFAULT_D_MAX,
           est_tokens: Optional[Callable[[str], int]] = None, expand_past: int = 0) -> ExpansionResult:
    est = est_tokens or packet_mod.est_tokens
    C, m = find_focal(index, task)
    ctx = Context(types={erase(C.fqn)}, files={C.file} if C.file else set())
    resolver = Resolver(index, d_max=d_max, test_package=C.package or None)
    unresolvable: dict[str, Need] = {}
    reasons: dict[str, str] = {}
    trace: list[str] = []
    ent_ids = {id(e) for e in ctx.entities}

    def absorb(objs) -> bool:
        """Add entities + their files; returns True when the context actually grew."""
        changed = False
        for e in objs:
            if id(e) not in ent_ids:
                ent_ids.add(id(e))
                ctx.entities.append(e)
                changed = True
            for part in (e if isinstance(e, tuple) else (e,)):
                f = getattr(part, "file", None)
                if f and f not in ctx.files:
                    ctx.files.add(f)
                    changed = True
        return changed

    def add_type(tau: str) -> bool:
        base = erase(tau)
        if base not in ctx.types:
            ctx.types.add(base)
            return True
        return False

    while True:
        ok, unresolved = sufficient(index, demands, ctx)
        if ok:
            break
        if len(ctx.files) >= budget_files or _ctx_tokens(ctx, est) >= budget_tokens:
            break
        changed = False
        for n in unresolved:
            if n.key() in unresolvable:
                continue
            if n.kind in ("receiver", "arg", "setup"):
                r = resolver.resolve(n.type, 0)  # resolved over the WHOLE index
                if r is None:
                    unresolvable[n.key()] = n
                    reasons[n.key()] = resolver.reasons.get(erase(n.type), "no_path")
                    continue
                ctx.recipes[n.key()] = r
                step_files = set()
                if absorb(r.entities()):
                    changed = True
                    step_files |= {f for e in r.entities() for part in (e if isinstance(e, tuple) else (e,))
                                   if (f := getattr(part, "file", None))}
                if add_type(n.type):
                    changed = True
                for e in r.entities():
                    o = getattr(e, "owner", None)
                    if o and add_type(o):
                        changed = True
                trace.append(f"{n.key()} <- {r.kind}:{r.render()} (+files={sorted(step_files)})")
            elif n.kind == "oracle":
                tau = erase(n.type)
                if n.detail == "interaction":
                    if index.project.get("mocking_lib", "none") == "none":
                        unresolvable[n.key()] = n
                        reasons[n.key()] = "no_mocking_lib"
                    continue
                if n.detail == "exception":
                    t = index.type(tau)
                    if t is None:
                        unresolvable[n.key()] = n  # non-JDK exception type not in the index
                        reasons[n.key()] = "offindex"
                    else:
                        add_type(tau)
                        if t.file and t.file not in ctx.files:
                            ctx.files.add(t.file)
                        changed = True
                        trace.append(f"{n.key()} <- type:{tau} (+files={[t.file] if t.file else []})")
                else:  # return / state: pull in observables (§2.5 obs branch)
                    obs = index.observables(tau)[:6]
                    if not obs:
                        unresolvable[n.key()] = n
                        reasons[n.key()] = "no_observable"
                    else:
                        new_files = [o.file for o in obs if o.file and o.file not in ctx.files]
                        if absorb(obs):
                            changed = True
                        add_type(tau)
                        trace.append(f"{n.key()} <- observables:{[o.name for o in obs]} (+files={sorted(set(new_files))})")
            elif n.kind == "idiom":
                if not index.project.get("test_framework"):
                    unresolvable[n.key()] = n
                    reasons[n.key()] = "no_framework"
            if len(ctx.files) >= budget_files:
                break
        if not changed:
            break

    ok, unresolved_final = sufficient(index, demands, ctx)
    status = "sufficient" if ok and not unresolvable else "fallback"
    referable: list = []
    diffs: dict = {}
    extended = 0
    if status == "fallback":  # demand-proximal backstop (§2.5.1): ranked by overlap with D, not by "calls m"
        ranked = proximal.rank(index, task, demands, k=2)
        referable = [d.test for d in ranked]
        diffs = {d.test.id: d for d in ranked}
        for t in referable:
            if t.file:
                ctx.files.add(t.file)
    elif expand_past > 0:  # §2.5 continuation rule: deterministic, prefix-closed, so Σ+2 ⊆ Σ+4
        extended = _continue_past(index, demands, task, ctx, resolver, expand_past, absorb, add_type,
                                  trace, referable, diffs)
    return ExpansionResult(ctx=ctx, status=status, unresolved=unresolved_final, trace=trace,
                           referable_tests=referable, referable_diffs=diffs,
                           unresolved_reasons=reasons, extended_by=extended)


def _continue_past(index: Index, demands, task, ctx: Context, resolver: Resolver, k: int,
                   absorb, add_type, trace: list, referable: list, diffs: dict) -> int:
    """Continuation past Σ (§2.5): (1) the cheapest unused alternative recipe per construction need, in D
    order; (2) observables beyond the first six for return/state oracle needs; (3) demand-proximal tests.
    Adds whole entities until at least k were added; the sequence is fixed, so packets are nested."""
    added = 0
    ent_ids = {id(e) for e in ctx.entities}
    for n in demands:
        if added >= k:
            break
        if n.kind not in ("receiver", "arg", "setup") or is_literal(n.type):
            continue
        chosen = ctx.recipes.get(n.key())
        cands = sorted(resolver.candidates(erase(n.type), 0), key=lambda r: (r.cost, TIE_BREAK[r.kind], r.render()))
        for r in cands:
            if chosen is not None and r.render() == chosen.render():
                continue
            new = [e for e in r.entities() if id(e) not in ent_ids]
            if not new:
                continue
            absorb(r.entities())
            ent_ids.update(id(e) for e in r.entities())
            for e in r.entities():
                o = getattr(e, "owner", None)
                if o:
                    add_type(o)
            trace.append(f"past-Σ {n.key()} <- alt {r.kind}:{r.render()}")
            added += len(new)
            break
    for n in demands:
        if added >= k:
            break
        if n.kind != "oracle" or n.detail not in ("return", "state"):
            continue
        for o in index.observables(erase(n.type))[6:]:
            if added >= k:
                break
            if id(o) not in ent_ids and absorb([o]):
                ent_ids.add(id(o))
                trace.append(f"past-Σ {n.key()} <- observable {o.name}")
                added += 1
    if added < k:
        for d in proximal.rank(index, task, demands, k=k - added):
            if all(t.id != d.test.id for t in referable):
                referable.append(d.test)
                diffs[d.test.id] = d
                if d.test.file:
                    ctx.files.add(d.test.file)
                trace.append(f"past-Σ related <- {d.test.id}")
                added += 1
    return added

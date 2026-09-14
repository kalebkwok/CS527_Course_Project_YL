"""Knowledge packet rendering (SPEC §2.6) and token estimation.

Spec conflict resolved in favor of the §12 acceptance test: PROJECT FACTS
(section 5) is never dropped and ASSERTION STYLE (section 6) is droppable,
while §2.6's prose says "sections 1–3 and 6 are never dropped". Sections 1–3
plus 5 are protected; droppable sections truncate from the tail in order 7, 6, 4.
Flagged to the owner as a spec inconsistency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .demand import Task  # noqa: F401  (re-exported type for callers)
from .index import Index, erase, simple

_ENCODER = object  # sentinel: not yet attempted

SECTION_KEYS = [
    "1. INTENTION",
    "2. FOCAL",
    "3. HOW TO OBTAIN VALUES (from this project)",
    "4. OBSERVABLE FOR ASSERTIONS",
    "5. PROJECT FACTS",
    "6. ASSERTION STYLE IN THIS PROJECT (example, do not copy blindly)",
    "7. RELATED EXISTING TEST",
]
NEVER_DROP = {"1. INTENTION", "2. FOCAL", "3. HOW TO OBTAIN VALUES (from this project)", "5. PROJECT FACTS"}


@dataclass
class Packet:
    text: str
    est_tokens: int
    sections: dict = field(default_factory=dict)


def est_tokens(text: str) -> int:
    """tiktoken cl100k_base if available, else len(text)/3.6 (§2.6). Budget check only."""
    global _ENCODER
    if _ENCODER is object:
        try:
            import tiktoken
            _ENCODER = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENCODER = False
    if _ENCODER is False:
        return max(1, round(len(text) / 3.6))
    return len(_ENCODER.encode(text))


def extract_method_source(src: str, method_name: str) -> str:
    """Best-effort source slice of one method/ctor via brace matching."""
    if not src or not method_name:
        return ""
    for m in re.finditer(rf"(?m)^[\t ]*(?:@\w+[\t ]*\n[\t ]*)?(?:@\w+[\t ]+)*[\w<>\[\], ]*\b{re.escape(method_name)}\s*\(", src):
        tail = src[m.start():]
        brace = tail.find("{")
        header = tail[:brace] if brace >= 0 else tail
        if "->" in header or "=" in header.split("(")[0]:
            continue  # lambda/method reference, not a declaration
        if brace < 0:
            continue
        depth, i = 0, brace
        while i < len(tail):
            if tail[i] == "{":
                depth += 1
            elif tail[i] == "}":
                depth -= 1
                if depth == 0:
                    return tail[: i + 1].rstrip()
            i += 1
    return ""


def _focal_section(task: Task, focal_source: Optional[str], index: Index) -> str:
    lines = [f"// {task.focal_file}"]
    decl = None
    if focal_source:
        imports = [ln for ln in focal_source.splitlines() if ln.strip().startswith("import ")][:20]
        lines += [ln for ln in imports]
        for ln in focal_source.splitlines():
            if re.search(r"\b(class|interface|enum)\s+\w+", ln) and "=" not in ln:
                decl = ln.strip()
                break
        method_src = extract_method_source(focal_source, task.focal_method)
        if decl:
            lines.append(decl)
        if method_src:
            lines.append(method_src)
        else:
            lines.append(f"// source of {task.focal_method} not extracted; see {task.focal_file}")
    else:
        lines.append(f"// focal source not available on disk; focal method: {task.focal_method}")
        try:
            from .demand import find_focal
            C, m = find_focal(index, task)
            lines.append(f"// signature: {C.fqn}#{m.signature()}")
        except KeyError:
            pass
    return "\n".join(lines)


def _idiom_line(proj: dict) -> str:
    fw, al, ml = proj.get("test_framework", "?"), proj.get("assertion_lib", "?"), proj.get("mocking_lib", "none")
    return f"JUnit framework: {fw}; assertions: {al}; mocking: {ml}"


def _recipe_section(index: Index, task: Task, demands, expansion) -> str:
    lines: list[str] = []
    unresolved_keys = {n.key() for n in expansion.unresolved}
    for n in demands:
        role = _role(n)
        tau = simple(erase(n.type))
        r = expansion.ctx.recipes.get(n.key())
        if n.kind == "idiom":
            proj = index.project
            lines.append(f"- idiom: {_idiom_line(proj)}")
            continue
        if n.kind == "oracle":
            lines.append(f"- {role} ({tau}): {_oracle_hint(n.detail)}")
            continue
        if r is None:
            if n.key() in unresolved_keys:
                lines.append(f"- {role} ({tau}): UNRESOLVED — see related tests below")
            continue
        if r.kind == "fixture":
            test, f = r.entity
            lines.append(f"- {role} ({tau}): // see {simple(test.class_)}: field {f.name} of type {f.type}")
            if f.initializer:
                lines.append(f"  {f.type} {f.name} = {f.initializer};")
        else:
            suffix = f"  // {n.constraint}" if n.constraint else ""
            lines.append(f"- {role} ({tau}): {r.render()}{suffix}")
            for e in r.entities():
                from .index import Field, Method
                if isinstance(e, tuple) or isinstance(e, Field):
                    continue
                owner = simple(getattr(e, "owner", "?") or "?")
                lines.append(f"  {owner}#{e.signature()}  // {owner}")
    return "\n".join(lines)


def _oracle_hint(detail: str) -> str:
    return {
        "exception": "assert that the documented exception is thrown",
        "return": "assert the returned value",
        "state": "assert the resulting observable state",
        "interaction": "verify the interaction through the mocking library",
    }.get(detail, "assert the documented behaviour")


def _role(n) -> str:
    if n.kind == "arg":
        return f"arg{n.detail}"
    if n.kind == "setup":
        return f"setup {n.detail}"
    if n.kind == "oracle":
        return f"oracle/{n.detail}"
    return n.kind


def _observable_section(index: Index, demands, expansion) -> str:
    from .index import Field, Method
    seen: set[str] = set()
    lines: list[str] = []
    for n in demands:
        if n.kind != "oracle" or n.detail not in ("return", "state"):
            continue
        for e in expansion.ctx.entities:
            if not isinstance(e, Method) or not _obs_of(e, erase(n.type)):
                continue
            owner = simple(e.owner or "?")
            line = f"{owner}#{e.signature()}  // {owner}"
            if line not in seen:
                seen.add(line)
                lines.append(f"  {line}")
            if len(lines) >= 8:
                break
        if len(lines) >= 8:
            break
    return "\n".join(lines)


def _obs_of(m: Method, tau: str) -> bool:
    from .index import Field
    if isinstance(m, Field):
        return False
    return erase(m.owner or "") == tau


def _assertion_style(index: Index, task: Task) -> str:
    try:
        from .demand import find_focal
        C, m = find_focal(index, task)
        sig = f"{C.fqn}#{m.erased_sig()}"
        picks = index.tests_calling(sig)[:1] or index.tests_of_class(C.fqn)[:1]
    except KeyError:
        picks = []
    # §9 leakage control: the excluded reference test must not become the idiom example.
    visible = [t for t in index.tests if t.id not in index.excluded]
    pick = picks[0] if picks else (visible[0] if visible else None)
    if pick is None or not pick.source:
        return ""
    body = "\n".join(pick.source.splitlines()[:25])
    return f"{pick.id} ({pick.file})\n{body}"


def _related_section(referable_tests) -> str:
    blocks = []
    for t in referable_tests:
        body = "\n".join(t.source.splitlines()[:40])
        blocks.append(f"--- {t.id} ({t.file}) ---\n{body}")
    return "\n\n".join(blocks)


def render(index: Index, task: Task, demands, expansion, focal_source: Optional[str] = None,
           budget_tokens: int = 2000) -> Packet:
    proj = index.project
    try:
        pkg = simple_pkg(task.focal_class)
    except Exception:
        pkg = ""
    sections = {
        SECTION_KEYS[0]: (
            f"OBJECTIVE: {(task.intention or {}).get('objective', '')}\n"
            f"PRECONDITIONS: {(task.intention or {}).get('preconditions', '')}\n"
            f"EXPECTED RESULTS: {(task.intention or {}).get('expected_results', '')}"
        ),
        SECTION_KEYS[1]: _focal_section(task, focal_source, index),
        SECTION_KEYS[2]: _recipe_section(index, task, demands, expansion),
        SECTION_KEYS[3]: _observable_section(index, demands, expansion),
        SECTION_KEYS[4]: (
            f"test framework: {proj.get('test_framework', '?')}\n"
            f"assertion lib: {proj.get('assertion_lib', '?')}\n"
            f"mocking lib: {proj.get('mocking_lib', 'none')}\n"
            f"package of focal class: {pkg}\n"
            f"test root: {', '.join(proj.get('test_roots', [])) or 'src/test/java'}"
        ),
        SECTION_KEYS[5]: _assertion_style(index, task),
    }
    if expansion.status == "fallback":
        sections[SECTION_KEYS[6]] = _related_section(expansion.referable_tests)

    kept = dict(sections)
    droppable = [k for k in SECTION_KEYS if k not in NEVER_DROP][::-1]  # drop from the tail: 7, 6, 4

    def joined(parts: dict) -> str:
        return "\n\n".join(f"{k}\n{parts[k]}" for k in SECTION_KEYS if k in parts and parts[k].strip())

    for key in droppable:
        if est_tokens(joined(kept)) <= budget_tokens:
            break
        if key in kept:
            del kept[key]
    text = joined(kept)
    return Packet(text=text, est_tokens=est_tokens(text), sections={k: v for k, v in kept.items()})


def simple_pkg(fqn: str) -> str:
    return fqn.rsplit(".", 1)[0] if "." in fqn else ""

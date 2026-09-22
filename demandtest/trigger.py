"""Oracle trigger hints (SPEC §2.6 section 3). Syntactic, no LLM, no symbolic execution.

TestTailor (Zhou et al., FSE 2026) tells the model not only *what* remains uncovered but
*how* to reach it, using path constraints from symbolic execution. DemandTest's target is an
oracle, not a path, so the analog is cheap: collect the guards (if / else-if / else / loop
headers / catch) that enclose each `throw` and `return` of the focal body. Brace and paren
matching in the spirit of §2.8's regex first cut; the hint is advisory and never affects Σ.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .index import simple

HEADER = re.compile(r"(else\s+if|else|if|for|while|do|switch|try|catch|finally|synchronized|throw|return)\b")
CASE_LABEL = re.compile(r"(?:case\b[^:>{;]*?|default)\s*(?::|->)")
MAX_HINTS = 3


@dataclass
class Trigger:
    kind: str          # "throw" | "return"
    text: str          # statement without the keyword and the trailing ';'
    guards: list = field(default_factory=list)  # enclosing conditions, outermost first

    def condition(self) -> str:
        return " && ".join(self.guards)


@dataclass
class _Frame:
    guard: str | None
    chain: str | None = None   # disjunction of the if / else-if conditions so far, for a following `else`
    single: bool = False       # header without `{`: guards exactly one statement


def clean(src: str) -> str:
    """Remove comments; blank string/char literal contents (quotes kept)."""
    out: list[str] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if src.startswith('"""', i):
            j = src.find('"""', i + 3)
            out.append('""')
            i = n if j < 0 else j + 3
            continue
        if ch in ('"', "'"):
            j = i + 1
            while j < n and src[j] != ch:
                j += 2 if src[j] == "\\" else 1
            out.append(ch + ch)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def body(method_src: str) -> str:
    """Text between the method's first `{` and its matching `}`, cleaned."""
    s = clean(method_src)
    start = s.find("{")
    if start < 0:
        return ""
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:i]
    return s[start + 1:]


def _ws(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


def _paren(s: str, i: int) -> tuple[str, int]:
    """Balanced (...) at the first '(' at or after i -> (inner text, index after ')')."""
    j = s.find("(", i)
    if j < 0:
        return "", i
    depth = 0
    for k in range(j, len(s)):
        if s[k] == "(":
            depth += 1
        elif s[k] == ")":
            depth -= 1
            if depth == 0:
                return _ws(s[j + 1:k]), k + 1
    return _ws(s[j + 1:]), len(s)


def _statement(s: str, i: int) -> tuple[str, int]:
    """Text up to the terminating ';' at depth 0 (nested braces are opaque) -> (text, index after)."""
    paren = brace = 0
    k = i
    while k < len(s):
        ch = s[k]
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        elif ch == "{":
            brace += 1
        elif ch == "}":
            if brace == 0:
                return _ws(s[i:k]), k   # last statement of a block, no ';'
            brace -= 1
        elif ch == ";" and paren == 0 and brace == 0:
            return _ws(s[i:k]), k + 1
        k += 1
    return _ws(s[i:]), len(s)


def triggers(method_src: str) -> list[Trigger]:
    """Every `throw` and `return` of the body with the conjunction of guards that encloses it."""
    s = body(method_src)
    stack: list[_Frame] = []
    pending: _Frame | None = None
    last_chain: str | None = None
    out: list[Trigger] = []
    i, n = 0, len(s)

    def guards() -> list[str]:
        g = [f.guard for f in stack if f.guard]
        if pending is not None and pending.guard:
            g.append(pending.guard)
        return g

    def next_is_else(pos: int) -> bool:
        return re.match(r"\s*else\b", s[pos:]) is not None

    def end_statement(pos: int) -> None:
        """A single statement ended: close the pending header; enclosing single headers too, unless an
        `else` follows (the if-statement is not over). A dangling else binds to the innermost if."""
        nonlocal pending, last_chain
        if pending is not None:
            last_chain = pending.chain
            pending = None
        if next_is_else(pos):
            return
        while stack and stack[-1].single:
            stack.pop()

    def open_header(frame: _Frame) -> None:
        nonlocal pending
        if pending is not None:  # `if (a) if (b) ...`: the outer header guards the inner one
            pending.single = True
            stack.append(pending)
        pending = frame

    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch == ";":
            i += 1
            end_statement(i)
            continue
        if ch == "}":
            i += 1
            if stack:
                last_chain = stack.pop().chain
            if not next_is_else(i):
                while stack and stack[-1].single:
                    stack.pop()
            continue
        if ch == "{":
            i += 1
            frame = pending if pending is not None else _Frame(None)
            frame.single = False
            stack.append(frame)
            pending = None
            continue
        lab = CASE_LABEL.match(s, i)
        if lab:
            i = lab.end()
            continue
        m = HEADER.match(s, i)
        if m:
            kw = _ws(m.group(1))
            j = m.end()
            if kw == "if":
                cond, j = _paren(s, j)
                open_header(_Frame(cond, chain=cond))
            elif kw == "else if":
                cond, j = _paren(s, j)
                prev = last_chain
                guard = f"!({prev}) && {cond}" if prev else cond
                open_header(_Frame(guard, chain=f"{prev} || {cond}" if prev else cond))
            elif kw == "else":
                prev = last_chain
                open_header(_Frame(f"!({prev})" if prev else "else-branch"))
            elif kw in ("for", "while", "switch", "catch", "synchronized"):
                header, j = _paren(s, j)
                label = {"for": "inside for", "while": "inside while", "switch": "switch",
                         "catch": "caught", "synchronized": None}[kw]
                open_header(_Frame(f"{label} ({header})" if label else None))
            elif kw == "do":
                open_header(_Frame("inside do-while"))
            elif kw in ("try", "finally"):
                open_header(_Frame(None))
            else:  # throw | return
                text, j = _statement(s, j)
                out.append(Trigger(kw, text, guards()))
                end_statement(j)
            i = j
            continue
        _, i = _statement(s, i)
        end_statement(i)
    return out


def oracle_triggers(method_src: str, kind: str, tau: str = "") -> list[str]:
    """Rendered hints for one oracle need (≤ MAX_HINTS): exception -> `triggered when: …`; return -> `returns: …`."""
    if not method_src:
        return []
    ts = triggers(method_src)
    if kind == "exception":
        throws = [t for t in ts if t.kind == "throw"]
        want = simple(tau) if tau else ""
        typed = [t for t in throws if want and re.search(rf"\b{re.escape(want)}\b", t.text)]
        chosen = typed or throws
        return [f"triggered when: {t.condition() or 'unconditionally'}" for t in chosen[:MAX_HINTS]]
    if kind == "return":
        out = []
        for t in [t for t in ts if t.kind == "return" and t.text][:MAX_HINTS]:
            cond = t.condition()
            out.append(f"returns: {t.text}" + (f" when {cond}" if cond else ""))
        return out
    return []

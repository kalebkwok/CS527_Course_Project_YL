"""Gate 1 replay (docs/PAPER_PLAN.md RQ1): evaluate Σ after every step of a recorded agent trajectory. No LLM.

Σ at step n is computed over the files the agent has printed up to and including step n: the index is
restricted to members (constructors, factories, observables, fixtures, helpers, ...) whose declaring
file was read, recipes are resolved over that restricted index only, and §2.4 `sufficient` decides. The resolver applies the same accessibility rule as S2 (§2.4.5), and the
held-out reference test stays hidden (§9). Σ is monotone in the set of files read, so once it holds it
keeps holding.

Material assumptions:
- A read is a file printed by cat/nl/head/tail/less/more/tac/bat, sed or awk with a file operand, or
  stdin redirection. Partial reads (`sed -n '1,40p'`, `head`) count as whole-file reads, which favors
  the agent. Grep hits (explicit file operands, or paths parsed from the output) are kept apart and
  enter Σ only with `with_grep_hits` (sensitivity analysis).
- Shell commands are parsed statically; a command that does not tokenize (unbalanced quotes) adds no
  file and is counted in `Step.unparsed`. Globs are expanded against the index's file set.
- Tokens come from provider usage only (§6); a call without usage makes token totals None, never an
  estimate.

Trajectory format: mini-swe-agent 2.x `.traj.json`, checked against 2.4.6. An assistant message is one
LLM call: `extra.actions` is a list of {"command"} and `extra.response.usage` holds the usage. A billed
call that failed to parse is a user message with `extra.interrupt_type == "FormatError"` and
`extra.response`. Each action is followed by one observation message (role "tool" or "user") whose
`extra.raw_output` is the command output; the submitting action has none (the exit message replaces it).
"""
from __future__ import annotations

import fnmatch
import json
import posixpath
import re
import shlex
import statistics
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Optional

from .demand import Need, Task, compute_demands, find_focal
from .expand import Context, Resolver, sufficient
from .index import Index, TypeInfo, erase

# program -> options that take a separate value
_READERS = {
    "cat": set(), "tac": set(), "less": set(), "more": set(),
    "nl": {"-b", "-d", "-f", "-h", "-i", "-l", "-n", "-s", "-v", "-w"},
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "--lines", "--bytes"},
    "bat": {"-r", "-l", "--line-range", "--language"},
    "batcat": {"-r", "-l", "--line-range", "--language"},
}
# program -> (options that take a value, options whose value is the script)
_SCRIPTED = {
    "sed": ({"-e", "-f", "-l", "--expression", "--file"}, {"-e", "-f", "--expression", "--file"}),
    "awk": ({"-F", "-v", "-f"}, {"-f"}),
    "gawk": ({"-F", "-v", "-f"}, {"-f"}),
}
_GREP_VALUE_OPTS = {"-e", "-f", "-m", "-A", "-B", "-C", "--regexp", "--file", "--max-count"}
_GREPS = {
    "grep": _GREP_VALUE_OPTS, "egrep": _GREP_VALUE_OPTS, "fgrep": _GREP_VALUE_OPTS,
    "rg": _GREP_VALUE_OPTS | {"-g", "-t", "-T", "--glob", "--type", "--type-not"},
}
_PREFIX_WORDS = {"sudo", "command", "time", "nice", "env", "exec"}
_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")", "|&"}
_REDIRECT_OUT = {">", ">>", ">|", "&>", "&>>"}
_HEREDOC_MARK = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_FD_REDIRECT = re.compile(r"(?<![\w>])\d*>&\d+|(?<![\w&])\d+(?=>)")
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_PATH_LIKE = re.compile(r"[\w./\-]+\.[A-Za-z0-9]+")


@dataclass
class FileReads:
    reads: list = field(default_factory=list)
    grep_hits: list = field(default_factory=list)
    parsed: bool = True


@dataclass
class Step:
    n: int  # 1-based LLM call index
    prompt_tokens: int  # provider usage; -1 when the trajectory does not carry it (§6)
    completion_tokens: int
    commands: list = field(default_factory=list)
    reads: list = field(default_factory=list)  # repo-relative, in first-seen order
    grep_hits: list = field(default_factory=list)
    unparsed: int = 0


@dataclass
class Replay:
    task: str  # ref_test_id
    n_steps: int
    n_demands: int
    sigma_step: Optional[int]  # first call after whose observations Σ holds; 0 = before the first call
    steps_after_sigma: Optional[int]
    files_at_sigma: Optional[int]
    files_total: int
    tokens_total: Optional[int]
    tokens_after_sigma: Optional[int]
    share_after_sigma: Optional[float]
    open_at_end: list  # need keys Σ still misses after the last step
    unparsed_commands: int
    trace: list  # per step: {n, new_files, sigma, open}


# ------------------------------------------------------------------ shell parsing


def _strip_heredocs(command: str) -> str:
    """Drop heredoc bodies; the `<<DELIM` marker stays so the parser knows stdin is inline."""
    out: list[str] = []
    pending: list[str] = []
    for line in command.split("\n"):
        if pending:
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        out.append(line)
        pending.extend(m.group(2) for m in _HEREDOC_MARK.finditer(line))
    return "\n".join(out)


def _tokens(command: str) -> Optional[list[str]]:
    text = _FD_REDIRECT.sub(" ", _strip_heredocs(command)).replace("\n", " ; ")
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        return list(lex)
    except ValueError:  # unbalanced quotes
        return None


def _segments(tokens: list[str]) -> list[tuple[list[str], list[str]]]:
    """Split into simple commands: (words, stdin files from `<`). Redirect targets are dropped."""
    segs: list[tuple[list[str], list[str]]] = []
    words: list[str] = []
    stdin: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _SEPARATORS:
            if words or stdin:
                segs.append((words, stdin))
            words, stdin = [], []
        elif tok in _REDIRECT_OUT or tok in ("<<", "<<-", "<<<"):
            i += 1  # skip the write target / heredoc delimiter / here-string
        elif tok == "<":
            if i + 1 < len(tokens):
                stdin.append(tokens[i + 1])
            i += 1
        else:
            words.append(tok)
        i += 1
    if words or stdin:
        segs.append((words, stdin))
    return segs


def _split_args(args: list[str], takes_value: set[str]) -> tuple[list[str], list[str]]:
    """(positionals, options); values of options in `takes_value` are attached to the option."""
    pos: list[str] = []
    opts: list[str] = []
    i, end_of_opts = 0, False
    while i < len(args):
        a = args[i]
        if not end_of_opts and a == "--":
            end_of_opts = True
        elif not end_of_opts and a.startswith("-") and a != "-":
            opts.append(a)
            if a in takes_value:
                i += 1
        elif not end_of_opts and a.startswith("+"):  # tail +N
            pass
        else:
            pos.append(a)
        i += 1
    return pos, opts


def _has_flag(opts: list[str], short: str, longs: tuple[str, ...] = ()) -> bool:
    for o in opts:
        if o.startswith("--"):
            if o.split("=", 1)[0] in longs:
                return True
        elif re.fullmatch(r"-[A-Za-z]+", o) and short in o[1:]:
            return True
    return False


def _rel_paths(path: str, cwd: str, root: str, known: Optional[set]) -> list[str]:
    """Repository-relative path(s) for an operand; [] when outside the repo or not resolvable."""
    if not path or path.startswith("~") or "$" in path or path == "-":
        return []
    p = posixpath.normpath(path if path.startswith("/") else posixpath.join(cwd, path))
    if root:
        r = posixpath.normpath(root)
        if not p.startswith(r + "/"):
            return []
        p = p[len(r) + 1:]
    elif p.startswith("/") or p.startswith(".."):
        return []
    if p in (".", ""):
        return []
    if any(ch in p for ch in "*?["):
        return sorted(fnmatch.filter(known, p)) if known else []
    return [p]


def _grep_output_paths(output: str, files_mode: bool) -> list[str]:
    out: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line == "--":
            continue
        cand = line.split(":", 1)[0] if (not files_mode or ":" in line) else line
        if _PATH_LIKE.fullmatch(cand) and not cand.startswith("-"):
            out.append(cand)
    return out


def shell_reads(command: str, output: str = "", root: str = "", known: Optional[set] = None) -> FileReads:
    """Files a bash command prints (reads) or greps into (grep_hits), relative to `root`."""
    tokens = _tokens(command)
    if tokens is None:
        return FileReads(parsed=False)
    reads: list[str] = []
    hits: list[str] = []
    cwd = posixpath.normpath(root) if root else ""

    def add(target: list[str], operand: str, at: str) -> None:
        for p in _rel_paths(operand, at, root, known):
            if p not in target:
                target.append(p)

    for words, stdin in _segments(tokens):
        for s in stdin:
            add(reads, s, cwd)
        while words and (words[0] in _PREFIX_WORDS or _ASSIGNMENT.fullmatch(words[0])):
            words = words[1:]
        if words and words[0] == "xargs":  # operands come from stdin; only grep output is attributable
            rest, _ = _split_args(words[1:], {"-n", "-I", "-L", "-P", "-d", "-E", "-s"})
            words = rest
        if not words:
            continue
        prog = posixpath.basename(words[0])
        args = words[1:]
        if prog == "cd":
            pos, _ = _split_args(args, set())
            target = pos[0] if pos else ""
            if target and not target.startswith("~") and "$" not in target:
                cwd = posixpath.normpath(target if target.startswith("/") else posixpath.join(cwd, target))
            else:
                cwd = posixpath.normpath(root) if root else ""
        elif prog in _READERS:
            pos, _ = _split_args(args, _READERS[prog])
            for a in pos:
                add(reads, a, cwd)
        elif prog in _SCRIPTED:
            takes_value, script_opts = _SCRIPTED[prog]
            pos, opts = _split_args(args, takes_value)
            if prog == "sed" and (_has_flag(opts, "i", ("--in-place",)) or any(o.startswith("-i") for o in opts)):
                continue  # in-place edit, not a read
            if not any(o.split("=", 1)[0] in script_opts for o in opts):
                pos = pos[1:]  # first positional is the script
            for a in pos:
                add(reads, a, cwd)
        elif prog in _GREPS:
            pos, opts = _split_args(args, _GREPS[prog])
            if not any(o in ("-e", "-f", "--regexp", "--file") or o.startswith(("--regexp=", "--file="))
                       for o in opts):
                pos = pos[1:]  # first positional is the pattern
            explicit = [a for a in pos if posixpath.splitext(a)[1] and not any(ch in a for ch in "*?[")]
            for a in explicit:
                add(hits, a, cwd)
            if len(explicit) != len(pos) or not pos or len(pos) > 1 or prog == "rg":
                files_mode = _has_flag(opts, "l", ("--files-with-matches",))
                for p in _grep_output_paths(output, files_mode):
                    add(hits, p, cwd)
    return FileReads(reads=reads, grep_hits=hits)


# ------------------------------------------------------------------ trajectories


def load_trajectory(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _usage(extra: dict) -> tuple[int, int]:
    resp = extra.get("response")
    usage = resp.get("usage") if isinstance(resp, dict) else None
    if not isinstance(usage, dict):
        return -1, -1
    pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
    return (int(pt) if pt is not None else -1, int(ct) if ct is not None else -1)


@dataclass
class Call:
    extra: dict
    commands: list
    outputs: list
    prev_ts: Optional[float]  # timestamp of the last timestamped message before this call


def _ts(extra: dict) -> Optional[float]:
    ts = extra.get("timestamp")
    return float(ts) if isinstance(ts, (int, float)) else None


def billed_calls(traj: dict) -> list[Call]:
    """One Call per billed LLM call, in order (format: module docstring)."""
    msgs = traj.get("messages") or []
    calls: list[Call] = []
    last_ts: Optional[float] = None
    i = 0
    while i < len(msgs):
        m = msgs[i]
        extra = m.get("extra") or {}
        is_assistant = m.get("role") == "assistant"
        if not is_assistant and not (extra.get("interrupt_type") == "FormatError" and "response" in extra):
            last_ts = _ts(extra) or last_ts
            i += 1
            continue
        call = Call(extra, [], [], last_ts)
        last_ts = _ts(extra) or last_ts
        if is_assistant:
            call.commands = [str(a.get("command", "")) for a in (extra.get("actions") or []) if isinstance(a, dict)]
        j = i + 1
        while j < len(msgs) and len(call.outputs) < len(call.commands):
            obs_extra = msgs[j].get("extra") or {}
            if msgs[j].get("role") not in ("tool", "user") or "raw_output" not in obs_extra:
                break
            call.outputs.append(str(obs_extra.get("raw_output") or ""))
            last_ts = _ts(obs_extra) or last_ts
            j += 1
        call.outputs += [""] * (len(call.commands) - len(call.outputs))
        calls.append(call)
        i = j
    return calls


def trajectory_root(traj: dict) -> str:
    env = (((traj.get("info") or {}).get("config") or {}).get("environment") or {})
    return env.get("cwd") or ""


def steps_from_mswea(traj: dict, root: Optional[str] = None, known: Optional[set] = None) -> list[Step]:
    """One Step per billed LLM call, with the files its actions read."""
    root = trajectory_root(traj) if root is None else root
    steps: list[Step] = []
    for call in billed_calls(traj):
        pt, ct = _usage(call.extra)
        step = Step(n=len(steps) + 1, prompt_tokens=pt, completion_tokens=ct, commands=call.commands)
        for cmd, out in zip(call.commands, call.outputs):
            fr = shell_reads(cmd, out, root, known)
            step.unparsed += 0 if fr.parsed else 1
            step.reads += [p for p in fr.reads if p not in step.reads]
            step.grep_hits += [p for p in fr.grep_hits if p not in step.grep_hits]
        steps.append(step)
    return steps


# ------------------------------------------------------------------ Σ over read files


def index_files(index: Index) -> set[str]:
    out = {t.file for t in index.types.values() if t.file}
    out |= {t.file for t in index.tests if t.file}
    out |= {m.file for ms in index.test_helpers.values() for m in ms if m.file}
    return out


def restricted(index: Index, files: set[str]) -> Index:
    """The part of the index an agent that has read `files` can use; exclusions carry over (§9).

    Every type stays known by name (a fixture or helper in a read test file can supply a type whose own
    file was never read, and a mock needs only the name), but its members are kept only when the file
    that declares them was read."""
    def keep(t: TypeInfo, members: list) -> list:
        return [m for m in members if (m.file or t.file) in files]

    types = {
        fqn: replace(t, ctors=keep(t, t.ctors), factories=keep(t, t.factories), builders=keep(t, t.builders),
                     singletons=keep(t, t.singletons), observables=keep(t, t.observables),
                     methods=keep(t, t.methods), fields=keep(t, t.fields))
        for fqn, t in index.types.items()
    }
    tests = [t for t in index.tests if t.file in files]
    helpers = {k: [m for m in ms if m.file in files] for k, ms in index.test_helpers.items()}
    sub = Index(index.project, types, tests, {k: v for k, v in helpers.items() if v})
    sub.excluded = set(index.excluded)
    return sub


def sigma_over(index: Index, task: Task, demands: list[Need], files: set[str]) -> tuple[bool, list[Need]]:
    """Σ(D, ctx) where ctx holds exactly what the files in `files` can supply."""
    C, _ = find_focal(index, task)
    sub = restricted(index, files)
    resolver = Resolver(sub, test_package=C.package or None)  # same accessibility rule as expand()
    read_types = {erase(t.fqn) for t in index.types.values() if t.file in files}  # exception oracles (§2.4)
    ctx = Context(types=read_types, files=set(files))
    seen: set[int] = set()

    def absorb(objs) -> None:
        for e in objs:
            if id(e) not in seen:
                seen.add(id(e))
                ctx.entities.append(e)

    for n in demands:
        if n.kind in ("receiver", "arg", "setup"):
            r = resolver.resolve(n.type, 0)
            if r is not None:
                ctx.recipes[n.key()] = r
                absorb(r.entities())
    for t in sub.types.values():
        absorb(t.observables)
    return sufficient(sub, demands, ctx)


def replay(index: Index, task: Task, steps: list[Step], demands: Optional[list[Need]] = None,
           given_files=(), with_grep_hits: bool = False) -> Replay:
    demands = demands if demands is not None else compute_demands(index, task)
    files: set[str] = set(given_files)
    ok, open_needs = sigma_over(index, task, demands, files)
    sigma_step = 0 if ok else None
    files_at_sigma = len(files) if ok else None
    trace: list[dict] = []
    for s in steps:
        new = [f for f in s.reads + (s.grep_hits if with_grep_hits else []) if f not in files]
        files.update(new)
        if new and not ok:  # Σ is monotone in files: recompute only while it does not hold yet
            ok, open_needs = sigma_over(index, task, demands, files)
            if ok:
                sigma_step, files_at_sigma = s.n, len(files)
        trace.append({"n": s.n, "new_files": new, "sigma": ok, "open": len(open_needs)})

    have_usage = bool(steps) and all(s.prompt_tokens >= 0 and s.completion_tokens >= 0 for s in steps)
    total = sum(s.prompt_tokens + s.completion_tokens for s in steps) if have_usage else None
    after = None
    if have_usage and sigma_step is not None:
        after = sum(s.prompt_tokens + s.completion_tokens for s in steps if s.n > sigma_step)
    return Replay(
        task=task.ref_test_id, n_steps=len(steps), n_demands=len(demands), sigma_step=sigma_step,
        steps_after_sigma=(len(steps) - sigma_step) if sigma_step is not None else None,
        files_at_sigma=files_at_sigma, files_total=len(files), tokens_total=total, tokens_after_sigma=after,
        share_after_sigma=round(after / total, 4) if after is not None and total else None,
        open_at_end=[n.key() for n in open_needs], unparsed_commands=sum(s.unparsed for s in steps),
        trace=trace,
    )


def summarize(replays: list[Replay]) -> dict:
    reached = [r for r in replays if r.sigma_step is not None]
    shares = [r.share_after_sigma for r in reached if r.share_after_sigma is not None]
    with_tokens = [r for r in reached if r.tokens_total]
    pooled_after = sum(r.tokens_after_sigma for r in with_tokens)
    pooled_total = sum(r.tokens_total for r in with_tokens)
    return {
        "tasks": len(replays),
        "sigma_reached": len(reached),
        "sigma_rate": round(len(reached) / len(replays), 3) if replays else None,
        "median_share_after": round(statistics.median(shares), 3) if shares else None,
        "pooled_share_after": round(pooled_after / pooled_total, 3) if pooled_total else None,
        "median_steps_after": statistics.median([r.steps_after_sigma for r in reached]) if reached else None,
        "unparsed_commands": sum(r.unparsed_commands for r in replays),
    }


# ------------------------------------------------------------------ CLI (`python -m demandtest replay`)


def task_slug(ref_test_id: str) -> str:
    """File stem of a task's trajectory; the agent runner MUST save trajectories under this name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", ref_test_id)


def find_trajectory(traj_dir: str, ref_test_id: str) -> Optional[Path]:
    slug = task_slug(ref_test_id)
    for cand in (Path(traj_dir) / f"{slug}.traj.json", Path(traj_dir) / slug / f"{slug}.traj.json"):
        if cand.is_file():
            return cand
    return None


def load_tasks(path: str, repo: str) -> list[Task]:
    tasks: list[Task] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            obj = json.loads(line)
            tasks.append(Task(repo=repo, focal_class=obj["focal_class"], focal_method=obj["focal_method"],
                              focal_sig=obj.get("focal_sig", ""), focal_file=obj.get("focal_file", ""),
                              intention=obj.get("intention") or {}, ref_test_id=obj["ref_test_id"]))
    return tasks


def cmd(args) -> int:
    from . import index as index_mod, metrics
    index = index_mod.load(args.index)
    known = index_files(index)
    results: list[Replay] = []
    lines: list[str] = []
    for task in load_tasks(args.tasks, index.project.get("name", "")):
        path = find_trajectory(args.traj_dir, task.ref_test_id)
        if path is None:
            print(f"replay: no trajectory for {task.ref_test_id} in {args.traj_dir}", file=sys.stderr)
            continue
        steps = steps_from_mswea(load_trajectory(str(path)), root=args.repo_root, known=known)
        with index.excluded_scope({task.ref_test_id}):  # leakage control (§9)
            given = [task.focal_file] if args.given_focal and task.focal_file else []
            r = replay(index, task, steps, given_files=given, with_grep_hits=args.with_grep_hits)
        results.append(r)
        lines.append(json.dumps(asdict(r), sort_keys=True))
    if args.out:
        Path(args.out).write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    else:
        for line in lines:
            print(line)
    summary = summarize(results)
    print(metrics.format_table([summary], list(summary)), file=sys.stdout if args.out else sys.stderr)
    return 0

"""mini-swe-agent baseline runner on OpenRouter (docs/PAPER_PLAN.md arm A, vanilla).

Per task (SPEC §9 leakage rule, §10.1 protocol):
1. Copy the repository to a per-task work directory without `.git` and `target/`, and delete the
   held-out reference test method from its file, together with the comment block directly above it
   (the indexer's range covers annotations but not an attached Javadoc).
2. Run mini-swe-agent's DefaultAgent with the fixed prompt of `config.yaml` on a LocalEnvironment rooted
   at the copy. The trajectory is saved as `<traj-dir>[/r<k>]/<task_slug(ref_test_id)>.traj.json`, the
   name `python -m demandtest replay` looks for.
3. Take the test class the agent created (a new or changed `.java` under the test root) and compile and
   run it with our S4 step 5 on a fresh copy of the original repository, so edits the agent made to
   other files cannot change the verdict. Static repair is not applied (§10.1).
4. Ledger: one llm_calls row per billed call (provider usage; -1 when absent), file_access rows for
   files printed or grepped into (§6 rule 3), results via db.finish_run, and an "AGENT" checkpoint with
   the exit status, OpenRouter providers, cached tokens, cost, and changed non-test files.

Workers run agents in threads; only the main thread writes the ledger. Requires
`pip install -r baselines/mswea/requirements.txt` and OPENROUTER_API_KEY. The demandtest package stays
standard-library only: minisweagent, jinja2 and yaml are imported only by the functions that need them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from demandtest import db, demand, execute, index as index_mod, replay
from demandtest.demand import Task
from demandtest.index import Index, TestInfo

SYSTEM = "mswea"
CONFIG = Path(__file__).resolve().parent / "config.yaml"
COPY_IGNORE = shutil.ignore_patterns(".git", "target", "build", ".gradle", ".idea", "*.class")
_COMMENT_LINE = re.compile(r"\s*(//|/\*|\*)")


@dataclass
class Outcome:
    traj: dict
    workdir: str
    exit_status: str
    agent_wall_ms: int
    test_path: Optional[str] = None  # relative to the repository root
    test_source: str = ""
    changed_other: list = field(default_factory=list)
    verdict: Optional[execute.Verdict] = None
    error: str = ""


# ------------------------------------------------------------------ checkout (§9)


def remove_method(path: Path, line_start: int, line_end: int) -> None:
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    start = line_start - 1
    while start > 0 and _COMMENT_LINE.match(lines[start - 1]):
        start -= 1
    del lines[start:line_end]
    path.write_text("".join(lines), encoding="utf-8")


def reference_test(index: Index, ref_test_id: str) -> TestInfo:
    ref = next((t for t in index.tests if t.id == ref_test_id), None)
    if ref is None or not (ref.file and ref.line_start and ref.line_end):
        raise ValueError(f"reference test {ref_test_id} has no file/line range in the index; cannot hide it (§9)")
    return ref


def prepare_checkout(repo: str, dest: Path, ref: TestInfo) -> None:
    shutil.copytree(repo, dest, ignore=COPY_IGNORE, symlinks=True)
    remove_method(dest / ref.file, ref.line_start, ref.line_end)


def snapshot(root: Path) -> dict[str, tuple[str, float]]:
    """relative path -> (sha1, mtime) for every file outside build outputs."""
    out: dict[str, tuple[str, float]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ("target", "build", ".gradle", ".git")]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                out[p.relative_to(root).as_posix()] = (hashlib.sha1(p.read_bytes()).hexdigest(), p.stat().st_mtime)
            except OSError:
                continue
    return out


def generated_test(before: dict, after: dict, test_root: str, task: Task, root: Path) -> tuple[Optional[str], list[str]]:
    """(the agent's test file, other changed files). Prefers a changed test that calls the focal method."""
    changed = sorted(p for p, (sha, _) in after.items() if before.get(p, ("",))[0] != sha)
    tests = [p for p in changed if p.startswith(test_root.rstrip("/") + "/") and p.endswith(".java")]
    other = [p for p in changed if p not in tests]
    if not tests:
        return None, other
    name = re.escape(task.focal_method)

    def rank(p: str) -> tuple:
        src = (root / p).read_text(encoding="utf-8", errors="replace")
        return (bool(re.search(rf"\b{name}\s*\(", src)), "@Test" in src, after[p][1])

    return max(tests, key=rank), other


# ------------------------------------------------------------------ agent


def template_vars(task: Task, test_root: str) -> dict:
    pkg = execute.package_of(task)
    intention = task.intention or {}
    return {
        "focal_class": task.focal_class, "focal_sig": task.focal_sig or task.focal_method,
        "focal_file": task.focal_file, "test_dir": f"{test_root}/{pkg.replace('.', '/')}".rstrip("/"),
        "test_package": pkg or "(default)",
        "objective": intention.get("objective") or "(none)",
        "preconditions": intention.get("preconditions") or "(none)",
        "expected_results": intention.get("expected_results") or "(none)",
    }


def load_config(path: Path) -> dict:
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def make_model(cfg: dict, model_name: str, providers: list[str], quantizations: list[str], model_kwargs: dict):
    from minisweagent.models.openrouter_model import OpenRouterModel
    kwargs = dict(cfg.get("model") or {})
    mk = dict(kwargs.pop("model_kwargs", None) or {}) | model_kwargs
    if providers or quantizations:
        provider: dict = {"allow_fallbacks": False}  # pinned: the provider never changes silently
        if providers:
            provider["order"] = providers
        if quantizations:
            provider["quantizations"] = quantizations
        mk["provider"] = provider
    return OpenRouterModel(model_name=model_name, model_kwargs=mk, **kwargs)


def run_agent(model, cfg: dict, task: Task, workdir: Path, traj_path: Path, test_root: str) -> tuple[dict, str, int]:
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.local import LocalEnvironment
    env_cfg = dict(cfg.get("environment") or {})
    env_vars = dict(env_cfg.pop("env", None) or {})
    env_vars["MAVEN_ARGS"] = (os.environ.get("MAVEN_ARGS", "") + " -o -B").strip()  # offline, batch
    env = LocalEnvironment(cwd=str(workdir), env=env_vars, **env_cfg)
    agent = DefaultAgent(model, env, output_path=traj_path, **(cfg.get("agent") or {}))
    started_at, started = time.time(), time.monotonic()
    try:
        info = agent.run(**template_vars(task, test_root))
        exit_status = str(info.get("exit_status") or "")
    except Exception as e:  # recorded, never fatal for the batch (§5)
        exit_status = f"{type(e).__name__}: {e}"[:300]
    wall_ms = int((time.monotonic() - started) * 1000)
    traj = agent.save(traj_path, {"info": {"demandtest": {
        "task": task.ref_test_id, "started_at": started_at, "agent_wall_ms": wall_ms}}})
    return traj, exit_status, wall_ms


def run_task(task: Task, ref: TestInfo, repo: str, cfg: dict, model, traj_path: Path, work_root: Path,
             test_root: str, timeout_s: int, module: Optional[str], verify: bool = True,
             keep_workdir: bool = False) -> Outcome:
    """One task end to end, without touching the ledger (safe in a worker thread)."""
    workdir = work_root / traj_path.name.replace(".traj.json", "")
    if workdir.exists():
        shutil.rmtree(workdir)
    prepare_checkout(repo, workdir, ref)
    before = snapshot(workdir)
    traj, exit_status, wall_ms = run_agent(model, cfg, task, workdir, traj_path, test_root)
    after = snapshot(workdir)
    test_rel, other = generated_test(before, after, test_root, task, workdir)
    out = Outcome(traj=traj, workdir=str(workdir), exit_status=exit_status, agent_wall_ms=wall_ms,
                  test_path=test_rel, changed_other=other)
    if test_rel:
        out.test_source = (workdir / test_rel).read_text(encoding="utf-8", errors="replace")
        if verify:
            with tempfile.TemporaryDirectory(prefix="dt-verify-") as tmp:
                fresh = Path(tmp) / "repo"
                shutil.copytree(repo, fresh, ignore=COPY_IGNORE, symlinks=True)
                out.verdict = execute.compile_and_run(str(fresh), task, out.test_source, test_root=test_root,
                                                      timeout_s=timeout_s, module=module)
    if not keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    return out


# ------------------------------------------------------------------ ledger (main thread only)


def ingest(conn, run_id: int, traj: dict, model_name: str, known: Optional[set] = None) -> dict:
    """llm_calls and file_access rows for one trajectory; returns per-run call metadata."""
    root = replay.trajectory_root(traj)
    steps = replay.steps_from_mswea(traj, root=root, known=known)
    started_at = ((traj.get("info") or {}).get("demandtest") or {}).get("started_at")
    providers: Counter = Counter()
    cached = cost = 0.0
    model_ms = 0
    for step, call in zip(steps, replay.billed_calls(traj)):
        resp = call.extra.get("response") if isinstance(call.extra.get("response"), dict) else {}
        usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
        providers[str(resp.get("provider") or "?")] += 1
        cached += float((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        cost += float(usage.get("cost") or call.extra.get("cost") or 0.0)
        # model latency: this call's timestamp minus the message before it (tool time excluded); 0 = unknown
        ts, prev = call.extra.get("timestamp"), call.prev_ts if call.prev_ts is not None else started_at
        latency = int((ts - prev) * 1000) if isinstance(ts, (int, float)) and isinstance(prev, (int, float)) else 0
        model_ms += max(latency, 0)
        rid = str(resp.get("id") or "")[:16]
        db.log_llm_call(conn, run_id, stage="agent", model=model_name, prompt_tokens=step.prompt_tokens,
                        completion_tokens=step.completion_tokens, latency_ms=max(latency, 0),
                        prompt_sha=rid, response_sha=rid)
    seen: list[str] = []
    for step in steps:
        for path in step.reads + step.grep_hits:
            if path not in seen:
                seen.append(path)
                db.log_file_access(conn, run_id, path, "agent")
    return {"n_calls": len(steps), "providers": dict(providers), "cached_tokens": int(cached),
            "cost_usd": round(cost, 6), "model_ms": model_ms, "files": seen,
            "unparsed_commands": sum(s.unparsed for s in steps)}


def record(conn, run_id: int, out: Outcome, task: Task, demands, model_name: str, known: Optional[set]) -> None:
    meta = ingest(conn, run_id, out.traj, model_name, known)
    v = out.verdict
    hit = execute.target_hit(out.test_source, task, demands) if out.test_source else 0
    db.checkpoint(conn, run_id, "AGENT", {
        **meta, "exit_status": out.exit_status, "agent_wall_ms": out.agent_wall_ms, "test_path": out.test_path,
        "changed_other": out.changed_other, "src": out.test_source,
        "verify_wall_ms": v.wall_ms if v else None, "diagnostics": (v.diagnostics[:2000] if v else ""),
    })
    notes = out.exit_status if out.test_path else f"no-test; {out.exit_status}"
    db.finish_run(conn, run_id, "done", compiled=v.compiled if v else 0, passed=v.passed if v else 0,
                  n_asserts=execute.count_asserts(out.test_source), wall_ms=out.agent_wall_ms,
                  test_path=out.test_path, notes=notes[:500], target_hit=hit)


# ------------------------------------------------------------------ CLI


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m baselines.mswea.run", description=__doc__.splitlines()[0])
    p.add_argument("--db", required=True)
    p.add_argument("--repo", required=True, help="repo name registered with `demandtest add-repo`")
    p.add_argument("--index", required=True)
    p.add_argument("--model", required=True, help="OpenRouter model id, e.g. qwen/qwen3-coder-next")
    p.add_argument("--provider", default="", help="comma-separated OpenRouter providers to pin (no fallbacks)")
    p.add_argument("--quantizations", default="", help="comma-separated, e.g. bf16,fp8")
    p.add_argument("--model-kwargs", default="{}", help='JSON merged into the request, e.g. \'{"temperature": 1.0}\'')
    p.add_argument("--config", default=str(CONFIG))
    p.add_argument("--traj-dir", required=True)
    p.add_argument("--work-dir", default=None, help="per-task checkouts (default: a temporary directory)")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--repeat", type=int, default=0, help="repeat index; trajectories go to <traj-dir>/r<k>/")
    p.add_argument("--test-root", default=execute.DEFAULT_TEST_ROOT)
    p.add_argument("--module", default=None)
    p.add_argument("--timeout", type=int, default=execute.DEFAULT_TIMEOUT_S, help="verdict compile/run timeout (s)")
    p.add_argument("--keep-workdir", action="store_true")
    p.add_argument("--force", action="store_true", help="re-run tasks whose run is already done")
    p.add_argument("--show-prompt", action="store_true", help="print the first task's prompt and exit (no API call)")
    return p


def _split(text: str) -> list[str]:
    return [s.strip() for s in text.split(",") if s.strip()]


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    cfg_text = Path(args.config).read_text(encoding="utf-8")
    cfg = load_config(Path(args.config))
    model_kwargs = json.loads(args.model_kwargs)
    with closing(db.connect(args.db)) as conn:
        db.init_schema(conn)
        repo_row = db.get_repo(conn, args.repo)
        if repo_row is None:
            print(f"unknown repo {args.repo}; run `python -m demandtest add-repo` first", file=sys.stderr)
            return 2
        index = index_mod.load(args.index)
        known = replay.index_files(index)
        tasks = [Task.from_row(args.repo, r) for r in db.get_tasks(conn, repo_row["id"])][: args.limit]
        if args.show_prompt:
            from jinja2 import StrictUndefined, Template
            if tasks:
                tv = template_vars(tasks[0], args.test_root) | {"cwd": "<work dir>"}
                print(Template(cfg["agent"]["instance_template"], undefined=StrictUndefined).render(**tv))
            return 0
        import minisweagent
        config = {
            "system": SYSTEM, "model": args.model, "provider": _split(args.provider),
            "quantizations": _split(args.quantizations), "model_kwargs": model_kwargs,
            "config_sha": hashlib.sha256(cfg_text.encode("utf-8")).hexdigest()[:12],
            "mini_version": minisweagent.__version__, "test_root": args.test_root, "module": args.module,
        }
        if args.repeat:
            config["repeat"] = args.repeat
        traj_dir = Path(args.traj_dir) / (f"r{args.repeat}" if args.repeat else "")
        traj_dir.mkdir(parents=True, exist_ok=True)
        work_root = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix="dt-mswea-"))
        work_root.mkdir(parents=True, exist_ok=True)

        jobs = []
        for task in tasks:
            run_id = db.start_run(conn, task.id, SYSTEM, args.model, config)
            if db.get_run(conn, run_id)["status"] == "done" and not args.force:
                continue
            jobs.append((task, run_id))
        print(f"{len(jobs)} task(s) to run with {args.model} ({args.workers} workers)", file=sys.stderr)

        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {}
            for task, run_id in jobs:
                try:
                    ref = reference_test(index, task.ref_test_id)
                except ValueError as e:
                    db.finish_run(conn, run_id, "error", notes=str(e)[:500])
                    continue
                model = make_model(cfg, args.model, _split(args.provider), _split(args.quantizations), model_kwargs)
                traj_path = traj_dir / f"{replay.task_slug(task.ref_test_id)}.traj.json"
                futures[pool.submit(run_task, task, ref, repo_row["path"], cfg, model, traj_path, work_root,
                                    args.test_root, args.timeout, args.module, True, args.keep_workdir)] = (task, run_id)
            for fut in as_completed(futures):
                task, run_id = futures[fut]
                try:
                    out = fut.result()
                    with index.excluded_scope({task.ref_test_id}):  # leakage control (§9)
                        demands = demand.compute_demands(index, task)
                    record(conn, run_id, out, task, demands, args.model, known)
                    status = "passed" if out.verdict and out.verdict.passed else (out.exit_status or "done")
                except Exception as e:  # one task never aborts the batch (§5)
                    db.finish_run(conn, run_id, "error", notes=f"{type(e).__name__}: {e}"[:500])
                    status = f"error: {e}"
                print(f"[{run_id}] {task.ref_test_id}: {status}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

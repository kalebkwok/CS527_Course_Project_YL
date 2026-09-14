"""CLI and S1–S5 orchestration (SPEC §5 cli.py, §7 CLI).

- `run` is idempotent per (task, system, model, config_hash): a finished run is
  skipped unless --force. Every stage writes a checkpoint; a crashed run (status
  'running') resumes from the S3 checkpoint when it holds generated source.
- Any exception inside one task is caught, recorded in results.notes with
  status='error'; the batch never aborts (§5).
- demandtest inspected_files = |ctx.files| ∪ focal file, logged at S2 and never
  changed by S3–S5 (§6 rule 2).
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from . import db, demand, execute, expand, generate, index as index_mod, metrics, packet as packet_mod, repair, refine
from .demand import Task
from .llm import DryRunClient, LLMClient

PROMPTS_SHA = hashlib.sha256((generate.SYSTEM_PROMPT + refine.S5_PROMPT).encode("utf-8")).hexdigest()[:12]
SYSTEMS = ("demandtest", "openhands", "sweagent", "intentiontest")


# ------------------------------------------------------------------ helpers


def _count_java(root: Path, sub: str) -> int:
    d = root / sub
    return len(list(d.rglob("*.java"))) if d.is_dir() else 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="demandtest", description="Demand-driven test generation (SPEC §7).")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init-db", help="create the SQLite ledger schema")
    s.add_argument("--db", required=True)

    s = sub.add_parser("add-repo", help="register a repository")
    s.add_argument("--db", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--path", required=True)
    s.add_argument("--build-tool", default="maven")
    s.add_argument("--commit", dest="commit_sha", default=None)

    s = sub.add_parser("import-tasks", help="import tasks.jsonl")
    s.add_argument("--db", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--tasks", required=True)

    s = sub.add_parser("run", help="run S1-S5 over the repo's tasks")
    s.add_argument("--db", required=True)
    s.add_argument("--repo", required=True)
    s.add_argument("--system", default="demandtest", choices=SYSTEMS)
    s.add_argument("--model", required=True)
    s.add_argument("--index", default=None, help="path to index.json (required for --system demandtest)")
    s.add_argument("--budget-files", type=int, default=expand.DEFAULT_BUDGET_FILES)
    s.add_argument("--budget-tokens", type=int, default=expand.DEFAULT_BUDGET_TOKENS)
    s.add_argument("--d-max", type=int, default=expand.DEFAULT_D_MAX)
    s.add_argument("--refine", type=int, default=1, help="max S5 rounds (0 or 1)")
    s.add_argument("--limit", type=int, default=None)
    s.add_argument("--force", action="store_true")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--keep", action="store_true", help="keep the generated test file after the run")
    s.add_argument("--timeout", type=int, default=execute.DEFAULT_TIMEOUT_S)
    s.add_argument("--test-root", default=execute.DEFAULT_TEST_ROOT)
    s.add_argument("--module", default=None)

    s = sub.add_parser("report", help="print summarize() and pareto() as markdown")
    s.add_argument("--db", required=True)
    return p


def _config(args, system: str) -> dict:
    return {
        "system": system, "model": args.model, "budget_files": args.budget_files,
        "budget_tokens": args.budget_tokens, "d_max": args.d_max, "refine": args.refine,
        "dry_run": bool(args.dry_run), "keep": bool(args.keep), "prompts_sha": PROMPTS_SHA,
        "test_root": args.test_root, "module": args.module,
    }


# ---------------------------------------------------------------- S1-S5 run


def run_one(conn, repo_row, index, task: Task, run_id: int, args) -> None:
    started = time.monotonic()
    repo_path = repo_row["path"]
    focal_path = Path(repo_path) / task.focal_file
    focal_source: Optional[str] = None
    if focal_path.is_file():
        focal_source = focal_path.read_text(encoding="utf-8", errors="replace")

    previous = db.get_checkpoint(conn, run_id, "S3") or {}
    resumed_src = previous.get("src")

    with index.excluded_scope({task.ref_test_id}):  # leakage control (§9)
        # ---- S1 demand set (no LLM)
        demands = demand.compute_demands(index, task)
        db.checkpoint(conn, run_id, "S1", {"demands": [asdict(n) for n in demands]})

        # ---- S2 bounded expansion + sufficiency predicate (no LLM)
        expansion = expand.expand(index, demands, task, budget_files=args.budget_files,
                                 budget_tokens=args.budget_tokens, d_max=args.d_max,
                                 est_tokens=packet_mod.est_tokens)
        inspected = set(expansion.ctx.files) | {task.focal_file}
        for path in sorted(inspected):
            db.log_file_access(conn, run_id, path, "S2")
        db.checkpoint(conn, run_id, "S2", {
            "status": expansion.status,
            "trace": expansion.trace,
            "unresolved": [n.key() for n in expansion.unresolved],
            "files": sorted(expansion.ctx.files),
            "inspected_files": sorted(inspected),
            "referable_tests": [t.id for t in expansion.referable_tests],
        })
        pkt = packet_mod.render(index, task, demands, expansion, focal_source=focal_source,
                               budget_tokens=args.budget_tokens)

        # ---- S3 generation (exactly one call)
        if resumed_src:
            src = resumed_src
            db.checkpoint(conn, run_id, "S3", {**previous, "resumed": True})
        else:
            client = _make_client(conn, run_id, args)
            reply = generate.generate(client, pkt)
            try:
                src = generate.extract_java(reply)
            except generate.ParseError as e:
                db.checkpoint(conn, run_id, "S3", {
                    "packet_est_tokens": pkt.est_tokens, "raw_reply": reply[:4000],
                    "parse_error": str(e),
                })
                db.finish_run(conn, run_id, "done", compiled=0, passed=0, n_asserts=0,
                              wall_ms=int((time.monotonic() - started) * 1000), notes="parse-error")
                return
            db.checkpoint(conn, run_id, "S3", {
                "packet_est_tokens": pkt.est_tokens, "packet_status": expansion.status,
                "raw_reply": reply[:4000], "src": src,
            })

    # ---- S4 static repair + compile/run (no LLM)
    src_fixed, fixes = repair.static_repair(index, task, src, focal_source)
    if args.dry_run:
        verdict = execute.Verdict(0, 0, 0, "", execute.count_asserts(src_fixed), None, "",
                                  "dry-run: execution skipped")
    else:
        verdict = execute.compile_and_run(repo_path, task, src_fixed, test_root=args.test_root,
                                         timeout_s=args.timeout, module=args.module, keep=args.keep)
    db.checkpoint(conn, run_id, "S4", {
        "fixes": fixes, "compiled": verdict.compiled, "passed": verdict.passed,
        "n_asserts": verdict.n_asserts, "diagnostics": verdict.diagnostics[:2000], "src": src_fixed,
    })

    # ---- S5 semantic repair: at most one call, only on compiled-but-failed
    if not args.dry_run and verdict.compiled and not verdict.passed and args.refine > 0:
        client = _make_client(conn, run_id, args)
        src2 = refine.refine_once(client, index, task, src_fixed, verdict, pkt)
        src2_fixed, fixes2 = repair.static_repair(index, task, src2, focal_source)
        verdict = execute.compile_and_run(repo_path, task, src2_fixed, test_root=args.test_root,
                                         timeout_s=args.timeout, module=args.module, keep=args.keep)
        db.checkpoint(conn, run_id, "S5", {
            "fixes": fixes2, "compiled": verdict.compiled, "passed": verdict.passed,
            "diagnostics": verdict.diagnostics[:2000], "src": src2_fixed,
        })
        src_fixed = src2_fixed

    if not args.keep and not args.dry_run:
        execute.remove_test(repo_path, verdict.test_path)
    db.finish_run(conn, run_id, "done", compiled=verdict.compiled, passed=verdict.passed,
                  n_asserts=verdict.n_asserts, wall_ms=int((time.monotonic() - started) * 1000),
                  test_path=verdict.test_path if (args.keep and not args.dry_run) else None,
                  notes=verdict.notes or None)


def _make_client(conn, run_id: int, args):
    if args.dry_run:
        return DryRunClient(model=args.model, conn=conn, run_id=run_id)
    return LLMClient(model=args.model, conn=conn, run_id=run_id, timeout_s=args.timeout)


# --------------------------------------------------------------- commands


def cmd_init_db(args) -> int:
    with closing(db.connect(args.db)) as conn:
        db.init_schema(conn)
    print(f"initialized {args.db}")
    return 0


def cmd_add_repo(args) -> int:
    root = Path(args.path)
    with closing(db.connect(args.db)) as conn:
        db.init_schema(conn)
        repo_id = db.add_repo(conn, args.name, str(root), build_tool=args.build_tool,
                              commit_sha=args.commit_sha,
                              n_source_files=_count_java(root, "src/main/java"),
                              n_test_files=_count_java(root, "src/test/java"))
    print(f"repo {args.name} id={repo_id} path={root}")
    return 0


def cmd_import_tasks(args) -> int:
    with closing(db.connect(args.db)) as conn:
        db.init_schema(conn)
        row = db.get_repo(conn, args.repo)
        if row is None:
            print(f"unknown repo: {args.repo}", file=sys.stderr)
            return 2
        n = db.import_tasks(conn, row["id"], args.tasks)
    print(f"imported {n} new task(s) for {args.repo}")
    return 0


def cmd_run(args) -> int:
    with closing(db.connect(args.db)) as conn:
        return _run(args, conn)


def _run(args, conn) -> int:
    db.init_schema(conn)
    if args.system != "demandtest":
        print(f"--system {args.system} is written by its own runner (baselines/), not by `demandtest run`",
              file=sys.stderr)
        return 2
    if not args.index:
        print("--index is required for --system demandtest", file=sys.stderr)
        return 2
    repo_row = db.get_repo(conn, args.repo)
    if repo_row is None:
        print(f"unknown repo: {args.repo}", file=sys.stderr)
        return 2
    loaded = index_mod.load(args.index)
    tasks = [Task.from_row(args.repo, row) for row in db.get_tasks(conn, repo_row["id"])]
    if args.limit is not None:
        tasks = tasks[: args.limit]

    config = _config(args, args.system)
    n_run = n_skip = n_error = 0
    for task in tasks:
        run_id = db.start_run(conn, task.id, args.system, args.model, config)
        existing = db.get_run(conn, run_id)
        if existing["status"] == "done" and not args.force:
            n_skip += 1
            continue
        try:
            run_one(conn, repo_row, loaded, task, run_id, args)
            n_run += 1
        except Exception as e:  # never abort the batch (§5)
            db.finish_run(conn, run_id, "error", notes=f"{type(e).__name__}: {e}")
            n_error += 1
            print(f"error on {task.focal_class}#{task.focal_method}: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"run: {n_run} executed, {n_skip} skipped (already done), {n_error} error(s); config_hash={db.config_hash(config)}")
    return 1 if n_error else 0


def cmd_report(args) -> int:
    with closing(db.connect(args.db)) as conn:
        db.init_schema(conn)
        summary = metrics.format_table(metrics.summarize(conn))
        frontier = metrics.format_table(metrics.pareto(conn))
    print("## Summary (per system x model)\n")
    print(summary)
    print("\n## Pareto (demandtest configs: pass_rate vs tokens_per_task)\n")
    print(frontier)
    return 0


COMMANDS = {
    "init-db": cmd_init_db,
    "add-repo": cmd_add_repo,
    "import-tasks": cmd_import_tasks,
    "run": cmd_run,
    "report": cmd_report,
}


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    return COMMANDS[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

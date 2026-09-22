"""SQLite ledger (SPEC §6) and accounting rules.

Accounting rules (MUST, §6): token/call/file columns in `results` are derived
from `llm_calls` / `file_access` at finish_run and never written by hand.
Estimates are never written to llm_calls.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, path TEXT NOT NULL,
  commit_sha TEXT, build_tool TEXT, n_source_files INTEGER, n_test_files INTEGER);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY, repo_id INTEGER NOT NULL REFERENCES repos(id),
  focal_class TEXT NOT NULL, focal_method TEXT NOT NULL, focal_sig TEXT NOT NULL, focal_file TEXT NOT NULL,
  intention_json TEXT NOT NULL, ref_test_id TEXT NOT NULL, UNIQUE(repo_id, focal_sig, ref_test_id));
CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL REFERENCES tasks(id),
  system TEXT NOT NULL, model TEXT NOT NULL, config_hash TEXT NOT NULL, config_json TEXT NOT NULL,
  started_at REAL, finished_at REAL, status TEXT NOT NULL DEFAULT 'pending',
  UNIQUE(task_id, system, model, config_hash));
CREATE TABLE IF NOT EXISTS checkpoints(run_id INTEGER NOT NULL REFERENCES runs(id), stage TEXT NOT NULL,
  payload_json TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(run_id, stage));
CREATE TABLE IF NOT EXISTS llm_calls(id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES runs(id), seq INTEGER NOT NULL,
  stage TEXT NOT NULL, model TEXT NOT NULL, prompt_tokens INTEGER NOT NULL, completion_tokens INTEGER NOT NULL,
  latency_ms INTEGER NOT NULL, prompt_sha TEXT NOT NULL, response_sha TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS file_access(run_id INTEGER NOT NULL REFERENCES runs(id), path TEXT NOT NULL, stage TEXT NOT NULL,
  PRIMARY KEY(run_id, path));
CREATE TABLE IF NOT EXISTS results(run_id INTEGER PRIMARY KEY REFERENCES runs(id), compiled INTEGER, passed INTEGER,
  n_asserts INTEGER, mutation_score REAL, alignment_score REAL, wall_ms INTEGER, inspected_files INTEGER,
  prompt_tokens INTEGER, completion_tokens INTEGER, n_llm_calls INTEGER, test_path TEXT, notes TEXT,
  target_hit INTEGER, packet_status TEXT);
"""

# Columns added after the schema was frozen (§6); init_schema adds them to older ledgers.
MIGRATIONS = {"results": {"target_hit": "INTEGER", "packet_status": "TEXT"}}

TASK_REQUIRED_KEYS = {"focal_class", "focal_method", "focal_sig", "focal_file", "intention", "ref_test_id"}


def connect(path: str) -> sqlite3.Connection:
    if path != ":memory:":
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: CREATE TABLE IF NOT EXISTS plus ALTER TABLE for MIGRATIONS; twice is a no-op (§12 db test)."""
    conn.executescript(SCHEMA)
    for table, columns in MIGRATIONS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, decl in columns.items():
            if column not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def config_hash(config: dict) -> str:
    return hashlib.sha256(canonical(config).encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- repos/tasks


def add_repo(conn, name: str, path: str, build_tool: str | None = None, commit_sha: str | None = None,
             n_source_files: int | None = None, n_test_files: int | None = None) -> int:
    with conn:
        conn.execute(
            "INSERT INTO repos(name, path, commit_sha, build_tool, n_source_files, n_test_files) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET path=excluded.path, commit_sha=excluded.commit_sha, "
            "build_tool=excluded.build_tool, n_source_files=excluded.n_source_files, n_test_files=excluded.n_test_files",
            (name, path, commit_sha, build_tool, n_source_files, n_test_files),
        )
    return int(conn.execute("SELECT id FROM repos WHERE name=?", (name,)).fetchone()[0])


def get_repo(conn, name: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM repos WHERE name=?", (name,)).fetchone()


def import_tasks(conn, repo_id: int, tasks_jsonl_path: str) -> int:
    """Import tasks.jsonl (§7). Returns number of newly inserted rows."""
    inserted = 0
    with open(tasks_jsonl_path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            missing = TASK_REQUIRED_KEYS - obj.keys()
            if missing:
                raise ValueError(f"{tasks_jsonl_path}:{lineno}: missing keys {sorted(missing)}")
            if not isinstance(obj.get("intention"), dict):
                raise ValueError(f"{tasks_jsonl_path}:{lineno}: intention must be an object")
            with conn:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO tasks(repo_id, focal_class, focal_method, focal_sig, focal_file, "
                    "intention_json, ref_test_id) VALUES(?,?,?,?,?,?,?)",
                    (repo_id, obj["focal_class"], obj["focal_method"], obj["focal_sig"], obj["focal_file"],
                     canonical(obj["intention"]), obj["ref_test_id"]),
                )
            inserted += cur.rowcount
    return inserted


def get_tasks(conn, repo_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM tasks WHERE repo_id=? ORDER BY id", (repo_id,)).fetchall()


# ----------------------------------------------------------------------- runs


def start_run(conn, task_id: int, system: str, model: str, config: dict) -> int:
    """Idempotent per (task_id, system, model, config_hash): returns the same id twice (§12)."""
    ch = config_hash(config)
    row = conn.execute("SELECT id FROM runs WHERE task_id=? AND system=? AND model=? AND config_hash=?",
                       (task_id, system, model, ch)).fetchone()
    if row:
        return int(row[0])
    with conn:
        cur = conn.execute(
            "INSERT INTO runs(task_id, system, model, config_hash, config_json, started_at, status) "
            "VALUES(?,?,?,?,?,?,'running')",
            (task_id, system, model, ch, canonical(config), time_now()),
        )
    return int(cur.lastrowid)


def get_run(conn, run_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def checkpoint(conn, run_id: int, stage: str, payload: dict) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO checkpoints(run_id, stage, payload_json, created_at) VALUES(?,?,?,?)",
            (run_id, stage, canonical(payload), time_now()),
        )


def get_checkpoint(conn, run_id: int, stage: str) -> dict | None:
    row = conn.execute("SELECT payload_json FROM checkpoints WHERE run_id=? AND stage=?", (run_id, stage)).fetchone()
    return json.loads(row[0]) if row else None


# ------------------------------------------------------------------- ledger


def log_llm_call(conn, run_id: int, stage: str, model: str, prompt_tokens: int, completion_tokens: int,
                 latency_ms: int, prompt_sha: str, response_sha: str) -> int:
    """seq is per-run, increasing (§12 llm test). Tokens are the provider's usage; never estimates."""
    with conn:
        seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM llm_calls WHERE run_id=?", (run_id,)).fetchone()[0]
        conn.execute(
            "INSERT INTO llm_calls(run_id, seq, stage, model, prompt_tokens, completion_tokens, latency_ms, "
            "prompt_sha, response_sha, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (run_id, seq, stage, model, int(prompt_tokens), int(completion_tokens), int(latency_ms),
             prompt_sha, response_sha, time_now()),
        )
    return int(seq)


def log_file_access(conn, run_id: int, path: str, stage: str) -> None:
    """Distinct path once per run (§6 accounting rule 3)."""
    with conn:
        conn.execute("INSERT OR IGNORE INTO file_access(run_id, path, stage) VALUES(?,?,?)", (run_id, path, stage))


def finish_run(conn, run_id: int, status: str, *, compiled: int | None = None, passed: int | None = None,
               n_asserts: int | None = None, mutation_score: float | None = None,
               alignment_score: float | None = None, wall_ms: int | None = None,
               test_path: str | None = None, notes: str | None = None,
               target_hit: int | None = None, packet_status: str | None = None) -> None:
    """Write results with derived token/call/file columns, then close the run."""
    pt, ct, ncalls = conn.execute(
        "SELECT COALESCE(SUM(prompt_tokens), 0), COALESCE(SUM(completion_tokens), 0), COUNT(*) "
        "FROM llm_calls WHERE run_id=?", (run_id,)).fetchone()
    nfiles = conn.execute("SELECT COUNT(*) FROM file_access WHERE run_id=?", (run_id,)).fetchone()[0]
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO results(run_id, compiled, passed, n_asserts, mutation_score, alignment_score, "
            "wall_ms, inspected_files, prompt_tokens, completion_tokens, n_llm_calls, test_path, notes, target_hit, "
            "packet_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, compiled, passed, n_asserts, mutation_score, alignment_score, wall_ms, nfiles,
             pt, ct, ncalls, test_path, notes, target_hit, packet_status),
        )
        conn.execute("UPDATE runs SET status=?, finished_at=? WHERE id=?", (status, time_now(), run_id))


def time_now() -> float:
    import time
    return time.time()

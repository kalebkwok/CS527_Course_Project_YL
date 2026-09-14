"""§12 — db: schema idempotence, run idempotence, derived accounting columns."""
from __future__ import annotations

import unittest

from demandtest import db

from tests._util import make_task_row, memory_conn


class DbTest(unittest.TestCase):
    def _conn(self):
        conn = memory_conn()
        self.addCleanup(conn.close)
        return conn

    def test_init_schema_twice_is_idempotent(self):
        conn = self._conn()
        db.init_schema(conn)  # second call must not raise
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"repos", "tasks", "runs", "checkpoints", "llm_calls", "file_access", "results"} <= tables)

    def test_start_run_twice_returns_same_id(self):
        conn = self._conn()
        repo_id = db.add_repo(conn, "mini", "/tmp/mini")
        task_id = make_task_row(conn, repo_id)
        config = {"system": "demandtest", "model": "m", "budget_files": 12}
        first = db.start_run(conn, task_id, "demandtest", "m", config)
        second = db.start_run(conn, task_id, "demandtest", "m", config)
        self.assertEqual(first, second)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        # a different config is a different run
        third = db.start_run(conn, task_id, "demandtest", "m", {**config, "budget_files": 8})
        self.assertNotEqual(first, third)

    def test_finish_run_derives_tokens_calls_files(self):
        conn = self._conn()
        repo_id = db.add_repo(conn, "mini", "/tmp/mini")
        task_id = make_task_row(conn, repo_id)
        run_id = db.start_run(conn, task_id, "demandtest", "m", {"x": 1})
        db.log_llm_call(conn, run_id, "generate", "m", 100, 50, 10, "p1", "r1")
        db.log_llm_call(conn, run_id, "refine", "m", 20, 5, 4, "p2", "r2")
        db.log_file_access(conn, run_id, "src/main/java/com/mini/Foo.java", "S2")
        db.log_file_access(conn, run_id, "src/main/java/com/mini/Bar.java", "S2")
        db.log_file_access(conn, run_id, "src/main/java/com/mini/Bar.java", "S2")  # distinct per run
        db.finish_run(conn, run_id, "done", compiled=1, passed=0, n_asserts=3, wall_ms=1234)
        row = conn.execute("SELECT * FROM results WHERE run_id=?", (run_id,)).fetchone()
        self.assertEqual(row["prompt_tokens"], 120)
        self.assertEqual(row["completion_tokens"], 55)
        self.assertEqual(row["n_llm_calls"], 2)
        self.assertEqual(row["inspected_files"], 2)
        self.assertEqual(row["compiled"], 1)
        self.assertEqual(db.get_run(conn, run_id)["status"], "done")

    def test_llm_seq_increases_per_run(self):
        conn = self._conn()
        repo_id = db.add_repo(conn, "mini", "/tmp/mini")
        task_id = make_task_row(conn, repo_id)
        run_id = db.start_run(conn, task_id, "demandtest", "m", {"x": 1})
        db.log_llm_call(conn, run_id, "generate", "m", 1, 1, 1, "a", "b")
        db.log_llm_call(conn, run_id, "refine", "m", 1, 1, 1, "c", "d")
        seqs = [r[0] for r in conn.execute("SELECT seq FROM llm_calls WHERE run_id=? ORDER BY id", (run_id,))]
        self.assertEqual(seqs, [1, 2])

    def test_checkpoint_round_trip(self):
        conn = self._conn()
        repo_id = db.add_repo(conn, "mini", "/tmp/mini")
        task_id = make_task_row(conn, repo_id)
        run_id = db.start_run(conn, task_id, "demandtest", "m", {"x": 1})
        db.checkpoint(conn, run_id, "S1", {"demands": [{"kind": "receiver"}]})
        db.checkpoint(conn, run_id, "S1", {"demands": []})  # upsert, not append
        self.assertEqual(db.get_checkpoint(conn, run_id, "S1"), {"demands": []})
        self.assertIsNone(db.get_checkpoint(conn, run_id, "S9"))


if __name__ == "__main__":
    unittest.main()

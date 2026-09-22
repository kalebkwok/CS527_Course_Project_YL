"""§12 — cli: `run --dry-run` end-to-end, idempotence, report table."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from demandtest import cli, db

from tests._util import MINI_INDEX, MINI_REPO, MINI_TASKS


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self._tmp.name) / "runs.db")
        self.run_args = [
            "run", "--db", self.db_path, "--repo", "mini", "--system", "demandtest",
            "--model", "dry", "--index", str(MINI_INDEX), "--dry-run",
        ]

    def tearDown(self):
        self._tmp.cleanup()

    def _main(self, argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(argv)
        return rc, out.getvalue()

    def _conn(self):
        conn = db.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def _prepare(self):
        self.assertEqual(self._main(["init-db", "--db", self.db_path])[0], 0)
        self.assertEqual(self._main(["add-repo", "--db", self.db_path, "--name", "mini",
                                     "--path", str(MINI_REPO)])[0], 0)
        self.assertEqual(self._main(["import-tasks", "--db", self.db_path, "--repo", "mini",
                                     "--tasks", str(MINI_TASKS)])[0], 0)

    def test_dry_run_produces_one_done_run_with_checkpoints_s1_to_s4(self):
        self._prepare()
        rc, output = self._main(self.run_args)
        self.assertEqual(rc, 0)
        self.assertIn("1 executed", output)

        conn = self._conn()
        runs = conn.execute("SELECT * FROM runs").fetchall()
        self.assertEqual(len(runs), 1)
        run = runs[0]
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["system"], "demandtest")
        self.assertEqual(len(run["config_hash"]), 12)

        stages = {r["stage"] for r in conn.execute("SELECT stage FROM checkpoints WHERE run_id=?", (run["id"],))}
        self.assertEqual(stages, {"S1", "S2", "S3", "S4"})  # no S5 in a dry run

        s1 = db.get_checkpoint(conn, run["id"], "S1")
        self.assertEqual([n["key"] if "key" in n else f"{n['kind']}:{n['type']}:{n['detail']}"
                          for n in s1["demands"]],
                         ["receiver:com.mini.Foo:", "arg:com.mini.Bar:0", "arg:int:1",
                          "setup:com.mini.Store:store", "oracle:java.io.IOException:exception",
                          "oracle:com.mini.Foo:state", "idiom:junit5:junit:mockito:"])
        s2 = db.get_checkpoint(conn, run["id"], "S2")
        self.assertEqual(s2["status"], "sufficient")
        self.assertGreaterEqual(len(s2["trace"]), 1)
        s4 = db.get_checkpoint(conn, run["id"], "S4")
        self.assertIn("S4", stages)
        self.assertTrue(s4["src"].startswith("package com.mini;"))
        self.assertEqual(s4["target_hit"], 0)  # the canned dry-run test never calls process(...)

        # accounting is derived from the ledger, never written by hand (§6)
        results = conn.execute("SELECT * FROM results WHERE run_id=?", (run["id"],)).fetchone()
        file_rows = conn.execute("SELECT path FROM file_access WHERE run_id=?", (run["id"],)).fetchall()
        self.assertEqual(results["inspected_files"], len(file_rows))
        self.assertGreaterEqual(results["inspected_files"], 3)
        self.assertIn("src/main/java/com/mini/Foo.java", {r["path"] for r in file_rows})
        self.assertEqual(results["n_llm_calls"], 1)  # exactly one S3 call; S5 skipped
        self.assertGreater(results["prompt_tokens"], 0)
        self.assertIn("dry-run", results["notes"] or "")
        self.assertEqual(results["target_hit"], 0)

    def test_repeat_creates_a_distinct_run_for_the_variance_protocol(self):
        self._prepare()
        self._main(self.run_args)
        rc, output = self._main(self.run_args + ["--repeat", "1"])
        self.assertEqual(rc, 0)
        self.assertIn("1 executed", output)
        conn = self._conn()
        hashes = [r[0] for r in conn.execute("SELECT config_hash FROM runs ORDER BY id")]
        self.assertEqual(len(hashes), 2)
        self.assertNotEqual(hashes[0], hashes[1])

    def test_refine_rejects_more_than_two_rounds(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli._build_parser().parse_args(self.run_args + ["--refine", "3"])

    def test_second_run_is_a_no_op(self):
        self._prepare()
        self._main(self.run_args)
        rc, output = self._main(self.run_args)
        self.assertEqual(rc, 0)
        self.assertIn("1 skipped", output)
        conn = self._conn()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0], 1)

    def test_force_reruns_and_keeps_one_run_row(self):
        self._prepare()
        self._main(self.run_args)
        self._main(self.run_args + ["--force"])
        conn = self._conn()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)

    def test_report_prints_markdown_tables(self):
        self._prepare()
        self._main(self.run_args)
        rc, output = self._main(["report", "--db", self.db_path])
        self.assertEqual(rc, 0)
        self.assertIn("| system |", output)
        self.assertIn("demandtest", output)
        self.assertIn("tokens_per_task", output)
        self.assertIn("pass_rate", output)
        self.assertIn("aligned_pass_rate", output)
        self.assertIn("Budget curve", output)
        self.assertIn("cum_tokens", output)

    def test_run_without_index_is_rejected(self):
        self._prepare()
        rc, _ = self._main(["run", "--db", self.db_path, "--repo", "mini", "--system", "demandtest",
                            "--model", "dry", "--dry-run"])
        self.assertEqual(rc, 2)

    def test_unknown_repo_is_rejected(self):
        self._main(["init-db", "--db", self.db_path])
        rc, _ = self._main(["run", "--db", self.db_path, "--repo", "nope", "--system", "demandtest",
                            "--model", "dry", "--index", str(MINI_INDEX), "--dry-run"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

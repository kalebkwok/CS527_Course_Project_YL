"""§12 — parse_trajectory: an OpenHands trajectory fixture yields the expected ledger rows."""
from __future__ import annotations

import unittest
from pathlib import Path

from demandtest import db

from baselines.openhands import parse_trajectory
from tests._util import FIXTURES, make_task_row, memory_conn

TRAJECTORY = FIXTURES / "openhands_trajectory.jsonl"


class ParseTrajectoryTest(unittest.TestCase):
    def _conn(self):
        conn = memory_conn()
        self.addCleanup(conn.close)
        return conn

    def _run_id(self, conn) -> int:
        repo_id = db.add_repo(conn, "mini", "/tmp/mini")
        task_id = make_task_row(conn, repo_id)
        return db.start_run(conn, task_id, "openhands", "m", {"system": "openhands"})

    def test_expected_number_of_llm_calls_and_distinct_file_accesses(self):
        conn = self._conn()
        run_id = self._run_id(conn)
        summary = parse_trajectory.run(conn, run_id, str(TRAJECTORY), model="m")

        rows = conn.execute("SELECT seq, prompt_tokens, completion_tokens FROM llm_calls ORDER BY seq").fetchall()
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["seq"] for r in rows], [1, 2, 3])
        self.assertEqual([r["prompt_tokens"] for r in rows], [900, 1400, 1500])
        self.assertEqual([r["completion_tokens"] for r in rows], [60, 120, 80])

        paths = {r["path"] for r in conn.execute("SELECT path FROM file_access WHERE run_id=?", (run_id,))}
        self.assertEqual(paths, {
            "src/main/java/com/mini/Foo.java",   # read twice → one row
            "src/main/java/com/mini/Bar.java",   # matched by grep
            "src/test/java/com/mini/FooTest.java",
        })
        # the empty directory listing is not an inspected file (§6 rule 3)
        self.assertNotIn("src/test/java/com/mini", paths)

        self.assertEqual(summary["wall_ms"], 44000)
        self.assertEqual(summary["n_llm_calls"], 3)
        self.assertEqual(summary["test_path"], "src/test/java/com/mini/FooGeneratedTest.java")
        self.assertIn("class FooGeneratedTest", summary["test_source"])

    def test_finish_run_derives_baseline_columns(self):
        conn = self._conn()
        run_id = self._run_id(conn)
        summary = parse_trajectory.run(conn, run_id, str(TRAJECTORY), model="m")
        db.finish_run(conn, run_id, "done", compiled=1, passed=0, wall_ms=summary["wall_ms"])
        row = conn.execute("SELECT * FROM results WHERE run_id=?", (run_id,)).fetchone()
        self.assertEqual(row["prompt_tokens"], 3800)
        self.assertEqual(row["completion_tokens"], 260)
        self.assertEqual(row["n_llm_calls"], 3)
        self.assertEqual(row["inspected_files"], 3)
        self.assertEqual(row["wall_ms"], 44000)

    def test_missing_usage_is_recorded_as_minus_one(self):
        event = {"action": "read", "args": {"path": "a.java"}}
        self.assertFalse(parse_trajectory.is_completion(event))
        events = [{"action": "message"}, {"action": "read", "args": {"path": "a.java"}},
                  {"action": "read", "args": {"path": "a.java"}}]
        self.assertEqual([e["action"] for e in events], ["message", "read", "read"])
        self.assertEqual(parse_trajectory.event_paths(events[1]), ["a.java"])

    def test_listing_with_content_counts(self):
        with_content = {"action": "list", "args": {"path": "src/test/java"},
                        "observation": {"content": "FooTest.java\nBarTest.java"}}
        self.assertEqual(parse_trajectory.event_paths(with_content), ["src/test/java"])
        without = {"action": "list", "args": {"path": "src/test/java"}, "observation": {"content": "  "}}
        self.assertEqual(parse_trajectory.event_paths(without), [])

    def test_json_array_form_is_accepted(self):
        import json
        import tempfile
        events = parse_trajectory.load_events(str(TRAJECTORY))
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(events, fh)
            path = fh.name
        try:
            self.assertEqual(len(parse_trajectory.load_events(path)), len(events))
        finally:
            Path(path).unlink()


if __name__ == "__main__":
    unittest.main()

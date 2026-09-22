"""§12 — metrics: target-hit / aligned-pass / call-success columns and the budget curve (§11)."""
from __future__ import annotations

import unittest

from demandtest import db, metrics

from tests._util import make_task_row, memory_conn


class MetricsTest(unittest.TestCase):
    def _seed(self, conn, outcomes):
        """outcomes: list of (passed, target_hit, prompt_tokens, n_calls)."""
        repo_id = db.add_repo(conn, "mini", "/tmp/mini")
        for i, (passed, hit, tokens, calls) in enumerate(outcomes):
            task_id = make_task_row(conn, repo_id, ref_test_id=f"T#t{i}", focal_sig=f"m{i}()")
            run_id = db.start_run(conn, task_id, "demandtest", "m", {"i": 0})
            for c in range(calls):
                db.log_llm_call(conn, run_id, "generate", "m", tokens, 10, 1, f"p{i}{c}", f"r{i}{c}")
            db.finish_run(conn, run_id, "done", compiled=1, passed=passed, n_asserts=1, wall_ms=10, target_hit=hit,
                          packet_status="sufficient" if i % 2 == 0 else "fallback")

    def test_summary_exposes_alignment_and_call_success(self):
        conn = memory_conn()
        self.addCleanup(conn.close)
        self._seed(conn, [(1, 1, 100, 1), (1, 0, 100, 2), (0, 0, 100, 1), (0, 1, 100, 1)])
        rows = metrics.summarize(conn)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["pass_rate"], 0.5)
        self.assertAlmostEqual(row["target_hit_rate"], 0.5)
        self.assertAlmostEqual(row["aligned_pass_rate"], 0.25)   # passes AND checks the intention
        self.assertAlmostEqual(row["call_success"], 2 / 5)       # 2 passing tests over 5 LLM calls
        table = metrics.format_table(rows)
        for col in ("target_hit_rate", "aligned_pass_rate", "call_success"):
            self.assertIn(col, table)

    def test_budget_curve_is_cumulative_and_sampled(self):
        conn = memory_conn()
        self.addCleanup(conn.close)
        self._seed(conn, [(1, 1, 100, 1)] * 5 + [(0, 0, 100, 1)] * 20)
        curve = metrics.budget_curve(conn, points=5)
        self.assertEqual(len(curve), 5)
        self.assertEqual([c["runs"] for c in curve], [5, 10, 15, 20, 25])
        self.assertEqual([c["cum_tokens"] for c in curve], [550, 1100, 1650, 2200, 2750])
        self.assertEqual([c["cum_passed"] for c in curve], [5, 5, 5, 5, 5])
        self.assertAlmostEqual(curve[-1]["pass_rate"], 0.2)
        self.assertIn("cum_tokens", metrics.format_table(curve, metrics.CURVE_COLUMNS))

    def test_by_status_stratifies_outcomes(self):
        conn = memory_conn()
        self.addCleanup(conn.close)
        self._seed(conn, [(1, 1, 100, 1), (0, 0, 100, 1), (1, 1, 100, 1), (0, 0, 100, 1)])
        rows = {r["packet_status"]: r for r in metrics.by_status(conn)}
        self.assertEqual(set(rows), {"sufficient", "fallback"})
        self.assertAlmostEqual(rows["sufficient"]["pass_rate"], 1.0)
        self.assertAlmostEqual(rows["fallback"]["pass_rate"], 0.0)
        self.assertIn("packet_status", metrics.format_table(list(rows.values()), metrics.STATUS_COLUMNS))

    def test_budget_curve_empty_ledger(self):
        conn = memory_conn()
        self.addCleanup(conn.close)
        self.assertEqual(metrics.budget_curve(conn), [])
        self.assertEqual(metrics.format_table([]), "_no rows_")


if __name__ == "__main__":
    unittest.main()

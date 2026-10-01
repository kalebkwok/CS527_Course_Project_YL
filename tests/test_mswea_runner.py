"""mini-swe-agent runner (baselines/mswea/run.py): leakage-safe checkout, generated-test detection, ledger.

The end-to-end class needs mini-swe-agent (`pip install -r baselines/mswea/requirements.txt`) and is
skipped without it; it drives the real DefaultAgent with mini-swe-agent's deterministic model, so no
API call is made."""
from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from baselines.mswea import run as runner
from demandtest import demand, execute, replay
from demandtest.index import TestInfo

from tests._util import FIXTURES, MINI_REPO, REF_TEST_ID, load_mini_index, memory_conn, make_task_row, mini_task

HAVE_MSWEA = importlib.util.find_spec("minisweagent") is not None
FOO_TEST = "src/test/java/com/mini/FooTest.java"
# the fixture file's real range of testProcessRoundTrips (@Test through the closing brace)
REF = TestInfo(id=REF_TEST_ID, file=FOO_TEST, class_="com.mini.FooTest", method="testProcessRoundTrips",
               line_start=10, line_end=15)


class CheckoutTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_remove_method_takes_the_attached_javadoc_too(self):
        path = self.tmp / "T.java"
        path.write_text("class T {\n    int x;\n\n    /**\n     * Checks that x is zero.\n     */\n"
                        "    @Test\n    void t() {\n        assert x == 0;\n    }\n}\n", encoding="utf-8")
        runner.remove_method(path, 7, 10)
        self.assertEqual(path.read_text(encoding="utf-8"), "class T {\n    int x;\n\n}\n")

    def test_checkout_hides_the_reference_test_git_history_and_build_outputs(self):
        src = self.tmp / "src_repo"
        shutil.copytree(MINI_REPO, src)
        (src / ".git").mkdir()
        (src / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (src / "target" / "test-classes").mkdir(parents=True)
        (src / "target" / "test-classes" / "FooTest.class").write_bytes(b"\xca\xfe")
        dest = self.tmp / "work"
        runner.prepare_checkout(str(src), dest, REF)
        text = (dest / FOO_TEST).read_text(encoding="utf-8")
        self.assertNotIn("testProcessRoundTrips", text)
        self.assertIn("private Store store = new Store();", text)  # class-level fixture stays (§9)
        self.assertFalse((dest / ".git").exists())
        self.assertFalse((dest / "target").exists())
        self.assertIn("testProcessRoundTrips", (src / FOO_TEST).read_text(encoding="utf-8"))  # original intact

    def test_reference_test_without_a_line_range_is_refused(self):
        index = load_mini_index()
        index.tests[0].line_start = None
        with self.assertRaises(ValueError):
            runner.reference_test(index, index.tests[0].id)

    def test_generated_test_prefers_a_changed_test_that_calls_the_focal_method(self):
        root = self.tmp / "r"
        shutil.copytree(MINI_REPO, root)
        before = runner.snapshot(root)
        pkg = root / "src/test/java/com/mini"
        (pkg / "Scratch.java").write_text("class Scratch {}\n")
        time.sleep(0.01)
        (pkg / "FooGenTest.java").write_text("class FooGenTest { @Test void t() { new Foo().process(null, 1); } }\n")
        (root / "src/main/java/com/mini/Foo.java").write_text("// edited\n")
        (root / "notes.txt").write_text("x\n")
        test, other = runner.generated_test(before, runner.snapshot(root), "src/test/java", mini_task(), root)
        self.assertEqual(test, "src/test/java/com/mini/FooGenTest.java")
        self.assertEqual(other, ["notes.txt", "src/main/java/com/mini/Foo.java"])

    def test_template_vars_render_the_intention_verbatim(self):
        tv = runner.template_vars(mini_task(), "src/test/java")
        self.assertEqual((tv["test_dir"], tv["test_package"]), ("src/test/java/com/mini", "com.mini"))
        self.assertEqual(tv["focal_sig"], "process(Bar,int)")
        self.assertEqual(tv["preconditions"], "bar is a validated Bar; n is the delta to apply")


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.conn = memory_conn()
        self.addCleanup(self.conn.close)
        task_id = make_task_row(self.conn, runner.db.add_repo(self.conn, "mini", str(MINI_REPO)))
        self.run_id = runner.db.start_run(self.conn, task_id, runner.SYSTEM, "test/model", {"x": 1})
        self.traj = replay.load_trajectory(str(FIXTURES / "mswea_trajectory.json"))

    def test_ingest_writes_one_call_per_billed_step_and_every_inspected_file(self):
        meta = runner.ingest(self.conn, self.run_id, self.traj, "test/model")
        rows = self.conn.execute("SELECT prompt_tokens + completion_tokens AS t, latency_ms FROM llm_calls "
                                 "WHERE run_id=? ORDER BY seq", (self.run_id,)).fetchall()
        self.assertEqual([r["t"] for r in rows], [1050, 1260, 1570, 1880, 2190, 2500, 3200, 3330, 3420])
        # call timestamps are 0.5 s after the previous observation; the FormatError call has none;
        # the last call follows call 7's observation by 1.5 s; the first call has no start time here.
        self.assertEqual([r["latency_ms"] for r in rows], [0, 500, 500, 500, 500, 500, 500, 0, 1500])
        files = {r[0] for r in self.conn.execute("SELECT path FROM file_access WHERE run_id=?", (self.run_id,))}
        self.assertEqual(files, {"src/main/java/com/mini/Foo.java", "src/main/java/com/mini/Store.java",
                                 "src/test/java/com/mini/TestUtil.java", "src/main/java/com/mini/Bar.java",
                                 "src/main/java/com/mini/WheelImpl.java", "src/main/java/com/mini/Wheel.java"})
        self.assertEqual((meta["n_calls"], meta["providers"]), (9, {"?": 9}))
        self.assertEqual(meta["model_ms"], 4500)

    def test_record_uses_our_verdict_and_target_hit(self):
        task = mini_task()
        index = load_mini_index()
        with index.excluded_scope({REF_TEST_ID}):
            demands = demand.compute_demands(index, task)
        src = ("class FooGenTest { @Test void t() throws Exception {\n"
               "  assertThrows(IOException.class, () -> new Foo().process(Bar.of(\"a\"), -1));\n"
               "  assertEquals(1, new Foo().size()); } }\n")
        out = runner.Outcome(traj=self.traj, workdir="/work/mini", exit_status="Submitted", agent_wall_ms=4200,
                             test_path="src/test/java/com/mini/FooGenTest.java", test_source=src,
                             verdict=execute.Verdict(compiled=1, passed=1, wall_ms=900))
        runner.record(self.conn, self.run_id, out, task, demands, "test/model", None)
        res = self.conn.execute("SELECT * FROM results WHERE run_id=?", (self.run_id,)).fetchone()
        self.assertEqual((res["compiled"], res["passed"], res["wall_ms"], res["n_llm_calls"]), (1, 1, 4200, 9))
        self.assertEqual(res["inspected_files"], 6)
        self.assertEqual(res["prompt_tokens"] + res["completion_tokens"], 20400)
        self.assertEqual(res["n_asserts"], 2)
        self.assertEqual(res["target_hit"], 1)
        cp = runner.db.get_checkpoint(self.conn, self.run_id, "AGENT")
        self.assertEqual((cp["exit_status"], cp["verify_wall_ms"]), ("Submitted", 900))

    def test_no_generated_test_is_recorded_as_a_failure(self):
        out = runner.Outcome(traj=self.traj, workdir="/work/mini", exit_status="LimitsExceeded", agent_wall_ms=10)
        runner.record(self.conn, self.run_id, out, mini_task(), [], "test/model", None)
        res = self.conn.execute("SELECT compiled, passed, notes FROM results WHERE run_id=?", (self.run_id,)).fetchone()
        self.assertEqual((res["compiled"], res["passed"]), (0, 0))
        self.assertTrue(res["notes"].startswith("no-test"))


@unittest.skipUnless(HAVE_MSWEA, "mini-swe-agent not installed (pip install -r baselines/mswea/requirements.txt)")
class EndToEndTest(unittest.TestCase):
    """Real DefaultAgent + LocalEnvironment, scripted by mini-swe-agent's DeterministicToolcallModel."""

    def _call(self, i: int, command: str) -> dict:
        from minisweagent.models.test_models import make_toolcall_output
        tc = [{"id": f"c{i}", "type": "function",
               "function": {"name": "bash", "arguments": json.dumps({"command": command})}}]
        out = make_toolcall_output(None, tc, [{"command": command, "tool_call_id": f"c{i}"}])
        out["extra"].update(cost=0.0, response={"id": f"r{i}", "provider": "TestProvider",
                                                "usage": {"prompt_tokens": 100 * i, "completion_tokens": 10}})
        return out

    def test_scripted_agent_run_is_saved_found_and_ingested(self):
        from minisweagent.models.test_models import DeterministicToolcallModel
        test_src = ("package com.mini;\nimport org.junit.jupiter.api.Test;\n"
                    "class FooGenTest { @Test void t() throws Exception { new Foo().process(Bar.of(\"a\"), 1); } }")
        model = DeterministicToolcallModel(outputs=[
            self._call(1, "cat src/main/java/com/mini/Foo.java"),
            self._call(2, "grep -n testProcessRoundTrips src/test/java/com/mini/FooTest.java || echo hidden"),
            self._call(3, f"cat <<'EOF' > src/test/java/com/mini/FooGenTest.java\n{test_src}\nEOF"),
            self._call(4, "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"),
        ])
        cfg = runner.load_config(runner.CONFIG)
        task = mini_task()
        with tempfile.TemporaryDirectory() as tmp:
            traj_path = Path(tmp) / "traj" / f"{replay.task_slug(task.ref_test_id)}.traj.json"
            out = runner.run_task(task, REF, str(MINI_REPO), cfg, model, traj_path, Path(tmp) / "work",
                                  "src/test/java", 60, None, verify=False, keep_workdir=True)
            self.assertEqual(out.exit_status, "Submitted")
            self.assertEqual(out.test_path, "src/test/java/com/mini/FooGenTest.java")
            self.assertIn("class FooGenTest", out.test_source)
            self.assertEqual(out.changed_other, [])
            self.assertTrue(traj_path.is_file())
            observations = [m for m in out.traj["messages"] if m.get("role") == "tool"]
            self.assertIn("hidden", observations[1]["content"])  # the reference method is gone (§9)
            self.assertIn("Expected results: process throws IOException", out.traj["messages"][1]["content"])

            conn = memory_conn()
            self.addCleanup(conn.close)
            task_id = make_task_row(conn, runner.db.add_repo(conn, "mini", str(MINI_REPO)))
            run_id = runner.db.start_run(conn, task_id, runner.SYSTEM, "test/model", {})
            meta = runner.ingest(conn, run_id, json.loads(traj_path.read_text()), "test/model")
            self.assertEqual((meta["n_calls"], meta["providers"]), (4, {"TestProvider": 4}))
            files = [r[0] for r in conn.execute("SELECT path FROM file_access WHERE run_id=?", (run_id,))]
            self.assertEqual(sorted(files), ["src/main/java/com/mini/Foo.java", FOO_TEST])
            steps = replay.steps_from_mswea(json.loads(traj_path.read_text()))
            self.assertEqual(steps[0].reads, ["src/main/java/com/mini/Foo.java"])


if __name__ == "__main__":
    unittest.main()

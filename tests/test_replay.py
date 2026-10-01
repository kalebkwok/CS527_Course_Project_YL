"""Gate 1 replay (docs/PAPER_PLAN.md RQ1): shell-read parsing, mini-swe-agent steps, Σ after every step."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from demandtest import cli, demand, replay
from demandtest.replay import shell_reads, steps_from_mswea

from tests._util import FIXTURES, MINI_INDEX, MINI_TASKS, REF_TEST_ID, load_mini_index, mini_task

TRAJ = FIXTURES / "mswea_trajectory.json"
FOO = "src/main/java/com/mini/Foo.java"
BAR = "src/main/java/com/mini/Bar.java"
STORE = "src/main/java/com/mini/Store.java"
TEST_UTIL = "src/test/java/com/mini/TestUtil.java"
FOO_TEST = "src/test/java/com/mini/FooTest.java"
WHEEL_IMPL = "src/main/java/com/mini/WheelImpl.java"
WHEEL = "src/main/java/com/mini/Wheel.java"
# provider usage per call in the fixture: prompt + completion
CALL_TOKENS = [1050, 1260, 1570, 1880, 2190, 2500, 3200, 3330, 3420]


class ShellReadsTest(unittest.TestCase):
    def reads(self, command, output="", root="/r", known=None):
        return shell_reads(command, output, root, known).reads

    def hits(self, command, output="", root="/r"):
        return shell_reads(command, output, root).grep_hits

    def test_readers_print_their_file_operands(self):
        self.assertEqual(self.reads("cat a/B.java c/D.java"), ["a/B.java", "c/D.java"])
        self.assertEqual(self.reads("head -n 50 src/X.java"), ["src/X.java"])
        self.assertEqual(self.reads("tail -20 src/X.java"), ["src/X.java"])
        self.assertEqual(self.reads("nl -ba src/X.java | sed -n '10,30p'"), ["src/X.java"])
        self.assertEqual(self.reads("FOO=1 cat ./src/A.java"), ["src/A.java"])

    def test_sed_and_awk_skip_the_script_and_in_place_edits(self):
        self.assertEqual(self.reads("sed -n '1,40p' src/X.java"), ["src/X.java"])
        self.assertEqual(self.reads("sed -n -e '1p' src/X.java"), ["src/X.java"])
        self.assertEqual(self.reads("sed -i 's/a/b/' src/X.java"), [])
        self.assertEqual(self.reads("awk -F: '{print $1}' src/X.java"), ["src/X.java"])

    def test_paths_resolve_against_cd_and_the_repo_root(self):
        self.assertEqual(self.reads("cd src && cat main/A.java"), ["src/main/A.java"])
        self.assertEqual(self.reads("cat /r/src/A.java"), ["src/A.java"])
        self.assertEqual(self.reads("cat /elsewhere/A.java"), [])  # outside the repository
        self.assertEqual(self.reads("cat ./src/A.java", root=""), ["src/A.java"])

    def test_writes_heredocs_and_fd_redirects_are_not_reads(self):
        self.assertEqual(self.reads("cat > src/T.java <<'EOF'\ncat src/Hidden.java\nEOF"), [])
        self.assertEqual(self.reads("echo hi >&2; cat src/A.java 2>&1 > /tmp/out.txt"), ["src/A.java"])
        self.assertEqual(self.reads("python3 x.py < input/data.txt"), ["input/data.txt"])
        self.assertEqual(self.reads("mvn -q test 2>&1 | tail -n 20"), [])
        self.assertEqual(self.reads("ls -la src/main && find src -name '*.java'"), [])

    def test_globs_expand_against_known_files(self):
        known = {"src/A.java", "src/B.java", "test/C.java"}
        self.assertEqual(self.reads("cat src/*.java", known=known), ["src/A.java", "src/B.java"])
        self.assertEqual(self.reads("cat src/*.java"), [])  # nothing known: no guess

    def test_grep_hits_are_separate_from_reads(self):
        fr = shell_reads("grep -n foo src/X.java", "12:foo\n", "/r")
        self.assertEqual((fr.reads, fr.grep_hits), ([], ["src/X.java"]))
        self.assertEqual(self.hits("grep -rln Foo src", "src/A.java\nsrc/b/B.java\n"), ["src/A.java", "src/b/B.java"])
        self.assertEqual(self.hits('grep -rn "x\\|y" src', "src/A.java:3:x\n--\nsrc/B.java:9:y\n"),
                         ["src/A.java", "src/B.java"])
        self.assertEqual(self.hits("find src -name '*.java' | xargs grep -l Foo", "src/A.java\n"), ["src/A.java"])

    def test_untokenizable_command_adds_nothing_and_is_flagged(self):
        fr = shell_reads('cat "unbalanced src/A.java', "", "/r")
        self.assertFalse(fr.parsed)
        self.assertEqual(fr.reads, [])


class StepsTest(unittest.TestCase):
    def setUp(self):
        self.traj = replay.load_trajectory(str(TRAJ))

    def test_one_step_per_billed_call_with_provider_usage(self):
        steps = steps_from_mswea(self.traj)
        self.assertEqual([s.n for s in steps], list(range(1, 10)))  # 8 assistant calls + 1 FormatError call
        self.assertEqual([s.prompt_tokens + s.completion_tokens for s in steps], CALL_TOKENS)
        self.assertEqual(steps[7].commands, [])  # the FormatError call has no action

    def test_reads_per_step_use_the_environment_cwd_as_root(self):
        steps = steps_from_mswea(self.traj)
        self.assertEqual([s.reads for s in steps], [
            [], [FOO], [], [BAR], [TEST_UTIL], [WHEEL_IMPL, WHEEL], [], [], []])
        self.assertEqual(steps[2].grep_hits, [STORE, TEST_UTIL])
        self.assertEqual(sum(s.unparsed for s in steps), 0)

    def test_textbased_observations_are_paired_too(self):
        traj = {"messages": [
            {"role": "assistant", "content": "```mswea_bash_command\ncat src/A.java\n```",
             "extra": {"actions": [{"command": "cat src/A.java"}],
                       "response": {"usage": {"prompt_tokens": 10, "completion_tokens": 2}}}},
            {"role": "user", "content": "<output>...</output>", "extra": {"raw_output": "...", "returncode": 0}},
        ]}
        steps = steps_from_mswea(traj, root="")
        self.assertEqual((len(steps), steps[0].reads), (1, ["src/A.java"]))


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.index = load_mini_index()
        self.task = mini_task()
        self.demands = demand.compute_demands(self.index, self.task)
        self.steps = steps_from_mswea(replay.load_trajectory(str(TRAJ)))

    def run_replay(self, **kw):
        with self.index.excluded_scope({REF_TEST_ID}):
            return replay.replay(self.index, self.task, self.steps, demands=self.demands, **kw)

    def test_sigma_first_holds_once_bar_and_a_store_helper_are_read(self):
        r = self.run_replay()
        # Foo (step 2) supplies the receiver and the state observable, Bar.of (step 4) the argument,
        # TestUtil.emptyStore (step 5) the Store setup; Store.java itself is never read.
        self.assertEqual(r.sigma_step, 5)
        self.assertEqual(r.steps_after_sigma, 4)
        self.assertEqual((r.files_at_sigma, r.files_total), (3, 5))
        self.assertEqual(r.tokens_total, sum(CALL_TOKENS))
        self.assertEqual(r.tokens_after_sigma, sum(CALL_TOKENS[5:]))
        self.assertEqual(r.share_after_sigma, round(sum(CALL_TOKENS[5:]) / sum(CALL_TOKENS), 4))
        self.assertEqual(r.open_at_end, [])
        self.assertEqual([t["open"] for t in r.trace], [4, 2, 2, 1, 0, 0, 0, 0, 0])
        self.assertEqual([t["sigma"] for t in r.trace], [False] * 4 + [True] * 5)  # monotone

    def test_grep_hits_sensitivity_moves_sigma_earlier(self):
        r = self.run_replay(with_grep_hits=True)
        self.assertEqual(r.sigma_step, 4)  # Store.java and TestUtil.java were grep hits at step 3
        self.assertEqual(r.tokens_after_sigma, sum(CALL_TOKENS[4:]))

    def test_given_files_can_satisfy_sigma_before_the_first_call(self):
        r = self.run_replay(given_files=[FOO, BAR, TEST_UTIL])
        self.assertEqual((r.sigma_step, r.steps_after_sigma, r.share_after_sigma), (0, 9, 1.0))

    def test_missing_usage_gives_no_token_totals(self):
        self.steps[3].prompt_tokens = -1
        r = self.run_replay()
        self.assertEqual(r.sigma_step, 5)
        self.assertIsNone(r.tokens_total)
        self.assertIsNone(r.share_after_sigma)

    def test_reference_test_fixture_never_satisfies_sigma(self):
        files = {FOO, BAR, FOO_TEST}  # FooTest's Store fixture is the held-out reference test (§9)
        with self.index.excluded_scope({REF_TEST_ID}):
            ok, unresolved = replay.sigma_over(self.index, self.task, self.demands, files)
        self.assertFalse(ok)
        self.assertEqual([n.key() for n in unresolved], ["setup:com.mini.Store:store"])
        ok, _ = replay.sigma_over(self.index, self.task, self.demands, files)  # visible: the fixture counts
        self.assertTrue(ok)

    def test_summary_pools_tokens_over_tasks(self):
        r = self.run_replay()
        s = replay.summarize([r])
        self.assertEqual((s["tasks"], s["sigma_reached"], s["sigma_rate"]), (1, 1, 1.0))
        self.assertEqual(s["pooled_share_after"], round(sum(CALL_TOKENS[5:]) / sum(CALL_TOKENS), 3))


class ReplayCliTest(unittest.TestCase):
    def test_replay_command_writes_one_line_per_trajectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(TRAJ, Path(tmp) / f"{replay.task_slug(REF_TEST_ID)}.traj.json")
            out_path = Path(tmp) / "replay.jsonl"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                rc = cli.main(["replay", "--index", str(MINI_INDEX), "--tasks", str(MINI_TASKS),
                               "--traj-dir", tmp, "--out", str(out_path)])
            self.assertEqual(rc, 0)
            rows = [json.loads(line) for line in out_path.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["task"], rows[0]["sigma_step"]), (REF_TEST_ID, 5))
        self.assertIn("sigma_rate", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

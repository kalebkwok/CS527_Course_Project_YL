"""§12 — execute: test placement and diagnostics trimming (§2.8.5–2.8.6)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from demandtest import execute

from tests._util import MAVEN_FAILURE_LOG, mini_task

FAILURE_LOG = """[INFO] Running com.mini.FooTestGeneratedTest
[ERROR] Tests run: 1, Failures: 1, Errors: 0, Skipped: 0, Time elapsed: 0.05 s <<< FAILURE! -- in com.mini.FooTestGeneratedTest
[ERROR] com.mini.FooTestGeneratedTest.validatesIntention -- Time elapsed: 0.05 s <<< FAILURE!
org.opentest4j.AssertionFailedError: expected: <2> but was: <3>
\tat com.mini.FooTestGeneratedTest.validatesIntention(FooTestGeneratedTest.java:12)
\tat java.base/java.lang.reflect.Method.invoke(Method.java:568)
\tat java.base/java.util.ArrayList.forEach(ArrayList.java:1511)
[INFO] BUILD FAILURE
"""


class ExecuteTest(unittest.TestCase):
    def test_write_test_places_file_under_focal_package_path(self):
        with tempfile.TemporaryDirectory() as repo:
            src = "package com.mini;\n\nclass FooTestGeneratedTest {\n}\n"
            path = execute.write_test(repo, mini_task(), src)
            self.assertEqual(path, Path(repo) / "src/test/java/com/mini/FooTestGeneratedTest.java")
            self.assertTrue(path.is_file())
            self.assertIn("package com.mini;", path.read_text(encoding="utf-8"))
            execute.remove_test(repo, path)
            self.assertFalse(path.exists())

    def test_trim_failure_extracts_first_javac_error_block(self):
        log = Path(MAVEN_FAILURE_LOG).read_text(encoding="utf-8")
        trimmed = execute._trim_failure(log)
        lines = trimmed.splitlines()
        self.assertLessEqual(len(lines), 8)
        self.assertIn("COMPILATION ERROR", lines[0])
        self.assertIn("cannot find symbol", trimmed)

    def test_trim_failure_extracts_first_failure_line_and_frames(self):
        trimmed = execute._trim_failure(FAILURE_LOG)
        self.assertIn("<<< FAILURE!", trimmed)
        self.assertIn("AssertionFailedError", trimmed)
        self.assertLessEqual(len(trimmed.splitlines()), 7)
        self.assertIn("at com.mini.FooTestGeneratedTest.validatesIntention", trimmed)

    def test_parse_verdict_compilation_error(self):
        log = Path(MAVEN_FAILURE_LOG).read_text(encoding="utf-8")
        compiled, passed, diagnostics = execute.parse_verdict(log, exit_code=1)
        self.assertFalse(compiled)
        self.assertFalse(passed)
        self.assertIn("COMPILATION ERROR", diagnostics)

    def test_parse_verdict_success(self):
        log = "[INFO] Tests run: 2, Failures: 0, Errors: 0, Skipped: 0\n[INFO] BUILD SUCCESS\n"
        compiled, passed, diagnostics = execute.parse_verdict(log, exit_code=0)
        self.assertTrue(compiled)
        self.assertTrue(passed)
        self.assertEqual(diagnostics, "")

    def test_parse_verdict_test_failure(self):
        compiled, passed, diagnostics = execute.parse_verdict(FAILURE_LOG, exit_code=1)
        self.assertTrue(compiled)  # it compiled, the assertion failed
        self.assertFalse(passed)
        self.assertIn("AssertionFailedError", diagnostics)

    def test_parse_verdict_timeout(self):
        compiled, passed, diagnostics = execute.parse_verdict("", exit_code=-1, timed_out=True)
        self.assertFalse(compiled)
        self.assertFalse(passed)
        self.assertIn("timeout", diagnostics)

    def test_count_asserts(self):
        src = ('assertEquals(1, 1);\nassertTrue(true);\nassert x > 0;\n'
               'assertThat(y).isEqualTo(2);\nfoo(1);\n')
        self.assertEqual(execute.count_asserts(src), 4)

    def test_target_hit_requires_focal_call_and_every_oracle(self):
        from demandtest import demand
        from tests._util import load_mini_index
        index, task = load_mini_index(), mini_task()
        demands = demand.compute_demands(index, task)  # oracles: exception + state
        both = ('class T { @Test void t() throws Exception { Foo foo = new Foo();\n'
                'assertThrows(IOException.class, () -> foo.process(Bar.of("a"), -1));\n'
                'assertEquals(1, foo.process(Bar.of("a"), 1)); } }')
        self.assertEqual(execute.target_hit(both, task, demands), 1)
        no_call = 'class T { @Test void t() { assertEquals(2, 1 + 1); } }'
        self.assertEqual(execute.target_hit(no_call, task, demands), 0)
        only_state = 'class T { @Test void t() throws Exception { assertEquals(1, new Foo().process(Bar.of("a"), 1)); } }'
        self.assertEqual(execute.target_hit(only_state, task, demands), 0)  # exception oracle missing
        method_ref = 'class T { @Test void t() { assertThrows(IOException.class, foo::process); assertTrue(true); } }'
        self.assertEqual(execute.target_hit(method_ref, task, demands), 1)

    def test_maven_command_matches_spec(self):
        cmd = execute.maven_command("FooTestGeneratedTest")
        self.assertEqual(cmd[:4], ["mvn", "-q", "-B", "-o"])
        self.assertIn("-Dtest=FooTestGeneratedTest", cmd)
        self.assertIn("-DfailIfNoTests=false", cmd)
        self.assertIn("-Dsurefire.failIfNoSpecifiedTests=false", cmd)
        self.assertEqual(cmd[-1], "test")
        self.assertIn("-pl", execute.maven_command("T", module="core"))


if __name__ == "__main__":
    unittest.main()

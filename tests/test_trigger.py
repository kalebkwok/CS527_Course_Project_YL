"""§12 — trigger: guard collection for throw/return sites (§2.6 section 3 hints)."""
from __future__ import annotations

import unittest

from demandtest import trigger
from demandtest.packet import extract_method_source

from tests._util import focal_source


def _by(ts, kind):
    return [(t.text, t.condition()) for t in ts if t.kind == kind]


class TriggerTest(unittest.TestCase):
    def test_focal_method_exception_and_return(self):
        src = extract_method_source(focal_source(), "process")
        ts = trigger.triggers(src)
        self.assertEqual(_by(ts, "throw"), [('new IOException("")', "n < 0")])
        self.assertEqual(_by(ts, "return"), [("store.size() + n", "")])
        self.assertEqual(trigger.oracle_triggers(src, "exception", "java.io.IOException"), ["triggered when: n < 0"])
        self.assertEqual(trigger.oracle_triggers(src, "return", "int"), ["returns: store.size() + n"])
        self.assertEqual(trigger.oracle_triggers(src, "state", "com.mini.Foo"), [])

    def test_else_if_chain_negates_predecessors(self):
        src = """int f(int x, String s) {
            if (x < 0) {
                throw new IllegalArgumentException("neg");
            } else if (x == 0) {
                return 0;
            } else {
                if (s == null) throw new NullPointerException();
                return x * 2;
            }
        }"""
        ts = trigger.triggers(src)
        self.assertEqual(_by(ts, "throw"), [('new IllegalArgumentException("")', "x < 0"),
                                            ("new NullPointerException()", "!(x < 0 || x == 0) && s == null")])
        self.assertEqual(_by(ts, "return"), [("0", "!(x < 0) && x == 0"), ("x * 2", "!(x < 0 || x == 0)")])
        # the typed filter picks the matching throw site; an unknown type falls back to every site
        self.assertEqual(trigger.oracle_triggers(src, "exception", "java.lang.NullPointerException"),
                         ["triggered when: !(x < 0 || x == 0) && s == null"])
        self.assertEqual(len(trigger.oracle_triggers(src, "exception", "java.lang.RuntimeException")), 2)

    def test_single_statement_if_loops_strings_and_comments(self):
        src = """void g(int n) {
            if (n > 10) throw new IllegalStateException("big"); // comment { with brace
            for (int i = 0; i < n; i++) {
                if (i == 7) throw new RuntimeException("seven");
            }
            String t = "if (fake) throw new X();"; /* throw new Y(); */
            return;
        }"""
        ts = trigger.triggers(src)
        self.assertEqual(_by(ts, "throw"), [('new IllegalStateException("")', "n > 10"),
                                            ('new RuntimeException("")', "inside for (int i = 0; i < n; i++) && i == 7")])
        self.assertEqual(_by(ts, "return"), [("", "")])

    def test_dangling_else_binds_to_innermost_if(self):
        src = "int h(int a, int b) {\n    if (a > 0) if (b > 0) return 1; else return 2;\n    return 3;\n}"
        self.assertEqual(_by(trigger.triggers(src), "return"),
                         [("1", "a > 0 && b > 0"), ("2", "a > 0 && !(b > 0)"), ("3", "")])

    def test_switch_catch_and_opaque_lambda(self):
        sw = """int sw(int k) {
            switch (k) {
                case 1: return 10;
                case 2 -> { throw new IllegalArgumentException(); }
                default: return -1;
            }
        }"""
        self.assertEqual(_by(trigger.triggers(sw), "throw"), [("new IllegalArgumentException()", "switch (k)")])
        self.assertEqual([c for _, c in _by(trigger.triggers(sw), "return")], ["switch (k)", "switch (k)"])
        tr = """void t() throws IOException {
            try { io(); } catch (IOException e) { throw new IOException("wrapped", e); }
        }"""
        self.assertEqual(_by(trigger.triggers(tr), "throw"), [('new IOException("", e)', "caught (IOException e)")])
        lam = """int l(List<Integer> xs) {
            xs.forEach(x -> { if (x < 0) throw new IllegalArgumentException(); });
            return xs.size();
        }"""
        ts = trigger.triggers(lam)
        self.assertEqual(_by(ts, "throw"), [])  # lambda bodies are opaque: no false guard
        self.assertEqual(_by(ts, "return"), [("xs.size()", "")])

    def test_no_body_or_empty_input(self):
        self.assertEqual(trigger.triggers(""), [])
        self.assertEqual(trigger.triggers("abstract int a(int x);"), [])
        self.assertEqual(trigger.oracle_triggers("", "exception", "java.io.IOException"), [])


if __name__ == "__main__":
    unittest.main()

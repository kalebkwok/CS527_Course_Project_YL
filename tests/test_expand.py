"""§12 — expand: recipe selection, sufficiency predicate, budget fallback, trace."""
from __future__ import annotations

import unittest

from demandtest import demand, expand
from demandtest.expand import Context, Resolver

from tests._util import load_mini_index, mini_task


class ExpandTest(unittest.TestCase):
    def setUp(self):
        self.index = load_mini_index()
        self.task = mini_task()
        self.demands = demand.compute_demands(self.index, self.task)

    def test_bar_resolves_to_factory_with_zero_cost(self):
        r = Resolver(self.index).resolve("com.mini.Bar")
        self.assertIsNotNone(r)
        self.assertEqual(r.kind, "factory")  # only Bar.of(String) can build a Bar (ctor is private)
        self.assertEqual(r.cost, 0)  # String is Literal-resolvable
        self.assertIn("Bar.of", r.render())

    def test_resolver_depth_zero_returns_nothing(self):
        self.assertIsNone(Resolver(self.index, d_max=0).resolve("com.mini.Bar"))

    def test_sufficient_false_on_empty_ctx_then_true_after_expansion(self):
        empty = Context(types={"com.mini.Foo"}, files={"src/main/java/com/mini/Foo.java"})
        ok, unresolved = expand.sufficient(self.index, self.demands, empty)
        self.assertFalse(ok)
        kinds = {n.key() for n in unresolved}
        self.assertIn("receiver:com.mini.Foo:", kinds)
        self.assertIn("arg:com.mini.Bar:0", kinds)
        self.assertIn("setup:com.mini.Store:store", kinds)
        self.assertIn("oracle:com.mini.Foo:state", kinds)  # no observable of Foo is in the context yet
        # oracle/exception is satisfied by the JDK and idiom by the project block
        self.assertNotIn("oracle:java.io.IOException:exception", kinds)
        self.assertNotIn("idiom:junit5:junit:mockito:", kinds)

        result = expand.expand(self.index, self.demands, self.task)
        self.assertEqual(result.status, "sufficient")
        ok, unresolved = expand.sufficient(self.index, self.demands, result.ctx)
        self.assertTrue(ok)
        self.assertEqual(unresolved, [])
        # the state oracle was satisfied by pulling Foo's observables into the context
        self.assertTrue(any("observables:" in line for line in result.trace))

    def test_expansion_records_one_trace_line_per_step(self):
        result = expand.expand(self.index, self.demands, self.task)
        construction = [n for n in self.demands if n.kind in ("receiver", "arg", "setup")]
        for n in construction:  # every construction need is one step
            self.assertTrue(any(line.startswith(n.key() + " <-") for line in result.trace), n.key())
        for line in result.trace:  # and every step is one line
            self.assertIn("<-", line)
            self.assertIn("(+files=", line)
            self.assertEqual(line.count("<-"), 1)
        self.assertTrue(any("factory:Bar.of" in line for line in result.trace))
        self.assertTrue(any("observables:" in line for line in result.trace))  # state oracle step

    def test_recipes_and_entities_populated(self):
        result = expand.expand(self.index, self.demands, self.task)
        self.assertEqual(result.ctx.recipes["receiver:com.mini.Foo:"].kind, "ctor")
        self.assertIn("src/main/java/com/mini/Bar.java", result.ctx.files)
        self.assertIn("src/test/java/com/mini/FooTest.java", result.ctx.files)  # fixture recipe

    def test_budget_files_one_falls_back_and_lists_referable_tests(self):
        result = expand.expand(self.index, self.demands, self.task, budget_files=1)
        self.assertEqual(result.status, "fallback")
        self.assertEqual(result.trace, [])  # budget already exhausted by the focal file
        # §2.5.1: ranked by demand overlap — the test calling m first, then the one that builds a Bar;
        # WheelImplTest overlaps nothing and is not referable.
        self.assertEqual([t.id for t in result.referable_tests],
                         ["com.mini.FooTest#testProcessRoundTrips", "com.mini.BarTest#testOfParsesValue"])
        self.assertIn("src/test/java/com/mini/FooTest.java", result.ctx.files)
        self.assertIn("src/test/java/com/mini/BarTest.java", result.ctx.files)
        self.assertEqual(set(result.referable_diffs), {t.id for t in result.referable_tests})
        self.assertGreater(result.referable_diffs["com.mini.FooTest#testProcessRoundTrips"].score,
                           result.referable_diffs["com.mini.BarTest#testOfParsesValue"].score)

    def test_fallback_referable_tests_exclude_the_reference_test(self):
        with self.index.excluded_scope({self.task.ref_test_id}):
            result = expand.expand(self.index, self.demands, self.task, budget_files=1)
        self.assertEqual(result.status, "fallback")
        ids = [t.id for t in result.referable_tests]
        self.assertNotIn("com.mini.FooTest#testProcessRoundTrips", ids)  # §9 leakage control
        self.assertEqual(ids, ["com.mini.BarTest#testOfParsesValue"])

    def test_interaction_oracle_without_mocking_is_unresolvable(self):
        self.index.project["mocking_lib"] = "none"
        task = mini_task()
        task.intention = {"objective": "delegates the call", "preconditions": "",
                          "expected_results": "process must invoke the delegate"}
        demands = demand.compute_demands(self.index, task)
        self.assertIn("oracle:com.mini.Store:interaction", [n.key() for n in demands])
        result = expand.expand(self.index, demands, task)
        self.assertEqual(result.status, "fallback")
        self.assertIn("oracle:com.mini.Store:interaction", [n.key() for n in result.unresolved])


if __name__ == "__main__":
    unittest.main()

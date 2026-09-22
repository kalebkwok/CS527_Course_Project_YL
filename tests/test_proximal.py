"""§12 — proximal: demand-proximal ranking, leakage control, demand diff (§2.5.1)."""
from __future__ import annotations

import unittest

from demandtest import demand, proximal
from demandtest.index import TestInfo

from tests._util import REF_TEST_ID, load_mini_index, mini_task

FOO = "com.mini.FooTest#testProcessRoundTrips"
BAR = "com.mini.BarTest#testOfParsesValue"
WHEEL = "com.mini.WheelImplTest#testSpinDelegates"


class ProximalTest(unittest.TestCase):
    def setUp(self):
        self.index = load_mini_index()
        self.task = mini_task()
        self.demands = demand.compute_demands(self.index, self.task)

    def test_scorable_drops_literal_args_and_idiom(self):
        keys = [n.key() for n in proximal.scorable(self.demands)]
        self.assertEqual(keys, ["receiver:com.mini.Foo:", "arg:com.mini.Bar:0", "setup:com.mini.Store:store",
                                "oracle:java.io.IOException:exception", "oracle:com.mini.Foo:state"])

    def test_rank_orders_by_demand_overlap_and_drops_zero_overlap(self):
        ranked = proximal.rank(self.index, self.task, self.demands, k=5)
        self.assertEqual([d.test.id for d in ranked], [FOO, BAR])  # WheelImplTest overlaps nothing: dropped
        self.assertAlmostEqual(ranked[0].score, 3 / 5)  # receiver, arg0 (calls m), setup store (fixture)
        self.assertAlmostEqual(ranked[1].score, 1 / 5)  # arg0 via Bar.of(...)
        self.assertTrue(ranked[0].calls_focal)
        self.assertFalse(ranked[1].calls_focal)

    def test_rank_honors_leakage_control(self):
        with self.index.excluded_scope({REF_TEST_ID}):
            ranked = proximal.rank(self.index, self.task, self.demands, k=5)
        self.assertEqual([d.test.id for d in ranked], [BAR])

    def test_diff_lists_satisfied_and_missing_needs(self):
        bar = next(t for t in self.index.tests if t.id == BAR)
        d = proximal.diff(self.index, self.task, self.demands, bar)
        self.assertEqual([n.key() for n in d.satisfied], ["arg:com.mini.Bar:0"])
        self.assertEqual([n.key() for n in d.missing],
                         ["receiver:com.mini.Foo:", "setup:com.mini.Store:store",
                          "oracle:java.io.IOException:exception", "oracle:com.mini.Foo:state"])
        text = proximal.render_diff(d)
        self.assertIn("satisfies: arg0 (Bar)", text)
        self.assertIn("missing: receiver (Foo)", text)
        self.assertIn("oracle/exception (IOException): assert that the documented exception is thrown", text)

    def test_oracle_evidence_patterns(self):
        wheel = next(t for t in self.index.tests if t.id == WHEEL)
        foo = next(t for t in self.index.tests if t.id == FOO)
        inter = demand.Need("oracle", "com.mini.Wheel", detail="interaction")
        exc = demand.Need("oracle", "java.io.IOException", detail="exception")
        state = demand.Need("oracle", "com.mini.Foo", detail="state")
        self.assertTrue(proximal.oracle_evidence(self.index, wheel, inter))   # verify(...)
        self.assertFalse(proximal.oracle_evidence(self.index, foo, exc))      # no assertThrows / expected
        self.assertFalse(proximal.oracle_evidence(self.index, foo, state))    # asserts, but never reads size()
        reads_size = TestInfo(id="x#y", source="@Test void y() { Foo f = new Foo(); assertEquals(0, f.size()); }")
        self.assertTrue(proximal.oracle_evidence(self.index, reads_size, state))
        throws = TestInfo(id="x#z", source="@Test void z() { assertThrows(IOException.class, () -> f.process(b, -1)); }")
        self.assertTrue(proximal.oracle_evidence(self.index, throws, exc))

    def test_obtains_recognises_project_construction_idioms(self):
        t = TestInfo(id="x#w", source="Wheel w = mock(Wheel.class); Store s = TestUtil.emptyStore();")
        self.assertTrue(proximal.obtains(self.index, t, "com.mini.Wheel"))   # interface + mocking idiom
        self.assertTrue(proximal.obtains(self.index, t, "com.mini.Store"))   # test helper returning Store
        self.assertFalse(proximal.obtains(self.index, t, "com.mini.Bar"))


if __name__ == "__main__":
    unittest.main()

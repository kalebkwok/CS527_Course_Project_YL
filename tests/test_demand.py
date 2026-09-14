"""§12 — demand: the demand set of Foo#process(Bar,int) with a "throws" intention."""
from __future__ import annotations

import unittest

from demandtest import demand

from tests._util import load_mini_index, mini_task


class DemandTest(unittest.TestCase):
    def setUp(self):
        self.index = load_mini_index()
        self.task = mini_task()

    def test_compute_demands_exact_sequence(self):
        keys = [n.key() for n in demand.compute_demands(self.index, self.task)]
        # expected_results says "throws ... otherwise it returns the updated size":
        # row 1 adds the exception oracle, row 3's "updated" keyword adds the state oracle.
        self.assertEqual(keys, [
            "receiver:com.mini.Foo:",
            "arg:com.mini.Bar:0",
            "arg:int:1",
            "setup:com.mini.Store:store",
            "oracle:java.io.IOException:exception",
            "oracle:com.mini.Foo:state",
            "idiom:junit5:junit:mockito:",
        ])

    def test_return_oracle_when_expected_results_has_no_exception_or_state_keywords(self):
        task = mini_task()
        task.intention = {"objective": "process reports the resulting size", "preconditions": "",
                          "expected_results": "process returns the new size"}
        keys = [n.key() for n in demand.compute_demands(self.index, task)]
        self.assertIn("oracle:int:return", keys)
        self.assertNotIn("oracle:com.mini.Foo:state", keys)
        self.assertNotIn("oracle:java.io.IOException:exception", keys)

    def test_void_method_without_matching_keywords_gets_state_oracle(self):
        task = mini_task()
        task.focal_class = "com.mini.WheelImpl"
        task.focal_method = "spin"
        task.focal_sig = "spin()"
        task.focal_file = "src/main/java/com/mini/WheelImpl.java"
        task.intention = {"objective": "spin runs the delegate", "preconditions": "",
                          "expected_results": "nothing in particular"}
        keys = [n.key() for n in demand.compute_demands(self.index, task)]
        self.assertIn("oracle:com.mini.WheelImpl:state", keys)  # row 3: void method, no keywords
        self.assertIn("setup:com.mini.Wheel:delegate", keys)

    def test_keys_are_unique(self):
        keys = [n.key() for n in demand.compute_demands(self.index, self.task)]
        self.assertEqual(len(keys), len(set(keys)))

    def test_parameter_constraints_come_from_preconditions(self):
        needs = {n.key(): n for n in demand.compute_demands(self.index, self.task)}
        self.assertEqual(needs["arg:com.mini.Bar:0"].constraint, "bar is a validated Bar")
        self.assertEqual(needs["arg:int:1"].constraint, "n is the delta to apply")

    def test_receiver_skipped_for_static_and_ctor(self):
        task = mini_task()
        task.focal_method = "<init>"
        task.focal_sig = "<init>()"
        keys = [n.key() for n in demand.compute_demands(self.index, task)]
        self.assertNotIn("receiver:com.mini.Foo:", keys)

    def test_find_focal(self):
        C, m = demand.find_focal(self.index, self.task)
        self.assertEqual(C.fqn, "com.mini.Foo")
        self.assertEqual(m.name, "process")
        self.assertEqual([p.type for p in m.params], ["com.mini.Bar", "int"])
        with self.assertRaises(KeyError):
            demand.find_focal(self.index, demand.Task(
                repo="mini", focal_class="com.mini.Nope", focal_method="x", focal_sig="x()",
                focal_file="x.java", intention={}, ref_test_id="t"))


if __name__ == "__main__":
    unittest.main()

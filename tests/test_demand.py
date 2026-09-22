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

    def test_negated_exception_cue_is_not_an_exception_oracle(self):
        task = mini_task()
        task.intention = {"objective": "process accepts a positive delta", "preconditions": "",
                          "expected_results": "process does not throw and returns the updated size"}
        keys = [n.key() for n in demand.compute_demands(self.index, task)]
        self.assertNotIn("oracle:java.io.IOException:exception", keys)
        self.assertIn("oracle:int:return", keys)
        # a positive cue elsewhere in the sentence still counts
        task.intention["expected_results"] = "process throws IOException for a negative delta and does not throw otherwise"
        keys = [n.key() for n in demand.compute_demands(self.index, task)]
        self.assertIn("oracle:java.io.IOException:exception", keys)

    def test_objective_is_the_cue_source_when_expected_results_is_empty(self):
        task = mini_task()
        task.intention = {"objective": "process rejects a negative delta with an exception", "preconditions": "",
                          "expected_results": ""}
        keys = [n.key() for n in demand.compute_demands(self.index, task)]
        self.assertIn("oracle:java.io.IOException:exception", keys)

    def test_semantic_gaps_zero_on_the_fixture_task(self):
        gaps = demand.compute_gaps(self.index, self.task)
        self.assertEqual(gaps["counts"], {"relational": 0, "state": 0, "unbound": 0, "same_type_args": 0})
        self.assertEqual(gaps["total"], 0)

    def test_semantic_gaps_relational_state_unbound(self):
        task = mini_task()
        task.intention = {"objective": "o", "expected_results": "returns",
                          "preconditions": "n must be smaller than the length of bar. "
                                           "The store already contains one entry. "
                                           "The moon is full."}
        gaps = demand.compute_gaps(self.index, task)
        self.assertEqual(gaps["counts"]["relational"], 1)
        self.assertEqual(gaps["relations"][0]["params"], ["bar", "n"])
        self.assertEqual(gaps["counts"]["state"], 1)   # mentions field `store` / "already contains"
        self.assertEqual(gaps["counts"]["unbound"], 1)
        self.assertEqual(gaps["total"], 3)

    def test_semantic_gaps_same_type_args(self):
        from demandtest.index import Method, Param
        foo = self.index.type("com.mini.Foo")
        foo.methods.append(Method(name="merge", params=[Param("a", "com.mini.Bar"), Param("b", "com.mini.Bar")],
                                  returns="int", owner="com.mini.Foo"))
        task = mini_task()
        task.focal_method, task.focal_sig = "merge", "merge(Bar,Bar)"
        self.assertEqual(demand.compute_gaps(self.index, task)["counts"]["same_type_args"], 1)

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

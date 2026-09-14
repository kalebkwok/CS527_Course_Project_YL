"""§12 — packet: section order, budget truncation, leakage control."""
from __future__ import annotations

import unittest

from demandtest import demand, expand, packet

from tests._util import REF_TEST_ID, focal_source, load_mini_index, mini_task


class PacketTest(unittest.TestCase):
    def setUp(self):
        self.index = load_mini_index()
        self.task = mini_task()
        self.demands = demand.compute_demands(self.index, self.task)
        self.sufficient = expand.expand(self.index, self.demands, self.task)
        self.pkt = packet.render(self.index, self.task, self.demands, self.sufficient,
                                 focal_source=focal_source())

    def test_sections_appear_in_specified_order(self):
        positions = [self.pkt.text.index(k) for k in packet.SECTION_KEYS[:6]]
        self.assertEqual(positions, sorted(positions))
        for key in packet.SECTION_KEYS[:6]:
            self.assertIn(key, self.pkt.text)
        self.assertNotIn(packet.SECTION_KEYS[6], self.pkt.text)  # section 7 is fallback-only

    def test_packet_contains_intention_verbatim_and_recipe(self):
        self.assertIn(self.task.intention["objective"], self.pkt.text)
        self.assertIn("Bar.of(<literal String>)", self.pkt.text)
        self.assertIn("package of focal class: com.mini", self.pkt.text)

    def test_small_budget_drops_section_6_but_never_1_3_5(self):
        small = packet.render(self.index, self.task, self.demands, self.sufficient,
                              focal_source=focal_source(), budget_tokens=50)
        self.assertLess(small.est_tokens, self.pkt.est_tokens)
        self.assertNotIn(packet.SECTION_KEYS[5], small.text)  # ASSERTION STYLE dropped
        self.assertNotIn(packet.SECTION_KEYS[3], small.text)  # droppable, earlier section
        for key in ("1. INTENTION", "2. FOCAL", "3. HOW TO OBTAIN VALUES (from this project)",
                    "5. PROJECT FACTS"):
            self.assertIn(key, small.text)
        self.assertEqual(set(small.sections), {"1. INTENTION", "2. FOCAL",
                                              "3. HOW TO OBTAIN VALUES (from this project)",
                                              "5. PROJECT FACTS"})

    def test_fallback_packet_includes_related_tests(self):
        fallback = expand.expand(self.index, self.demands, self.task, budget_files=1)
        pkt = packet.render(self.index, self.task, self.demands, fallback, focal_source=focal_source())
        self.assertIn(packet.SECTION_KEYS[6], pkt.text)
        self.assertIn("com.mini.FooTest#testProcessRoundTrips", pkt.text)
        self.assertIn("UNRESOLVED", pkt.text)

    def test_reference_test_never_leaks_into_the_packet(self):
        with self.index.excluded_scope({REF_TEST_ID}):
            demands = demand.compute_demands(self.index, self.task)
            expansion = expand.expand(self.index, demands, self.task, budget_files=1)
            pkt = packet.render(self.index, self.task, demands, expansion, focal_source=focal_source())
        self.assertNotIn("testProcessRoundTrips", pkt.text)  # neither the idiom example nor section 7
        self.assertNotIn("FooTest", pkt.text)
        self.assertIn("BarTest", pkt.text)  # falls back to another visible test

    def test_est_tokens_falls_back_without_tiktoken(self):
        self.assertGreaterEqual(packet.est_tokens(""), 1)
        self.assertGreater(packet.est_tokens("a" * 360), packet.est_tokens("a" * 36))


if __name__ == "__main__":
    unittest.main()

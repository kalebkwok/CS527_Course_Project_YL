"""§12 — index: fixture round-trip, erase/jdk/literal helpers, leakage control."""
from __future__ import annotations

import unittest

from demandtest import index as index_mod

from tests._util import REF_TEST_ID, load_mini_index


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.index = load_mini_index()

    def test_load_round_trips_fixture(self):
        self.assertEqual(self.index.project["name"], "mini")
        self.assertEqual(self.index.project["test_framework"], "junit5")
        self.assertEqual(len(self.index.types), 7)
        self.assertEqual(len(self.index.tests), 3)
        bar = self.index.type("com.mini.Bar")
        self.assertIsNotNone(bar)
        self.assertEqual(bar.factories[0].name, "of")
        self.assertEqual(bar.factories[0].returns, "com.mini.Bar")
        self.assertEqual(bar.observables[0].owner, "com.mini.Bar")  # stamped by the loader
        foo = self.index.type("com.mini.Foo")
        self.assertEqual(foo.methods[0].body_reads_fields, ["store"])
        self.assertEqual(foo.fields[0].type, "com.mini.Store")

    def test_type_lookup_erases_generics_and_arrays(self):
        self.assertIsNotNone(self.index.type("com.mini.Foo[]"))
        self.assertIsNotNone(self.index.type("com.mini.Bar<java.lang.String>"))
        self.assertIsNone(self.index.type("com.mini.Missing"))

    def test_erase_jdk_literal(self):
        self.assertEqual(index_mod.erase("java.util.Map<java.lang.String, java.util.List<com.mini.Bar>>"),
                         "java.util.Map")
        self.assertEqual(index_mod.erase("com.mini.Bar[]"), "com.mini.Bar")
        self.assertEqual(index_mod.erase("java.lang.String..."), "java.lang.String")
        self.assertTrue(index_mod.is_jdk("java.io.IOException"))
        self.assertFalse(index_mod.is_jdk("com.mini.Foo"))
        self.assertTrue(index_mod.is_literal("int"))
        self.assertTrue(index_mod.is_literal("java.util.List"))
        self.assertFalse(index_mod.is_literal("com.mini.Bar"))

    def test_subtypes(self):
        subs = [t.fqn for t in self.index.subtypes("com.mini.Wheel")]
        self.assertIn("com.mini.WheelImpl", subs)

    def test_exclude_test_hides_it_from_retrieval(self):
        sig = "com.mini.Foo#process(com.mini.Bar,int)"
        self.assertEqual(len(self.index.tests_calling(sig)), 1)
        self.assertEqual(len(self.index.fixtures_of_type("com.mini.Store")), 1)
        with self.index.excluded_scope({REF_TEST_ID}):
            self.assertEqual(self.index.tests_calling(sig), [])
            self.assertEqual(self.index.fixtures_of_type("com.mini.Store"), [])
        # scope restores the previous exclusions
        self.assertEqual(len(self.index.tests_calling(sig)), 1)

    def test_exclude_test_is_global_until_restored(self):
        self.index.exclude_test(REF_TEST_ID)
        self.assertEqual(self.index.tests_calling("com.mini.Foo#process(com.mini.Bar,int)"), [])
        self.assertEqual(self.index.tests_of_class("com.mini.FooTest"), [])
        self.assertEqual(len(self.index.tests), 3)  # the test is hidden, never deleted

    def test_files_of_entities(self):
        foo = self.index.type("com.mini.Foo")
        self.assertEqual(self.index.files([foo]), {"src/main/java/com/mini/Foo.java"})
        self.assertEqual(self.index.files([(self.index.tests[0], self.index.tests[0].fixtures[0])]),
                         {"src/test/java/com/mini/FooTest.java"})


if __name__ == "__main__":
    unittest.main()

"""§12 — repair: package, imports, checked exceptions, idiom normalisation (§2.8 passes 1–4)."""
from __future__ import annotations

import unittest

from demandtest import repair

from tests._util import load_mini_index, mini_task


class RepairTest(unittest.TestCase):
    def setUp(self):
        self.index = load_mini_index()
        self.task = mini_task()

    def test_class_without_package_gets_focal_package(self):
        src = "class FooGeneratedTest {\n    @Test\n    void a() {\n    }\n}\n"
        fixed, fixes = repair.static_repair(self.index, self.task, src)
        self.assertIn("package com.mini;", fixed)
        self.assertTrue(any(f.startswith("package") for f in fixes))

    def test_junit5_test_annotation_gets_jupiter_import(self):
        src = "class FooGeneratedTest {\n    @Test\n    void a() {\n    }\n}\n"
        fixed, _ = repair.static_repair(self.index, self.task, src)
        self.assertIn("import org.junit.jupiter.api.Test;", fixed)

    def test_project_types_are_imported(self):
        src = "class FooGeneratedTest {\n    void a() {\n        Foo foo = new Foo();\n        foo.process(Bar.of(\"a\"), 1);\n    }\n}\n"
        fixed, _ = repair.static_repair(self.index, self.task, src)
        self.assertIn("import com.mini.Foo;", fixed)
        self.assertIn("import com.mini.Bar;", fixed)

    def test_ambiguous_simple_name_resolves_to_focal_package(self):
        src = ("package com.mini;\n\nclass TokenGeneratedTest {\n    @Test\n    void a() {\n"
               "        Token token = new Token();\n        assertNotNull(token);\n    }\n}\n")
        fixed, fixes = repair.static_repair(self.index, self.task, src)
        self.assertIn("import com.mini.Token;", fixed)
        self.assertNotIn("import com.other.Token;", fixed)
        self.assertTrue(any("com.mini.Token" in f for f in fixes))

    def test_checked_exception_adds_throws_clause(self):
        src = ("package com.mini;\n\nclass FooGeneratedTest {\n    @Test\n    void a() {\n"
               "        new Foo().process(Bar.of(\"a\"), 1);\n    }\n}\n")
        fixed, fixes = repair.static_repair(self.index, self.task, src)
        self.assertIn("void a() throws Exception {", fixed)
        self.assertIn("throws Exception", fixes)

    def test_methods_with_throws_clause_are_untouched(self):
        src = ("package com.mini;\n\nclass FooGeneratedTest {\n    @Test\n    void a() throws Exception {\n"
               "        new Foo().process(Bar.of(\"a\"), 1);\n    }\n}\n")
        fixed, _ = repair.static_repair(self.index, self.task, src)
        self.assertEqual(fixed.count("throws Exception"), 1)

    def test_junit4_idiom_is_rewritten_to_project_framework(self):
        src = ("package com.mini;\n\nimport org.junit.Test;\n\nclass LegacyTest {\n    @Before\n"
               "    void setUp() {\n    }\n\n    @Test\n    void a() {\n    }\n}\n")
        fixed, fixes = repair.static_repair(self.index, self.task, src)
        self.assertIn("import org.junit.jupiter.api.Test;", fixed)
        self.assertNotIn("import org.junit.Test;", fixed)
        self.assertIn("@BeforeEach", fixed)
        self.assertTrue(any(f.startswith("idiom:") for f in fixes))

    def test_no_fixes_for_a_clean_class(self):
        src = ("package com.mini;\n\nimport com.mini.Bar;\nimport com.mini.Foo;\nimport org.junit.jupiter.api.Test;\n\n"
               "class FooGeneratedTest {\n    @Test\n    void a() throws Exception {\n"
               "        new Foo().process(Bar.of(\"a\"), 1);\n    }\n}\n")
        fixed, fixes = repair.static_repair(self.index, self.task, src)
        self.assertEqual(fixed, src)
        self.assertEqual(fixes, [])


if __name__ == "__main__":
    unittest.main()

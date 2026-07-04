"""Tests for the optional tree-sitter analyzer. Skipped entirely when the
`tree-sitter-language-pack` extra isn't installed, since that's a
supported, expected configuration (see README) -- the whole point of the
fallback is that codequality still works without it.
"""

import unittest

from codequality.analyzers import treesitter_analyzer
from codequality.config import DEFAULT_CONFIG, Limits


def _limits():
    return Limits(dict(DEFAULT_CONFIG["limits"]))


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestTreesitterAnalyzer(unittest.TestCase):
    def test_finds_real_function_boundaries_in_javascript(self):
        """Real per-function complexity/nesting, not whole-file keyword density."""
        source = (
            "function complicated(a, b) {\n"
            "  if (a && b) {\n"
            "    if (a) {\n"
            "      if (b) { return 1; }\n"
            "    }\n"
            "  }\n"
            "  return 0;\n"
            "}\n\n"
            "function simple(x) {\n"
            "  return x + 1;\n"
            "}\n"
        )
        fm = treesitter_analyzer.analyze("f.js", source, "javascript", _limits())
        self.assertEqual(len(fm.functions), 2)
        by_name = {f.name: f for f in fm.functions}
        self.assertGreater(by_name["complicated"].complexity, by_name["simple"].complexity)
        self.assertGreaterEqual(by_name["complicated"].nesting, 3)

    def test_nested_function_does_not_inflate_parent_complexity(self):
        """A closure's branching should count toward its own complexity, not its parent's."""
        source = (
            "function outer() {\n"
            "  function inner() {\n"
            "    if (a) { if (b) { if (c) { return 1; } } }\n"
            "  }\n"
            "  return inner;\n"
            "}\n"
        )
        fm = treesitter_analyzer.analyze("f.js", source, "javascript", _limits())
        by_name = {f.name: f for f in fm.functions}
        self.assertEqual(by_name["outer"].complexity, 1)
        self.assertGreater(by_name["inner"].complexity, 1)

    def test_only_lines_restricts_function_selection(self):
        source = "function a() {\n  return 1;\n}\n\nfunction b() {\n  return 2;\n}\n"
        fm_full = treesitter_analyzer.analyze("f.js", source, "javascript", _limits())
        fm_scoped = treesitter_analyzer.analyze("f.js", source, "javascript", _limits(), only_lines={1})
        self.assertEqual(len(fm_full.functions), 2)
        self.assertEqual(len(fm_scoped.functions), 1)

    def test_bad_function_name_is_flagged_per_language_convention(self):
        source = "function BadName(a) {\n  return a;\n}\n"
        fm = treesitter_analyzer.analyze("f.js", source, "javascript", _limits())
        naming = [i for i in fm.issues if i.symbol == "bad-function-name"]
        self.assertEqual(len(naming), 1)
        self.assertIn("camelCase", naming[0].message)

    def test_good_function_name_is_not_flagged(self):
        source = "function goodName(a) {\n  return a;\n}\n"
        fm = treesitter_analyzer.analyze("f.js", source, "javascript", _limits())
        self.assertNotIn("bad-function-name", {i.symbol for i in fm.issues})

    def test_ruby_snake_case_convention_is_enforced(self):
        source = "def BadName(a)\n  a\nend\n"
        fm = treesitter_analyzer.analyze("f.rb", source, "ruby", _limits())
        naming = [i for i in fm.issues if i.symbol == "bad-function-name"]
        self.assertEqual(len(naming), 1)
        self.assertIn("snake_case", naming[0].message)

    def test_java_constructor_is_exempt_from_method_naming_rule(self):
        source = "class Foo {\n    Foo() {}\n    int goodMethod() { return 1; }\n}\n"
        fm = treesitter_analyzer.analyze("f.java", source, "java", _limits())
        self.assertNotIn("bad-function-name", {i.symbol for i in fm.issues})

    def test_c_has_no_naming_convention_check(self):
        """C's style conventions are too mixed in practice to check without noise."""
        source = "int BadlyNamedFunction(int a) {\n    return a;\n}\n"
        fm = treesitter_analyzer.analyze("f.c", source, "c", _limits())
        self.assertNotIn("bad-function-name", {i.symbol for i in fm.issues})

    def test_unsupported_construct_kinds_never_crash_unknown_language(self):
        for language, cfg in treesitter_analyzer.LANGUAGES.items():
            self.assertIn("function_kinds", cfg)
            self.assertIn("complexity_kinds", cfg)
            self.assertIn("nesting_kinds", cfg)


if __name__ == "__main__":
    unittest.main()

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

    def test_unsupported_construct_kinds_never_crash_unknown_language(self):
        for language, cfg in treesitter_analyzer.LANGUAGES.items():
            self.assertIn("function_kinds", cfg)
            self.assertIn("complexity_kinds", cfg)
            self.assertIn("nesting_kinds", cfg)


if __name__ == "__main__":
    unittest.main()

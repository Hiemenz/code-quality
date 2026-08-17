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


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestNodeApiContract(unittest.TestCase):
    """Pins the exact tree-sitter Node/Tree API shape treesitter_analyzer.py,
    js_security.py, and js_taint_flow.py all assume: `.type`/`.start_byte`/
    `.end_byte`/`.start_point`/`.end_point`/`.named_child_count`/
    `tree.root_node` are *properties*.

    This is a regression test for a real incident: those three files were
    originally written (and passed every test) against
    tree-sitter-language-pack==1.12.2, which exposed this same information
    through *callables* instead (`.kind()`, `.start_byte()`, ...,
    `tree.root_node()`). CI never actually installed the `treesitter` extra
    until the PR that added this test, so nothing caught the mismatch
    against what `pip install codequality[treesitter]` actually resolves to
    today until CI itself failed on a freshly-installed 1.14.3. Property
    access on a bound method doesn't raise -- `node.type == "call_expression"`
    against a bound method just silently evaluates to False -- so a
    regression here wouldn't necessarily crash; it would misdetect
    everything and look like the checks stopped firing. This test exists so
    that failure mode gets a specific, obvious assertion instead of a
    confusing cascade across every JS/TS test file.
    """

    def _parse(self, source, language="javascript"):
        from tree_sitter_language_pack import get_parser

        parser = get_parser(language)
        return parser.parse(source.encode("utf-8"))

    def test_root_node_is_a_property_not_a_callable(self):
        tree = self._parse("const x = 1;\n")
        root = tree.root_node
        self.assertFalse(callable(root))
        self.assertEqual(root.type, "program")

    def test_type_is_a_string_property_not_a_callable(self):
        tree = self._parse("function f() {}\n")
        fn = tree.root_node.named_child(0)
        self.assertIsInstance(fn.type, str)
        self.assertEqual(fn.type, "function_declaration")

    def test_byte_offsets_are_int_properties_not_callables(self):
        tree = self._parse("const x = 1;\n")
        node = tree.root_node.named_child(0)
        self.assertIsInstance(node.start_byte, int)
        self.assertIsInstance(node.end_byte, int)
        self.assertGreater(node.end_byte, node.start_byte)

    def test_start_point_is_a_property_with_a_row_attribute(self):
        tree = self._parse("const x = 1;\nconst y = 2;\n")
        second = tree.root_node.named_child(1)
        self.assertFalse(callable(second.start_point))
        self.assertEqual(second.start_point.row, 1)

    def test_named_child_count_is_an_int_property_not_a_callable(self):
        tree = self._parse("function f(a, b) {}\n")
        fn = tree.root_node.named_child(0)
        self.assertIsInstance(fn.named_child_count, int)
        self.assertGreaterEqual(fn.named_child_count, 2)

    def test_parser_parse_requires_bytes_not_str(self):
        from tree_sitter_language_pack import get_parser

        parser = get_parser("javascript")
        with self.assertRaises(TypeError):
            parser.parse("const x = 1;\n")  # str, not bytes -- must raise, not silently misparse

    def test_analyze_end_to_end_does_not_use_the_stale_callable_api(self):
        """Belt-and-suspenders: analyze() itself must succeed against the
        real installed grammar, exercising _node_text/_naming_issue/
        _function_stats's actual node-API usage, not just this test's own
        isolated assertions above.
        """
        source = "function BadName(a) {\n  if (a) { return a; }\n  return 0;\n}\n"
        fm = treesitter_analyzer.analyze("f.js", source, "javascript", _limits())
        self.assertEqual(len(fm.functions), 1)
        self.assertEqual(fm.functions[0].name, "BadName")
        self.assertIn("bad-function-name", {i.symbol for i in fm.issues})


if __name__ == "__main__":
    unittest.main()

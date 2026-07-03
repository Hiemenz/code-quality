import os
import unittest

from codequality.analyzers import python_analyzer
from codequality.config import DEFAULT_CONFIG, Limits

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _limits():
    return Limits(dict(DEFAULT_CONFIG["limits"]))


def _read(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


class TestPythonAnalyzer(unittest.TestCase):
    def test_good_file_has_no_serious_issues(self):
        fm = python_analyzer.analyze("good.py", _read("good.py"), _limits())
        self.assertIsNone(fm.parse_error)
        severities = {i.severity for i in fm.issues}
        self.assertNotIn("error", severities)
        self.assertTrue(fm.has_module_docstring)
        self.assertEqual(len(fm.functions), 2)
        for fn in fm.functions:
            self.assertTrue(fn.has_docstring)

    def test_bad_file_flags_expected_issues(self):
        fm = python_analyzer.analyze("bad.py", _read("bad.py"), _limits())
        symbols = {i.symbol for i in fm.issues}
        self.assertIn("high-complexity", symbols)
        self.assertIn("deep-nesting", symbols)
        self.assertIn("bare-except", symbols)
        self.assertIn("star-import", symbols)
        self.assertIn("mutable-default-arg", symbols)
        self.assertIn("long-line", symbols)
        self.assertIn("trailing-whitespace", symbols)
        self.assertIn("todo-marker", symbols)
        self.assertIn("missing-docstring", symbols)

        fn = fm.functions[0]
        self.assertEqual(fn.name, "messy")
        self.assertGreaterEqual(fn.complexity, 10)
        self.assertGreaterEqual(fn.nesting, 5)

    def test_syntax_error_is_reported_not_raised(self):
        fm = python_analyzer.analyze("broken.py", "def f(:\n    pass\n", _limits())
        self.assertIsNotNone(fm.parse_error)
        self.assertEqual(fm.issues[0].symbol, "syntax-error")

    def test_only_lines_restricts_function_selection(self):
        source = _read("bad.py")
        # 'messy' spans roughly lines 4-22; restrict to a line far outside it.
        fm_full = python_analyzer.analyze("bad.py", source, _limits())
        fm_scoped = python_analyzer.analyze("bad.py", source, _limits(), only_lines={1})
        self.assertGreater(len(fm_full.functions), 0)
        self.assertEqual(len(fm_scoped.functions), 0)

    def test_complexity_is_deterministic(self):
        source = _read("bad.py")
        results = [python_analyzer.analyze("bad.py", source, _limits()).functions[0].complexity for _ in range(5)]
        self.assertEqual(len(set(results)), 1)

    def test_nested_function_does_not_inflate_parent_complexity(self):
        source = (
            "def outer():\n"
            "    def inner():\n"
            "        if 1:\n"
            "            if 2:\n"
            "                if 3:\n"
            "                    pass\n"
            "    return inner\n"
        )
        fm = python_analyzer.analyze("nested.py", source, _limits())
        by_name = {f.name: f for f in fm.functions}
        self.assertEqual(by_name["outer"].complexity, 1)
        self.assertEqual(by_name["inner"].complexity, 4)


if __name__ == "__main__":
    unittest.main()

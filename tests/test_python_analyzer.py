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
        """A tidy, docstringed module should score clean."""
        fm = python_analyzer.analyze("good.py", _read("good.py"), _limits())
        self.assertIsNone(fm.parse_error)
        severities = {i.severity for i in fm.issues}
        self.assertNotIn("error", severities)
        self.assertTrue(fm.has_module_docstring)
        self.assertEqual(len(fm.functions), 2)
        for fn in fm.functions:
            self.assertTrue(fn.has_docstring)

    def test_bad_file_flags_expected_issues(self):
        """Every deliberately planted defect in the bad.py fixture should be caught."""
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

    def test_unused_import_is_flagged_but_used_one_is_not(self):
        source = "import os\nimport sys\n\n\ndef f():\n    return sys.argv\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        unused = [i for i in fm.issues if i.symbol == "unused-import"]
        self.assertEqual(len(unused), 1)
        self.assertIn("os", unused[0].message)

    def test_dunder_all_exempts_reexported_import(self):
        source = "import os\n\n__all__ = ['os']\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unused-import", {i.symbol for i in fm.issues})

    def test_unused_local_variable_is_flagged(self):
        source = "def f():\n    x = 1\n    y = 2\n    return y\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        unused = [i for i in fm.issues if i.symbol == "unused-variable"]
        self.assertEqual(len(unused), 1)
        self.assertIn("x", unused[0].message)

    def test_underscore_prefixed_variable_is_not_flagged_as_unused(self):
        source = "def f():\n    _ignored = compute()\n    return 1\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unused-variable", {i.symbol for i in fm.issues})

    def test_eval_and_shell_true_are_flagged_as_security_issues(self):
        """Both eval() and shell=True should surface as security-category issues."""
        source = (
            "import subprocess\n\n"
            "def f(cmd):\n"
            "    eval(cmd)\n"
            "    subprocess.run(cmd, shell=True)\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        symbols = {i.symbol for i in fm.issues if i.category == "security"}
        self.assertIn("dangerous-eval", symbols)
        self.assertIn("shell-true", symbols)

    def test_hardcoded_secret_is_flagged(self):
        source = "password = 'hunter2'\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        secrets = [i for i in fm.issues if i.symbol == "hardcoded-secret"]
        self.assertEqual(len(secrets), 1)

    def test_placeholder_secret_value_is_not_flagged(self):
        source = "password = 'changeme'\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("hardcoded-secret", {i.symbol for i in fm.issues})

    def test_bad_function_and_class_names_are_flagged(self):
        source = "def BadName():\n    pass\n\n\nclass lowercase_class:\n    pass\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        symbols = {i.symbol for i in fm.issues}
        self.assertIn("bad-function-name", symbols)
        self.assertIn("bad-class-name", symbols)

    def test_visitor_and_unittest_method_names_are_exempt_from_naming_check(self):
        source = "class T:\n    def setUp(self):\n        pass\n\n    def visit_If(self, node):\n        pass\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("bad-function-name", {i.symbol for i in fm.issues})

    def test_nested_function_does_not_inflate_parent_complexity(self):
        """A closure's branching should count toward its own complexity, not its parent's."""
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

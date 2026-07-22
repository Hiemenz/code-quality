import ast
import unittest

from codequality.analyzers.redos import pattern_is_catastrophic, redos_issues


def _issues(source, only_lines=None):
    return redos_issues(ast.parse(source), "f.py", only_lines)


class TestPatternDetection(unittest.TestCase):
    def test_nested_quantifiers_are_catastrophic(self):
        for pat in ["(a+)+", "(a*)*", "(.*)+", r"(\d+)+", "(a+b+)+", r"(\w+\s?)+", "((x+)+)+"]:
            self.assertTrue(pattern_is_catastrophic(pat), pat)

    def test_safe_patterns_are_not_flagged(self):
        for pat in ["(ab)+", "a+", "a+b+", r"\d{4}-\d{2}", "(abc)+def", "^[a-z]+$", "(a|b)+", "a{1,5}b{1,5}"]:
            self.assertFalse(pattern_is_catastrophic(pat), pat)

    def test_invalid_regex_is_not_flagged(self):
        self.assertFalse(pattern_is_catastrophic("(a+"))


class TestRedosIssues(unittest.TestCase):
    def test_re_compile_with_evil_literal_flagged(self):
        issues = _issues("import re\nre.compile('(a+)+')\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "catastrophic-regex")
        self.assertEqual(issues[0].category, "security")
        self.assertEqual(issues[0].severity, "warn")

    def test_re_search_flagged(self):
        src = "import re\nre.search(r'(\\d+)+', s)\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_safe_re_call_not_flagged(self):
        self.assertEqual(_issues("import re\nre.compile('^[a-z]+$')\n"), [])

    def test_non_re_call_not_flagged(self):
        self.assertEqual(_issues("something.compile('(a+)+')\n"), [])

    def test_non_literal_pattern_not_flagged(self):
        # No dataflow: a variable pattern is not inspected.
        self.assertEqual(_issues("import re\np = '(a+)+'\nre.compile(p)\n"), [])

    def test_only_lines_scoping(self):
        src = "import re\nre.compile('(a+)+')\nre.compile('(b*)*')\n"
        self.assertEqual(len(_issues(src, only_lines={3})), 1)


if __name__ == "__main__":
    unittest.main()

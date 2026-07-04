import os
import tempfile
import unittest

from codequality import suppress
from codequality.analyzers.base import Issue
from codequality.config import Config
from codequality.scanner import analyze_file


class TestSuppressModule(unittest.TestCase):
    def test_parse_blanket_ignore(self):
        suppressions = suppress.parse("x = 1  # codequality: ignore\n")
        self.assertTrue(suppress.is_suppressed(suppressions.get(1), "anything"))

    def test_parse_scoped_ignore(self):
        suppressions = suppress.parse("x = 1  # codequality: ignore[unused-variable, bad-name]\n")
        symbols = suppressions.get(1)
        self.assertTrue(suppress.is_suppressed(symbols, "unused-variable"))
        self.assertTrue(suppress.is_suppressed(symbols, "bad-name"))
        self.assertFalse(suppress.is_suppressed(symbols, "other-symbol"))

    def test_line_without_marker_is_not_suppressed(self):
        suppressions = suppress.parse("x = 1\n")
        self.assertFalse(suppress.is_suppressed(suppressions.get(1), "anything"))

    def test_filter_issues_removes_only_matching_symbol(self):
        """A scoped suppression must not remove other symbols on the same line."""
        issues = [
            Issue("f.py", 1, "style", "info", "unused-variable", "..."),
            Issue("f.py", 1, "style", "info", "other-symbol", "..."),
        ]
        suppressions = {1: frozenset({"unused-variable"})}
        kept, count = suppress.filter_issues(issues, suppressions)
        self.assertEqual(count, 1)
        self.assertEqual([i.symbol for i in kept], ["other-symbol"])


class TestSuppressIntegration(unittest.TestCase):
    """End-to-end through scanner.analyze_file, which is where suppression
    actually gets applied to a real analyzer's output.
    """

    def _analyze(self, source):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "f.py")
            with open(path, "w") as f:
                f.write(source)
            return analyze_file(root, "f.py", "python", Config({}))

    def test_suppressed_issue_is_removed_from_report(self):
        source = "password = 'hunter2'  # codequality: ignore[hardcoded-secret]\n"
        fm = self._analyze(source)
        self.assertNotIn("hardcoded-secret", {i.symbol for i in fm.issues})
        self.assertEqual(fm.suppressed_count, 1)

    def test_suppression_also_removes_the_score_penalty(self):
        """Suppressing high-complexity shouldn't just hide the issue -- the
        score should stop reflecting it too, or the feature would be lying.
        """
        from codequality.scorer import score_complexity
        from codequality.config import DEFAULT_CONFIG, Limits

        limits = Limits(dict(DEFAULT_CONFIG["limits"]))
        params = ", ".join(f"a{i}" for i in range(12))
        conditions = "".join(f"    if a{i}:\n        pass\n" for i in range(12))
        source = f"def messy({params}):  # codequality: ignore[high-complexity]\n{conditions}"
        fm = self._analyze(source)
        self.assertNotIn("high-complexity", {i.symbol for i in fm.issues})
        self.assertEqual(score_complexity(fm.functions, limits), 100.0)

    def test_other_symbols_on_the_same_line_are_unaffected(self):
        source = "import os  # codequality: ignore[bad-function-name]\n"
        fm = self._analyze(source)
        self.assertIn("unused-import", {i.symbol for i in fm.issues})


if __name__ == "__main__":
    unittest.main()

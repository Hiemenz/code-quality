"""Tests for the optional mypy wrapper. Skipped entirely when the
`types` extra isn't installed -- codequality must still work without it.
"""

import os
import tempfile
import unittest

from codequality import typecheck


@unittest.skipUnless(typecheck.AVAILABLE, "mypy extra not installed")
class TestTypecheck(unittest.TestCase):
    def test_finds_a_real_type_error(self):
        """A str passed where add() expects int should surface as a correctness issue."""
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "a.py"), "w") as f:
                f.write("def add(a: int, b: int) -> int:\n    return a + b\n\n\nx: int = add('x', 'y')\n")
            issues_by_file = typecheck.run(root)
        self.assertIn("a.py", issues_by_file)
        issue = issues_by_file["a.py"][0]
        self.assertEqual(issue.category, "correctness")
        self.assertEqual(issue.severity, "error")

    def test_clean_file_has_no_issues(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "a.py"), "w") as f:
                f.write("def add(a: int, b: int) -> int:\n    return a + b\n")
            issues_by_file = typecheck.run(root)
        self.assertEqual(issues_by_file, {})

    def test_separate_runs_in_different_directories_do_not_contaminate_each_other(self):
        """Regression test: mypy's incremental cache keys on module name,
        not full path, and mixed up results across unrelated directories
        that both had an `a.py` before --no-incremental was added.
        """
        with tempfile.TemporaryDirectory() as root_a, tempfile.TemporaryDirectory() as root_b:
            with open(os.path.join(root_a, "a.py"), "w") as f:
                f.write("x: int = 'not an int'\n")
            with open(os.path.join(root_b, "a.py"), "w") as f:
                f.write("x: int = 1\n")
            result_a = typecheck.run(root_a)
            result_b = typecheck.run(root_b)
        self.assertIn("a.py", result_a)
        self.assertEqual(result_b, {})


if __name__ == "__main__":
    unittest.main()

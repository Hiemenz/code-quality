"""Tests for the optional coverage.py wrapper. Skipped entirely when the
`coverage` extra isn't installed.
"""

import os
import tempfile
import unittest

from codequality import coverage_check


class TestRatio(unittest.TestCase):
    """The pure ratio computation doesn't need coverage.py installed."""

    def test_full_file_ratio(self):
        lines = {"covered": {1, 2, 3}, "missing": set()}
        self.assertEqual(coverage_check.ratio(lines), 1.0)

    def test_partial_ratio(self):
        lines = {"covered": {1}, "missing": {2}}
        self.assertEqual(coverage_check.ratio(lines), 0.5)

    def test_no_executable_lines_is_none(self):
        lines = {"covered": set(), "missing": set()}
        self.assertIsNone(coverage_check.ratio(lines))

    def test_only_lines_restricts_to_the_diff(self):
        """Diff mode measures patch coverage: just the lines that changed."""
        lines = {"covered": {1, 2}, "missing": {3, 4}}
        self.assertEqual(coverage_check.ratio(lines, only_lines={1, 3}), 0.5)
        self.assertEqual(coverage_check.ratio(lines, only_lines={1, 2}), 1.0)


@unittest.skipUnless(coverage_check.AVAILABLE, "coverage extra not installed")
class TestCoverageRun(unittest.TestCase):
    def test_measures_real_coverage(self):
        """An actually-called function should score higher than an uncalled one."""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "tests"))
            with open(os.path.join(root, "lib.py"), "w") as f:
                f.write("def covered():\n    return 1\n\n\ndef uncovered():\n    return 2\n")
            open(os.path.join(root, "tests", "__init__.py"), "w").close()
            with open(os.path.join(root, "tests", "test_lib.py"), "w") as f:
                f.write(
                    "import unittest\nfrom lib import covered\n\n\n"
                    "class T(unittest.TestCase):\n    def test_it(self):\n        self.assertEqual(covered(), 1)\n"
                )
            result = coverage_check.run(root)
        self.assertIn("lib.py", result)
        ratio = coverage_check.ratio(result["lib.py"])
        self.assertLess(ratio, 1.0)
        self.assertGreater(ratio, 0.0)


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest

from codequality import complexity_coverage_risk
from codequality.config import Config

_COMPLEX_FN = """
def complex_fn(a, b, c, d):
    if a:
        if b:
            for i in range(10):
                if c and d:
                    while i < 5:
                        if a or b:
                            return i
                        elif c:
                            return c
                        else:
                            return d
    return 0
"""

_SIMPLE_FN = """
def simple_fn(x):
    return x + 1
"""


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


class TestComplexityCoverageRisk(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self.config = Config.load(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_complex_file_with_no_test_ranks_high(self):
        _write(self.root, "risky.py", _COMPLEX_FN)
        rows = complexity_coverage_risk.compute(self.root, self.config)
        by_file = {r["file"]: r for r in rows}
        self.assertFalse(by_file["risky.py"]["has_test"])
        self.assertGreater(by_file["risky.py"]["complexity"], 0)
        self.assertEqual(by_file["risky.py"]["risk_score"], by_file["risky.py"]["complexity"])

    def test_complex_file_with_matching_test_ranks_zero(self):
        _write(self.root, "safe.py", _COMPLEX_FN)
        _write(self.root, "test_safe.py", "def test_something():\n    assert True\n")
        rows = complexity_coverage_risk.compute(self.root, self.config)
        by_file = {r["file"]: r for r in rows}
        self.assertTrue(by_file["safe.py"]["has_test"])
        self.assertGreater(by_file["safe.py"]["complexity"], 0)
        self.assertEqual(by_file["safe.py"]["risk_score"], 0)

    def test_matching_test_via_suffix_convention_also_counts(self):
        _write(self.root, "safe2.py", _COMPLEX_FN)
        _write(self.root, "safe2_test.py", "def test_something():\n    assert True\n")
        rows = complexity_coverage_risk.compute(self.root, self.config)
        by_file = {r["file"]: r for r in rows}
        self.assertTrue(by_file["safe2.py"]["has_test"])
        self.assertEqual(by_file["safe2.py"]["risk_score"], 0)

    def test_untested_but_simple_file_does_not_rank_high(self):
        _write(self.root, "risky.py", _COMPLEX_FN)
        _write(self.root, "trivial.py", _SIMPLE_FN)
        rows = complexity_coverage_risk.compute(self.root, self.config)
        by_file = {r["file"]: r for r in rows}
        self.assertFalse(by_file["trivial.py"]["has_test"])
        self.assertGreater(by_file["risky.py"]["risk_score"], by_file["trivial.py"]["risk_score"])
        ranked_files = [r["file"] for r in rows]
        self.assertLess(ranked_files.index("risky.py"), ranked_files.index("trivial.py"))

    def test_test_files_themselves_are_excluded_from_report(self):
        _write(self.root, "risky.py", _COMPLEX_FN)
        _write(self.root, "test_risky.py", "def test_something():\n    assert True\n")
        rows = complexity_coverage_risk.compute(self.root, self.config)
        files = [r["file"] for r in rows]
        self.assertNotIn("test_risky.py", files)
        self.assertIn("risky.py", files)

    def test_file_with_no_functions_scores_zero_and_is_omitted_from_render(self):
        _write(self.root, "constants.py", "FOO = 1\nBAR = 2\n")
        rows = complexity_coverage_risk.compute(self.root, self.config)
        by_file = {r["file"]: r for r in rows}
        self.assertEqual(by_file["constants.py"]["complexity"], 0)
        self.assertEqual(by_file["constants.py"]["risk_score"], 0)
        text = complexity_coverage_risk.render_text(rows)
        self.assertNotIn("constants.py", text)

    def test_render_text_lists_untested_complex_file(self):
        _write(self.root, "risky.py", _COMPLEX_FN)
        rows = complexity_coverage_risk.compute(self.root, self.config)
        text = complexity_coverage_risk.render_text(rows)
        self.assertIn("risky.py", text)
        self.assertIn("False", text)

    def test_empty_repo_does_not_crash(self):
        rows = complexity_coverage_risk.compute(self.root, self.config)
        self.assertEqual(rows, [])
        self.assertEqual(complexity_coverage_risk.render_text(rows), "No files found.")


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest

from codequality import baseline
from codequality.analyzers.base import FileMetrics, FunctionMetrics, Issue


def _fm_with_issues(path, *symbols_by_line):
    fm = FileMetrics(path=path, language="python", total_lines=20, loc=20)
    for line, symbol in symbols_by_line:
        fm.issues.append(Issue(path, line, "style", "info", symbol, "..."))
    return fm


class TestBaseline(unittest.TestCase):
    def test_build_counts_groups_by_file_and_symbol(self):
        fm = _fm_with_issues("a.py", (1, "unused-import"), (2, "unused-import"), (3, "bare-except"))
        counts = baseline.build_counts([fm])
        self.assertEqual(counts["a.py::unused-import"], 2)
        self.assertEqual(counts["a.py::bare-except"], 1)

    def test_save_and_load_round_trip(self):
        fm = _fm_with_issues("a.py", (1, "unused-import"))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "baseline.json")
            baseline.save(path, [fm])
            counts = baseline.load(path)
        self.assertEqual(counts, {"a.py::unused-import": 1})

    def test_apply_forgives_up_to_the_recorded_count(self):
        fm = _fm_with_issues("a.py", (1, "unused-import"), (2, "unused-import"), (3, "unused-import"))
        baseline.apply([fm], {"a.py::unused-import": 2})
        remaining = [(i.line, i.symbol) for i in fm.issues]
        self.assertEqual(remaining, [(3, "unused-import")])
        self.assertEqual(fm.suppressed_count, 2)

    def test_apply_with_no_baseline_entry_changes_nothing(self):
        fm = _fm_with_issues("a.py", (1, "unused-import"))
        baseline.apply([fm], {"other.py::unused-import": 5})
        self.assertEqual(len(fm.issues), 1)

    def test_apply_forgives_metric_driven_symbol_on_its_function_too(self):
        """high-complexity is scored from FunctionMetrics.complexity directly,
        not just from the issue -- forgiving it must also mark the function
        suppressed, or the score would stay penalized despite the baseline.
        """
        fm = _fm_with_issues("a.py", (5, "high-complexity"))
        fm.functions.append(
            FunctionMetrics(file="a.py", name="f", lineno=5, end_lineno=20, complexity=15,
                             length=15, nesting=1, params=0, has_docstring=True)
        )
        baseline.apply([fm], {"a.py::high-complexity": 1})
        self.assertEqual(len(fm.issues), 0)
        self.assertIn("high-complexity", fm.functions[0].suppressed)


if __name__ == "__main__":
    unittest.main()

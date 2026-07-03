import unittest

from codequality.analyzers.base import FileMetrics, FunctionMetrics, Issue
from codequality.config import DEFAULT_CONFIG, Config
from codequality.scorer import compute_scores, grade


def _config():
    return Config({})


class TestScorer(unittest.TestCase):
    def test_grade_boundaries(self):
        self.assertEqual(grade(95), "A")
        self.assertEqual(grade(90), "A")
        self.assertEqual(grade(89.9), "B")
        self.assertEqual(grade(70), "C")
        self.assertEqual(grade(60), "D")
        self.assertEqual(grade(59.9), "F")

    def test_empty_input_scores_perfect(self):
        result = compute_scores([], _config())
        self.assertEqual(result.overall, 100.0)
        self.assertEqual(result.grade, "A")

    def test_clean_file_scores_higher_than_messy_file(self):
        clean = FileMetrics(path="clean.py", language="python", total_lines=10, loc=10, has_module_docstring=True)
        clean.functions.append(
            FunctionMetrics(
                file="clean.py", name="f", lineno=1, end_lineno=5, complexity=2,
                length=5, nesting=1, params=1, has_docstring=True,
            )
        )

        messy = FileMetrics(path="messy.py", language="python", total_lines=200, loc=200, has_module_docstring=False)
        messy.functions.append(
            FunctionMetrics(
                file="messy.py", name="g", lineno=1, end_lineno=150, complexity=30,
                length=150, nesting=8, params=6, has_docstring=False,
            )
        )
        messy.issues.append(Issue("messy.py", 1, "style", "warn", "bare-except", "Bare except"))

        clean_score = compute_scores([clean], _config()).overall
        messy_score = compute_scores([messy], _config()).overall
        self.assertGreater(clean_score, messy_score)

    def test_weights_are_respected(self):
        cfg = Config({"weights": {"complexity": 100, "structure": 0, "duplication": 0, "documentation": 0, "style": 0}})
        messy_structure_only = FileMetrics(path="a.py", language="python", total_lines=500, loc=500)
        messy_structure_only.functions.append(
            FunctionMetrics(
                file="a.py", name="f", lineno=1, end_lineno=200, complexity=1,
                length=200, nesting=1, params=0, has_docstring=True,
            )
        )
        result = compute_scores([messy_structure_only], cfg)
        # All weight is on complexity (which is fine here), so overall should be ~100
        # even though structure alone would score much lower.
        self.assertGreater(result.overall, 95)

    def test_deterministic_repeated_runs(self):
        fm = FileMetrics(path="a.py", language="python", total_lines=50, loc=50)
        fm.functions.append(
            FunctionMetrics(file="a.py", name="f", lineno=1, end_lineno=20, complexity=12, length=20, nesting=3, params=2, has_docstring=False)
        )
        scores = {compute_scores([fm], _config()).overall for _ in range(10)}
        self.assertEqual(len(scores), 1)


if __name__ == "__main__":
    unittest.main()

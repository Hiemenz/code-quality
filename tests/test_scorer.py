import unittest

from codequality.analyzers.base import FileMetrics, FunctionMetrics, Issue
from codequality.config import Config
from codequality.scorer import compute_scores, grade, score_correctness, score_coverage, score_security


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
        """Sanity check: a tidy file must outscore a messy one under default weights."""
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
        """Zeroing out every category but one should let a defect in the others go unpenalized."""
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

    def test_security_issues_lower_the_security_score(self):
        clean = FileMetrics(path="clean.py", language="python", total_lines=20, loc=20)
        dirty = FileMetrics(path="dirty.py", language="python", total_lines=20, loc=20)
        dirty.issues.append(Issue("dirty.py", 1, "security", "error", "hardcoded-secret", "looks like a secret"))
        self.assertEqual(score_security([clean]), 100.0)
        self.assertLess(score_security([dirty]), 100.0)

    def test_security_category_is_included_in_overall_score(self):
        fm = FileMetrics(path="a.py", language="python", total_lines=20, loc=20)
        fm.issues.append(Issue("a.py", 1, "security", "error", "dangerous-eval", "eval() call"))
        result = compute_scores([fm], _config())
        self.assertIn("security", result.categories)
        self.assertLess(result.categories["security"].score, 100.0)

    def test_correctness_score_penalizes_unresolved_imports_and_type_errors(self):
        clean = FileMetrics(path="clean.py", language="python", total_lines=20, loc=20)
        dirty = FileMetrics(path="dirty.py", language="python", total_lines=20, loc=20)
        dirty.issues.append(Issue("dirty.py", 1, "correctness", "error", "unresolved-import", "..."))
        self.assertEqual(score_correctness([clean]), 100.0)
        self.assertLess(score_correctness([dirty]), 100.0)

    def test_coverage_score_is_100_when_never_measured(self):
        """Coverage is opt-in -- absence of a measurement isn't a penalty."""
        fm = FileMetrics(path="a.py", language="python", total_lines=20, loc=20)
        self.assertEqual(score_coverage([fm]), 100.0)

    def test_coverage_score_reflects_measured_ratio(self):
        fully = FileMetrics(path="a.py", language="python", total_lines=20, loc=20, coverage_ratio=1.0)
        partly = FileMetrics(path="b.py", language="python", total_lines=20, loc=20, coverage_ratio=0.5)
        self.assertEqual(score_coverage([fully]), 100.0)
        self.assertEqual(score_coverage([partly]), 50.0)

    def test_deterministic_repeated_runs(self):
        """Running the same scorer input repeatedly must always yield the same score."""
        fm = FileMetrics(path="a.py", language="python", total_lines=50, loc=50)
        fm.functions.append(
            FunctionMetrics(
                file="a.py", name="f", lineno=1, end_lineno=20, complexity=12,
                length=20, nesting=3, params=2, has_docstring=False,
            )
        )
        scores = {compute_scores([fm], _config()).overall for _ in range(10)}
        self.assertEqual(len(scores), 1)


if __name__ == "__main__":
    unittest.main()

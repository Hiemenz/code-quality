"""Tests for codequality.report_compare."""

import json
import os
import tempfile
import unittest

from codequality.report_compare import (
    compare,
    is_regression,
    load_report,
    render_text,
)


def _report(score=80.0, categories=None, issues=None):
    return {
        "overall": {"score": score, "grade": "B"},
        "categories": categories or {
            "style": {"score": score, "weight": 12},
            "correctness": {"score": score, "weight": 15},
        },
        "issues": issues or [],
    }


def _issue(file="f.py", line=1, symbol="some-rule", message="msg"):
    return {"file": file, "line": line, "symbol": symbol, "rule": symbol, "message": message}


class TestCompare(unittest.TestCase):
    def test_overall_delta_positive_on_improvement(self):
        result = compare(_report(70.0), _report(80.0))
        self.assertAlmostEqual(result["overall_delta"], 10.0)
        self.assertAlmostEqual(result["overall_before"], 70.0)
        self.assertAlmostEqual(result["overall_after"], 80.0)

    def test_overall_delta_negative_on_regression(self):
        result = compare(_report(80.0), _report(75.0))
        self.assertAlmostEqual(result["overall_delta"], -5.0)

    def test_category_deltas_computed(self):
        before = _report(80.0, categories={"style": {"score": 80.0, "weight": 12}})
        after = _report(90.0, categories={"style": {"score": 90.0, "weight": 12}})
        result = compare(before, after)
        self.assertIn("style", result["category_deltas"])
        self.assertAlmostEqual(result["category_deltas"]["style"]["delta"], 10.0)

    def test_new_issues_detected(self):
        before = _report(issues=[])
        after = _report(issues=[_issue(line=5, symbol="new-rule")])
        result = compare(before, after)
        self.assertEqual(result["new_issue_count"], 1)
        self.assertEqual(result["resolved_issue_count"], 0)
        self.assertEqual(result["new_issues"][0]["symbol"], "new-rule")

    def test_resolved_issues_detected(self):
        before = _report(issues=[_issue(line=5, symbol="old-rule")])
        after = _report(issues=[])
        result = compare(before, after)
        self.assertEqual(result["resolved_issue_count"], 1)
        self.assertEqual(result["new_issue_count"], 0)

    def test_unchanged_issues_not_reported(self):
        iss = _issue(line=5, symbol="persistent")
        result = compare(_report(issues=[iss]), _report(issues=[iss]))
        self.assertEqual(result["new_issue_count"], 0)
        self.assertEqual(result["resolved_issue_count"], 0)

    def test_category_in_after_only(self):
        before = _report(categories={})
        after = _report(categories={"security": {"score": 95.0, "weight": 15}})
        result = compare(before, after)
        self.assertIn("security", result["category_deltas"])


class TestIsRegression(unittest.TestCase):
    def test_no_change_is_not_regression(self):
        self.assertFalse(is_regression({"overall_delta": 0.0}))

    def test_improvement_not_regression(self):
        self.assertFalse(is_regression({"overall_delta": 5.0}))

    def test_small_drop_within_tolerance(self):
        self.assertFalse(is_regression({"overall_delta": -2.0}, tolerance=3.0))

    def test_drop_exceeding_tolerance(self):
        self.assertTrue(is_regression({"overall_delta": -3.1}, tolerance=3.0))

    def test_any_drop_with_zero_tolerance(self):
        self.assertTrue(is_regression({"overall_delta": -0.1}))


class TestLoadReport(unittest.TestCase):
    def test_loads_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"overall": {"score": 85.0}}, f)
            path = f.name
        try:
            report = load_report(path)
            self.assertEqual(report["overall"]["score"], 85.0)
        finally:
            os.unlink(path)


class TestRenderText(unittest.TestCase):
    def _delta(self, score_before=80.0, score_after=75.0, new=0, resolved=0):
        return {
            "overall_before": score_before,
            "overall_after": score_after,
            "overall_delta": score_after - score_before,
            "category_deltas": {"style": {"before": score_before, "after": score_after,
                                           "delta": score_after - score_before}},
            "new_issues": [_issue() for _ in range(new)],
            "resolved_issues": [_issue() for _ in range(resolved)],
            "new_issue_count": new,
            "resolved_issue_count": resolved,
        }

    def test_shows_overall_scores(self):
        text = render_text(self._delta(80.0, 75.0))
        self.assertIn("80.0", text)
        self.assertIn("75.0", text)

    def test_shows_regression_on_drop(self):
        text = render_text(self._delta(80.0, 70.0))
        self.assertIn("REGRESSION", text)

    def test_shows_no_regression_on_improvement(self):
        text = render_text(self._delta(70.0, 80.0))
        self.assertIn("No regression", text)

    def test_shows_new_issue_count(self):
        text = render_text(self._delta(80.0, 80.0, new=3))
        self.assertIn("3", text)
        self.assertIn("New issues", text)

    def test_tolerance_in_regression_message(self):
        text = render_text(self._delta(80.0, 75.0), tolerance=3.0)
        self.assertIn("3.0", text)


if __name__ == "__main__":
    unittest.main()

"""Tests for the --fail-on category filtering in build_summary."""

from codequality.report import build_summary
from codequality.analyzers.base import FileMetrics, Issue
from codequality.scorer import ScoreResult, CategoryResult


def _score():
    cats = {name: CategoryResult(score=80.0, weight=10) for name in
            ["complexity", "structure", "duplication", "documentation", "style", "security", "correctness", "coverage"]}
    return ScoreResult(overall=80.0, grade="B", categories=cats)


def _fm_with_issue(category, symbol="test-sym"):
    fm = FileMetrics(path="foo.py", language="python", total_lines=10, loc=8)
    fm.issues = [Issue("foo.py", 1, category, "error", symbol, "msg")]
    return fm


def test_no_fail_on_passes():
    summary = build_summary([_fm_with_issue("security")], _score(), "scan", "/r")
    assert summary["threshold"]["passed"] is True


def test_fail_on_matching_category_fails():
    summary = build_summary(
        [_fm_with_issue("security")], _score(), "scan", "/r",
        fail_on=["security"]
    )
    assert summary["threshold"]["passed"] is False
    assert "security" in summary["threshold"]["fail_on_triggered"]


def test_fail_on_non_matching_category_passes():
    summary = build_summary(
        [_fm_with_issue("style")], _score(), "scan", "/r",
        fail_on=["security"]
    )
    assert summary["threshold"]["passed"] is True


def test_fail_on_comma_separated():
    summary = build_summary(
        [_fm_with_issue("style")], _score(), "scan", "/r",
        fail_on=["security,style"]
    )
    assert summary["threshold"]["passed"] is False
    assert "style" in summary["threshold"]["fail_on_triggered"]


def test_fail_on_combines_with_fail_under():
    summary = build_summary(
        [_fm_with_issue("style")], _score(), "scan", "/r",
        fail_under=90.0, fail_on=["security"]
    )
    assert summary["threshold"]["passed"] is False  # score 80 < 90


def test_fail_on_field_present_in_summary():
    summary = build_summary([], _score(), "scan", "/r", fail_on=["security"])
    assert summary["threshold"]["fail_on"] == ["security"]

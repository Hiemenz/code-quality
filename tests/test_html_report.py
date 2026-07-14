"""Tests for render_html in report.py."""

from codequality.report import render_html


def _summary(issues=None, fail_under=None, fail_on=None):
    return {
        "tool": "codequality",
        "version": "0.0.0",
        "mode": "scan",
        "root": "/repo",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "overall": {"score": 82.5, "grade": "B"},
        "categories": {
            "complexity": {"score": 90.0, "weight": 15},
            "style": {"score": 75.0, "weight": 12},
        },
        "summary": {
            "files_analyzed": 5,
            "loc": 500,
            "functions": 30,
            "issues": len(issues or []),
            "suppressed": 0,
            "test_loc": 100,
            "source_loc": 400,
            "test_ratio": 0.25,
        },
        "worst_files": [{"path": "foo.py", "score": 60.0, "lines": 120}],
        "complex_functions": [],
        "issues": issues or [],
        "diff": None,
        "threshold": {
            "fail_under": fail_under,
            "fail_on": fail_on,
            "fail_on_triggered": None,
            "passed": True,
        },
    }


def test_html_is_valid_html():
    html = render_html(_summary())
    assert "<!DOCTYPE html>" in html
    assert "<title>" in html
    assert "</html>" in html


def test_score_appears():
    html = render_html(_summary())
    assert "82.5" in html
    assert "B" in html


def test_category_appears():
    html = render_html(_summary())
    assert "complexity" in html
    assert "style" in html


def test_worst_file_appears():
    html = render_html(_summary())
    assert "foo.py" in html


def test_issue_appears():
    issues = [{"file": "bar.py", "line": 5, "severity": "error", "symbol": "bare-except", "message": "Bad"}]
    html = render_html(_summary(issues=issues))
    assert "bare-except" in html
    assert "bar.py" in html


def test_special_chars_escaped():
    issues = [{"file": "a<b>.py", "line": 1, "severity": "warn", "symbol": "x", "message": "a&b"}]
    html = render_html(_summary(issues=issues))
    assert "<b>" not in html
    assert "&amp;" in html or "&lt;" in html


def test_filter_script_present():
    issues = [{"file": "f.py", "line": 1, "severity": "info", "symbol": "x", "message": "m"}]
    html = render_html(_summary(issues=issues))
    assert "filterIssues" in html


def test_no_issues_no_table():
    html = render_html(_summary())
    assert "filterIssues" not in html

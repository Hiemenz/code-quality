"""Tests for render_gitlab in report.py (GitLab Code Quality report format)."""

import json

from codequality.report import render_gitlab


def _summary(issues):
    return {"version": "0.0.0", "issues": issues}


def _issue(**kw):
    base = {
        "file": "pkg/mod.py",
        "line": 12,
        "category": "style",
        "severity": "warn",
        "symbol": "long-line",
        "message": "Line too long",
    }
    base.update(kw)
    return base


def test_gitlab_report_is_a_json_array_of_findings():
    out = json.loads(render_gitlab(_summary([_issue()])))
    assert isinstance(out, list)
    finding = out[0]
    assert set(finding) == {"description", "check_name", "fingerprint", "severity", "location"}
    assert finding["description"] == "Line too long"
    assert finding["check_name"] == "long-line"
    assert finding["location"] == {"path": "pkg/mod.py", "lines": {"begin": 12}}


def test_severity_mapping():
    issues = [
        _issue(severity="error", symbol="syntax-error"),
        _issue(severity="warn", symbol="bare-except"),
        _issue(severity="info", symbol="magic-number"),
    ]
    out = json.loads(render_gitlab(_summary(issues)))
    by_symbol = {f["check_name"]: f["severity"] for f in out}
    assert by_symbol["syntax-error"] == "critical"
    assert by_symbol["bare-except"] == "major"
    assert by_symbol["magic-number"] == "minor"


def test_fingerprint_is_stable_and_distinct():
    a1 = json.loads(render_gitlab(_summary([_issue()])))[0]["fingerprint"]
    a2 = json.loads(render_gitlab(_summary([_issue()])))[0]["fingerprint"]
    b = json.loads(render_gitlab(_summary([_issue(line=99)])))[0]["fingerprint"]
    assert a1 == a2  # deterministic
    assert a1 != b   # different location -> different fingerprint


def test_line_floor_is_one():
    out = json.loads(render_gitlab(_summary([_issue(line=0)])))
    assert out[0]["location"]["lines"]["begin"] == 1


def test_empty_issues_produces_empty_array():
    assert json.loads(render_gitlab(_summary([]))) == []

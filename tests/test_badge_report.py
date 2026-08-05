"""Tests for render_badge in report.py."""

import json

from codequality.report import _badge_color, render_badge


def _summary(score, grade):
    return {
        "overall": {"score": score, "grade": grade},
    }


def test_badge_is_valid_shields_endpoint_json():
    out = json.loads(render_badge(_summary(92.8, "A")))
    assert out["schemaVersion"] == 1
    assert out["label"] == "code quality"
    assert out["message"] == "92.8 (A)"
    assert out["color"] == "brightgreen"


def test_badge_message_formats_one_decimal():
    out = json.loads(render_badge(_summary(70.0, "C")))
    assert out["message"] == "70.0 (C)"


def test_badge_color_thresholds():
    assert _badge_color(95) == "brightgreen"
    assert _badge_color(85) == "green"
    assert _badge_color(75) == "yellowgreen"
    assert _badge_color(65) == "yellow"
    assert _badge_color(55) == "orange"
    assert _badge_color(30) == "red"
    # boundaries are inclusive on the upper band
    assert _badge_color(90) == "brightgreen"
    assert _badge_color(60) == "yellow"

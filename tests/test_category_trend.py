"""Tests for the per-category section in render_trend_text."""

from codequality.history import render_trend_text, _render_category_section


def _entry(overall=85.0, categories=None):
    return {
        "timestamp": "2026-01-01T00:00:00+00:00",
        "overall": overall,
        "grade": "B",
        "categories": categories or {"style": 80.0, "security": 90.0},
        "test_loc": 100,
        "source_loc": 400,
        "test_ratio": 0.25,
    }


def test_category_section_shows_categories():
    entries = [_entry(80.0), _entry(85.0)]
    lines = _render_category_section(entries)
    text = "\n".join(lines)
    assert "style" in text
    assert "security" in text


def test_category_section_shows_sparkline():
    entries = [_entry(80.0, {"style": 70.0}), _entry(85.0, {"style": 90.0})]
    lines = _render_category_section(entries)
    text = "\n".join(lines)
    assert "style" in text
    # sparkline chars from _SPARK_CHARS range
    assert any(c in text for c in "▁▂▃▄▅▆▇█")


def test_category_section_shows_latest():
    entries = [_entry(categories={"correctness": 77.5})]
    lines = _render_category_section(entries)
    text = "\n".join(lines)
    assert "77.5" in text


def test_render_trend_text_includes_categories():
    entries = [_entry(80.0), _entry(85.0)]
    text = render_trend_text(entries)
    assert "Category Score History" in text
    assert "style" in text


def test_render_trend_text_no_entries():
    assert render_trend_text([]) == "No history entries found."


def test_category_section_empty_if_no_category_data():
    entries = [{"timestamp": "t", "overall": 80.0, "grade": "B",
                "test_loc": 0, "source_loc": 0, "test_ratio": None}]
    lines = _render_category_section(entries)
    assert lines == []

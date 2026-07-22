"""Tests for codequality.suppression_debt."""

import unittest

from codequality.suppression_debt import (
    DEFAULT_STALE_DAYS,
    _match_kind,
    render_text,
    summarize,
)


class TestMatchKind(unittest.TestCase):
    """Unit tests for the suppression pattern detector."""

    def test_bare_noqa_flagged(self):
        self.assertEqual(_match_kind("x = foo()  # noqa"), "noqa")

    def test_uppercase_noqa_flagged(self):
        self.assertEqual(_match_kind("x = foo()  # NOQA"), "noqa")

    def test_scoped_noqa_not_flagged(self):
        self.assertIsNone(_match_kind("x = foo()  # noqa: E501"))

    def test_scoped_noqa_with_space_not_flagged(self):
        self.assertIsNone(_match_kind("x = foo()  # noqa: E501, F401"))

    def test_bare_type_ignore_flagged(self):
        self.assertEqual(_match_kind("x: int = foo()  # type: ignore"), "type-ignore")

    def test_scoped_type_ignore_not_flagged(self):
        self.assertIsNone(_match_kind("x = foo()  # type: ignore[attr-defined]"))

    def test_bare_codequality_ignore_flagged(self):
        self.assertEqual(
            _match_kind("x = 1  # codequality: ignore"),
            "codequality-ignore",
        )

    def test_scoped_codequality_ignore_not_flagged(self):
        self.assertIsNone(_match_kind("x = 1  # codequality: ignore[todo-marker]"))

    def test_normal_comment_not_flagged(self):
        self.assertIsNone(_match_kind("# just a comment"))

    def test_no_comment_not_flagged(self):
        self.assertIsNone(_match_kind("x = 1"))

    def test_codequality_ignore_any_comment_prefix(self):
        self.assertEqual(
            _match_kind("x = 1  // codequality: ignore"),
            "codequality-ignore",
        )


class TestSummarize(unittest.TestCase):
    def _entry(self, group, age, stale=False):
        return {"group": group, "age_days": age, "stale": stale}

    def test_empty(self):
        groups = summarize([])
        self.assertIsNone(groups["ai"]["avg_age_days"])
        self.assertIsNone(groups["human"]["oldest_age_days"])
        self.assertEqual(groups["ai"]["count"], 0)

    def test_counts_correctly(self):
        entries = [
            self._entry("ai", 30),
            self._entry("ai", 100, stale=True),
            self._entry("human", 50),
        ]
        groups = summarize(entries)
        self.assertEqual(groups["ai"]["count"], 2)
        self.assertEqual(groups["human"]["count"], 1)
        self.assertEqual(groups["ai"]["stale_count"], 1)
        self.assertEqual(groups["ai"]["oldest_age_days"], 100)
        self.assertAlmostEqual(groups["ai"]["avg_age_days"], 65.0)

    def test_human_no_stale(self):
        entries = [self._entry("human", 10)]
        groups = summarize(entries)
        self.assertEqual(groups["human"]["stale_count"], 0)


class TestRenderText(unittest.TestCase):
    def test_no_entries(self):
        text = render_text([])
        self.assertIn("Suppression Debt", text)
        self.assertIn("(none)", text)

    def test_stale_entry_listed(self):
        entries = [{
            "file": "foo.py", "line": 10, "kind": "noqa",
            "snippet": "x = foo()  # noqa",
            "sha": "abc12345", "commit_date": "2025-01-01T00:00:00+00:00",
            "age_days": 200, "group": "human", "stale": True,
        }]
        text = render_text(entries, stale_days=90)
        self.assertIn("foo.py:10", text)
        self.assertIn("200d", text)
        self.assertIn("noqa", text)

    def test_non_stale_not_listed(self):
        entries = [{
            "file": "foo.py", "line": 5, "kind": "type-ignore",
            "snippet": "x  # type: ignore",
            "sha": "abc12345", "commit_date": "2026-07-01T00:00:00+00:00",
            "age_days": 10, "group": "ai", "stale": False,
        }]
        text = render_text(entries, stale_days=90)
        self.assertNotIn("foo.py:5", text)

    def test_stale_threshold_in_header(self):
        text = render_text([], stale_days=180)
        self.assertIn("180", text)


if __name__ == "__main__":
    unittest.main()

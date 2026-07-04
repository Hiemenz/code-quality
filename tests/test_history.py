import os
import tempfile
import unittest

from codequality.analyzers.base import FileMetrics
from codequality.config import Config
from codequality.history import append_entry, read_entries, render_trend_text
from codequality.report import build_summary
from codequality.scorer import compute_scores


def _summary_for(file_metrics_list):
    result = compute_scores(file_metrics_list, Config({}))
    return build_summary(file_metrics_list, result, "scan", ".")


class TestBuildSummaryTestRatio(unittest.TestCase):
    """The test/source LOC split that `history.py` records comes from
    `build_summary` -- covered here since both features are new together.
    """

    def test_splits_test_and_source_loc(self):
        files = [
            FileMetrics(path="codequality/scanner.py", language="python", total_lines=20, loc=20),
            FileMetrics(path="tests/test_scanner.py", language="python", total_lines=10, loc=10),
            FileMetrics(path="lib_test.py", language="python", total_lines=0, loc=5),
        ]
        # lib_test.py matches the `*_test.py` convention even without a tests/ dir.
        summary = _summary_for(files)
        s = summary["summary"]
        self.assertEqual(s["source_loc"], 20)
        self.assertEqual(s["test_loc"], 15)
        self.assertAlmostEqual(s["test_ratio"], 15 / 20)
        self.assertEqual(s["loc"], 35)

    def test_no_source_files_gives_none_ratio_not_a_crash(self):
        files = [FileMetrics(path="tests/test_only.py", language="python", total_lines=10, loc=10)]
        summary = _summary_for(files)
        s = summary["summary"]
        self.assertEqual(s["source_loc"], 0)
        self.assertEqual(s["test_loc"], 10)
        self.assertIsNone(s["test_ratio"])

    def test_no_files_at_all(self):
        summary = _summary_for([])
        s = summary["summary"]
        self.assertEqual(s["source_loc"], 0)
        self.assertEqual(s["test_loc"], 0)
        self.assertIsNone(s["test_ratio"])

    def test_test_directory_convention_without_test_prefix(self):
        """A file under test/ or tests/ counts as a test file even without
        a test_*.py/*_test.py name -- matches property_scaffold's convention
        plus the directory check documented for this feature.
        """
        files = [FileMetrics(path="test/helpers.py", language="python", total_lines=5, loc=5)]
        summary = _summary_for(files)
        self.assertEqual(summary["summary"]["test_loc"], 5)
        self.assertEqual(summary["summary"]["source_loc"], 0)


class TestHistoryEntries(unittest.TestCase):
    def _write_and_read(self, summaries):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            for summary in summaries:
                append_entry(path, summary)
            return read_entries(path)

    def test_append_entry_records_test_ratio_fields(self):
        files = [
            FileMetrics(path="src/a.py", language="python", total_lines=10, loc=10),
            FileMetrics(path="tests/test_a.py", language="python", total_lines=4, loc=4),
        ]
        summary = _summary_for(files)
        entries = self._write_and_read([summary])
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["test_loc"], 4)
        self.assertEqual(entry["source_loc"], 10)
        self.assertAlmostEqual(entry["test_ratio"], 0.4)
        # Existing fields must still be there.
        self.assertIn("overall", entry)
        self.assertIn("categories", entry)

    def test_append_entry_handles_zero_source_loc_without_crashing(self):
        files = [FileMetrics(path="tests/test_only.py", language="python", total_lines=4, loc=4)]
        summary = _summary_for(files)
        entries = self._write_and_read([summary])
        self.assertIsNone(entries[0]["test_ratio"])
        self.assertEqual(entries[0]["test_loc"], 4)
        self.assertEqual(entries[0]["source_loc"], 0)


class TestRenderTrendText(unittest.TestCase):
    def test_shows_score_and_test_ratio_sections(self):
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00+00:00", "overall": 80.0, "grade": "B",
                "categories": {}, "test_loc": 10, "source_loc": 20, "test_ratio": 0.5,
            },
            {
                "timestamp": "2026-01-02T00:00:00+00:00", "overall": 85.0, "grade": "B",
                "categories": {}, "test_loc": 15, "source_loc": 20, "test_ratio": 0.75,
            },
        ]
        text = render_trend_text(entries)
        self.assertIn("Score History", text)
        self.assertIn("Test Ratio History", text)
        self.assertIn("0.50", text)
        self.assertIn("0.75", text)
        self.assertIn("+0.25", text)

    def test_handles_none_ratio_without_crashing(self):
        """A run with zero source LOC has a `None` test_ratio -- must render
        as "n/a", never raise a ZeroDivisionError or TypeError.
        """
        entries = [
            {
                "timestamp": "2026-01-01T00:00:00+00:00", "overall": 80.0, "grade": "B",
                "categories": {}, "test_loc": 10, "source_loc": 0, "test_ratio": None,
            },
        ]
        text = render_trend_text(entries)
        self.assertIn("n/a", text)

    def test_missing_ratio_fields_on_old_entries_do_not_crash(self):
        """Entries recorded before this feature existed won't have the new
        keys at all -- rendering must tolerate that, not KeyError.
        """
        entries = [
            {"timestamp": "2026-01-01T00:00:00+00:00", "overall": 80.0, "grade": "B", "categories": {}},
        ]
        text = render_trend_text(entries)
        self.assertIn("n/a", text)

    def test_empty_entries(self):
        self.assertEqual(render_trend_text([]), "No history entries found.")


if __name__ == "__main__":
    unittest.main()

"""Tests for diff-mode patch coverage: report.py's `_patch_coverage`
aggregation, the range-collapsing helpers, and its rendering in
render_text/render_markdown/render_html.
"""

import unittest

from codequality.analyzers.base import FileMetrics
from codequality.config import Config
from codequality.report import (
    _collapse_ranges,
    _format_ranges,
    _patch_coverage,
    build_summary,
    render_html,
    render_markdown,
    render_text,
)
from codequality.scorer import compute_scores


def _fm(path, covered=frozenset(), uncovered=frozenset(), ratio=None):
    return FileMetrics(
        path=path, language="python", total_lines=10, loc=10,
        coverage_ratio=ratio, coverage_covered_lines=frozenset(covered),
        coverage_uncovered_lines=frozenset(uncovered),
    )


class TestCollapseRanges(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(_collapse_ranges([]), [])

    def test_single_number(self):
        self.assertEqual(_collapse_ranges([5]), [(5, 5)])

    def test_contiguous_run(self):
        self.assertEqual(_collapse_ranges([1, 2, 3]), [(1, 3)])

    def test_multiple_runs(self):
        self.assertEqual(_collapse_ranges([1, 2, 3, 7, 8, 12]), [(1, 3), (7, 8), (12, 12)])

    def test_unsorted_input(self):
        self.assertEqual(_collapse_ranges([3, 1, 2]), [(1, 3)])

    def test_format_ranges(self):
        self.assertEqual(_format_ranges([1, 2, 3, 7, 8, 12]), "1-3, 7-8, 12")


class TestPatchCoverage(unittest.TestCase):
    def test_none_in_scan_mode(self):
        fms = [_fm("a.py", covered={1}, ratio=1.0)]
        self.assertIsNone(_patch_coverage(fms, "scan"))

    def test_none_when_nothing_measured(self):
        fms = [_fm("a.py")]  # coverage_ratio stays None -- not measured
        self.assertIsNone(_patch_coverage(fms, "diff"))

    def test_aggregates_counts_across_files(self):
        fms = [
            _fm("a.py", covered={1, 2}, uncovered={3}, ratio=2 / 3),
            _fm("b.py", covered={10}, uncovered=set(), ratio=1.0),
        ]
        pc = _patch_coverage(fms, "diff")
        self.assertEqual(pc["covered_lines"], 3)
        self.assertEqual(pc["total_lines"], 4)
        self.assertAlmostEqual(pc["ratio"], 0.75)

    def test_uncovered_listing_sorted_by_path(self):
        fms = [
            _fm("z.py", covered=set(), uncovered={5, 6}, ratio=0.0),
            _fm("a.py", covered=set(), uncovered={1}, ratio=0.0),
        ]
        pc = _patch_coverage(fms, "diff")
        self.assertEqual([e["file"] for e in pc["uncovered"]], ["a.py", "z.py"])
        self.assertEqual(pc["uncovered"][1]["lines"], [5, 6])

    def test_fully_covered_file_not_in_uncovered_listing(self):
        fms = [_fm("a.py", covered={1, 2}, uncovered=set(), ratio=1.0)]
        pc = _patch_coverage(fms, "diff")
        self.assertEqual(pc["uncovered"], [])


class TestPatchCoverageRendering(unittest.TestCase):
    def _summary(self, fms, mode="diff"):
        score_result = compute_scores(fms, Config({}))
        return build_summary(fms, score_result, mode, "/repo")

    def test_build_summary_includes_patch_coverage_key(self):
        fms = [_fm("a.py", covered={1}, uncovered={2}, ratio=0.5)]
        summary = self._summary(fms)
        self.assertIsNotNone(summary["patch_coverage"])
        self.assertEqual(summary["patch_coverage"]["total_lines"], 2)

    def test_scan_mode_has_no_patch_coverage(self):
        fms = [_fm("a.py", covered={1}, uncovered={2}, ratio=0.5)]
        summary = self._summary(fms, mode="scan")
        self.assertIsNone(summary["patch_coverage"])

    def test_render_text_shows_patch_coverage(self):
        fms = [_fm("a.py", covered={1, 2}, uncovered={3, 4, 5}, ratio=0.4)]
        summary = self._summary(fms)
        text = render_text(summary, use_color=False)
        self.assertIn("Patch coverage", text)
        self.assertIn("2/5 changed lines covered", text)
        self.assertIn("a.py:3-5", text)

    def test_render_text_no_section_without_coverage(self):
        fms = [_fm("a.py")]
        summary = self._summary(fms)
        text = render_text(summary, use_color=False)
        self.assertNotIn("Patch coverage", text)

    def test_render_markdown_shows_patch_coverage(self):
        fms = [_fm("a.py", covered={1}, uncovered={2}, ratio=0.5)]
        summary = self._summary(fms)
        md = render_markdown(summary)
        self.assertIn("Patch coverage", md)
        self.assertIn("1/2 changed lines covered", md)

    def test_render_html_shows_patch_coverage(self):
        fms = [_fm("a.py", covered={1}, uncovered={2}, ratio=0.5)]
        summary = self._summary(fms)
        html = render_html(summary)
        self.assertIn("Patch coverage", html)
        self.assertIn("a.py", html)


if __name__ == "__main__":
    unittest.main()

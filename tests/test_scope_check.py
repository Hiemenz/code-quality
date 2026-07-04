import unittest

from codequality.analyzers.scope_check import scope_mismatch_issues


class TestScopeCheck(unittest.TestCase):
    def test_unrelated_file_in_different_area_is_flagged(self):
        issues = scope_mismatch_issues(
            "Fix signature_diff off-by-one",
            ["codequality/analyzers/signature_diff.py", "docs/unrelated_notes.md"],
        )
        self.assertIn("docs/unrelated_notes.md", issues)
        self.assertNotIn("codequality/analyzers/signature_diff.py", issues)

    def test_all_files_matching_is_silent(self):
        issues = scope_mismatch_issues(
            "Fix signature_diff bug",
            ["codequality/analyzers/signature_diff.py", "tests/test_signature_diff.py"],
        )
        self.assertEqual(issues, {})

    def test_vague_description_produces_no_tokens(self):
        issues = scope_mismatch_issues("fix bug", ["a/one.py", "b/two.py"])
        self.assertEqual(issues, {})

    def test_single_changed_file_is_silent(self):
        issues = scope_mismatch_issues("Fix signature_diff bug", ["codequality/analyzers/signature_diff.py"])
        self.assertEqual(issues, {})

    def test_no_file_matches_is_silent(self):
        issues = scope_mismatch_issues("Improve widget rendering", ["a/one.py", "b/two.py"])
        self.assertEqual(issues, {})

    def test_unrelated_file_in_same_directory_as_a_match_is_not_flagged(self):
        issues = scope_mismatch_issues(
            "Fix signature_diff bug",
            ["codequality/analyzers/signature_diff.py", "codequality/analyzers/duplication.py"],
        )
        self.assertEqual(issues, {})

    def test_empty_description_is_silent(self):
        issues = scope_mismatch_issues(None, ["a/one.py", "b/two.py"])
        self.assertEqual(issues, {})


if __name__ == "__main__":
    unittest.main()

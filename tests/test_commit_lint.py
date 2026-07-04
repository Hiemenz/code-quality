import os
import subprocess
import tempfile
import unittest

from codequality import commit_lint


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


class TestCommitLint(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_generic_subject_fix_fails(self):
        _commit(self.repo, "a.py", "x = 1\n", "fix")
        result = commit_lint.compute(self.repo)
        self.assertEqual(result["human"]["failed"], 1)
        self.assertIn("generic-subject", result["human"]["by_rule"])
        self.assertEqual(len(result["failures"]), 1)
        self.assertIn("generic-subject", result["failures"][0]["failed_rules"])

    def test_generic_subject_wip_fails(self):
        _commit(self.repo, "a.py", "x = 1\n", "wip")
        result = commit_lint.compute(self.repo)
        self.assertEqual(result["human"]["failed"], 1)
        self.assertIn("generic-subject", result["human"]["by_rule"])

    def test_descriptive_subject_passes_everything(self):
        _commit(self.repo, "a.py", "x = 1\n", "Add retry logic to the upload path")
        result = commit_lint.compute(self.repo, strict=True)
        self.assertEqual(result["human"]["failed"], 0)
        self.assertEqual(result["failures"], [])

    def test_subject_starting_with_fix_is_not_flagged_as_generic(self):
        """Only an *exact* match to the banned list counts -- a real
        descriptive subject that happens to start with "fix" must not be
        flagged just because of that prefix.
        """
        _commit(self.repo, "a.py", "x = 1\n", "Fix the null pointer in auth")
        result = commit_lint.compute(self.repo)
        self.assertNotIn("generic-subject", result["human"]["by_rule"])

    def test_short_subject_fails_too_short(self):
        _commit(self.repo, "a.py", "x = 1\n", "oops fix")
        result = commit_lint.compute(self.repo)
        self.assertEqual(result["human"]["by_rule"].get("too-short"), 1)

    def test_strict_rules_only_apply_when_requested(self):
        """trailing-period/not-capitalized must be silent unless --strict is passed."""
        _commit(self.repo, "a.py", "x = 1\n", "lowercase subject with a period.")

        lax = commit_lint.compute(self.repo, strict=False)
        self.assertEqual(lax["human"]["failed"], 0)
        self.assertNotIn("trailing-period", lax["human"]["by_rule"])
        self.assertNotIn("not-capitalized", lax["human"]["by_rule"])

        strict = commit_lint.compute(self.repo, strict=True)
        self.assertEqual(strict["human"]["failed"], 1)
        self.assertIn("trailing-period", strict["human"]["by_rule"])
        self.assertIn("not-capitalized", strict["human"]["by_rule"])

    def test_ai_marker_classifies_commit_as_ai(self):
        _commit(self.repo, "a.py", "x = 1\n", "fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        result = commit_lint.compute(self.repo)
        self.assertEqual(result["ai"]["commits"], 1)
        self.assertEqual(result["ai"]["failed"], 1)
        self.assertEqual(result["human"]["commits"], 0)

    def test_empty_group_reports_none_rate_not_a_crash(self):
        _commit(self.repo, "a.py", "x = 1\n", "Add retry logic to the upload path")
        result = commit_lint.compute(self.repo)
        self.assertIsNone(result["ai"]["rate"])

    def test_failures_list_is_capped_but_total_is_reported(self):
        for i in range(3):
            _commit(self.repo, f"a{i}.py", "x = 1\n", "fix")
        result = commit_lint.compute(self.repo, max_failures=2)
        self.assertEqual(len(result["failures"]), 2)
        self.assertEqual(result["failures_total"], 3)


if __name__ == "__main__":
    unittest.main()

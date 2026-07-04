import os
import subprocess
import tempfile
import unittest

from codequality import churn


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


class TestChurn(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ai_commit_reworked_within_window_is_counted(self):
        _commit(self.repo, "a.py", "x = 1\n", "initial AI change\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
        _commit(self.repo, "a.py", "x = 2\n", "follow-up fix")

        counts = churn.compute(self.repo, window_days=30)
        self.assertEqual(counts["ai"]["commits"], 1)
        self.assertEqual(counts["ai"]["reworked"], 1)
        self.assertEqual(counts["human"]["commits"], 1)

    def test_marker_matching_is_case_insensitive(self):
        """GitHub normalizes 'Co-Authored-By:' to 'Co-authored-by:' on squash
        merge -- the default marker must still match that variant.
        """
        _commit(self.repo, "a.py", "x = 1\n", "change\n\nCo-authored-by: Claude <noreply@anthropic.com>")
        counts = churn.compute(self.repo, window_days=30)
        self.assertEqual(counts["ai"]["commits"], 1)
        self.assertEqual(counts["human"]["commits"], 0)

    def test_custom_marker_is_respected(self):
        _commit(self.repo, "a.py", "x = 1\n", "change\n\nGenerated-By: MyBot")
        counts = churn.compute(self.repo, marker="Generated-By: MyBot", window_days=30)
        self.assertEqual(counts["ai"]["commits"], 1)

    def test_commit_with_no_follow_up_is_not_reworked(self):
        _commit(self.repo, "a.py", "x = 1\n", "only change\n\nCo-Authored-By: Claude")
        counts = churn.compute(self.repo, window_days=30)
        self.assertEqual(counts["ai"]["commits"], 1)
        self.assertEqual(counts["ai"]["reworked"], 0)

    def test_empty_group_reports_none_rate_not_a_crash(self):
        _commit(self.repo, "a.py", "x = 1\n", "human only change")
        counts = churn.compute(self.repo, window_days=30)
        self.assertIsNone(counts["ai"]["rate"])


if __name__ == "__main__":
    unittest.main()

import os
import subprocess
import tempfile
import unittest

from codequality import edit_distance


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


_AI_TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"


class TestEditDistance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_untouched_lines_show_zero_edit_distance(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", f"initial AI change\n\n{_AI_TRAILER}")

        counts = edit_distance.compute(self.repo)
        self.assertEqual(counts["ai"]["commits"], 1)
        self.assertEqual(counts["ai"]["lines_added"], 2)
        self.assertEqual(counts["ai"]["lines_survived"], 2)
        self.assertEqual(counts["ai"]["mean_edit_distance"], 0.0)

    def test_fully_overwritten_lines_show_high_edit_distance(self):
        """A later commit that replaces every added line drives edit
        distance for the original commit to 1.0.
        """
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", f"initial AI change\n\n{_AI_TRAILER}")
        _commit(self.repo, "a.py", "x = 100\ny = 200\n", "human rewrite")

        counts = edit_distance.compute(self.repo)
        self.assertEqual(counts["ai"]["commits"], 1)
        self.assertEqual(counts["ai"]["lines_added"], 2)
        self.assertEqual(counts["ai"]["lines_survived"], 0)
        self.assertEqual(counts["ai"]["mean_edit_distance"], 1.0)
        self.assertEqual(counts["human"]["commits"], 1)
        self.assertEqual(counts["human"]["mean_edit_distance"], 0.0)

    def test_partial_overwrite_gives_fractional_edit_distance(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", f"initial AI change\n\n{_AI_TRAILER}")
        _commit(self.repo, "a.py", "x = 1\ny = 200\n", "human tweak")

        counts = edit_distance.compute(self.repo)
        self.assertEqual(counts["ai"]["lines_added"], 2)
        self.assertEqual(counts["ai"]["lines_survived"], 1)
        self.assertAlmostEqual(counts["ai"]["mean_edit_distance"], 0.5)

    def test_marker_matching_is_case_insensitive(self):
        _commit(self.repo, "a.py", "x = 1\n", "change\n\nCo-authored-by: Claude <noreply@anthropic.com>")
        counts = edit_distance.compute(self.repo)
        self.assertEqual(counts["ai"]["commits"], 1)
        self.assertEqual(counts["human"]["commits"], 0)

    def test_custom_marker_is_respected(self):
        _commit(self.repo, "a.py", "x = 1\n", "change\n\nGenerated-By: MyBot")
        counts = edit_distance.compute(self.repo, marker="Generated-By: MyBot")
        self.assertEqual(counts["ai"]["commits"], 1)

    def test_deleted_file_contributes_zero_survived_lines(self):
        """A file removed after the commit that added its lines can't be
        blamed at HEAD, so it counts as 0 survived lines, not a crash.
        """
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", f"initial AI change\n\n{_AI_TRAILER}")
        os.remove(os.path.join(self.repo, "a.py"))
        _git(["add", "-A"], self.repo)
        _git(["commit", "-q", "-m", "remove file"], self.repo)

        counts = edit_distance.compute(self.repo)
        self.assertEqual(counts["ai"]["lines_added"], 2)
        self.assertEqual(counts["ai"]["lines_survived"], 0)
        self.assertEqual(counts["ai"]["mean_edit_distance"], 1.0)

    def test_empty_repo_reports_none_rate_not_a_crash(self):
        counts = edit_distance.compute(self.repo)
        self.assertIsNone(counts["ai"]["mean_edit_distance"])
        self.assertIsNone(counts["human"]["mean_edit_distance"])
        self.assertEqual(counts["ai"]["commits"], 0)

    def test_pure_deletion_commit_is_skipped_not_counted(self):
        """A commit that only removes lines (lines_added == 0) is excluded
        from the aggregate entirely, since its ratio is undefined.
        """
        _commit(self.repo, "a.py", "x = 1\n", "human add\n")
        with open(os.path.join(self.repo, "a.py"), "w") as f:
            f.write("")
        _git(["add", "."], self.repo)
        _git(["commit", "-q", "-m", "human delete content"], self.repo)

        counts = edit_distance.compute(self.repo)
        self.assertEqual(counts["human"]["commits"], 1)


if __name__ == "__main__":
    unittest.main()

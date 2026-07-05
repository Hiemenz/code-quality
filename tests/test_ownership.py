import os
import subprocess
import tempfile
import unittest

from codequality import ownership


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message, author=None):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    args = ["commit", "-q", "-m", message]
    if author:
        args.append(f"--author={author}")
    _git(args, cwd)


_AI_TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"


class TestOwnership(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_single_author_file_shows_full_concentration(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\nz = 3\n", "initial commit")

        entries = ownership.compute(self.repo)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["file"], "a.py")
        self.assertEqual(entry["total_lines"], 3)
        self.assertEqual(entry["top_author_lines"], 3)
        self.assertEqual(entry["top_author_share"], 1.0)
        self.assertEqual(entry["author_count"], 1)
        self.assertTrue(entry["low_bus_factor"])

    def test_two_authors_split_gives_correct_share(self):
        _commit(self.repo, "a.py", "x = 1\n", "initial commit", author="Alice <alice@example.com>")
        _commit(
            self.repo, "a.py", "x = 1\ny = 2\nz = 3\nw = 4\n", "add more lines",
            author="Bob <bob@example.com>",
        )

        entries = ownership.compute(self.repo)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["total_lines"], 4)
        self.assertEqual(entry["author_count"], 2)
        self.assertEqual(entry["top_author"], "Bob")
        self.assertEqual(entry["top_author_lines"], 3)
        self.assertAlmostEqual(entry["top_author_share"], 0.75)
        self.assertFalse(entry["low_bus_factor"])

    def test_ai_marked_commit_gives_near_full_ai_line_fraction(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\nz = 3\n", f"AI change\n\n{_AI_TRAILER}")

        entries = ownership.compute(self.repo)
        entry = entries[0]
        self.assertEqual(entry["ai_line_fraction"], 1.0)

    def test_human_commit_gives_zero_ai_line_fraction(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", "human change, no marker here")

        entries = ownership.compute(self.repo)
        entry = entries[0]
        self.assertEqual(entry["ai_line_fraction"], 0.0)

    def test_marker_matching_is_case_insensitive(self):
        _commit(self.repo, "a.py", "x = 1\n", "change\n\nCo-authored-by: Claude <noreply@anthropic.com>")
        entries = ownership.compute(self.repo)
        self.assertEqual(entries[0]["ai_line_fraction"], 1.0)

    def test_custom_marker_is_respected(self):
        _commit(self.repo, "a.py", "x = 1\n", "change\n\nGenerated-By: MyBot")
        entries = ownership.compute(self.repo, marker="Generated-By: MyBot")
        self.assertEqual(entries[0]["ai_line_fraction"], 1.0)

    def test_custom_threshold_changes_the_flag(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", "initial commit", author="Alice <alice@example.com>")
        _commit(
            self.repo, "a.py", "x = 1\ny = 2\nz = 3\nw = 4\n", "add more lines",
            author="Bob <bob@example.com>",
        )

        entries = ownership.compute(self.repo, threshold=0.4)
        self.assertTrue(entries[0]["low_bus_factor"])

        entries = ownership.compute(self.repo, threshold=0.9)
        self.assertFalse(entries[0]["low_bus_factor"])

    def test_empty_repo_does_not_crash(self):
        entries = ownership.compute(self.repo)
        self.assertEqual(entries, [])

    def test_render_text_runs_without_error(self):
        _commit(self.repo, "a.py", "x = 1\n", "initial commit")
        entries = ownership.compute(self.repo)
        text = ownership.render_text(entries, 0.9)
        self.assertIn("a.py", text)

    def test_render_text_handles_no_entries(self):
        text = ownership.render_text([], 0.9)
        self.assertIn("No blamable files found", text)


if __name__ == "__main__":
    unittest.main()

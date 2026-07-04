import os
import subprocess
import tempfile
import unittest

from codequality.git_utils import get_file_at_ref, get_last_commit_subject


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


class TestGitUtilsExtras(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def _commit(self, path, content, message):
        with open(os.path.join(self.repo, path), "w") as f:
            f.write(content)
        _git(["add", "."], self.repo)
        _git(["commit", "-q", "-m", message], self.repo)

    def test_get_file_at_ref_returns_old_content(self):
        self._commit("a.py", "x = 1\n", "first")
        self._commit("a.py", "x = 2\n", "second")
        old_content = get_file_at_ref("HEAD~1", "a.py", self.repo)
        self.assertEqual(old_content, "x = 1\n")

    def test_get_file_at_ref_returns_none_for_new_file(self):
        self._commit("a.py", "x = 1\n", "first")
        result = get_file_at_ref("HEAD", "never_existed.py", self.repo)
        self.assertIsNone(result)

    def test_get_last_commit_subject(self):
        self._commit("a.py", "x = 1\n", "fix the thing")
        self.assertEqual(get_last_commit_subject(self.repo), "fix the thing")


if __name__ == "__main__":
    unittest.main()

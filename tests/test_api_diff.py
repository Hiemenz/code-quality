import os
import subprocess
import tempfile
import unittest

from codequality import api_diff


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    full = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(full) or cwd, exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


def _rm_commit(cwd, path, message):
    os.remove(os.path.join(cwd, path))
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


class TestApiDiff(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_removed_parameter_between_two_refs_is_detected(self):
        _commit(self.repo, "a.py", "def f(a, b):\n    return a + b\n", "v1")
        from_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        _commit(self.repo, "a.py", "def f(a):\n    return a\n", "v2 -- drop a param")

        result = api_diff.compare(self.repo, from_sha, "HEAD")
        symbols = {i["symbol"] for i in result["issues"]}
        self.assertIn("breaking-signature-change", symbols)
        self.assertEqual(result["files_compared"], 1)

    def test_file_added_after_from_ref_is_skipped_without_error(self):
        _commit(self.repo, "a.py", "def f(a):\n    return a\n", "v1")
        from_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        _commit(self.repo, "b.py", "def g(x):\n    return x\n", "add a new file")

        result = api_diff.compare(self.repo, from_sha, "HEAD")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["files_compared"], 2)

    def test_file_removed_between_refs_is_flagged_without_crashing(self):
        _commit(self.repo, "a.py", "def f(a):\n    return a\n\ndef g(b):\n    return b\n", "v1")
        from_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        _rm_commit(self.repo, "a.py", "delete a.py")

        result = api_diff.compare(self.repo, from_sha, "HEAD")
        symbols = {i["symbol"] for i in result["issues"]}
        self.assertEqual(symbols, {"removed-public-file"})
        # one issue per public top-level function that used to live in a.py
        self.assertEqual(len(result["issues"]), 2)

    def test_identical_refs_produce_no_issues(self):
        _commit(self.repo, "a.py", "def f(a, b):\n    return a + b\n", "v1")
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()

        result = api_diff.compare(self.repo, sha, sha)
        self.assertEqual(result["issues"], [])

    def test_render_text_reports_no_issues_cleanly(self):
        _commit(self.repo, "a.py", "def f(a):\n    return a\n", "v1")
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        result = api_diff.compare(self.repo, sha, sha)
        text = api_diff.render_text(result)
        self.assertIn("No breaking public-API changes detected.", text)


if __name__ == "__main__":
    unittest.main()

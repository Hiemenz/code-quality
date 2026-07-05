import os
import subprocess
import tempfile
import unittest

from codequality import complexity_regression_diff


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    full = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(full) or cwd, exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


def _rev_parse(cwd):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True
    ).stdout.strip()


_COMPLEX_BODY = "\n".join(f"    if x == {i}:\n        return {i}" for i in range(7))


class TestComplexityRegressionDiff(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_complexity_regression_between_two_refs_is_detected(self):
        _commit(self.repo, "a.py", "def f(x):\n    return x\n", "v1")
        from_sha = _rev_parse(self.repo)
        _commit(self.repo, "a.py", f"def f(x):\n{_COMPLEX_BODY}\n    return -1\n", "v2 -- much more complex")

        result = complexity_regression_diff.compare(self.repo, from_sha, "HEAD")
        symbols = {i["symbol"] for i in result["issues"]}
        self.assertIn("complexity-regression", symbols)
        self.assertEqual(result["files_compared"], 1)

    def test_file_added_after_from_ref_is_skipped_without_error(self):
        _commit(self.repo, "a.py", "def f(a):\n    return a\n", "v1")
        from_sha = _rev_parse(self.repo)
        _commit(self.repo, "b.py", f"def g(x):\n{_COMPLEX_BODY}\n    return -1\n", "add a new file")

        result = complexity_regression_diff.compare(self.repo, from_sha, "HEAD")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["files_compared"], 2)

    def test_file_removed_between_refs_is_silently_skipped(self):
        _commit(self.repo, "a.py", "def f(a):\n    return a\n", "v1")
        from_sha = _rev_parse(self.repo)
        os.remove(os.path.join(self.repo, "a.py"))
        _commit(self.repo, "b.py", "def g(x):\n    return x\n", "delete a.py, add b.py")

        result = complexity_regression_diff.compare(self.repo, from_sha, "HEAD")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["files_compared"], 1)

    def test_identical_refs_produce_no_issues(self):
        _commit(self.repo, "a.py", "def f(a, b):\n    return a + b\n", "v1")
        sha = _rev_parse(self.repo)

        result = complexity_regression_diff.compare(self.repo, sha, sha)
        self.assertEqual(result["issues"], [])

    def test_render_text_reports_no_issues_cleanly(self):
        _commit(self.repo, "a.py", "def f(a):\n    return a\n", "v1")
        sha = _rev_parse(self.repo)
        result = complexity_regression_diff.compare(self.repo, sha, sha)
        text = complexity_regression_diff.render_text(result)
        self.assertIn("No significant complexity regressions detected.", text)

    def test_threshold_argument_is_respected(self):
        _commit(self.repo, "a.py", "def f(x):\n    if x:\n        return 1\n    return 0\n", "v1")
        from_sha = _rev_parse(self.repo)
        _commit(
            self.repo, "a.py",
            "def f(x):\n    if x:\n        return 1\n    elif not x:\n        return 2\n    return 0\n",
            "v2 -- small increase",
        )

        result = complexity_regression_diff.compare(self.repo, from_sha, "HEAD", threshold=5)
        self.assertEqual(result["issues"], [])

        result = complexity_regression_diff.compare(self.repo, from_sha, "HEAD", threshold=0)
        symbols = {i["symbol"] for i in result["issues"]}
        self.assertIn("complexity-regression", symbols)


if __name__ == "__main__":
    unittest.main()

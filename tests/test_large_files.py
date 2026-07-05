import os
import subprocess
import tempfile
import unittest

from codequality import large_files


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit_text(cwd, rel_path, content, message="add file"):
    full = os.path.join(cwd, rel_path)
    os.makedirs(os.path.dirname(full) or cwd, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


def _commit_bytes(cwd, rel_path, data, message="add binary file"):
    full = os.path.join(cwd, rel_path)
    os.makedirs(os.path.dirname(full) or cwd, exist_ok=True)
    with open(full, "wb") as f:
        f.write(data)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


class LargeFilesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()


class TestLargeFile(LargeFilesTestCase):
    def test_file_over_max_size_is_flagged(self):
        _commit_text(self.repo, "big.txt", "x" * 2000)
        # 2000 bytes with a ~0 MB threshold is comfortably "over".
        issues = large_files.check(self.repo, max_size_mb=0.0005)
        large_file_issues = [i for i in issues if i.symbol == "large-file"]
        self.assertEqual(len(large_file_issues), 1)
        self.assertEqual(large_file_issues[0].file, "big.txt")
        self.assertEqual(large_file_issues[0].severity, "warn")

    def test_small_text_file_is_not_flagged(self):
        _commit_text(self.repo, "small.txt", "hello world\n")
        issues = large_files.check(self.repo)
        self.assertEqual(issues, [])

    def test_max_size_mb_threshold_is_respected(self):
        _commit_text(self.repo, "medium.txt", "x" * 5000)
        # 5000 bytes is under a 1 MB threshold...
        issues_under = large_files.check(self.repo, max_size_mb=1)
        self.assertEqual([i for i in issues_under if i.symbol == "large-file"], [])
        # ...but over a much smaller one.
        issues_over = large_files.check(self.repo, max_size_mb=0.001)
        flagged = [i for i in issues_over if i.symbol == "large-file"]
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0].file, "medium.txt")


class TestBinaryFile(LargeFilesTestCase):
    def test_small_file_with_nul_byte_is_flagged_as_binary(self):
        _commit_bytes(self.repo, "data.bin", b"abc\x00def")
        issues = large_files.check(self.repo)
        binary_issues = [i for i in issues if i.symbol == "large-binary-file"]
        self.assertEqual(len(binary_issues), 1)
        self.assertEqual(binary_issues[0].file, "data.bin")
        # small enough to stay under the binary size floor -> info, not warn
        self.assertEqual(binary_issues[0].severity, "info")

    def test_small_png_extension_is_flagged_as_binary_even_without_nul_byte(self):
        _commit_bytes(self.repo, "icon.png", b"not-real-png-bytes-but-has-the-right-extension")
        issues = large_files.check(self.repo)
        binary_issues = [i for i in issues if i.symbol == "large-binary-file"]
        self.assertEqual(len(binary_issues), 1)
        self.assertEqual(binary_issues[0].file, "icon.png")
        self.assertEqual(binary_issues[0].severity, "info")

    def test_binary_file_over_binary_threshold_is_warn_severity(self):
        _commit_bytes(self.repo, "blob.bin", b"\x00" + b"a" * 200)
        issues = large_files.check(self.repo, binary_threshold_bytes=50)
        binary_issues = [i for i in issues if i.symbol == "large-binary-file"]
        self.assertEqual(len(binary_issues), 1)
        self.assertEqual(binary_issues[0].severity, "warn")

    def test_text_file_is_not_flagged_as_binary(self):
        _commit_text(self.repo, "plain.py", "def f():\n    return 1\n")
        issues = large_files.check(self.repo)
        self.assertEqual([i for i in issues if i.symbol == "large-binary-file"], [])


class TestEmptyRepo(LargeFilesTestCase):
    def test_repo_with_no_commits_does_not_crash(self):
        issues = large_files.check(self.repo)
        self.assertEqual(issues, [])


class TestExclude(LargeFilesTestCase):
    def test_excluded_path_is_skipped(self):
        _commit_text(self.repo, "vendor/big.txt", "x" * 2000)

        class _Cfg:
            exclude = ["vendor/*"]

        issues = large_files.check(self.repo, config=_Cfg(), max_size_mb=0.0005)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()

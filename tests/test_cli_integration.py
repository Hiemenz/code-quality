"""End-to-end tests that exercise a real git repo and the CLI's main()
entrypoint, covering the two headline features: full-repo scan and
diff-scoped scan.
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout

from codequality.cli import main


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


class TestCliIntegration(unittest.TestCase):
    def setUp(self):
        """Create a throwaway git repo with one clean, committed file."""
        self.repo = tempfile.mkdtemp(prefix="cq-test-")
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "test@example.com"], self.repo)
        _git(["config", "user.name", "Test"], self.repo)

        with open(os.path.join(self.repo, "clean.py"), "w") as f:
            f.write('"""Clean module."""\n\n\ndef add(a, b):\n    """Add two numbers."""\n    return a + b\n')

        _git(["add", "."], self.repo)
        _git(["commit", "-q", "-m", "initial"], self.repo)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _run(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(args)
        return code, buf.getvalue()

    def test_scan_passes_on_clean_repo(self):
        code, out = self._run(["scan", self.repo, "--format", "json"])
        data = json.loads(out)
        self.assertEqual(code, 0)
        self.assertTrue(data["threshold"]["passed"])
        self.assertEqual(data["summary"]["files_analyzed"], 1)

    def test_scan_fails_under_strict_threshold(self):
        code, _ = self._run(["scan", self.repo, "--format", "json", "--fail-under", "101"])
        self.assertEqual(code, 1)

    def test_diff_scores_only_the_uncommitted_change(self):
        """A newly added, deeply nested function should be flagged by diff mode."""
        messy_fn = (
            "\n\ndef messy(a, b, c, d, e):\n"
            "    if a:\n        if b:\n            if c:\n"
            "                if d:\n                    if e:\n                        return 1\n"
            "    return 0\n"
        )
        with open(os.path.join(self.repo, "clean.py"), "a") as f:
            f.write(messy_fn)

        code, out = self._run(["diff", self.repo, "--format", "json"])
        data = json.loads(out)
        self.assertIn("clean.py", data["diff"]["changed_files"])
        self.assertGreater(data["diff"]["changed_lines_count"], 0)
        symbols = {i["symbol"] for i in data["issues"]}
        self.assertIn("deep-nesting", symbols)

    def test_diff_with_no_changes_reports_clean_exit(self):
        code, out = self._run(["diff", self.repo, "--format", "json"])
        self.assertEqual(code, 0)
        self.assertIn("No changed files", out)

    def test_json_output_is_deterministic_across_runs(self):
        outputs = set()
        for _ in range(3):
            _, out = self._run(["scan", self.repo, "--format", "json"])
            data = json.loads(out)
            del data["generated_at"]
            outputs.add(json.dumps(data, sort_keys=True))
        self.assertEqual(len(outputs), 1)


if __name__ == "__main__":
    unittest.main()

import os
import subprocess
import tempfile
import unittest

from codequality import hotspots
from codequality.config import Config


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


_COMPLEX_FN = """
def complex_fn(a, b, c, d):
    if a:
        if b:
            for i in range(10):
                if c and d:
                    while i < 5:
                        if a or b:
                            return i
                        elif c:
                            return c
                        else:
                            return d
    return 0
"""

_SIMPLE_FN = """
def simple_fn(x):
    return x + 1
"""


class TestHotspots(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)
        self.config = Config.load(self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_complex_and_churny_file_ranks_above_complex_and_stable_file(self):
        _commit(self.repo, "busy.py", _COMPLEX_FN, "initial busy")
        _commit(self.repo, "stable.py", _COMPLEX_FN, "initial stable")
        # Churn busy.py with several more commits; stable.py never touched again.
        for i in range(10):
            _commit(self.repo, "busy.py", _COMPLEX_FN + f"\n# rev {i}\n", f"rework busy {i}")

        rows = hotspots.compute(self.repo, self.config)
        by_file = {r["file"]: r for r in rows}

        self.assertEqual(by_file["busy.py"]["complexity"], by_file["stable.py"]["complexity"])
        self.assertGreater(by_file["busy.py"]["commit_count"], by_file["stable.py"]["commit_count"])
        self.assertGreater(by_file["busy.py"]["hotspot_score"], by_file["stable.py"]["hotspot_score"])

        ranked_files = [r["file"] for r in rows]
        self.assertLess(ranked_files.index("busy.py"), ranked_files.index("stable.py"))

    def test_complex_and_churny_file_ranks_above_simple_and_churny_file(self):
        _commit(self.repo, "complex.py", _COMPLEX_FN, "initial complex")
        _commit(self.repo, "simple.py", _SIMPLE_FN, "initial simple")
        for i in range(10):
            _commit(self.repo, "complex.py", _COMPLEX_FN + f"\n# rev {i}\n", f"rework complex {i}")
            _commit(self.repo, "simple.py", _SIMPLE_FN + f"\n# rev {i}\n", f"rework simple {i}")

        rows = hotspots.compute(self.repo, self.config)
        by_file = {r["file"]: r for r in rows}

        self.assertEqual(by_file["complex.py"]["commit_count"], by_file["simple.py"]["commit_count"])
        self.assertGreater(by_file["complex.py"]["complexity"], by_file["simple.py"]["complexity"])
        self.assertGreater(by_file["complex.py"]["hotspot_score"], by_file["simple.py"]["hotspot_score"])

        ranked_files = [r["file"] for r in rows]
        self.assertLess(ranked_files.index("complex.py"), ranked_files.index("simple.py"))

    def test_repo_with_no_commits_does_not_crash(self):
        with open(os.path.join(self.repo, "a.py"), "w") as f:
            f.write(_SIMPLE_FN)
        # No commit made yet -- git log has nothing to walk, so the file
        # should still be scored (complexity only), with commit_count 0
        # rather than raising.
        rows = hotspots.compute(self.repo, self.config)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["file"], "a.py")
        self.assertEqual(rows[0]["commit_count"], 0)
        self.assertEqual(rows[0]["hotspot_score"], 0.0)

    def test_empty_repo_does_not_crash(self):
        rows = hotspots.compute(self.repo, self.config)
        self.assertEqual(rows, [])

    def test_top_truncates_render(self):
        for i in range(5):
            _commit(self.repo, f"f{i}.py", _SIMPLE_FN + f"\n# {i}\n", f"add f{i}")

        rows = hotspots.compute(self.repo, self.config)
        self.assertEqual(len(rows), 5)

        text = hotspots.render_text(rows, top_n=2)
        # Header + blank + table header + 2 data rows.
        self.assertEqual(len(text.splitlines()), 5)

    def test_no_rows_renders_placeholder_not_crash(self):
        self.assertEqual(hotspots.render_text([]), "No files found.")


if __name__ == "__main__":
    unittest.main()

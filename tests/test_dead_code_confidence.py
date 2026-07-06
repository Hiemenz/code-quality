import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from codequality import dead_code_confidence
from codequality.config import Config


def _git(args, cwd, env=None):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True, env=env)


def _git_date(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S +0000")


def _commit(cwd, path, content, message, author_date=None):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    env = None
    if author_date is not None:
        env = os.environ.copy()
        env["GIT_AUTHOR_DATE"] = author_date
        env["GIT_COMMITTER_DATE"] = author_date
    _git(["commit", "-q", "-m", message], cwd, env=env)


class TestDeadCodeConfidence(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)
        self.config = Config({})

    def tearDown(self):
        self._tmp.cleanup()

    def test_old_dead_function_gets_high_confidence(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=400))
        _commit(
            self.repo, "a.py",
            "def never_called():\n    return 1\n",
            "human add", author_date=old_date,
        )

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "never_called")
        self.assertEqual(results[0]["file"], "a.py")
        self.assertEqual(results[0]["line"], 1)
        self.assertGreater(results[0]["age_days"], 180)
        self.assertEqual(results[0]["confidence"], "high")

    def test_recent_dead_function_gets_low_confidence(self):
        _commit(
            self.repo, "a.py",
            "def never_called():\n    return 1\n",
            "human add",
        )

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        self.assertEqual(len(results), 1)
        self.assertLess(results[0]["age_days"], 90)
        self.assertEqual(results[0]["confidence"], "low")

    def test_medium_confidence_between_half_and_full_stale_days(self):
        mid_date = _git_date(datetime.now(timezone.utc) - timedelta(days=120))
        _commit(
            self.repo, "a.py",
            "def never_called():\n    return 1\n",
            "human add", author_date=mid_date,
        )

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["confidence"], "medium")

    def test_referenced_function_does_not_appear(self):
        _commit(
            self.repo, "a.py",
            "def used_elsewhere():\n    return 1\n",
            "human add",
        )
        _commit(
            self.repo, "b.py",
            "from a import used_elsewhere\nused_elsewhere()\n",
            "human add caller",
        )

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        names = [r["name"] for r in results]
        self.assertNotIn("used_elsewhere", names)

    def test_decorated_function_is_exempt(self):
        _commit(
            self.repo, "a.py",
            "import functools\n\n@functools.lru_cache\ndef never_called():\n    return 1\n",
            "human add",
        )

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        self.assertEqual(results, [])

    def test_dunder_all_exported_name_is_exempt(self):
        _commit(
            self.repo, "a.py",
            "__all__ = ['never_called']\n\n\ndef never_called():\n    return 1\n",
            "human add",
        )

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        self.assertEqual(results, [])

    def test_no_dead_code_returns_empty_list(self):
        _commit(self.repo, "a.py", "x = 1\n", "human add")

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        self.assertEqual(results, [])

    def test_results_sorted_oldest_first(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=400))
        _commit(
            self.repo, "old.py",
            "def old_dead():\n    return 1\n",
            "human add old", author_date=old_date,
        )
        _commit(
            self.repo, "new.py",
            "def new_dead():\n    return 1\n",
            "human add new",
        )

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["name"], "old_dead")
        self.assertEqual(results[1]["name"], "new_dead")
        self.assertGreaterEqual(results[0]["age_days"], results[1]["age_days"])

    def test_render_text_lists_findings(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=400))
        _commit(
            self.repo, "a.py",
            "def never_called():\n    return 1\n",
            "human add", author_date=old_date,
        )

        results = dead_code_confidence.compute(self.repo, self.config, stale_days=180)
        text = dead_code_confidence.render_text(results, stale_days=180)
        self.assertIn("a.py:1", text)
        self.assertIn("never_called", text)
        self.assertIn("high", text)
        self.assertIn("Dead-Code Confidence", text)

    def test_render_text_handles_no_findings(self):
        text = dead_code_confidence.render_text([], stale_days=180)
        self.assertIn("no dead-code findings", text)


if __name__ == "__main__":
    unittest.main()

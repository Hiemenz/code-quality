import os
import subprocess
import tempfile
import unittest

from codequality import hallucination_metrics, typecheck
from codequality.config import Config


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


class TestHallucinationMetrics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def _config(self, **overrides):
        return Config.load(self.repo, overrides=overrides)

    def test_neither_flag_raises_usage_error(self):
        _commit(self.repo, "a.py", "x = 1\n", "human change")
        with self.assertRaises(hallucination_metrics.UsageError):
            hallucination_metrics.compute(self.repo, self._config())

    def test_no_issues_found_gives_zero_rate_not_a_crash(self):
        _commit(self.repo, "a.py", "x = 1\n", "human change")
        counts = hallucination_metrics.compute(self.repo, self._config(check_imports=True))
        self.assertEqual(counts["human"]["flagged"], 0)
        self.assertEqual(counts["human"]["rate_per_1000"], 0.0)
        self.assertEqual(counts["ai"]["loc"], 0)
        self.assertEqual(counts["ai"]["rate_per_1000"], 0.0)

    def test_ai_marked_hallucinated_import_scores_higher_than_human(self):
        """A hallucinated import on an AI-marked commit should push that
        group's rate above a human commit with a real import.
        """
        _commit(
            self.repo, "human.py", "import os\n\ndef f():\n    return os.getcwd()\n",
            "human change, real import",
        )
        _commit(
            self.repo, "ai.py", "import totally_made_up_package_xyz\n",
            "AI change\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        )

        counts = hallucination_metrics.compute(self.repo, self._config(check_imports=True))

        self.assertGreater(counts["ai"]["loc"], 0)
        self.assertGreater(counts["human"]["loc"], 0)
        self.assertEqual(counts["ai"]["flagged"], 1)
        self.assertEqual(counts["human"]["flagged"], 0)
        self.assertGreater(counts["ai"]["rate_per_1000"], counts["human"]["rate_per_1000"])

    @unittest.skipUnless(typecheck.AVAILABLE, "mypy is not installed")
    def test_check_types_alone_is_sufficient(self):
        """--check-types alone (no --check-imports) is enough to roll up."""
        _commit(
            self.repo, "ai.py", "x: int = 'not an int'\n",
            "AI change\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        )
        counts = hallucination_metrics.compute(self.repo, self._config(check_types=True))
        self.assertGreaterEqual(counts["ai"]["flagged"], 1)


if __name__ == "__main__":
    unittest.main()

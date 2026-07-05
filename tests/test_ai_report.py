import json
import os
import subprocess
import tempfile
import unittest

from codequality import ai_report
from codequality.config import Config


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


class TestAiReport(unittest.TestCase):
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

    def _seed_mixed_history(self):
        _commit(
            self.repo, "a.py", "import os\n\ndef f():\n    return os.getcwd()\n",
            "initial AI change\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
        )
        _commit(self.repo, "a.py", "import os\n\ndef f():\n    return os.getpid()\n", "AI follow-up fix")
        _commit(self.repo, "b.py", "x = 1\n", "add human helper module here")
        _commit(self.repo, "b.py", "x = 2\n", "human follow-up tweak of helper")

    def test_populated_report_has_all_four_metrics_both_sides(self):
        self._seed_mixed_history()
        result = ai_report.compute(self.repo, self._config(check_imports=True))

        for key in ("ai", "human"):
            group = result[key]
            self.assertGreater(group["commits_classified"], 0)
            self.assertIsNotNone(group["rework_rate"])
            self.assertIsNotNone(group["edit_distance"])
            self.assertIsNotNone(group["commit_lint_pass_rate"])
            self.assertIsNotNone(group["hallucination_rate_per_1000"])

        self.assertEqual(result["hallucination_notes"], [])
        self.assertIn("churn", result["raw"])
        self.assertIn("edit_distance", result["raw"])
        self.assertIn("commit_lint", result["raw"])
        self.assertIsNotNone(result["raw"]["hallucination"])

    def test_zero_ai_commits_reports_na_not_fake_zero(self):
        _commit(self.repo, "b.py", "x = 1\n", "human only change, nothing AI here")
        result = ai_report.compute(self.repo, self._config(check_imports=True))

        self.assertEqual(result["ai"]["commits_classified"], 0)
        self.assertIsNone(result["ai"]["rework_rate"])
        self.assertIsNone(result["ai"]["edit_distance"])
        self.assertIsNone(result["ai"]["commit_lint_pass_rate"])
        self.assertIsNone(result["ai"]["hallucination_rate_per_1000"])

        # human side has real history and should be populated
        self.assertGreater(result["human"]["commits_classified"], 0)
        self.assertIsNotNone(result["human"]["rework_rate"])

        text = ai_report.render_text(result)
        self.assertIn("n/a", text)

    def test_no_check_flags_skips_hallucination_row_with_a_note(self):
        self._seed_mixed_history()
        result = ai_report.compute(self.repo, self._config())

        self.assertIsNone(result["ai"]["hallucination_rate_per_1000"])
        self.assertIsNone(result["human"]["hallucination_rate_per_1000"])
        self.assertIsNone(result["raw"]["hallucination"])
        self.assertTrue(any("skipped" in note for note in result["hallucination_notes"]))

        # every other metric still ran fine
        self.assertIsNotNone(result["ai"]["rework_rate"])
        self.assertIsNotNone(result["human"]["rework_rate"])

        text = ai_report.render_text(result)
        self.assertIn("skipped", text)

    def test_since_with_hallucination_enabled_notes_full_history_caveat(self):
        self._seed_mixed_history()
        result = ai_report.compute(self.repo, self._config(check_imports=True), since="1 second ago")
        self.assertTrue(any("does not support --since" in note for note in result["hallucination_notes"]))

    def test_json_and_text_rendering_share_the_same_numbers(self):
        self._seed_mixed_history()
        result = ai_report.compute(self.repo, self._config(check_imports=True))

        # JSON round-trip must preserve every numeric field used by the text report
        dumped = json.loads(json.dumps(result))
        self.assertEqual(dumped["ai"]["rework_rate"], result["ai"]["rework_rate"])
        self.assertEqual(dumped["human"]["hallucination_rate_per_1000"], result["human"]["hallucination_rate_per_1000"])

        text = ai_report.render_text(result)
        self.assertIn("AI Code Quality Report", text)
        self.assertIn("Commits classified", text)
        # the human rework rate rendered in the text should match the raw number
        expected_pct = f"{result['human']['rework_rate'] * 100:.1f}%"
        self.assertIn(expected_pct, text)


if __name__ == "__main__":
    unittest.main()

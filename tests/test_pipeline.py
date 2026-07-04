"""Tests for `codequality pipeline`: orchestrating external format/lint/test
commands (from `[pipeline]` config) alongside codequality's own scan.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from codequality import pipeline
from codequality.config import Config

_TRUE = f"{sys.executable} -c \"pass\""
_FALSE = f"{sys.executable} -c \"import sys; sys.exit(1)\""


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


class TestPipeline(unittest.TestCase):
    def setUp(self):
        """A throwaway repo with one clean, committed file -- enough for
        codequality's own scan step to run and pass comfortably.
        """
        self.repo = tempfile.mkdtemp(prefix="cq-pipeline-")
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "test@example.com"], self.repo)
        _git(["config", "user.name", "Test"], self.repo)
        with open(os.path.join(self.repo, "clean.py"), "w") as f:
            f.write('"""Clean module."""\n\n\ndef add(a, b):\n    """Add two numbers."""\n    return a + b\n')
        _git(["add", "."], self.repo)
        _git(["commit", "-q", "-m", "initial"], self.repo)

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def _config_with_steps(self, *steps):
        return Config({"pipeline": {"steps": list(steps)}})

    def test_all_passing_steps_run_in_order_and_pipeline_passes(self):
        config = self._config_with_steps(
            {"name": "format-check", "command": _TRUE},
            {"name": "lint", "command": _TRUE},
        )
        result = pipeline.run(self.repo, config, fail_under=0)
        names = [s.name for s in result.steps]
        self.assertEqual(names, ["format-check", "lint", "codequality"])
        self.assertTrue(all(s.passed for s in result.steps))
        self.assertTrue(result.passed)

    def test_failing_step_stops_the_pipeline_before_later_steps(self):
        config = self._config_with_steps(
            {"name": "format-check", "command": _FALSE},
            {"name": "lint", "command": _TRUE},
        )
        result = pipeline.run(self.repo, config, fail_under=0)
        self.assertFalse(result.passed)
        by_name = {s.name: s for s in result.steps}
        self.assertFalse(by_name["format-check"].passed)
        self.assertEqual(by_name["format-check"].exit_code, 1)
        self.assertTrue(by_name["lint"].skipped)
        self.assertNotIn("codequality", by_name)  # never reached

    def test_allow_failure_suppresses_the_failure(self):
        config = self._config_with_steps(
            {"name": "format-check", "command": _FALSE, "allow_failure": True},
            {"name": "lint", "command": _TRUE},
        )
        result = pipeline.run(self.repo, config, fail_under=0)
        by_name = {s.name: s for s in result.steps}
        self.assertTrue(by_name["format-check"].passed)
        self.assertEqual(by_name["format-check"].exit_code, 1)
        self.assertFalse(by_name["lint"].skipped)
        self.assertTrue(result.passed)

    def test_continue_on_failure_runs_every_step_including_codequality(self):
        config = self._config_with_steps(
            {"name": "format-check", "command": _FALSE},
            {"name": "lint", "command": _TRUE},
        )
        result = pipeline.run(self.repo, config, fail_under=0, continue_on_failure=True)
        names = [s.name for s in result.steps]
        self.assertEqual(names, ["format-check", "lint", "codequality"])
        self.assertFalse(any(s.skipped for s in result.steps))
        self.assertFalse(result.passed)  # format-check still failed overall
        self.assertIsNotNone(result.codequality_summary)

    def test_codequality_step_fails_pipeline_when_score_below_fail_under(self):
        config = self._config_with_steps({"name": "lint", "command": _TRUE})
        result = pipeline.run(self.repo, config, fail_under=101)
        by_name = {s.name: s for s in result.steps}
        self.assertTrue(by_name["lint"].passed)
        self.assertFalse(by_name["codequality"].passed)
        self.assertFalse(result.passed)

    def test_to_dict_and_render_text_are_json_and_string_respectively(self):
        config = self._config_with_steps({"name": "lint", "command": _TRUE})
        result = pipeline.run(self.repo, config, fail_under=0)
        as_json = json.dumps(pipeline.to_dict(result))
        self.assertIn("codequality-pipeline", as_json)
        text = pipeline.render_text(result)
        self.assertIn("PASS", text)


if __name__ == "__main__":
    unittest.main()

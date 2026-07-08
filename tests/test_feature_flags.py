import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from codequality import feature_flags


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


class TestFeatureFlags(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_flag_call_is_detected(self):
        _commit(self.repo, "a.py", "if is_enabled('new_checkout'):\n    pass\n", "add flag")

        occurrences = feature_flags.compute(self.repo)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["flag"], "new_checkout")
        self.assertEqual(occurrences[0]["file"], "a.py")
        self.assertEqual(occurrences[0]["line"], 1)

    def test_flag_call_with_leading_args_is_detected(self):
        _commit(self.repo, "a.py", "if flag_is_active(request, 'beta_ui'):\n    pass\n", "add flag")

        occurrences = feature_flags.compute(self.repo)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["flag"], "beta_ui")

    def test_dict_lookup_flag_is_detected(self):
        _commit(self.repo, "a.py", "if FEATURE_FLAGS['dark_mode']:\n    pass\n", "add flag")

        occurrences = feature_flags.compute(self.repo)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["flag"], "dark_mode")

    def test_boolean_constant_flag_is_detected(self):
        _commit(self.repo, "a.py", "ENABLE_NEW_CHECKOUT = True\n", "add flag")

        occurrences = feature_flags.compute(self.repo)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["flag"], "ENABLE_NEW_CHECKOUT")

    def test_suffix_style_boolean_constant_flag_is_detected(self):
        _commit(self.repo, "a.py", "NEW_CHECKOUT_ENABLED = False\n", "add flag")

        occurrences = feature_flags.compute(self.repo)
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(occurrences[0]["flag"], "NEW_CHECKOUT_ENABLED")

    def test_old_flag_is_flagged_stale(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=400))
        _commit(self.repo, "a.py", "if is_enabled('old_flag'):\n    pass\n", "add flag", author_date=old_date)

        occurrences = feature_flags.compute(self.repo, stale_days=180)
        self.assertTrue(occurrences[0]["stale"])
        self.assertGreater(occurrences[0]["age_days"], 180)

    def test_recent_flag_is_not_stale(self):
        _commit(self.repo, "a.py", "if is_enabled('new_flag'):\n    pass\n", "add flag")

        occurrences = feature_flags.compute(self.repo, stale_days=180)
        self.assertFalse(occurrences[0]["stale"])

    def test_file_with_no_flags_contributes_nothing(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", "no flags here")

        occurrences = feature_flags.compute(self.repo)
        self.assertEqual(occurrences, [])

    def test_summarize_groups_by_flag_name_and_tracks_oldest(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=200))
        _commit(self.repo, "a.py", "if is_enabled('checkout'):\n    pass\n", "add flag", author_date=old_date)
        _commit(self.repo, "b.py", "if is_enabled('checkout'):\n    pass\n", "reuse flag")

        occurrences = feature_flags.compute(self.repo, stale_days=90)
        groups = feature_flags.summarize(occurrences)

        self.assertEqual(groups["checkout"]["count"], 2)
        self.assertTrue(groups["checkout"]["stale"])
        self.assertEqual(sorted(groups["checkout"]["files"]), ["a.py", "b.py"])

    def test_render_text_lists_stale_flags(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=400))
        _commit(self.repo, "a.py", "if is_enabled('old_flag'):\n    pass\n", "add flag", author_date=old_date)

        occurrences = feature_flags.compute(self.repo, stale_days=180)
        text = feature_flags.render_text(occurrences, stale_days=180)
        self.assertIn("old_flag", text)
        self.assertIn("Feature Flag Aging", text)

    def test_render_text_with_no_flags_does_not_crash(self):
        text = feature_flags.render_text([])
        self.assertIn("No flag-looking references found", text)


if __name__ == "__main__":
    unittest.main()

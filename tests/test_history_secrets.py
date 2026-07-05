import os
import subprocess
import tempfile
import unittest

from codequality import history_secrets


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _commit(cwd, path, content, message):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)
    _git(["add", "."], cwd)
    _git(["commit", "-q", "-m", message], cwd)


class TestHistorySecrets(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_secret_added_then_removed_is_flagged_as_still_in_history(self):
        _commit(self.repo, "a.py", 'password = "hunter2ishere"\n', "add secret")
        _commit(self.repo, "a.py", "x = 1\n", "remove secret")

        hits = history_secrets.scan(self.repo)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "a.py")
        self.assertEqual(hits[0]["name"], "password")
        self.assertFalse(hits[0]["still_in_head"])
        # never the full secret in the report
        self.assertNotIn("hunter2ishere", hits[0]["redacted"])

    def test_secret_still_present_at_head_is_flagged_distinctly(self):
        _commit(self.repo, "a.py", 'api_key = "sk-liveTOPSECRETVALUE"\n', "add secret")

        hits = history_secrets.scan(self.repo)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["still_in_head"])

    def test_no_secrets_ever_committed_returns_empty(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", "no secrets here")
        _commit(self.repo, "a.py", "x = 1\ny = 3\n", "still no secrets")

        hits = history_secrets.scan(self.repo)
        self.assertEqual(hits, [])

    def test_placeholder_value_is_not_flagged(self):
        _commit(self.repo, "a.py", 'password = "changeme"\n', "placeholder secret")

        hits = history_secrets.scan(self.repo)
        self.assertEqual(hits, [])

    def test_max_commits_bounds_the_walk(self):
        _commit(self.repo, "a.py", 'password = "firstsecretvalue"\n', "first")
        _commit(self.repo, "b.py", 'token = "secondsecretvalue"\n', "second")
        _commit(self.repo, "c.py", 'secret = "thirdsecretvalue"\n', "third")

        all_hits = history_secrets.scan(self.repo, max_commits=None)
        self.assertEqual(len(all_hits), 3)

        capped_hits = history_secrets.scan(self.repo, max_commits=1)
        self.assertEqual(len(capped_hits), 1)
        self.assertEqual(capped_hits[0]["file"], "c.py")

    def test_root_commit_with_no_parent_is_handled(self):
        """The very first commit in a repo has no parent to diff against --
        must fall back to the empty-tree sha instead of raising.
        """
        _commit(self.repo, "a.py", 'secret = "rootcommitsecretvalue"\n', "initial commit")

        hits = history_secrets.scan(self.repo)
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["still_in_head"])

    def test_since_ref_excludes_earlier_commits(self):
        _commit(self.repo, "a.py", 'password = "beforesecretvalue"\n', "before")
        _git(["tag", "checkpoint"], self.repo)
        _commit(self.repo, "b.py", 'token = "aftersecretvalue"\n', "after")

        hits = history_secrets.scan(self.repo, since="checkpoint")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["file"], "b.py")


if __name__ == "__main__":
    unittest.main()

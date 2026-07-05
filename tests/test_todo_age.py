import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from codequality import todo_age

_AI_TRAILER = "Co-Authored-By: Claude <noreply@anthropic.com>"


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


class TestTodoAge(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_old_todo_is_flagged_stale(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=400))
        _commit(self.repo, "a.py", "# TODO: fix this eventually\nx = 1\n", "human add", author_date=old_date)

        todos = todo_age.compute(self.repo, stale_days=90)
        self.assertEqual(len(todos), 1)
        self.assertTrue(todos[0]["stale"])
        self.assertGreater(todos[0]["age_days"], 90)
        self.assertEqual(todos[0]["file"], "a.py")
        self.assertEqual(todos[0]["line"], 1)
        self.assertEqual(todos[0]["group"], "human")

    def test_recent_todo_is_not_stale(self):
        _commit(self.repo, "a.py", "# TODO: fix this eventually\nx = 1\n", "human add")

        todos = todo_age.compute(self.repo, stale_days=90)
        self.assertEqual(len(todos), 1)
        self.assertFalse(todos[0]["stale"])
        self.assertLess(todos[0]["age_days"], 90)

    def test_ai_marked_introducing_commit_classifies_as_ai(self):
        _commit(self.repo, "a.py", "# TODO: fix this\nx = 1\n", f"initial change\n\n{_AI_TRAILER}")

        todos = todo_age.compute(self.repo)
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0]["group"], "ai")

    def test_human_introducing_commit_classifies_as_human(self):
        _commit(self.repo, "a.py", "# TODO: fix this\nx = 1\n", "initial change")

        todos = todo_age.compute(self.repo)
        self.assertEqual(len(todos), 1)
        self.assertEqual(todos[0]["group"], "human")

    def test_marker_matching_is_case_insensitive(self):
        _commit(self.repo, "a.py", "# TODO: fix this\nx = 1\n", "change\n\nCo-authored-by: Claude <n@a.com>")

        todos = todo_age.compute(self.repo)
        self.assertEqual(todos[0]["group"], "ai")

    def test_custom_marker_is_respected(self):
        _commit(self.repo, "a.py", "# TODO: fix this\nx = 1\n", "change\n\nGenerated-By: MyBot")

        todos = todo_age.compute(self.repo, marker="Generated-By: MyBot")
        self.assertEqual(todos[0]["group"], "ai")

    def test_file_with_no_todos_contributes_nothing(self):
        _commit(self.repo, "a.py", "x = 1\ny = 2\n", "no markers here")

        todos = todo_age.compute(self.repo)
        self.assertEqual(todos, [])

    def test_stale_days_threshold_is_respected(self):
        ten_days_ago = _git_date(datetime.now(timezone.utc) - timedelta(days=10))
        _commit(self.repo, "a.py", "# TODO: fix this\nx = 1\n", "human add", author_date=ten_days_ago)

        stale_at_5 = todo_age.compute(self.repo, stale_days=5)
        stale_at_30 = todo_age.compute(self.repo, stale_days=30)

        self.assertTrue(stale_at_5[0]["stale"])
        self.assertFalse(stale_at_30[0]["stale"])

    def test_fixme_and_xxx_markers_are_also_found(self):
        _commit(self.repo, "a.py", "# FIXME: broken\n# XXX: hack\nx = 1\n", "human add")

        todos = todo_age.compute(self.repo)
        self.assertEqual(len(todos), 2)

    def test_summarize_reports_group_stats(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=200))
        _commit(self.repo, "a.py", f"# TODO: old\nx = 1\n", "initial\n\n{}".format(_AI_TRAILER), author_date=old_date)
        _commit(self.repo, "b.py", "# TODO: new\ny = 2\n", "human add")

        todos = todo_age.compute(self.repo, stale_days=90)
        groups = todo_age.summarize(todos)

        self.assertEqual(groups["ai"]["count"], 1)
        self.assertEqual(groups["ai"]["stale_count"], 1)
        self.assertEqual(groups["human"]["count"], 1)
        self.assertEqual(groups["human"]["stale_count"], 0)

    def test_empty_group_reports_none_stats_not_a_crash(self):
        _commit(self.repo, "a.py", "# TODO: fix\nx = 1\n", "human add")

        todos = todo_age.compute(self.repo)
        groups = todo_age.summarize(todos)
        self.assertIsNone(groups["ai"]["avg_age_days"])
        self.assertIsNone(groups["ai"]["oldest_age_days"])
        self.assertEqual(groups["ai"]["count"], 0)

    def test_render_text_lists_stale_todos(self):
        old_date = _git_date(datetime.now(timezone.utc) - timedelta(days=400))
        _commit(self.repo, "a.py", "# TODO: fix this eventually\nx = 1\n", "human add", author_date=old_date)

        todos = todo_age.compute(self.repo, stale_days=90)
        text = todo_age.render_text(todos, stale_days=90)
        self.assertIn("a.py:1", text)
        self.assertIn("TODO Aging", text)


if __name__ == "__main__":
    unittest.main()

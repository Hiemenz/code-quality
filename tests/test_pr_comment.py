import json
import unittest

from codequality import pr_comment


def _issue(file, line, rule="sql-injection-risk", severity="error", message="msg"):
    return {"file": file, "line": line, "category": "security", "severity": severity, "rule": rule,
            "symbol": rule, "message": message}


class FakeGh:
    """Records every call and returns a canned response per call index."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args, input_data=None):
        self.calls.append({"args": args, "input_data": input_data})
        return self.responses.pop(0)


class TestBuildReviewComments(unittest.TestCase):
    def test_only_in_diff_lines_become_comments(self):
        issues = [_issue("a.py", 5), _issue("a.py", 99)]
        changed_files = {"a.py": {5}}
        comments, dropped = pr_comment.build_review_comments(issues, changed_files)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["path"], "a.py")
        self.assertEqual(comments[0]["line"], 5)
        self.assertEqual(comments[0]["side"], "RIGHT")
        self.assertIn("sql-injection-risk", comments[0]["body"])
        self.assertEqual(dropped, 0)

    def test_issue_on_untouched_file_is_dropped(self):
        issues = [_issue("b.py", 5)]
        changed_files = {"a.py": {5}}
        comments, dropped = pr_comment.build_review_comments(issues, changed_files)
        self.assertEqual(comments, [])
        self.assertEqual(dropped, 0)  # not in-diff at all, not counted as "dropped by cap"

    def test_cap_limits_comments_and_reports_dropped_count(self):
        issues = [_issue("a.py", i) for i in range(1, 6)]
        changed_files = {"a.py": set(range(1, 6))}
        comments, dropped = pr_comment.build_review_comments(issues, changed_files, max_comments=2)
        self.assertEqual(len(comments), 2)
        self.assertEqual(dropped, 3)

    def test_no_issues_no_comments(self):
        comments, dropped = pr_comment.build_review_comments([], {"a.py": {1}})
        self.assertEqual((comments, dropped), ([], 0))


class TestGhWrappers(unittest.TestCase):
    def test_get_repo_info_parses_owner_repo(self):
        gh = FakeGh([json.dumps({"nameWithOwner": "acme/widgets"})])
        owner, repo = pr_comment.get_repo_info(runner=gh)
        self.assertEqual((owner, repo), ("acme", "widgets"))
        self.assertEqual(gh.calls[0]["args"], ["repo", "view", "--json", "nameWithOwner"])

    def test_get_pr_info_parses_fields(self):
        gh = FakeGh([json.dumps({"headRefOid": "abc123", "baseRefName": "main", "headRefName": "feature"})])
        info = pr_comment.get_pr_info(42, runner=gh)
        self.assertEqual(info, {"commit_id": "abc123", "base_ref": "main", "head_ref": "feature"})
        self.assertEqual(gh.calls[0]["args"][:3], ["pr", "view", "42"])

    def test_post_review_sends_expected_payload(self):
        gh = FakeGh(["{}"])
        comments = [{"path": "a.py", "line": 5, "side": "RIGHT", "body": "x"}]
        pr_comment.post_review("acme", "widgets", 42, "abc123", comments, summary_body="summary", runner=gh)

        call = gh.calls[0]
        self.assertEqual(call["args"][:2], ["api", "repos/acme/widgets/pulls/42/reviews"])
        payload = json.loads(call["input_data"])
        self.assertEqual(payload["commit_id"], "abc123")
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(payload["body"], "summary")
        self.assertEqual(payload["comments"], comments)

    def test_gh_failure_raises_pr_comment_error(self):
        def failing_runner(args, input_data=None):
            raise pr_comment.PrCommentError("boom")

        with self.assertRaises(pr_comment.PrCommentError):
            pr_comment.get_repo_info(runner=failing_runner)

    def test_missing_gh_binary_raises_pr_comment_error(self):
        import unittest.mock

        with unittest.mock.patch("subprocess.run", side_effect=FileNotFoundError()):
            with self.assertRaises(pr_comment.PrCommentError):
                pr_comment._run_gh(["repo", "view"])

    def test_gh_nonzero_exit_raises_pr_comment_error(self):
        import unittest.mock

        fake_result = unittest.mock.Mock(returncode=1, stderr="not found", stdout="")
        with unittest.mock.patch("subprocess.run", return_value=fake_result):
            with self.assertRaises(pr_comment.PrCommentError):
                pr_comment._run_gh(["pr", "view", "999"])


if __name__ == "__main__":
    unittest.main()

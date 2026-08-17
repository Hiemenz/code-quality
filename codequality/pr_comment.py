"""Post scan/diff findings as inline review comments on a GitHub PR.

This is the one subcommand in the whole tool that makes a network call --
every other check, including `dependency-check`'s explicit "no network
access, ever" promise, stays purely local (see README). `pr-comment` is a
deliberate, opt-in exception, scoped narrowly: it formats findings the
scanner already produced (no new detection logic here) into GitHub's
Reviews API shape and posts them via the `gh` CLI (not `requests` --
keeps the core tool free of the dependency; only this subcommand needs
`gh` on PATH and authenticated). Posting is not the default action of
running the command -- see `--post` in cli.py -- because it's visible to
others and each comment is its own notification.
"""

import json
import subprocess


class PrCommentError(RuntimeError):
    pass


def _run_gh(args, input_data=None):
    """Run `gh <args>`, optionally piping `input_data` (a string) to stdin.
    The sole real I/O boundary in this module -- every public function
    below takes this as an injectable `runner` parameter so tests can
    substitute a fake instead of touching the network.
    """
    try:
        result = subprocess.run(
            ["gh"] + args, input=input_data, capture_output=True, text=True
        )
    except FileNotFoundError:
        raise PrCommentError("'gh' CLI not found on PATH -- install it and run `gh auth login`") from None
    if result.returncode != 0:
        raise PrCommentError(result.stderr.strip() or f"gh {' '.join(args)} failed")
    return result.stdout


def get_repo_info(runner=_run_gh):
    """(owner, repo) for the repo `gh` is currently configured against."""
    data = json.loads(runner(["repo", "view", "--json", "nameWithOwner"]))
    owner, repo = data["nameWithOwner"].split("/", 1)
    return owner, repo


def get_pr_info(pr_number, runner=_run_gh):
    """{"commit_id", "base_ref", "head_ref"} for PR `pr_number`."""
    data = json.loads(runner(["pr", "view", str(pr_number), "--json", "headRefOid,baseRefName,headRefName"]))
    return {"commit_id": data["headRefOid"], "base_ref": data["baseRefName"], "head_ref": data["headRefName"]}


def build_review_comments(issues, changed_files, max_comments=50):
    """issues: list of Issue.to_dict()-shaped dicts. changed_files: dict[path]
    -> set[int] of added/modified line numbers, as returned by
    `git_utils.get_changed_files` -- GitHub only accepts inline comments on
    lines that are actually part of the diff, so anything else is dropped
    silently (it's still visible in the normal scan/diff output).

    Returns `(comments, dropped_count)`: `comments` is a list of
    `{"path", "line", "side": "RIGHT", "body"}` dicts ready for the Reviews
    API, capped at `max_comments`; `dropped_count` is how many additional
    in-diff findings didn't fit under the cap (folded into a summary line
    by the caller rather than silently lost).
    """
    in_diff = [
        issue for issue in issues
        if issue["line"] in changed_files.get(issue["file"], set())
    ]
    comments = [
        {
            "path": issue["file"],
            "line": issue["line"],
            "side": "RIGHT",
            "body": f"**{issue['rule']}** ({issue['severity']}): {issue['message']}",
        }
        for issue in in_diff[:max_comments]
    ]
    dropped_count = max(0, len(in_diff) - max_comments)
    return comments, dropped_count


def post_review(owner, repo, pr_number, commit_id, comments, summary_body=None, runner=_run_gh):
    """Post `comments` as a single review (one API call, one notification)
    via `POST /repos/{owner}/{repo}/pulls/{pr_number}/reviews`. Uses the
    modern `line`/`side` fields (no legacy diff-position math needed).
    """
    payload = {
        "commit_id": commit_id,
        "event": "COMMENT",
        "body": summary_body or "",
        "comments": comments,
    }
    endpoint = f"repos/{owner}/{repo}/pulls/{pr_number}/reviews"
    return runner(["api", endpoint, "--input", "-"], input_data=json.dumps(payload))

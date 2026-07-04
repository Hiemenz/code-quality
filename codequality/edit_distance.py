"""Git-history edit distance: how much of what a commit added is still
there, vs. how much has been rewritten since.

`codequality churn` answers "did this commit's *files* get touched again
soon after" -- file-level, not line-level. This module answers a sharper
question: of the lines a commit *added*, how many still read exactly as
that commit left them at `HEAD`? That's a proxy for "how many lines does a
developer end up changing before/after a change lands," adapted to a
single git history instead of needing a PR review API.

Commits are classified AI-assisted or not by the same marker-string
convention as `churn.py` (default `"Co-Authored-By: Claude"`, matching the
trailer this tool's own commits use). The mechanics are git-blame based:
for each commit, diff it against its first parent to find the lines it
added, then blame `HEAD` for each touched file to see how many of those
exact lines are still attributed to that commit. `edit_distance` for a
commit is `1 - lines_survived / lines_added` -- 0.0 means nothing has
changed since, 1.0 means every added line has since been rewritten or
removed. Commits that added no lines (pure deletions/renames) are skipped,
since the ratio is undefined for them.
"""

import re
from collections import Counter

from codequality.git_utils import GitError, _run, parse_added_lines

DEFAULT_MARKER = "Co-Authored-By: Claude"

# git's well-known hash for the empty tree -- constant across every repo,
# used to diff a root commit (no parent) against "nothing."
_EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

_FS = "\x1f"  # field separator
_RS = "\x1e"  # record separator

_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


def _commit_metadata(cwd, marker, since=None):
    args = ["log", "--no-merges", f"--format=%H{_FS}%aI{_FS}%B{_RS}"]
    if since:
        args.append(f"--since={since}")
    try:
        raw = _run(args, cwd)
    except GitError:
        return {}  # e.g. a freshly-initialized repo with no commits yet
    commits = {}
    for record in raw.split(_RS):
        record = record.strip("\n")
        if not record:
            continue
        sha, date_str, body = record.split(_FS, 2)
        commits[sha] = {"date": date_str, "is_ai": marker.lower() in body.lower()}
    return commits


def _diff_text_for_commit(cwd, sha):
    try:
        return _run(["diff", f"{sha}^", sha, "-U0", "--no-color"], cwd)
    except GitError:
        return _run(["diff", _EMPTY_TREE_SHA, sha, "-U0", "--no-color"], cwd)


def _added_lines_for_commit(cwd, sha):
    """dict[path] -> set of 1-based line numbers this commit added."""
    return parse_added_lines(_diff_text_for_commit(cwd, sha))


def _blame_counts(cwd, path):
    """Counter[sha] -> number of lines currently at HEAD in `path` still
    attributed to that sha. Empty for files deleted since (blame fails).
    """
    try:
        raw = _run(["blame", "--line-porcelain", "-w", "HEAD", "--", path], cwd)
    except GitError:
        return {}
    counts = Counter()
    current_sha = None
    for line in raw.splitlines():
        m = _BLAME_HEADER_RE.match(line)
        if m:
            current_sha = m.group(1)
        elif line.startswith("\t") and current_sha:
            counts[current_sha] += 1
    return counts


def _new_group():
    return {"commits": 0, "lines_added": 0, "lines_survived": 0}


def compute(cwd, marker=DEFAULT_MARKER, since=None):
    """Returns {"ai": {...}, "human": {...}}, each with commits/lines_added/
    lines_survived/mean_edit_distance.
    """
    metadata = _commit_metadata(cwd, marker, since)
    counts = {"ai": _new_group(), "human": _new_group()}
    blame_cache = {}

    for sha, meta in metadata.items():
        added = _added_lines_for_commit(cwd, sha)
        lines_added = sum(len(v) for v in added.values())
        if lines_added == 0:
            continue

        lines_survived = 0
        for path in added:
            if path not in blame_cache:
                blame_cache[path] = _blame_counts(cwd, path)
            lines_survived += blame_cache[path].get(sha, 0)

        group = counts["ai"] if meta["is_ai"] else counts["human"]
        group["commits"] += 1
        group["lines_added"] += lines_added
        group["lines_survived"] += lines_survived

    for group in counts.values():
        added = group["lines_added"]
        group["mean_edit_distance"] = 1 - group["lines_survived"] / added if added else None
    return counts


def render_text(counts):
    lines = ["Git History Edit Distance", ""]
    lines.append(f"  {'Group':<14}{'Commits':>9}{'Added':>9}{'Survived':>10}{'EditDist':>10}")
    for label, key in (("AI-assisted", "ai"), ("Human", "human")):
        g = counts[key]
        ed = "n/a" if g["mean_edit_distance"] is None else f"{g['mean_edit_distance'] * 100:.1f}%"
        lines.append(f"  {label:<14}{g['commits']:>9}{g['lines_added']:>9}{g['lines_survived']:>10}{ed:>10}")
    return "\n".join(lines)

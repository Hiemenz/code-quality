"""Secrets that were ever committed, even if a later commit deleted the
line -- the case a plain `scan`/`diff` can never catch, since both only
ever look at the current working tree. Deleting a line doesn't remove it
from git's object history; the blob with the secret in it is still
reachable from every commit that had it, forever, unless the repo was
rewritten with something like `git filter-repo`/BFG.

Mechanically: walk commits (newest-first, optionally bounded -- see
`DEFAULT_MAX_COMMITS` below), diff each one against its first parent
(`codequality.git_utils.diff_text_for_commit`, same empty-tree-sha fallback
for root commits as `edit_distance.py` uses), and run the *exact same*
hardcoded-secret regex the generic per-language analyzer uses
(`codequality.analyzers.secrets.SECRET_ASSIGN_RE` -- shared, not
reimplemented here) against only the lines that commit *added*. A secret
that was always there and never added within the scanned range doesn't
need re-flagging on every single commit that happened to touch the file.

For every hit, we also check whether that same secret value still appears
in the file at `HEAD`. If it doesn't, that's the headline finding of this
whole feature: a secret that's genuinely gone from the working tree today
but still sitting in a historical blob that `git clone` hands to anyone.

Performance tradeoff: diffing every commit in a large repo's full history
is `O(commits)` git subprocess calls, which can be slow (seconds to
minutes) on a repo with tens of thousands of commits. `max_commits`
defaults to `DEFAULT_MAX_COMMITS` (500, newest-first) for that reason --
pass `max_commits=None` (`--all-commits` on the CLI) to scan everything, or
`since` to bound the walk to commits reachable from `HEAD` but not from
some earlier ref/tag (`git log SINCE..HEAD` semantics -- a *ref*, unlike
the date-string `--since` accepted by `churn`/`edit-distance`/`commit-lint`).
"""

from codequality.analyzers.secrets import SECRET_ASSIGN_RE, is_placeholder
from codequality.git_utils import GitError, _run, diff_text_for_commit, get_file_at_ref, parse_added_lines

DEFAULT_MAX_COMMITS = 500


def _commit_shas(cwd, since=None, max_commits=DEFAULT_MAX_COMMITS):
    """Commit shas reachable from HEAD, newest-first. `since`, if given, is
    a git ref (tag/branch/sha) -- the walk is bounded to `since..HEAD`, i.e.
    commits reachable from HEAD but not from `since`. `max_commits=None`
    means no cap (full history).
    """
    args = ["log", "--format=%H"]
    if since:
        args.append(f"{since}..HEAD")
    if max_commits is not None:
        args += ["-n", str(max_commits)]
    try:
        raw = _run(args, cwd)
    except GitError:
        if since is None:
            return []  # e.g. a freshly-initialized repo with no commits yet
        raise  # a bad --since ref should be reported, not silently swallowed
    return [line for line in raw.splitlines() if line.strip()]


def _redact(value):
    """Show just enough of a matched secret to recognize it again without
    printing the whole thing: first/last few characters, `...` between.
    Short values (where that would show everything anyway) are fully
    masked instead.
    """
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _secret_hits_in_commit(cwd, sha):
    """[(path, lineno, name, value), ...] for every hardcoded-secret-looking
    line this commit *added* (not lines it merely touched/left alone).
    """
    added = parse_added_lines(diff_text_for_commit(cwd, sha))
    hits = []
    for path, linenos in added.items():
        content = get_file_at_ref(sha, path, cwd)
        if content is None:
            continue  # e.g. binary/decoding failure, or a since-renamed path
        lines = content.splitlines()
        for lineno in sorted(linenos):
            if lineno < 1 or lineno > len(lines):
                continue
            m = SECRET_ASSIGN_RE.search(lines[lineno - 1])
            if m and not is_placeholder(m.group(3)):
                hits.append((path, lineno, m.group(1), m.group(3)))
    return hits


def _still_in_head(cwd, path, value, head_cache):
    if path not in head_cache:
        head_cache[path] = get_file_at_ref("HEAD", path, cwd)
    content = head_cache[path]
    return content is not None and value in content


def scan(root, since=None, max_commits=DEFAULT_MAX_COMMITS):
    """Every hardcoded-secret-looking line added by any scanned commit.

    Returns a list of dicts, newest-commit-first:
    `commit` (short sha), `file`, `line`, `name` (the matched keyword, e.g.
    "password"), `redacted` (the matched value with most of it masked --
    never the full secret), and `still_in_head` (False is the actionable
    finding: the secret is gone from the working tree but still reachable
    in history).
    """
    shas = _commit_shas(root, since=since, max_commits=max_commits)
    head_cache = {}
    results = []
    for sha in shas:
        for path, lineno, name, value in _secret_hits_in_commit(root, sha):
            results.append({
                "commit": sha[:10],
                "file": path,
                "line": lineno,
                "name": name,
                "redacted": _redact(value),
                "still_in_head": _still_in_head(root, path, value, head_cache),
            })
    return results


def _format_hit(h):
    return f"  {h['commit']}  {h['file']}:{h['line']}  {h['name']} = {h['redacted']}"


def _render_group(title, group_hits):
    """Lines for one group (title + one row per hit), or [] if the group is
    empty -- pulled out of `render_text` purely to keep that function's own
    branching low.
    """
    if not group_hits:
        return []
    return [title] + [_format_hit(h) for h in group_hits] + [""]


def render_text(hits, scanned_commits=None):
    """Render `scan()`'s hits as text, grouped by whether the secret is
    still present at HEAD. The "removed from HEAD but still in history"
    group is listed first since it's the actionable, non-obvious finding --
    the one a plain `scan`/`diff` can never catch on its own.
    """
    lines = ["Secrets in Git History", ""]
    if scanned_commits is not None:
        lines.append(f"Commits scanned: {scanned_commits}")
    if not hits:
        lines.append("No hardcoded-secret-looking lines found in any scanned commit's additions.")
        return "\n".join(lines)

    removed = [h for h in hits if not h["still_in_head"]]
    present = [h for h in hits if h["still_in_head"]]

    lines.append(
        f"Total: {len(hits)} finding(s) -- {len(removed)} removed from HEAD but still in history, "
        f"{len(present)} still present at HEAD"
    )
    lines.append("")
    lines.extend(_render_group(
        "=== Leaked in history, no longer in HEAD (still reachable via `git clone`/`git show`) ===", removed
    ))
    lines.extend(_render_group("=== Still present at HEAD (also flagged by a normal `scan`) ===", present))

    return "\n".join(lines).rstrip("\n")

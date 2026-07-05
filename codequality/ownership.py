"""Per-file ownership: single-identity line concentration (a bus-factor
proxy) plus the fraction of a file's current lines that trace back to an
AI-assisted commit.

This reuses the same AI-vs-human classification as `churn.py`/
`edit_distance.py`/`commit_lint.py`/`hallucination_metrics.py` -- a marker
substring in the commit message/trailers (default
`"Co-Authored-By: Claude"`, case-insensitive) -- but applies it via `git
blame` instead of `git log`, the same blame-driven technique
`edit_distance.py`'s `_blame_counts` and `hallucination_metrics.py`'s
`_blame_shas` already use.

Two deliberately separate signals per file, both line-level:

- **`top_author_share`** -- of the file's current lines, what fraction does
  its single largest-contributing git identity (author name + email) own?
  A file where one person (or one AI session, committed under one human's
  git identity) owns 90%+ of the lines is a bus-factor risk if that person
  leaves -- or, read the other way, "one clean file nobody has messed with
  since it was written." This is about *who* (a git identity), never about
  AI-ness.
- **`ai_line_fraction`** -- of the file's current lines, what fraction were
  last touched by a commit whose message matches the AI marker? This is
  about *how the change was made*, never about who committed it -- an
  AI-assisted commit's author is still a human git identity, just one who
  had help. Keeping these two columns separate (rather than treating "top
  author is AI" as a thing) matters: authorship is per-commit, concentration
  is per-identity, and conflating them would misreport both.

Flagging a file `low_bus_factor` (single-identity concentration at or above
a configurable threshold, default 90%) is informational/reporting only --
there's no pass/fail gate here, same as `churn`/`edit-distance`/
`hallucination-rate`/`dependency-check`.
"""

import re
from collections import Counter

from codequality.git_utils import GitError, _run
from codequality.scanner import discover_files

DEFAULT_MARKER = "Co-Authored-By: Claude"
DEFAULT_THRESHOLD = 0.9

_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")
_AUTHOR_RE = re.compile(r"^author (.*)$")
_AUTHOR_MAIL_RE = re.compile(r"^author-mail (.*)$")


def _blame_lines(root, rel_path):
    """List of (sha, author_name, author_mail) tuples, one per line of
    `rel_path` at HEAD (index 0 is line 1).

    `--line-porcelain` (unlike plain `--porcelain`) repeats the full commit
    header -- including `author`/`author-mail` -- for *every* line, not
    just the first line of each commit's block, so the git identity for
    each line can be read straight out of this one call; no separate
    `git log`/`git show` per line is needed for the "who" half of this.
    Returns `[]` for paths git can't blame at HEAD (untracked, or deleted
    since).
    """
    try:
        raw = _run(["blame", "--line-porcelain", "-w", "HEAD", "--", rel_path], root)
    except GitError:
        return []
    lines = []
    current_sha = current_name = current_mail = None
    for line in raw.splitlines():
        m = _BLAME_HEADER_RE.match(line)
        if m:
            current_sha = m.group(1)
            continue
        m = _AUTHOR_RE.match(line)
        if m:
            current_name = m.group(1)
            continue
        m = _AUTHOR_MAIL_RE.match(line)
        if m:
            current_mail = m.group(1)
            continue
        if line.startswith("\t") and current_sha:
            lines.append((current_sha, current_name, current_mail))
    return lines


def _classify_commit(root, sha, marker, cache):
    """AI-assisted classification for one commit sha, cached so a file
    with hundreds of lines from the same few commits only ever runs `git
    show` once per unique sha, not once per line.
    """
    if sha not in cache:
        try:
            body = _run(["show", "-s", "--format=%B", sha], root)
        except GitError:
            body = ""
        cache[sha] = marker.lower() in body.lower()
    return cache[sha]


def _file_entry(root, rel_path, marker, threshold, commit_cache):
    blame = _blame_lines(root, rel_path)
    total = len(blame)
    if total == 0:
        return None

    identity_counts = Counter((name, mail) for _, name, mail in blame)
    (top_name, top_mail), top_count = identity_counts.most_common(1)[0]
    ai_lines = sum(1 for sha, _, _ in blame if _classify_commit(root, sha, marker, commit_cache))
    top_share = top_count / total

    return {
        "file": rel_path,
        "total_lines": total,
        "author_count": len(identity_counts),
        "top_author": top_name,
        "top_author_mail": top_mail,
        "top_author_lines": top_count,
        "top_author_share": top_share,
        "ai_line_fraction": ai_lines / total,
        "low_bus_factor": top_share >= threshold,
    }


def compute(root, marker=DEFAULT_MARKER, threshold=DEFAULT_THRESHOLD):
    """Returns a list of per-file dicts (see `_file_entry`), one per
    scanned, git-blamable file. Files git can't blame (untracked, or
    outside history) are silently skipped -- there's no ownership signal
    to compute without commit history.
    """
    files = discover_files(root, [], include_generic=True)
    commit_cache = {}
    entries = []
    for rel_path, _lang in files:
        entry = _file_entry(root, rel_path, marker, threshold, commit_cache)
        if entry is not None:
            entries.append(entry)
    return entries


def render_text(entries, threshold):
    """Table sorted by `top_author_share` descending; `ai_line_fraction` is
    kept as its own visible column rather than a second sort, since the two
    signals answer different questions (see module docstring).
    """
    lines = [f"Ownership / Bus Factor (low-bus-factor threshold: {threshold * 100:.0f}%)", ""]
    if not entries:
        lines.append("  No blamable files found.")
        return "\n".join(lines)

    ordered = sorted(entries, key=lambda e: e["top_author_share"], reverse=True)
    header = (
        f"  {'File':<50}{'Lines':>7}{'Top Author':>20}{'Top Share':>11}{'AI Frac':>9}{'Authors':>9}  Flag"
    )
    lines.append(header)
    for e in ordered:
        flag = "low-bus-factor" if e["low_bus_factor"] else ""
        lines.append(
            f"  {e['file']:<50.50}{e['total_lines']:>7}{e['top_author']:>20.20}"
            f"{e['top_author_share'] * 100:>10.1f}%{e['ai_line_fraction'] * 100:>8.1f}%"
            f"{e['author_count']:>9}  {flag}"
        )
    return "\n".join(lines)

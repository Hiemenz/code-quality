"""Dead-code confidence: how old is each cross-file dead-code finding, and
how safe is it to actually remove?

`analyzers/dead_code.py` finds public top-level functions/classes that
look unused (never referenced, as a whole word, anywhere else in the
repo's scanned source) -- but it's a pure snapshot: it has no notion of
*when* that code was last touched. A function that looks dead but was
written yesterday might just be new/in-progress work nobody has wired up
yet; one that's looked dead for two years is a much safer removal
candidate. `dead-code-confidence` adds that missing time dimension, the
same way `todo_age.py` added one to the `todo-marker` style check: for
every dead-code finding `analyzers/dead_code.py` already produces, `git
blame -w --line-porcelain HEAD` (same technique as `todo_age.py`/
`edit_distance.py`) finds the commit that introduced the exact `def`/
`class` line, and that commit's author date gives the finding an age.

This is deliberately just the age dimension, nothing more -- it reuses
`analyzers/dead_code.py`'s `find_dead_code` directly rather than
re-deriving the detection (and its exemptions: `__all__`, dunders, test
hooks, decorated definitions -- see that module's docstring), and never
re-judges whether a finding is a true or false positive.

`confidence` is a plain 3-tier label off that single number -- one signal
can't honestly support more precision than a coarse label, so this
doesn't invent a numeric score out of it (same "auditable, not a black
box" convention as `scorer.py`'s formulas). Given `age_days` and
`stale_days` (default 180):

    age_days >= stale_days        -> "high"    (e.g. >= 180 days old)
    age_days >= stale_days / 2    -> "medium"   (e.g. >= 90 days old)
    otherwise                     -> "low"

Results are sorted by `age_days` descending -- oldest (and so
highest-confidence) first, which doubles as a prioritized removal to-do
list: the dead code that's been sitting untouched longest is the safest
to look at first.
"""

import os
import re
from datetime import datetime, timezone

from codequality.analyzers import dead_code
from codequality.git_utils import GitError, _run
from codequality.scanner import scan_repo

DEFAULT_STALE_DAYS = 180
DEFAULT_MAX_LISTING = 25

# Matches the fixed message shape find_dead_code() emits:
# "Function 'foo' is defined but never referenced anywhere else in the repo"
_NAME_RE = re.compile(r"^(?:Function|Class) '([^']+)' is defined")
_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


def _blame_shas(cwd, rel_path):
    """List of commit shas, one per line of `rel_path` at HEAD (index 0 is
    line 1). Same technique as todo_age.py's `_blame_shas` -- kept as its
    own small local duplicate rather than a shared import, same reasoning
    as that module's docstring: each of these blame-based features answers
    a different question and stays decoupled on purpose. Returns [] for
    paths git can't blame at HEAD (untracked, or deleted since).
    """
    try:
        raw = _run(["blame", "-w", "--line-porcelain", "HEAD", "--", rel_path], cwd)
    except GitError:
        return []
    shas = []
    current_sha = None
    for line in raw.splitlines():
        m = _BLAME_HEADER_RE.match(line)
        if m:
            current_sha = m.group(1)
        elif line.startswith("\t"):
            shas.append(current_sha)
    return shas


def _commit_date(cwd, sha, cache):
    """Author date for `sha`, cached across calls so a commit that
    introduced several dead-code findings is only looked up once.
    """
    if sha not in cache:
        try:
            raw = _run(["show", "-s", "--format=%aI", sha], cwd)
        except GitError:
            cache[sha] = None
        else:
            cache[sha] = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    return cache[sha]


def _read_source(root, rel_path):
    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _extract_name(message):
    m = _NAME_RE.match(message)
    return m.group(1) if m else message


def _confidence(age_days, stale_days):
    if age_days >= stale_days:
        return "high"
    if age_days >= stale_days / 2:
        return "medium"
    return "low"


def compute(root, config, stale_days=DEFAULT_STALE_DAYS):
    """Run a full scan to get every Python file's source, feed it straight
    into `analyzers.dead_code.find_dead_code` (detection logic untouched),
    then age each finding via `git blame`.

    Returns a list of
        {"file", "line", "name", "age_days", "commit_date", "confidence"}
    sorted by age_days descending. A finding whose introducing line can't
    be blamed (untracked file, no git history yet) contributes nothing --
    same "no age to report" convention as todo_age.py.
    """
    file_metrics = scan_repo(root, config)
    file_sources = {}
    for fm in file_metrics:
        if fm.language != "python":
            continue
        source = _read_source(root, fm.path)
        if source is not None:
            file_sources[fm.path] = source

    issues_by_path = dead_code.find_dead_code(file_sources)

    now = datetime.now(timezone.utc)
    date_cache = {}
    results = []
    for rel_path, issues in issues_by_path.items():
        shas = _blame_shas(root, rel_path)
        for issue in issues:
            sha = shas[issue.line - 1] if issue.line - 1 < len(shas) else None
            if sha is None:
                continue
            commit_date = _commit_date(root, sha, date_cache)
            if commit_date is None:
                continue
            age_days = (now - commit_date).days
            results.append({
                "file": rel_path,
                "line": issue.line,
                "name": _extract_name(issue.message),
                "age_days": age_days,
                "commit_date": commit_date.isoformat(),
                "confidence": _confidence(age_days, stale_days),
            })

    results.sort(key=lambda r: r["age_days"], reverse=True)
    return results


def render_text(results, stale_days=DEFAULT_STALE_DAYS, max_listing=DEFAULT_MAX_LISTING):
    """Render `compute()`'s results as a file:line/name/age/confidence
    table, sorted oldest-first -- a prioritized removal to-do list, same
    "cap the list, keep the true total" convention as todo_age.py/
    commit_lint.py.
    """
    lines = [f"Dead-Code Confidence (stale threshold: {stale_days} days)", ""]
    if not results:
        lines.append("  (no dead-code findings)")
        return "\n".join(lines)

    lines.append(f"  {'File:Line':<50}{'Name':<30}{'Age':>8}{'Confidence':>12}")
    for r in results[:max_listing]:
        loc = f"{r['file']}:{r['line']}"
        lines.append(f"  {loc:<50}{r['name']:<30}{str(r['age_days']) + 'd':>8}{r['confidence']:>12}")
    remaining = len(results) - min(len(results), max_listing)
    if remaining > 0:
        lines.append(f"  ... and {remaining} more (see --format json for the full list)")
    return "\n".join(lines)

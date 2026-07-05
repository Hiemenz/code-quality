"""Hotspots: cross per-file complexity with change frequency (Michael
Feathers' "hotspot" technique) to surface the highest-risk files in a
repo. A file that's both complex *and* changed constantly is a much bigger
risk than either extreme alone -- a complex-but-stable file rarely needs a
human to touch it again, and a simple-but-busy file is cheap to reason
about even under frequent edits. Nothing here is a new analysis: it's a
recombination of two numbers `codequality` already computes elsewhere
(per-function complexity via `scanner.scan_repo`, commit frequency via the
same git-log technique `codequality/churn.py`'s `_commit_files` uses).

`codequality hotspots` runs one normal full-repo scan for complexity, plus
one `git log --name-only` pass over the whole repo's history for change
frequency -- a single git invocation regardless of file count, instead of
one `git log --follow` per file, which would be O(files) subprocess calls
on a repo of any real size. The two are combined into one score per file:

    hotspot_score = complexity * log(commit_count + 1)

`complexity` is the *maximum* cyclomatic complexity of any function in the
file, not the average. A single deeply-tangled function is the actual risk
in a file regardless of how many trivial one-line helpers happen to share
it, and max isn't diluted by file size the way an average is -- a
400-line file with one complexity-30 function and thirty complexity-2
getters would average out to something bland, hiding exactly the function
this feature exists to surface. A file with no functions at all (pure
module-level code, or a non-Python file without tree-sitter installed)
scores 0 for complexity and can never be a hotspot no matter how often
it's touched -- churn alone, with nothing complex to be risky about, isn't
what this feature measures.

`log(commit_count + 1)` dampens churn so a file touched 500 times isn't
literally 500x riskier than one touched 50 times: going from 50 to 500
commits is a real difference, but not a two-orders-of-magnitude one in
practical risk, whereas a linear multiplier would let churn alone dominate
the ranking and drown out complexity entirely. The `+1` keeps a file with
zero matching commits (e.g. newly added and not yet committed, or outside
the `--since` window) from taking `log(0)`.

Both raw numbers (`complexity`, `commit_count`) are kept alongside the
composite score in every result row -- like every other score in this
tool (see `scorer.py`), the formula is meant to be auditable, not a black
box. A repo with no commits yet (or none matching `--since`) still
produces a full complexity-only report rather than crashing: every file
just gets `commit_count = 0` and `hotspot_score = 0.0`.
"""

import math
from collections import Counter

from codequality.git_utils import GitError, _run
from codequality.scanner import scan_repo

_RS = "\x1e"  # record separator, matches churn.py's convention


def _commit_counts(cwd, since=None):
    """dict[path] -> number of non-merge commits that touched it, via one
    `git log --name-only` pass over the whole repo's history -- the same
    technique as `churn.py`'s `_commit_files`, just tallied straight into
    counts since hotspot scoring only needs the total, not the per-commit
    detail. Returns an empty Counter (rather than raising) if the repo has
    no commits at all, or none matching `--since`.
    """
    args = ["log", "--no-merges", f"--format={_RS}%H", "--name-only"]
    if since:
        args.append(f"--since={since}")
    try:
        raw = _run(args, cwd)
    except GitError:
        return Counter()
    counts = Counter()
    # Split on the record separator *before* splitting into lines -- same
    # reasoning as churn.py: str.splitlines() treats \x1e as a line
    # boundary in its own right, so doing it in the other order silently
    # eats the delimiter.
    for chunk in raw.split(_RS):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        _, _, rest = chunk.partition("\n")
        for f in rest.splitlines():
            f = f.strip()
            if f:
                counts[f] += 1
    return counts


def _file_complexity(fm):
    """Max cyclomatic complexity of any function in this file (see module
    docstring for why max over average); 0 for a file with no functions.
    """
    if not fm.functions:
        return 0
    return max(fn.complexity for fn in fm.functions)


def compute(root, config, since=None):
    """Run a full scan plus one git-log pass and return a list of
    {file, complexity, commit_count, hotspot_score}, sorted by
    hotspot_score descending (ties broken by file path for determinism).
    """
    file_metrics = scan_repo(root, config)
    commit_counts = _commit_counts(root, since=since)

    rows = []
    for fm in file_metrics:
        complexity = _file_complexity(fm)
        commit_count = commit_counts.get(fm.path, 0)
        hotspot_score = complexity * math.log(commit_count + 1)
        rows.append({
            "file": fm.path,
            "complexity": complexity,
            "commit_count": commit_count,
            "hotspot_score": hotspot_score,
        })
    rows.sort(key=lambda r: (-r["hotspot_score"], r["file"]))
    return rows


def render_text(rows, top_n=25):
    """Render the top `top_n` rows from `compute()` as a rank/file/
    complexity/commits/score table.
    """
    shown = rows[:top_n]
    if not shown:
        return "No files found."
    lines = ["Hotspots (complexity x log(commits + 1))", ""]
    lines.append(f"  {'#':>4}  {'File':<60}{'Complexity':>12}{'Commits':>10}{'Score':>10}")
    for i, r in enumerate(shown, start=1):
        lines.append(
            f"  {i:>4}  {r['file']:<60}{r['complexity']:>12}{r['commit_count']:>10}{r['hotspot_score']:>10.2f}"
        )
    return "\n".join(lines)

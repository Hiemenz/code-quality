"""`codequality complexity-regression`: per-function complexity regression
comparison between two arbitrary git refs (e.g. two tags -- "which
functions got a lot more complex between v1.2 and v1.3").

This is the `api-diff`-style generalization of the `complexity-regression`
check that already runs inside `codequality diff` (see
`analyzers/complexity_regression.py` and
`scanner._apply_complexity_regression`): that check only ever compares the
working tree (or one `--base`) against the current `diff` invocation, and
only for files that happen to appear in that one diff. This module instead
walks every Python file that exists at `--to`, fetches its content there
and (if it existed) at `--from` via `git show`, and runs the exact same
`compare_functions` comparison on each pair -- so it works for "how did
complexity drift between two releases" even when neither ref is the
current HEAD or working tree.

All comparison logic is reused as-is from `analyzers/complexity_regression.py`
(`compare_functions`) -- this module only adds the git plumbing to
enumerate files and fetch content at two refs, the same plumbing
`api_diff.py` already uses (`git ls-tree`/`git show` via `git_utils`).

Unlike `api_diff`, a file deleted between the two refs has no
"removed-*" case here: a function that no longer exists has no *new*
complexity to report a regression on, so a deleted file (or a deleted
function within a surviving file) is silently skipped, the same
"nothing to compare" treatment `compare_functions` already gives a
brand-new function in the other direction.
"""

import json

from codequality.analyzers.complexity_regression import DEFAULT_THRESHOLD, compare_functions
from codequality.git_utils import _run, get_file_at_ref

__all__ = ["compare", "render_text", "render_json"]


def _list_python_files(ref, cwd):
    """Every `.py` path tracked at `ref` (via `git ls-tree`, not a working-tree
    walk -- refs other than HEAD have no working tree to walk).
    """
    out = _run(["ls-tree", "-r", "--name-only", ref], cwd)
    return [line for line in out.splitlines() if line.endswith(".py")]


def compare(root, from_ref, to_ref="HEAD", threshold=DEFAULT_THRESHOLD):
    """Compare every Python file's per-function cyclomatic complexity
    between `from_ref` and `to_ref`. Returns a JSON-serializable dict;
    raises `GitError` if either ref doesn't resolve.
    """
    old_files = set(_list_python_files(from_ref, root))
    new_files = sorted(_list_python_files(to_ref, root))

    issues = []
    for path in new_files:
        old_source = get_file_at_ref(from_ref, path, root) if path in old_files else None
        new_source = get_file_at_ref(to_ref, path, root)
        issues.extend(compare_functions(old_source, new_source, path, threshold=threshold))
    issues.sort(key=lambda i: (i.file, i.line))

    return {
        "tool": "codequality",
        "mode": "complexity-regression",
        "from_ref": from_ref,
        "to_ref": to_ref,
        "threshold": threshold,
        "files_compared": len(new_files),
        "issues": [i.to_dict() for i in issues],
    }


def render_text(result):
    """Render `compare()`'s result dict as a plain-text report."""
    lines = [
        f"Complexity Regression: {result['from_ref']} -> {result['to_ref']} "
        f"(threshold: +{result['threshold']})",
        f"Files compared: {result['files_compared']}",
        "",
    ]
    issues = result["issues"]
    if not issues:
        lines.append("No significant complexity regressions detected.")
        return "\n".join(lines)

    lines.append(f"{len(issues)} issue(s):")
    for issue in issues:
        sev = issue["severity"].upper()
        lines.append(f"  [{sev:<5}] {issue['file']}:{issue['line']}  {issue['symbol']} - {issue['message']}")
    return "\n".join(lines)


def render_json(result):
    return json.dumps(result, indent=2, sort_keys=False)

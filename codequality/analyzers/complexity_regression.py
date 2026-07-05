"""Per-function complexity regression detection: compares the cyclomatic
complexity of each matched function between an old and new version of the
same Python source, flagging any function that got significantly harder to
reason about. This is the complexity-focused sibling of
`analyzers/signature_diff.py`'s "did the API silently break" question --
same shape (old node vs. new node, only for functions present in both),
same function-matching rule (`qualified_functions`: public top-level
functions and public methods of public top-level classes only), just a
different property compared.

Complexity itself is never recomputed here. `_build_function_metrics` --
the exact function `analyzers/python_analyzer.py` uses to build every
function's `FunctionMetrics.complexity` (the same number the Complexity
category is scored from, and the same number `complexity-trend` already
tracks over time) -- is imported and called directly on each matched
function's AST node, so this can never drift from the score's own
definition of "complexity."

Two entry points share this module's one core comparison function,
`compare_functions`:

- `codequality diff`'s always-on wiring (`scanner._apply_complexity_regression`),
  comparing the working tree against `--base` -- the same "old" ref
  `breaking-signature-change` already gets, diff-only for the same reason:
  there's no "old version" to compare against in a plain `scan`.
- `codequality complexity-regression --from REF --to REF`
  (`complexity_regression_diff.py`), the `api-diff`-style standalone
  subcommand for comparing any two points in history on demand, not just
  the current diff invocation.
"""

import ast

from codequality.analyzers.base import Issue
from codequality.analyzers.python_analyzer import _build_function_metrics
from codequality.analyzers.signature_diff import qualified_functions

DEFAULT_THRESHOLD = 5


def _is_regression(old_complexity, new_complexity, threshold):
    """A function is flagged if its complexity increased by more than
    `threshold`, an absolute delta -- the single rule this check uses.

    A relative rule ("doubled") was considered and dropped: it would flag
    a function going from 2 to 5 (a small, often-fine change) while
    missing one going from 20 to 30 (a much larger real increase) unless
    tuned separately per magnitude. One absolute threshold, applied
    consistently, avoids that split without losing the case that actually
    matters in practice: a function crossing from "fine" to "worth a
    second look" complexity in one change.
    """
    return new_complexity - old_complexity > threshold


def _issue(path, node, name, old_complexity, new_complexity):
    delta = new_complexity - old_complexity
    return Issue(
        path, node.lineno, "complexity", "warn", "complexity-regression",
        f"Function '{name}' cyclomatic complexity increased from {old_complexity} to {new_complexity} (+{delta})"
    )


def compare_functions(old_source, new_source, path, threshold=DEFAULT_THRESHOLD):
    """Compare every public top-level function/method present in both
    `old_source` and `new_source`, flagging one whose cyclomatic complexity
    increased by more than `threshold`.

    `old_source` may be None (a newly-added file has nothing to compare
    against -- same convention as `signature_diff.signature_diff_issues`).
    Returns [] on any parse failure, same "bonus signal, fail quiet rather
    than block the scan" rule every diff-only check here follows. A
    function with no old counterpart (brand new) is silently skipped --
    there's nothing to regress from.
    """
    if old_source is None:
        return []
    try:
        old_tree = ast.parse(old_source, filename=path)
        new_tree = ast.parse(new_source, filename=path)
    except SyntaxError:
        return []

    old_functions = qualified_functions(old_tree)
    new_functions = qualified_functions(new_tree)

    issues = []
    for name, new_node in new_functions.items():
        old_node = old_functions.get(name)
        if old_node is None:
            continue
        old_complexity = _build_function_metrics(old_node, path).complexity
        new_complexity = _build_function_metrics(new_node, path).complexity
        if _is_regression(old_complexity, new_complexity, threshold):
            issues.append(_issue(path, new_node, name, old_complexity, new_complexity))
    return issues

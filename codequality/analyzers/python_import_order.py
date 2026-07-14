"""Import-order checks: flags violations of the conventional block ordering
(__future__ → stdlib → third-party → local/relative) when they can be
detected without running anything.

Two checks, both purely AST-based:

  future-import-order   A ``from __future__`` import appears after a
                        non-future import.  The interpreter enforces this
                        at runtime too, but we catch it statically.

  relative-before-absolute  A relative import (``from .foo import ...``)
                        appears before one or more absolute imports in the
                        same module.  Relative imports are always local-to-
                        package, so they belong after stdlib and third-party.

We deliberately skip the stdlib-vs-third-party boundary: reliably classifying
a top-level module name requires either executing code or carrying a large
allowlist, and either approach adds noise / false positives that outweigh the
benefit.  The two checks above are unambiguous and zero-dependency.
"""

import ast
import sys

from codequality.analyzers.base import Issue

# sys.stdlib_module_names is available from Python 3.10+.
if hasattr(sys, "stdlib_module_names"):
    _STDLIB = sys.stdlib_module_names
else:  # pragma: no cover — Python < 3.10
    _STDLIB = frozenset()

_SYMBOL_FUTURE_ORDER = "future-import-order"
_SYMBOL_RELATIVE_ORDER = "relative-before-absolute"


def _top_level_import_stmts(tree):
    """Yield (node, is_future, is_relative) for every import statement that
    appears at module scope (not inside a function / class / try block).
    We stop at the first non-import, non-docstring, non-comment statement so
    that an import buried in conditional logic at the bottom of a file doesn't
    trigger false positives.
    """
    past_leading_docstring = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Expr):
            if not past_leading_docstring and isinstance(node.value, ast.Constant):
                past_leading_docstring = True
                continue
            break
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
            is_relative = isinstance(node, ast.ImportFrom) and (node.level or 0) > 0
            yield node, is_future, is_relative
        elif isinstance(node, (ast.If, ast.Try)):
            # TYPE_CHECKING guards and try/except ImportError are common
            # patterns for conditional imports at module level — don't
            # break the import block on them, but don't classify them as
            # plain imports either.
            continue
        else:
            break


def import_order_issues(tree, path, only_lines=None):
    """Return a list of Issues for import-ordering violations.

    Violations are collected across *all* top-level imports (regardless of
    ``only_lines``) so that state is always correct, then the final list is
    filtered so only issues whose line is in ``only_lines`` are returned.
    This mirrors how `_in_scope` works in other checkers: a relative import
    on line 1 triggers a violation whose Issue is at line 1; if line 1 is
    in scope the issue surfaces, even if the triggering absolute import is on
    a different (out-of-scope) line.
    """
    raw = []
    seen_non_future = False
    first_relative_lineno = None

    for node, is_future, is_relative in _top_level_import_stmts(tree):
        lineno = node.lineno
        if is_future:
            if seen_non_future:
                raw.append(Issue(
                    path, lineno, "style", "warn", _SYMBOL_FUTURE_ORDER,
                    "'from __future__ import' must appear before all other imports"
                ))
        else:
            seen_non_future = True

        if is_relative:
            if first_relative_lineno is None:
                first_relative_lineno = lineno
        else:
            if first_relative_lineno is not None:
                raw.append(Issue(
                    path, first_relative_lineno, "style", "warn", _SYMBOL_RELATIVE_ORDER,
                    "Relative import appears before an absolute import -- "
                    "relative imports belong after stdlib and third-party imports"
                ))
                first_relative_lineno = None

    if only_lines is None:
        return raw
    return [i for i in raw if i.line in only_lines]

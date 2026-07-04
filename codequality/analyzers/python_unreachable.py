"""Unreachable-code detection: a statement following an unconditional
`return`/`raise`/`continue`/`break` in the same block can never execute.
This occasionally shows up in LLM output when it over-generates a branch
after code that already unconditionally exits it -- a leftover from an
edit, or a misunderstanding of control flow.

Purely structural (McCabe-style tools like pyflakes/pylint do the same
check): find every statement-list in the tree (a function/if/for/while/
try/except/with body, or a match-case body) and flag anything after the
first terminator in that same list.
"""

import ast

from codequality.analyzers.base import Issue

_TERMINATORS = (ast.Return, ast.Raise, ast.Continue, ast.Break)
_BLOCK_ATTRS = ("body", "orelse", "finalbody")


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _first_unreachable(stmts):
    terminated = False
    for stmt in stmts:
        if terminated:
            return stmt
        if isinstance(stmt, _TERMINATORS):
            terminated = True
    return None


def unreachable_code_issues(tree, path, only_lines):
    """Every unreachable-code issue found anywhere in `tree`."""
    issues = []
    for node in ast.walk(tree):
        for attr in _BLOCK_ATTRS:
            stmts = getattr(node, attr, None)
            if not isinstance(stmts, list) or not stmts or not isinstance(stmts[0], ast.stmt):
                continue
            unreachable = _first_unreachable(stmts)
            if unreachable is not None and _in_scope(unreachable, only_lines):
                issues.append(
                    Issue(path, unreachable.lineno, "correctness", "warn", "unreachable-code",
                          "This code can never execute -- it follows an unconditional "
                          "return/raise/continue/break in the same block")
                )
    return issues

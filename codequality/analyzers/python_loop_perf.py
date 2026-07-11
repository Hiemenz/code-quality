"""Performance check: string concatenation inside a loop via `+=`.

`result += "literal"` or `result += f"..."` inside a `for`/`while` body
allocates a new string and copies the accumulated prefix on every iteration --
O(n²) in the result length. The fix is to collect fragments into a list and
join at the end: `parts.append(fragment)` → `"".join(parts)`.

Detection is narrow by design: only `AugAssign(op=Add)` where the right-hand
side is a string constant or f-string. General `result += var` is skipped --
without type information it would produce false positives on numeric +=.

Nested loops are handled correctly: each augmented assignment is attributed to
its immediately-enclosing loop only, never counted for an outer loop.
"""

import ast

from codequality.analyzers.base import Issue

SYMBOL = "string-concat-in-loop"


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _is_string_expr(node):
    """True if `node` is recognisably a string value without type inference."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string_expr(node.left) or _is_string_expr(node.right)
    return False


class _LoopBodyVisitor(ast.NodeVisitor):
    """Collect string-concat augmented assignments in a loop body, stopping
    at nested loop / function / class boundaries so each is attributed only
    to its immediately enclosing loop.
    """

    def __init__(self):
        self.findings = []  # list[(lineno, name)]

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop
    visit_ClassDef = _stop
    visit_For = _stop
    visit_AsyncFor = _stop
    visit_While = _stop

    def visit_AugAssign(self, node):
        if (
            isinstance(node.op, ast.Add)
            and isinstance(node.target, ast.Name)
            and _is_string_expr(node.value)
        ):
            self.findings.append((node.lineno, node.target.id))
        self.generic_visit(node)


def string_concat_in_loop_issues(tree, path, only_lines=None):
    """Every string-concat-in-loop finding in `tree`."""
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            continue
        if not _in_scope(node, only_lines):
            continue
        visitor = _LoopBodyVisitor()
        for child in node.body:
            visitor.visit(child)
        for lineno, name in visitor.findings:
            if only_lines is not None and lineno not in only_lines:
                continue
            issues.append(Issue(
                path, lineno, "style", "warn", SYMBOL,
                f"'{name} += ...' inside a loop builds a string in O(n²) "
                f"-- collect into a list and ''.join(...) at the end"
            ))
    return issues

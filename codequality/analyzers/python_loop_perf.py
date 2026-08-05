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

A `+=` whose target is plain-assigned earlier in the same loop body is skipped:
the accumulator is rebuilt from scratch every iteration (`s = f(x)` ...
`s += ", ..."`), so nothing accumulates across iterations and there is no
O(n²) growth. This deliberately also skips a conditional reset
(`if first: s = ""` ... `s += part`), trading a little recall for precision,
matching this check's narrow-by-design detection.
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
        self.assignments = []  # list[(lineno, name)] of plain assignments in this body

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

    def visit_Assign(self, node):
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.assignments.append((node.lineno, target.id))
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None and isinstance(node.target, ast.Name):
            self.assignments.append((node.lineno, node.target.id))
        self.generic_visit(node)

    def visit_NamedExpr(self, node):
        if isinstance(node.target, ast.Name):
            self.assignments.append((node.lineno, node.target.id))
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
            if any(a_line < lineno and a_name == name for a_line, a_name in visitor.assignments):
                continue  # rebuilt each iteration, not a cross-iteration accumulator
            issues.append(Issue(
                path, lineno, "style", "warn", SYMBOL,
                f"'{name} += ...' inside a loop builds a string in O(n²) "
                f"-- collect into a list and ''.join(...) at the end"
            ))
    return issues

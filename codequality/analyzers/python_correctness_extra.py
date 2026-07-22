"""Two small correctness checks that share a module:

- **`float-equality`** -- `==` or `!=` with a float literal on either side.
  Floating-point results are rarely bit-exact (`0.1 + 0.2 != 0.3`), so an
  exact equality test against a float is almost always a latent bug; compare
  with a tolerance (`math.isclose`) instead. A comparison to `0.0` is still
  flagged: exact-zero tests are a common source of the same surprise, and
  `math.isclose(x, 0.0)` / an explicit `abs(x) < eps` states the intent.

- **`mutable-global`** -- a function that rebinds a module-level name via the
  `global` statement *and* assigns to it. Module-level mutable state written
  from inside functions is a classic source of order-dependent, hard-to-test
  behaviour (and, in threaded code, races). Reported at `info`: it's
  sometimes deliberate (a memoization cache, a lazy singleton), so this is a
  smell to review, not a guaranteed defect.

Both are pure AST, no type information, consistent with the rest of the
analyzers.
"""

import ast

from codequality.analyzers.base import Issue

FLOAT_EQ_SYMBOL = "float-equality"
MUTABLE_GLOBAL_SYMBOL = "mutable-global"

_EQ_OPS = (ast.Eq, ast.NotEq)


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _is_float_literal(node):
    if isinstance(node, ast.Constant):
        return isinstance(node.value, float)
    # Negative literals parse as UnaryOp(USub, Constant); catch -1.5 too.
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return _is_float_literal(node.operand)
    return False


def float_equality_issues(tree, path, only_lines=None):
    """Flag `==`/`!=` comparisons involving a float literal."""
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not _in_scope(node, only_lines):
            continue
        operands = [node.left] + list(node.comparators)
        for i, op in enumerate(node.ops):
            if not isinstance(op, _EQ_OPS):
                continue
            if _is_float_literal(operands[i]) or _is_float_literal(operands[i + 1]):
                issues.append(Issue(
                    path, node.lineno, "correctness", "warn", FLOAT_EQ_SYMBOL,
                    "Exact equality against a float literal is unreliable "
                    "(floats rarely compare bit-exact) -- use math.isclose(...) with a tolerance"
                ))
                break  # one finding per comparison expression
    return issues


def mutable_global_issues(tree, path, only_lines=None):
    """Flag functions that declare a name `global` and then assign to it."""
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _in_scope(node, only_lines):
            continue
        declared = set()
        for inner in ast.walk(node):
            if isinstance(inner, ast.Global):
                declared.update(inner.names)
        if not declared:
            continue
        assigned = _assigned_names(node)
        mutated = sorted(declared & assigned)
        if mutated:
            shown = ", ".join(f"'{n}'" for n in mutated[:3])
            if len(mutated) > 3:
                shown += ", ..."
            issues.append(Issue(
                path, node.lineno, "correctness", "info", MUTABLE_GLOBAL_SYMBOL,
                f"Function '{node.name}' mutates module-level global(s) {shown} -- "
                f"global mutable state is order-dependent and hard to test; "
                f"prefer returning a value or a class attribute"
            ))
    return issues


def _assigned_names(func_node):
    """Names assigned (=, augmented, annotated, walrus, for-target) anywhere in func."""
    names = set()
    for n in ast.walk(func_node):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                names |= _target_names(t)
        elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
            names |= _target_names(n.target)
        elif isinstance(n, ast.NamedExpr):
            names |= _target_names(n.target)
        elif isinstance(n, (ast.For, ast.AsyncFor)):
            names |= _target_names(n.target)
    return names


def _target_names(target):
    """Set of Name ids bound by an assignment target (handles tuple/list unpack)."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        out = set()
        for elt in target.elts:
            out |= _target_names(elt)
        return out
    return set()

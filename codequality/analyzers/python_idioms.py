"""Python idiom checks: comparisons to None/True/False, shadowed builtins,
mutable class attributes, f-strings without placeholders, redundant else,
boolean-trap parameters, magic numbers, nested comprehensions, and complex
lambdas. All pure AST, no type information needed.
"""

import ast

from codequality.analyzers.base import Issue

# Non-exception, non-dunder builtins that are commonly accidentally shadowed.
# Exception classes are excluded: subclassing them for custom exceptions is
# legitimate and common, unlike accidentally naming a variable 'list' or 'id'.
_SHADOWED_BUILTINS = frozenset({
    "abs", "all", "any", "ascii", "bin", "bool", "breakpoint", "bytearray",
    "bytes", "callable", "chr", "classmethod", "compile", "complex", "delattr",
    "dict", "dir", "divmod", "enumerate", "eval", "exec", "filter", "float",
    "format", "frozenset", "getattr", "globals", "hasattr", "hash", "hex",
    "id", "input", "int", "isinstance", "issubclass", "iter", "len", "list",
    "locals", "map", "max", "memoryview", "min", "next", "object", "oct",
    "open", "ord", "pow", "print", "property", "range", "repr", "reversed",
    "round", "set", "setattr", "slice", "sorted", "staticmethod", "str",
    "sum", "super", "tuple", "type", "vars", "zip",
})


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _check_comparison_pair(op, lv, rv, path, lineno):
    """Issue if this (lv op rv) comparison is to None/True/False via == or !=."""
    for side in (lv, rv):
        if not isinstance(side, ast.Constant):
            continue
        val = side.value
        if val is None:
            if isinstance(op, ast.Eq):
                return Issue(path, lineno, "style", "warn", "comparison-to-none",
                             "Use 'is None' instead of '== None'")
            if isinstance(op, ast.NotEq):
                return Issue(path, lineno, "style", "warn", "comparison-to-none",
                             "Use 'is not None' instead of '!= None'")
        elif isinstance(val, bool):  # bool before int: bool is a subclass of int
            if isinstance(op, (ast.Eq, ast.NotEq)):
                literal = "True" if val else "False"
                return Issue(path, lineno, "style", "info", "comparison-to-true",
                             f"Use truthiness check instead of '== {literal}' or '!= {literal}'")
    return None


def comparison_idiom_issues(tree, path, only_lines=None):
    """Flag `x == None`, `x != None`, `x == True`, `x == False` (and reversed forms)."""
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not _in_scope(node, only_lines):
            continue
        all_operands = [node.left] + list(node.comparators)
        for i, op in enumerate(node.ops):
            issue = _check_comparison_pair(op, all_operands[i], all_operands[i + 1], path, node.lineno)
            if issue:
                issues.append(issue)
    return issues


def _shadowed_names(node):
    """Return (name, lineno) pairs for any builtin-shadowing bindings in node."""
    if isinstance(node, ast.Assign):
        return [(t.id, node.lineno) for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.lineno)]
    if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.lineno)]
    if isinstance(node, ast.For) and isinstance(node.target, ast.Name):
        return [(node.target.id, node.lineno)]
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [(node.name, node.lineno)]
    return []


def shadowed_builtin_issues(tree, path, only_lines=None):
    """Flag assignments, for-loop targets, and function/class definitions whose
    name shadows a Python builtin -- silently breaks any code that expects the
    original binding after this point.
    """
    issues = []
    for node in ast.walk(tree):
        if not _in_scope(node, only_lines):
            continue
        for name, lineno in _shadowed_names(node):
            if name in _SHADOWED_BUILTINS:
                issues.append(Issue(
                    path, lineno, "style", "warn", "shadowed-builtin",
                    f"'{name}' shadows the built-in '{name}' -- rename to avoid masking it downstream"
                ))
    return issues


def _class_stmt_targets_and_value(stmt):
    """Return (targets, value) for assignment statements inside a class body."""
    if isinstance(stmt, ast.Assign):
        return stmt.targets, stmt.value
    if isinstance(stmt, ast.AnnAssign):
        return [stmt.target], getattr(stmt, "value", None)
    return None, None


def mutable_class_attribute_issues(tree, path, only_lines=None):
    """Flag class-level list/dict/set literals: they are shared across all
    instances, unlike instance attributes defined in __init__, and are almost
    always an accidental mutable-default footgun at the class level.
    """
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for stmt in node.body:
            if not _in_scope(stmt, only_lines):
                continue
            targets, value = _class_stmt_targets_and_value(stmt)
            if targets is None or value is None:
                continue
            if not isinstance(value, (ast.List, ast.Dict, ast.Set)):
                continue
            kind = type(value).__name__.lower()
            for target in targets:
                if isinstance(target, ast.Name):
                    issues.append(Issue(
                        path, stmt.lineno, "style", "warn", "mutable-class-attribute",
                        f"Class-level '{target.id}' is a mutable {kind} shared across all instances "
                        f"-- assign it in __init__ instead"
                    ))
    return issues


def f_string_no_placeholder_issues(tree, path, only_lines=None):
    """Flag f-strings that contain no {} placeholders -- the 'f' prefix does
    nothing and may be hiding a missing interpolation.
    """
    # Format-spec sub-strings like `{label:<28}` have their `<28` part stored
    # as a nested JoinedStr on FormattedValue.format_spec.  Those inner nodes
    # contain only Constant values (no FormattedValue), but they're valid parts
    # of a real f-string and must not be flagged.  Collect them first so we can
    # skip them in the main walk.
    format_spec_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FormattedValue) and node.format_spec is not None:
            format_spec_nodes.add(id(node.format_spec))

    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or not _in_scope(node, only_lines):
            continue
        if id(node) in format_spec_nodes:
            continue
        if not any(isinstance(v, ast.FormattedValue) for v in node.values):
            issues.append(Issue(
                path, node.lineno, "style", "info", "f-string-no-placeholder",
                "f-string has no placeholders -- remove the 'f' prefix or add a {variable}"
            ))
    return issues


def _last_stmt_always_exits(stmts):
    """True if the last statement in `stmts` is an unconditional control
    transfer at statement level (return/raise/continue/break).
    """
    if not stmts:
        return False
    return isinstance(stmts[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))


def redundant_else_issues(tree, path, only_lines=None):
    """Flag `else` blocks after `if` branches that always exit via
    return/raise/continue/break -- the else indentation is redundant and
    can be flattened.
    """
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _in_scope(node, only_lines):
            continue
        if not node.orelse:
            continue
        # Skip `elif` chains: orelse[0] being an If node means it's `elif`,
        # not a standalone `else`. The inner `elif` nodes are visited separately.
        if isinstance(node.orelse[0], ast.If):
            continue
        if _last_stmt_always_exits(node.body):
            else_lineno = node.orelse[0].lineno
            issues.append(Issue(
                path, else_lineno, "style", "info", "redundant-else",
                "else block is redundant -- the preceding if branch always returns/raises/continues/breaks"
            ))
    return issues


# ---------------------------------------------------------------------------
# Boolean-trap parameters
# ---------------------------------------------------------------------------

def _is_bool_param(arg, default):
    """True if `arg` is typed or defaulted as bool."""
    if isinstance(arg.annotation, ast.Name) and arg.annotation.id == "bool":
        return True
    return isinstance(default, ast.Constant) and isinstance(default.value, bool)


def _bool_params(node):
    """Return the positional param names that are clearly boolean for `node`."""
    pos_args = list(node.args.posonlyargs) + list(node.args.args)
    if pos_args and pos_args[0].arg in ("self", "cls"):
        pos_args = pos_args[1:]
    n_no_default = len(pos_args) - len(node.args.defaults)
    result = []
    for i, arg in enumerate(pos_args):
        di = i - n_no_default
        default = node.args.defaults[di] if 0 <= di < len(node.args.defaults) else None
        if _is_bool_param(arg, default):
            result.append(arg.arg)
    return result


def boolean_trap_issues(tree, path, only_lines=None):
    """Flag functions with 2+ positional parameters that are clearly boolean
    (bool annotation or bool default value).  At call sites these force
    `f(True, False, True)` with no indication of what each flag means.  The
    fix is to make them keyword-only: `def f(*, ascending=True, stable=False)`.
    """
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _in_scope(node, only_lines):
            continue
        bool_p = _bool_params(node)
        if len(bool_p) >= 2:
            shown = ", ".join(f"'{p}'" for p in bool_p[:3])
            if len(bool_p) > 3:
                shown += ", ..."
            issues.append(Issue(
                path, node.lineno, "style", "info", "boolean-trap",
                f"Function '{node.name}' has {len(bool_p)} positional bool parameters "
                f"({shown}) -- make them keyword-only or use an options object"
            ))
    return issues


# ---------------------------------------------------------------------------
# Magic numbers
# ---------------------------------------------------------------------------

# Integers (by absolute value) that are common enough to be noise-free.
_MAGIC_SAFE = frozenset({
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    16, 24, 32, 60, 64, 100, 128, 256, 365, 512, 1000, 1024,
})


class _MagicFinder(ast.NodeVisitor):
    """Collect magic integer literals from BinOp / Compare / AugAssign,
    stopping at nested function/class boundaries.
    """

    def __init__(self):
        self.found = []  # list[(lineno, value)]

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_ClassDef = _stop

    def _check(self, node):
        if not isinstance(node, ast.Constant):
            return
        val = node.value
        if not isinstance(val, int) or isinstance(val, bool):
            return
        if abs(val) not in _MAGIC_SAFE:
            self.found.append((node.lineno, val))

    def visit_BinOp(self, node):
        self._check(node.left)
        self._check(node.right)
        self.generic_visit(node)

    def visit_Compare(self, node):
        for comp in node.comparators:
            self._check(comp)
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        self._check(node.value)
        self.generic_visit(node)


def magic_number_issues(tree, path, only_lines=None):
    """Flag integer literals used directly in arithmetic, comparisons, or
    augmented assignments that aren't in the common 'safe' set -- these
    should be extracted to a named constant.
    """
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _in_scope(node, only_lines):
            continue
        finder = _MagicFinder()
        finder.generic_visit(node)
        for lineno, val in finder.found:
            if only_lines is not None and lineno not in only_lines:
                continue
            issues.append(Issue(
                path, lineno, "style", "info", "magic-number",
                f"Magic number {val} -- extract to a named constant"
            ))
    return issues


# ---------------------------------------------------------------------------
# Nested comprehensions
# ---------------------------------------------------------------------------

_COMP_KIND = {
    ast.ListComp: "list comprehension",
    ast.SetComp: "set comprehension",
    ast.DictComp: "dict comprehension",
    ast.GeneratorExp: "generator expression",
}


def nested_comprehension_issues(tree, path, only_lines=None):
    """Flag list/dict/set comprehensions and generator expressions with 3+
    nested for-clauses -- these are nearly impossible to read; rewrite as a
    regular for-loop.
    """
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, tuple(_COMP_KIND)) or not _in_scope(node, only_lines):
            continue
        n = len(node.generators)
        if n >= 3:
            kind = _COMP_KIND[type(node)]
            issues.append(Issue(
                path, node.lineno, "style", "info", "nested-comprehension",
                f"{kind} with {n} nested for-clauses -- rewrite as a for-loop for readability"
            ))
    return issues


# ---------------------------------------------------------------------------
# Long (complex) lambdas
# ---------------------------------------------------------------------------

def _lambda_complexity(body):
    """Count 'heavy' operation nodes (operators, comparisons, calls, ternary)
    in a lambda body, not descending into nested lambdas.
    """
    count = 0
    for node in ast.walk(body):
        if isinstance(node, ast.Lambda) and node is not body:
            continue  # don't count nested lambda's ops against the outer one
        if isinstance(node, (ast.BinOp, ast.BoolOp, ast.UnaryOp, ast.Compare,
                              ast.IfExp, ast.Call)):
            count += 1
    return count


def long_lambda_issues(tree, path, only_lines=None):
    """Flag lambda expressions whose body contains more than 2 non-trivial
    operations (arithmetic, comparisons, function calls, ternary expressions)
    -- extract them into a named function for readability.
    """
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Lambda) or not _in_scope(node, only_lines):
            continue
        if _lambda_complexity(node.body) > 2:
            issues.append(Issue(
                path, node.lineno, "style", "info", "long-lambda",
                "Lambda body is too complex -- extract into a named function"
            ))
    return issues

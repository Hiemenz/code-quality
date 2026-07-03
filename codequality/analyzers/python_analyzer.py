"""AST-based analyzer for Python source files.

This is the deep-analysis path: real cyclomatic complexity, nesting depth,
function length, parameter counts and docstring coverage all come from the
parsed syntax tree rather than regex guessing. Line-based checks (long
lines, TODOs, trailing whitespace, ...) are layered on top since comments
and formatting are not visible in the AST.
"""

import ast
import re

from codequality.analyzers.base import FileMetrics, FunctionMetrics, Issue, is_public_name

TODO_RE = re.compile(r"#\s*(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)

_COMPOUND_TYPES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
)
if hasattr(ast, "TryStar"):
    _COMPOUND_TYPES = _COMPOUND_TYPES + (ast.TryStar,)

_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


class _ComplexityVisitor(ast.NodeVisitor):
    """McCabe-style cyclomatic complexity, scoped to a single function body.

    Nested function/class definitions stop the walk (they are scored on
    their own) so a helper closure doesn't inflate its parent's score.
    """

    def __init__(self):
        self.complexity = 1

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop
    visit_ClassDef = _stop

    def visit_If(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_For(self, node):
        self.complexity += 1
        self.generic_visit(node)

    visit_AsyncFor = visit_For

    def visit_While(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_Try(self, node):
        self.complexity += max(1, len(node.handlers))
        self.generic_visit(node)

    if hasattr(ast, "TryStar"):

        def visit_TryStar(self, node):
            self.complexity += max(1, len(node.handlers))
            self.generic_visit(node)

    def visit_BoolOp(self, node):
        self.complexity += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_comprehension(self, node):
        self.complexity += 1 + len(node.ifs)
        self.generic_visit(node)

    def visit_Assert(self, node):
        self.complexity += 1
        self.generic_visit(node)

    def visit_Match(self, node):
        self.complexity += max(0, len(node.cases) - 1)
        self.generic_visit(node)


class _NestingVisitor(ast.NodeVisitor):
    """Deepest nesting of compound blocks inside a single function body."""

    def __init__(self):
        self.max_depth = 0
        self._depth = 0

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop

    def generic_visit(self, node):
        if isinstance(node, _COMPOUND_TYPES):
            self._enter_compound(node)
        else:
            super().generic_visit(node)

    def _enter_compound(self, node):
        self._depth += 1
        self.max_depth = max(self.max_depth, self._depth)
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._depth -= 1


def _count_params(node):
    args = node.args
    n = len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
    if args.vararg:
        n += 1
    if args.kwarg:
        n += 1
    return n


def _has_mutable_default(node):
    defaults = list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]
    for d in defaults:
        if isinstance(d, (ast.List, ast.Dict, ast.Set)):
            return True
    return False


def _line_checks(path, lines, max_line_length, only_lines=None):
    """Comment/formatting checks that need raw source text, not the AST.

    If `only_lines` is given (a set of 1-based line numbers), checks are
    restricted to those lines -- used to score just the added lines of a
    diff instead of the whole file.
    """
    issues = []
    comment_lines = 0
    for i, raw in enumerate(lines, start=1):
        stripped = raw.rstrip("\n")
        if only_lines is not None and i not in only_lines:
            if stripped.lstrip().startswith("#"):
                comment_lines += 1
            continue
        text_only = stripped.lstrip()
        if text_only.startswith("#"):
            comment_lines += 1
        if len(stripped) > max_line_length:
            issues.append(
                Issue(
                    path,
                    i,
                    "style",
                    "info",
                    "long-line",
                    f"Line is {len(stripped)} characters (limit {max_line_length})",
                )
            )
        if stripped != stripped.rstrip():
            issues.append(Issue(path, i, "style", "info", "trailing-whitespace", "Trailing whitespace"))
        if TODO_RE.search(stripped):
            issues.append(Issue(path, i, "style", "info", "todo-marker", stripped.strip()[:120]))
        if "\t" in raw:
            issues.append(Issue(path, i, "style", "info", "tab-indent", "Tab character used for indentation"))
    return issues, comment_lines


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    end_lineno = getattr(node, "end_lineno", node.lineno)
    return any(node.lineno <= ln <= end_lineno for ln in only_lines)


def _process_function(node, path, limits, fm):
    end_lineno = getattr(node, "end_lineno", node.lineno)
    cv = _ComplexityVisitor()
    cv.generic_visit(node)
    nv = _NestingVisitor()
    nv.generic_visit(node)

    length = end_lineno - node.lineno + 1
    has_doc = ast.get_docstring(node) is not None
    public = is_public_name(node.name)

    fm.functions.append(
        FunctionMetrics(
            file=path,
            name=node.name,
            lineno=node.lineno,
            end_lineno=end_lineno,
            complexity=cv.complexity,
            length=length,
            nesting=nv.max_depth,
            params=_count_params(node),
            has_docstring=has_doc,
            is_public=public,
        )
    )

    if cv.complexity > limits.max_complexity:
        fm.issues.append(
            Issue(
                path,
                node.lineno,
                "complexity",
                "error" if cv.complexity > limits.max_complexity * 2 else "warn",
                "high-complexity",
                f"Function '{node.name}' has cyclomatic complexity {cv.complexity} (limit {limits.max_complexity})",
            )
        )
    if length > limits.max_function_lines:
        fm.issues.append(
            Issue(
                path,
                node.lineno,
                "structure",
                "warn",
                "long-function",
                f"Function '{node.name}' is {length} lines long (limit {limits.max_function_lines})",
            )
        )
    if nv.max_depth > limits.max_nesting:
        fm.issues.append(
            Issue(
                path,
                node.lineno,
                "structure",
                "warn",
                "deep-nesting",
                f"Function '{node.name}' nests {nv.max_depth} levels deep (limit {limits.max_nesting})",
            )
        )
    if public and not has_doc and length > limits.docstring_min_lines:
        fm.issues.append(
            Issue(path, node.lineno, "documentation", "info", "missing-docstring", f"Public function '{node.name}' has no docstring")
        )
    if _has_mutable_default(node):
        fm.issues.append(
            Issue(path, node.lineno, "style", "warn", "mutable-default-arg", f"Function '{node.name}' uses a mutable default argument")
        )


def _process_other_node(node, path, only_lines, fm):
    if isinstance(node, ast.ExceptHandler) and node.type is None:
        if _in_scope(node, only_lines):
            fm.issues.append(Issue(path, node.lineno, "style", "warn", "bare-except", "Bare 'except:' clause"))
    elif isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
        if _in_scope(node, only_lines):
            fm.issues.append(
                Issue(path, node.lineno, "style", "warn", "star-import", f"Wildcard import from '{node.module}'")
            )


def analyze(path, source, limits, only_lines=None):
    """Analyze a single Python file.

    `only_lines`, when provided, restricts function selection to functions
    that overlap those (1-based, new-file) line numbers, and restricts
    line-level checks to those lines -- this is how diff mode scores only
    the code that actually changed instead of re-grading the whole file.
    """
    lines = source.splitlines(keepends=True)
    total_lines = len(lines)
    loc = sum(1 for l in lines if l.strip())

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        fm = FileMetrics(path=path, language="python", total_lines=total_lines, loc=loc)
        fm.parse_error = f"SyntaxError: {e.msg} (line {e.lineno})"
        fm.issues.append(
            Issue(path, e.lineno or 1, "style", "error", "syntax-error", f"File does not parse: {e.msg}")
        )
        return fm

    fm = FileMetrics(
        path=path,
        language="python",
        total_lines=total_lines,
        loc=loc,
        has_module_docstring=bool(ast.get_docstring(tree)),
    )

    for node in ast.walk(tree):
        if isinstance(node, _FUNC_TYPES):
            if _in_scope(node, only_lines):
                _process_function(node, path, limits, fm)
        else:
            _process_other_node(node, path, only_lines, fm)

    if total_lines > limits.max_file_lines and only_lines is None:
        fm.issues.append(
            Issue(path, 1, "structure", "info", "long-file", f"File is {total_lines} lines long (limit {limits.max_file_lines})")
        )

    line_issues, comment_lines = _line_checks(path, lines, limits.max_line_length, only_lines)
    fm.issues.extend(line_issues)
    fm.comment_lines = comment_lines
    return fm

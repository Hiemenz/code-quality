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

_SECRET_NAME_RE = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key)", re.IGNORECASE)
_SECRET_PLACEHOLDER_RE = re.compile(
    r"^(|changeme|xxx+|todo|<.*>|\.\.\.|example|test|dummy|fake|placeholder)$", re.IGNORECASE
)

_SHELL_CALLS = {"subprocess.run", "subprocess.call", "subprocess.Popen", "subprocess.check_call",
                "subprocess.check_output"}

_SNAKE_CASE_RE = re.compile(r"^(__[a-z][a-z0-9_]*__|_{0,2}[a-z][a-z0-9_]*)$")
_PASCAL_CASE_RE = re.compile(r"^_?[A-Z][a-zA-Z0-9]*$")

# Framework-mandated method names that don't follow snake_case by convention
# (ast.NodeVisitor's visit_<NodeType> dispatch, unittest's camelCase hooks) --
# flagging these would be noise, not signal.
_NAMING_EXEMPT = {"setUp", "tearDown", "setUpClass", "tearDownClass", "setUpModule", "tearDownModule"}

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


class _AssignCollector(ast.NodeVisitor):
    """Simple-assignment targets (`x = ...`, `x: T = ...`) inside a single
    function body. Stops at nested function/class boundaries, same as
    `_ComplexityVisitor` -- those are collected separately when that nested
    def is visited in its own right.
    """

    def __init__(self):
        self.assigns = []  # list[(name, node)]

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop
    visit_ClassDef = _stop

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            self.assigns.append((node.targets[0].id, node))
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if isinstance(node.target, ast.Name) and node.value is not None:
            self.assigns.append((node.target.id, node))
        self.generic_visit(node)


def _find_unused_variables(fn_node, path, only_lines):
    """Local variables assigned but never read anywhere in the function
    (including inside nested closures, which count as a use of the outer
    name). Loop variables and tuple-unpacking targets are deliberately
    excluded -- both are common patterns for "don't care" bindings and
    flagging them would be mostly false positives.
    """
    used = {n.id for n in ast.walk(fn_node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    collector = _AssignCollector()
    collector.generic_visit(fn_node)

    issues = []
    seen = set()
    for name, node in collector.assigns:
        if name.startswith("_") or name in used or name in seen:
            continue
        seen.add(name)
        if not _in_scope(node, only_lines):
            continue
        issues.append(
            Issue(path, node.lineno, "style", "info", "unused-variable",
                  f"Local variable '{name}' is assigned but never used")
        )
    return issues


def _dunder_all_names(node):
    """String elements of a top-level `__all__ = [...]` assignment, or [] if
    `node` isn't one -- those names count as "used" even with no direct
    reference, since they're the module's declared public re-exports.
    """
    is_all_target = any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets)
    if not is_all_target or not isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
        return []
    return [elt.value for elt in node.value.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]


def _collect_used_names(tree):
    """Every identifier read anywhere in the module, plus names re-exported
    via `__all__` -- used to decide whether an import is ever referenced.
    """
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            used.update(_dunder_all_names(node))
    return used


def _import_aliases(node):
    """Bound names an Import/ImportFrom node introduces, skipping `__future__`
    imports and wildcard `*` (handled separately as a style issue).
    """
    if isinstance(node, ast.ImportFrom) and node.module == "__future__":
        return []
    return [a for a in node.names if a.name != "*"]


def _unused_import_issues(tree, path, only_lines):
    used = _collect_used_names(tree)
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or not _in_scope(node, only_lines):
            continue
        for alias in _import_aliases(node):
            bound = alias.asname or alias.name.split(".")[0]
            if bound == "_" or bound in used:
                continue
            issues.append(
                Issue(path, node.lineno, "style", "info", "unused-import",
                      f"Imported name '{bound}' is never used")
            )
    return issues


def _call_full_name(node):
    """Best-effort dotted name for a Call's callee, e.g. 'os.system' or 'eval'."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


# Dangerous calls whose mere presence is the issue -- symbol, severity, message.
_DANGEROUS_CALLS = {
    "eval": ("dangerous-eval", "error", "Use of 'eval()' can execute arbitrary code"),
    "exec": ("dangerous-eval", "error", "Use of 'exec()' can execute arbitrary code"),
    "os.system": ("shell-exec", "warn", "os.system() runs a shell command; prefer subprocess with a list of args"),
    "pickle.load": ("unsafe-deserialization", "warn",
                     "pickle.load() can execute arbitrary code from untrusted input"),
    "pickle.loads": ("unsafe-deserialization", "warn",
                      "pickle.loads() can execute arbitrary code from untrusted input"),
}


def _shell_true_issue(node, path, name):
    for kw in node.keywords:
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return Issue(path, node.lineno, "security", "error", "shell-true", f"{name}() called with shell=True")
    return None


def _unsafe_yaml_issue(node, path):
    loader_kw = next((kw for kw in node.keywords if kw.arg == "Loader"), None)
    safe = loader_kw is not None and isinstance(loader_kw.value, ast.Attribute) and loader_kw.value.attr == "SafeLoader"
    if safe:
        return None
    return Issue(path, node.lineno, "security", "warn", "unsafe-yaml-load",
                 "yaml.load() without Loader=yaml.SafeLoader can execute arbitrary code")


def _security_call_issue(node, path):
    name = _call_full_name(node)
    if name is None:
        return None
    if name in _DANGEROUS_CALLS:
        symbol, severity, message = _DANGEROUS_CALLS[name]
        return Issue(path, node.lineno, "security", severity, symbol, message)
    if name in _SHELL_CALLS:
        return _shell_true_issue(node, path, name)
    if name == "yaml.load":
        return _unsafe_yaml_issue(node, path)
    return None


def _hardcoded_secret_issue(node, path):
    if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
        return None
    name = node.targets[0].id
    if not _SECRET_NAME_RE.search(name):
        return None
    if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
        return None
    if _SECRET_PLACEHOLDER_RE.match(node.value.value.strip()):
        return None
    return Issue(path, node.lineno, "security", "error", "hardcoded-secret",
                 f"'{name}' looks like a hardcoded secret")


def _security_issues(tree, path, only_lines):
    issues = []
    for node in ast.walk(tree):
        if not _in_scope(node, only_lines):
            continue
        if isinstance(node, ast.Call):
            issue = _security_call_issue(node, path)
        elif isinstance(node, ast.Assign):
            issue = _hardcoded_secret_issue(node, path)
        else:
            issue = None
        if issue is not None:
            issues.append(issue)
    return issues


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
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _build_function_metrics(node, path):
    end_lineno = getattr(node, "end_lineno", node.lineno)
    cv = _ComplexityVisitor()
    cv.generic_visit(node)
    nv = _NestingVisitor()
    nv.generic_visit(node)

    return FunctionMetrics(
        file=path,
        name=node.name,
        lineno=node.lineno,
        end_lineno=end_lineno,
        complexity=cv.complexity,
        length=end_lineno - node.lineno + 1,
        nesting=nv.max_depth,
        params=_count_params(node),
        has_docstring=ast.get_docstring(node) is not None,
        is_public=is_public_name(node.name),
    )


def _complexity_structure_issues(fn, path, limits):
    issues = []
    if fn.complexity > limits.max_complexity:
        severity = "error" if fn.complexity > limits.max_complexity * 2 else "warn"
        issues.append(
            Issue(path, fn.lineno, "complexity", severity, "high-complexity",
                  f"Function '{fn.name}' has cyclomatic complexity {fn.complexity} (limit {limits.max_complexity})")
        )
    if fn.length > limits.max_function_lines:
        issues.append(
            Issue(path, fn.lineno, "structure", "warn", "long-function",
                  f"Function '{fn.name}' is {fn.length} lines long (limit {limits.max_function_lines})")
        )
    if fn.nesting > limits.max_nesting:
        issues.append(
            Issue(path, fn.lineno, "structure", "warn", "deep-nesting",
                  f"Function '{fn.name}' nests {fn.nesting} levels deep (limit {limits.max_nesting})")
        )
    return issues


def _is_bad_function_name(name):
    return not _SNAKE_CASE_RE.match(name) and not name.startswith("visit_") and name not in _NAMING_EXEMPT


def _style_doc_issues(fn, node, path, limits):
    issues = []
    if fn.is_public and not fn.has_docstring and fn.length > limits.docstring_min_lines:
        msg = f"Public function '{fn.name}' has no docstring"
        issues.append(Issue(path, fn.lineno, "documentation", "info", "missing-docstring", msg))
    if _has_mutable_default(node):
        msg = f"Function '{fn.name}' uses a mutable default argument"
        issues.append(Issue(path, fn.lineno, "style", "warn", "mutable-default-arg", msg))
    if _is_bad_function_name(fn.name):
        msg = f"Function '{fn.name}' should be snake_case"
        issues.append(Issue(path, fn.lineno, "style", "info", "bad-function-name", msg))
    return issues


def _check_function_issues(fn, node, path, limits):
    return _complexity_structure_issues(fn, path, limits) + _style_doc_issues(fn, node, path, limits)


def _process_function(node, path, limits, fm, only_lines):
    fn = _build_function_metrics(node, path)
    fm.functions.append(fn)
    fm.issues.extend(_check_function_issues(fn, node, path, limits))
    fm.issues.extend(_find_unused_variables(node, path, only_lines))


def _check_bare_except(node, path, only_lines):
    if isinstance(node, ast.ExceptHandler) and node.type is None and _in_scope(node, only_lines):
        return Issue(path, node.lineno, "style", "warn", "bare-except", "Bare 'except:' clause")
    return None


def _check_star_import(node, path, only_lines):
    if not isinstance(node, ast.ImportFrom) or not _in_scope(node, only_lines):
        return None
    if not any(a.name == "*" for a in node.names):
        return None
    return Issue(path, node.lineno, "style", "warn", "star-import", f"Wildcard import from '{node.module}'")


def _check_class_name(node, path, only_lines):
    if not isinstance(node, ast.ClassDef) or not _in_scope(node, only_lines):
        return None
    if _PASCAL_CASE_RE.match(node.name):
        return None
    return Issue(path, node.lineno, "style", "info", "bad-class-name", f"Class '{node.name}' should be PascalCase")


_OTHER_NODE_CHECKS = (_check_bare_except, _check_star_import, _check_class_name)


def _process_other_node(node, path, only_lines, fm):
    for check in _OTHER_NODE_CHECKS:
        issue = check(node, path, only_lines)
        if issue is not None:
            fm.issues.append(issue)


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
                _process_function(node, path, limits, fm, only_lines)
        else:
            _process_other_node(node, path, only_lines, fm)

    fm.issues.extend(_unused_import_issues(tree, path, only_lines))
    fm.issues.extend(_security_issues(tree, path, only_lines))

    if total_lines > limits.max_file_lines and only_lines is None:
        msg = f"File is {total_lines} lines long (limit {limits.max_file_lines})"
        fm.issues.append(Issue(path, 1, "structure", "info", "long-file", msg))

    line_issues, comment_lines = _line_checks(path, lines, limits.max_line_length, only_lines)
    fm.issues.extend(line_issues)
    fm.comment_lines = comment_lines
    return fm

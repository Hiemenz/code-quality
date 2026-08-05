"""AST-based analyzer for Python source files.

This is the deep-analysis path: real cyclomatic complexity, nesting depth,
function length, parameter counts and docstring coverage all come from the
parsed syntax tree rather than regex guessing. Line-based checks (long
lines, TODOs, trailing whitespace, ...) are layered on top since comments
and formatting are not visible in the AST.
"""

import ast
import importlib.util
import os
import re

from codequality.analyzers.async_await import unawaited_coroutine_issues
from codequality.analyzers.base import FileMetrics, FunctionMetrics, Issue, is_public_name
from codequality.analyzers.db_query_in_loop import query_in_loop_issues
from codequality.analyzers.deprecated_api import deprecated_api_issues
from codequality.analyzers.naive_datetime import naive_datetime_issues
from codequality.analyzers.placeholder_code import placeholder_comment_issues, stub_implementation_issues
from codequality.analyzers.redos import redos_issues
from codequality.analyzers.python_correctness_extra import float_equality_issues, mutable_global_issues
from codequality.analyzers.python_docstring_drift import docstring_drift_issues
from codequality.analyzers.python_idioms import (
    boolean_trap_issues,
    comparison_idiom_issues,
    f_string_no_placeholder_issues,
    long_lambda_issues,
    magic_number_issues,
    mutable_class_attribute_issues,
    nested_comprehension_issues,
    redundant_else_issues,
    shadowed_builtin_issues,
)
from codequality.analyzers.python_loop_perf import string_concat_in_loop_issues
from codequality.analyzers.python_import_order import import_order_issues
from codequality.analyzers.python_token_checks import implicit_string_concat_issues
from codequality.analyzers.python_security import assert_validation_issues, security_issues
from codequality.analyzers.python_test_quality import (
    assertion_free_test_issues,
    mock_only_test_issues,
    tautological_test_issues,
)
from codequality.analyzers.python_unreachable import unreachable_code_issues
from codequality.analyzers.resource_lifecycle import resource_lifecycle_issues
from codequality.analyzers.stdlib_attrs import stdlib_attribute_issues
from codequality.property_scaffold import is_test_file

TODO_RE = re.compile(r"#\s*(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)

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


class _ReturnCounter(ast.NodeVisitor):
    """Count return statements in a function body, not descending into nested
    functions/classes (they are scored on their own).
    """

    def __init__(self):
        self.count = 0

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_ClassDef = _stop

    def visit_Return(self, node):
        self.count += 1
        self.generic_visit(node)


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


class _CognitiveVisitor(ast.NodeVisitor):
    """Sonar-style *cognitive* complexity, scoped to a single function body.

    McCabe (above) counts branch points, so a flat 10-arm `elif` chain and
    five levels of nested `if`s score the same -- but they are nothing
    alike to read. Cognitive complexity weights by nesting: each
    `if`/`for`/`while`/`except`/ternary costs 1 *plus the depth it sits
    at*, so deeply nested logic scores much higher than the same number of
    branches laid out flat. This is a simplified, documented subset of
    Sonar's published spec:

    - `if`/`for`/`while`/`except` handler/ternary (`IfExp`): +1 + current
      nesting depth; their bodies are visited one level deeper.
    - `elif` and `else`: flat +1 each, no nesting penalty, exactly per
      Sonar's spec -- an `elif` chain must cost linearly, not
      quadratically, or a 7-arm dispatch chain scores like a disaster.
      (`elif` is an `If` as the sole statement of `orelse` in the AST;
      a hand-written `else: if ...:` on separate lines is
      indistinguishable and gets the same flat price -- the reader reads
      them nearly identically anyway.)
    - each `and`/`or` chain (`BoolOp` node): +1, nesting-independent.
    - nested `def`/`class`/`lambda` stop the walk -- they're scored on
      their own, same convention as `_ComplexityVisitor`.

    Recursion (+1 in Sonar's spec) is skipped: detecting it needs name
    resolution this tool deliberately doesn't do.
    """

    def __init__(self):
        self.cognitive = 0
        self._depth = 0

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop
    visit_ClassDef = _stop

    def _nested(self, node):
        self.cognitive += 1 + self._depth
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    visit_For = _nested
    visit_AsyncFor = _nested
    visit_While = _nested
    visit_ExceptHandler = _nested
    visit_IfExp = _nested

    def visit_If(self, node):
        self.cognitive += 1 + self._depth
        self._if_branches(node)

    def _if_branches(self, node):
        self.visit(node.test)  # BoolOps in the condition still count
        self._depth += 1
        for stmt in node.body:
            self.visit(stmt)
        self._depth -= 1
        orelse = node.orelse
        if len(orelse) == 1 and isinstance(orelse[0], ast.If):
            self.cognitive += 1  # elif: flat, no nesting penalty
            self._if_branches(orelse[0])
        elif orelse:
            self.cognitive += 1  # else: flat, no nesting penalty
            self._depth += 1
            for stmt in orelse:
                self.visit(stmt)
            self._depth -= 1

    def visit_BoolOp(self, node):
        self.cognitive += 1
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


_import_resolution_cache = {}


def _top_level_resolves(name):
    """Whether the top-level package `name` can be located in the
    environment codequality itself is running in. Uses `find_spec`, which
    locates but does not execute the module -- safe to call even on a
    name that turns out not to exist.
    """
    if name not in _import_resolution_cache:
        try:
            _import_resolution_cache[name] = importlib.util.find_spec(name) is not None
        except Exception:
            # An unusual/broken finder raised instead of returning None --
            # don't crash the scan, and don't penalize the code for it.
            _import_resolution_cache[name] = True
    return _import_resolution_cache[name]


def _unresolved_import_issues(tree, path, only_lines):
    """Flag imports that don't resolve to any installed module -- catches
    both typos and a well-documented LLM failure mode (inventing a
    plausible-sounding package that doesn't exist). Opt-in (--check-imports)
    because the result depends on what's installed in *this* environment,
    not on the source alone -- see README.
    """
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            candidates = [(a.name.split(".")[0], a.name) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0 or node.module is None:
                continue  # relative import; nothing top-level to resolve
            candidates = [(node.module.split(".")[0], node.module)]
        else:
            continue
        if not _in_scope(node, only_lines):
            continue
        for top_level, full_name in candidates:
            if not _top_level_resolves(top_level):
                issues.append(
                    Issue(path, node.lineno, "correctness", "error", "unresolved-import",
                          f"'{full_name}' does not resolve to an installed module in this environment")
                )
    return issues



_SCRIPT_DIR_NAMES = {"examples", "example", "scripts", "script"}


def _looks_like_script_path(path):
    """A file living under an `examples/`/`scripts/` directory -- the
    convention used elsewhere in this tool (see `is_test_file`) for "this
    isn't ordinary library code" by path alone, without needing to parse
    the file's content.
    """
    dirs = os.path.normpath(path).split(os.sep)[:-1]
    return any(d.lower() in _SCRIPT_DIR_NAMES for d in dirs)


def _is_dunder_name_main_pair(name_node, const_node):
    return (
        isinstance(name_node, ast.Name)
        and name_node.id == "__name__"
        and isinstance(const_node, ast.Constant)
        and const_node.value == "__main__"
    )


def _is_dunder_main_test(test):
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
        return False
    left, right = test.left, test.comparators[0]
    return _is_dunder_name_main_pair(left, right) or _is_dunder_name_main_pair(right, left)


def _has_main_guard(tree):
    """True if the module has a module-level `if __name__ == "__main__":`
    guard anywhere in its top-level body -- a strong, simple signal that
    this file is meant to be run directly (a script/CLI entry point) and
    not imported as a library module. Deliberately scoped to "does the
    file contain this guard at all" rather than tracing which functions
    the guarded block calls into: simpler, and it exempts a script's
    helper functions too (they exist only to be called from that guard),
    without needing a call-graph analysis.
    """
    return any(isinstance(node, ast.If) and _is_dunder_main_test(node.test) for node in tree.body)


def _print_call_issues(tree, path, only_lines):
    """Flag `print(...)` calls left in library/module code -- a common
    smell, especially in LLM-generated code that defaults to `print()`
    for debugging/status output instead of proper logging. A real CLI
    tool's own user-facing output is exempted via `_has_main_guard`
    below, so this only fires on code that looks like it's meant to be
    imported, not run directly.

    Python-only, like several other checks in this tool (see the
    "Python-only checks" list in the README): there's no single
    cross-language equivalent worth checking generically here, since
    what counts as a legitimate top-level "print" idiom vs. a debug
    leftover varies a lot by language and logging convention.
    """
    if is_test_file(path) or _looks_like_script_path(path) or _has_main_guard(tree):
        return []
    issues = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
            and _in_scope(node, only_lines)
        ):
            issues.append(
                Issue(path, node.lineno, "style", "info", "print-in-library-code",
                      "print() call in library code -- consider using logging instead")
            )
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
    cogv = _CognitiveVisitor()
    cogv.generic_visit(node)

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
        cognitive=cogv.cognitive,
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
    max_cognitive = getattr(limits, "max_cognitive", 15)
    if fn.cognitive > max_cognitive:
        severity = "error" if fn.cognitive > max_cognitive * 2 else "warn"
        issues.append(
            Issue(path, fn.lineno, "complexity", severity, "high-cognitive-complexity",
                  f"Function '{fn.name}' has cognitive complexity {fn.cognitive} (limit {max_cognitive}) -- "
                  f"nesting-weighted, so flattening the deepest branches helps most")
        )
    max_params = getattr(limits, "max_params", 6)
    if fn.params > max_params:
        issues.append(
            Issue(path, fn.lineno, "structure", "info", "too-many-params",
                  f"Function '{fn.name}' takes {fn.params} parameters (limit {max_params}) -- "
                  f"consider grouping related ones into an object")
        )
    return issues


def _is_bad_function_name(name):
    return not _SNAKE_CASE_RE.match(name) and not name.startswith("visit_") and name not in _NAMING_EXEMPT


def _has_any_annotation(node):
    """True if the function has at least one parameter annotation or a return annotation."""
    if node.returns is not None:
        return True
    all_args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
    return any(
        arg.arg not in ("self", "cls") and arg.annotation is not None
        for arg in all_args
    )


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
    if (fn.is_public and not is_test_file(path)
            and fn.length > limits.docstring_min_lines
            and not _has_any_annotation(node)):
        issues.append(Issue(
            path, fn.lineno, "documentation", "info", "missing-type-annotations",
            f"Public function '{fn.name}' has no type annotations"
        ))
    return issues


def _check_function_issues(fn, node, path, limits):
    return _complexity_structure_issues(fn, path, limits) + _style_doc_issues(fn, node, path, limits)


def _process_function(node, path, limits, fm, only_lines):
    fn = _build_function_metrics(node, path)
    fm.functions.append(fn)
    fm.issues.extend(_check_function_issues(fn, node, path, limits))
    fm.issues.extend(_find_unused_variables(node, path, only_lines))
    fm.issues.extend(assertion_free_test_issues(node, path))
    fm.issues.extend(tautological_test_issues(node, path))
    fm.issues.extend(mock_only_test_issues(node, path))
    fm.issues.extend(docstring_drift_issues(node, path))
    fm.issues.extend(_too_many_returns_issues(node, path, limits))


def _check_bare_except(node, path, only_lines):
    if isinstance(node, ast.ExceptHandler) and node.type is None and _in_scope(node, only_lines):
        return Issue(path, node.lineno, "style", "warn", "bare-except", "Bare 'except:' clause")
    return None


_BROAD_EXCEPTION_NAMES = {"Exception", "BaseException"}


def _is_broad_exception_type(type_node):
    if isinstance(type_node, ast.Name):
        return type_node.id in _BROAD_EXCEPTION_NAMES
    if isinstance(type_node, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in _BROAD_EXCEPTION_NAMES for e in type_node.elts)
    return False


def _handler_swallows_silently(handler):
    """True if the handler's body does nothing but `pass` (optionally with
    a leading string-literal "comment") -- no re-raise, no logging, no
    return signal. A real cleanup/logging call in the body is treated as
    "doing something" even if it's not a great fix, to keep false
    positives low.
    """
    body = [s for s in handler.body if not isinstance(s, ast.Pass)]
    if not body:
        return True
    is_bare_string = (
        isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    )
    return len(body) == 1 and is_bare_string


def _check_broad_except_swallow(node, path, only_lines):
    if not isinstance(node, ast.ExceptHandler) or node.type is None or not _in_scope(node, only_lines):
        return None
    if not _is_broad_exception_type(node.type) or not _handler_swallows_silently(node):
        return None
    return Issue(path, node.lineno, "style", "warn", "broad-except-swallow",
                 "Catches Exception/BaseException and silently discards it -- failures here vanish with no trace")


class _RaiseCollector(ast.NodeVisitor):
    """Every `raise` statement reachable from a node, stopping at nested
    function/class boundaries -- same convention as `_AssignCollector`.
    """

    def __init__(self):
        self.raises = []

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop
    visit_ClassDef = _stop

    def visit_Raise(self, node):
        self.raises.append(node)
        self.generic_visit(node)


def _check_lost_exception_context(node, path, only_lines):
    """An `except ... as e:` handler that raises a brand-new exception
    without either explicit chaining (`raise ... from e`) or referencing
    `e` anywhere in the new exception's construction loses the original
    cause -- the traceback/message that would have said *why* it failed is
    gone. A bare re-raise (`raise`) or re-raising the same bound name
    (`raise e`) is not this pattern; those are deliberate and keep the
    original exception intact.
    """
    if not isinstance(node, ast.ExceptHandler) or node.name is None or not _in_scope(node, only_lines):
        return None
    collector = _RaiseCollector()
    collector.generic_visit(node)
    for raise_node in collector.raises:
        if raise_node.exc is None or raise_node.cause is not None:
            continue
        if isinstance(raise_node.exc, ast.Name) and raise_node.exc.id == node.name:
            continue
        names_used = {n.id for n in ast.walk(raise_node.exc) if isinstance(n, ast.Name)}
        if node.name in names_used:
            continue
        return Issue(
            path, raise_node.lineno, "style", "warn", "lost-exception-context",
            f"Raises a new exception here without chaining from '{node.name}' "
            f"(add 'from {node.name}' or reference it in the new exception)"
        )
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


_OTHER_NODE_CHECKS = (
    _check_bare_except, _check_broad_except_swallow, _check_lost_exception_context, _check_star_import,
    _check_class_name,
)


def _process_other_node(node, path, only_lines, fm):
    for check in _OTHER_NODE_CHECKS:
        issue = check(node, path, only_lines)
        if issue is not None:
            fm.issues.append(issue)


def _syntax_error_result(path, error, total_lines, loc):
    fm = FileMetrics(path=path, language="python", total_lines=total_lines, loc=loc)
    fm.parse_error = f"SyntaxError: {error.msg} (line {error.lineno})"
    fm.issues.append(
        Issue(path, error.lineno or 1, "style", "error", "syntax-error", f"File does not parse: {error.msg}")
    )
    return fm


def _walk_tree(tree, path, limits, only_lines, fm):
    for node in ast.walk(tree):
        if isinstance(node, _FUNC_TYPES):
            if _in_scope(node, only_lines):
                _process_function(node, path, limits, fm, only_lines)
        else:
            _process_other_node(node, path, only_lines, fm)


def _too_many_returns_issues(node, path, limits):
    max_returns = getattr(limits, "max_return_statements", 5)
    rc = _ReturnCounter()
    rc.generic_visit(node)
    if rc.count > max_returns:
        return [Issue(
            path, node.lineno, "structure", "info", "too-many-return-statements",
            f"Function '{node.name}' has {rc.count} return statements (limit {max_returns}) "
            f"-- consider extracting branches into helper functions"
        )]
    return []


def _god_class_issues(tree, path, limits, only_lines):
    max_methods = getattr(limits, "max_class_methods", 20)
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not _in_scope(node, only_lines):
            continue
        methods = [n for n in node.body if isinstance(n, _FUNC_TYPES)]
        if len(methods) > max_methods:
            issues.append(Issue(
                path, node.lineno, "structure", "warn", "god-class",
                f"Class '{node.name}' has {len(methods)} methods (limit {max_methods}) "
                f"-- consider splitting into smaller, more focused classes"
            ))
    return issues


def _module_level_issues(tree, path, limits, only_lines, check_imports):
    issues = (
        _unused_import_issues(tree, path, only_lines)
        + security_issues(tree, path, only_lines)
        + redos_issues(tree, path, only_lines)
        + assert_validation_issues(tree, path, only_lines)
        + unreachable_code_issues(tree, path, only_lines)
        + resource_lifecycle_issues(tree, path, only_lines)
        + query_in_loop_issues(tree, path, only_lines)
        + unawaited_coroutine_issues(tree, path, only_lines)
        + _print_call_issues(tree, path, only_lines)
        + stub_implementation_issues(tree, path, only_lines)
        + deprecated_api_issues(tree, path, only_lines)
        + naive_datetime_issues(tree, path, only_lines)
        + float_equality_issues(tree, path, only_lines)
        + mutable_global_issues(tree, path, only_lines)
        + comparison_idiom_issues(tree, path, only_lines)
        + shadowed_builtin_issues(tree, path, only_lines)
        + mutable_class_attribute_issues(tree, path, only_lines)
        + f_string_no_placeholder_issues(tree, path, only_lines)
        + redundant_else_issues(tree, path, only_lines)
        + string_concat_in_loop_issues(tree, path, only_lines)
        + _god_class_issues(tree, path, limits, only_lines)
        + boolean_trap_issues(tree, path, only_lines)
        + magic_number_issues(tree, path, only_lines)
        + nested_comprehension_issues(tree, path, only_lines)
        + long_lambda_issues(tree, path, only_lines)
        + import_order_issues(tree, path, only_lines)
    )
    if check_imports:
        issues += _unresolved_import_issues(tree, path, only_lines)
        issues += stdlib_attribute_issues(tree, path, only_lines)
    return issues


def analyze(path, source, limits, only_lines=None, check_imports=False):
    """Analyze a single Python file.

    `only_lines`, when provided, restricts function selection to functions
    that overlap those (1-based, new-file) line numbers, and restricts
    line-level checks to those lines -- this is how diff mode scores only
    the code that actually changed instead of re-grading the whole file.

    `check_imports`, when True, additionally flags imports that don't
    resolve in the current environment (see `_unresolved_import_issues`).
    """
    lines = source.splitlines(keepends=True)
    total_lines = len(lines)
    loc = sum(1 for l in lines if l.strip())

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as e:
        return _syntax_error_result(path, e, total_lines, loc)

    fm = FileMetrics(
        path=path,
        language="python",
        total_lines=total_lines,
        loc=loc,
        has_module_docstring=bool(ast.get_docstring(tree)),
    )

    _walk_tree(tree, path, limits, only_lines, fm)
    fm.issues.extend(_module_level_issues(tree, path, limits, only_lines, check_imports))

    if total_lines > limits.max_file_lines and only_lines is None:
        msg = f"File is {total_lines} lines long (limit {limits.max_file_lines})"
        fm.issues.append(Issue(path, 1, "structure", "info", "long-file", msg))

    line_issues, comment_lines = _line_checks(path, lines, limits.max_line_length, only_lines)
    fm.issues.extend(line_issues)
    fm.issues.extend(placeholder_comment_issues(path, lines, only_lines))
    fm.issues.extend(implicit_string_concat_issues(source, path, only_lines))
    fm.comment_lines = comment_lines
    return fm

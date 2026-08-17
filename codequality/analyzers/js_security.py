"""AST-precise security checks for JavaScript/TypeScript, via tree-sitter.

Deliberately does **not** duplicate what `generic_analyzer.py`'s line-level
regex scan already catches for every tree-sitter-backed language,
JS/TS included (`treesitter_analyzer.py::_process_lines` runs it
unconditionally): bare `eval(...)`/`exec(...)` (-> `dangerous-eval`) and
`name = "value"`-shaped hardcoded secrets (-> `hardcoded-secret`) are
already emitted there. Adding AST versions of those two here would just
produce a second, redundant issue on the same line -- the same reasoning
that scoped `taint_flow.py` to SQL only instead of also covering
eval/exec/shell (see that module's docstring). What's actually new here:

- `dangerous-eval` for `new Function(...)` specifically (the regex above
  only matches literal `eval(`/`exec(` text, not the Function
  constructor -- same risk, different spelling, not covered elsewhere).
- `weak-hash`, `shell-true`, `sql-injection-risk` -- none of these have
  any existing JS/TS detection at all.

Reuses the same rule symbols Python's `python_security.py` emits
(`dangerous-eval`, `weak-hash`, `shell-true`, `sql-injection-risk`) --
all already registered in `rules.py` with CWE/OWASP tags -- so
`codequality compliance`/`explain`/scoring pick up JS/TS findings with
no registry changes.
"""

from codequality.analyzers.base import Issue

_SQL_EXEC_METHODS = {"execute", "executemany", "raw", "query"}


def _text(node, source):
    return source[node.start_byte():node.end_byte()]


def _line(node):
    return node.start_position().row + 1


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    start, end = node.start_position().row + 1, node.end_position().row + 1
    return any(start <= ln <= end for ln in only_lines)


def _dotted_name(node, source):
    """Best-effort dotted name for an identifier/member_expression chain,
    e.g. 'crypto.createHash' or 'child_process.exec'.
    """
    kind = node.kind()
    if kind == "identifier":
        return _text(node, source)
    if kind == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if obj is None or prop is None:
            return None
        base = _dotted_name(obj, source)
        return f"{base}.{_text(prop, source)}" if base is not None else None
    return None


def _call_args(call_node):
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return []
    return [args_node.named_child(i) for i in range(args_node.named_child_count())]


def _iter_kind(node, kind):
    if node.kind() == kind:
        yield node
    for i in range(node.named_child_count()):
        yield from _iter_kind(node.named_child(i), kind)


def _new_function_issue(node, path, source):
    ctor = node.child_by_field_name("constructor")
    if ctor is None or _dotted_name(ctor, source) != "Function":
        return None
    return Issue(
        path, _line(node), "security", "error", "dangerous-eval",
        "new Function(...) compiles a string into executable code -- same risk as eval()"
    )


_WEAK_HASH_ALGOS = {"md5", "sha1"}


def _weak_hash_issue(node, path, source):
    """`crypto.createHash('md5' | 'sha1')`."""
    func = node.child_by_field_name("function")
    if func is None or _dotted_name(func, source) != "crypto.createHash":
        return None
    args = _call_args(node)
    if not args or args[0].kind() != "string":
        return None
    algo = _text(args[0], source).strip("'\"").lower()
    if algo not in _WEAK_HASH_ALGOS:
        return None
    return Issue(
        path, _line(node), "security", "warn", "weak-hash",
        f"{algo.upper()} is cryptographically broken -- use crypto.createHash('sha256') or better"
    )


_SHELL_ALWAYS_CALLS = {"child_process.exec", "child_process.execSync", "exec", "execSync"}
_SHELL_OPTIONAL_CALLS = {"child_process.spawn", "child_process.execFile", "spawn", "execFile"}


def _object_has_shell_true(node, source):
    if node is None or node.kind() != "object":
        return False
    for pair in (node.named_child(i) for i in range(node.named_child_count())):
        if pair.kind() != "pair":
            continue
        key = pair.child_by_field_name("key")
        value = pair.child_by_field_name("value")
        if key is not None and value is not None and _text(key, source) == "shell" and value.kind() == "true":
            return True
    return False


def _shell_true_issue(node, path, source):
    """Node's `child_process.exec`/`execSync` always run through a shell
    (no flag needed, unlike Python's subprocess); `spawn`/`execFile` only
    do with an explicit `{shell: true}` options object.
    """
    func = node.child_by_field_name("function")
    if func is None:
        return None
    name = _dotted_name(func, source)
    if name is None:
        return None
    if name in _SHELL_ALWAYS_CALLS:
        return Issue(
            path, _line(node), "security", "error", "shell-true",
            f"{name}() always runs its command through a shell -- prefer execFile/spawn with an argument list"
        )
    if name in _SHELL_OPTIONAL_CALLS:
        args = _call_args(node)
        if any(_object_has_shell_true(a, source) for a in args):
            return Issue(
                path, _line(node), "security", "error", "shell-true",
                f"{name}() called with {{shell: true}}; prefer an argument list without shell interpretation"
            )
    return None


def _is_dynamic_string_expr(node):
    """True if `node` builds a string dynamically at the call site
    (template literal with an interpolation, or `+` concatenation) --
    mirrors `python_security._is_dynamic_string_expr`. A query string
    combined with parameters passed *separately* (the safe,
    parameterized idiom) is deliberately not flagged here either, same
    as the Python check -- it's the second, separate argument that
    matters, not whether the query text itself has a placeholder.
    """
    kind = node.kind()
    if kind == "template_string":
        return any(node.named_child(i).kind() == "template_substitution" for i in range(node.named_child_count()))
    if kind == "binary_expression":
        op = node.child_by_field_name("operator")
        return op is not None and op.kind() == "+"
    return False


def _sql_injection_issue(node, path, source):
    """`<obj>.execute(...)`/`.query(...)`/`.raw(...)` called with exactly
    one dynamically-built string argument.
    """
    func = node.child_by_field_name("function")
    if func is None or func.kind() != "member_expression":
        return None
    method = func.child_by_field_name("property")
    if method is None or _text(method, source) not in _SQL_EXEC_METHODS:
        return None
    args = _call_args(node)
    if len(args) != 1 or not _is_dynamic_string_expr(args[0]):
        return None
    return Issue(
        path, _line(node), "security", "error", "sql-injection-risk",
        "Query string is built with a template literal/+ concatenation instead of passing "
        "parameters separately -- vulnerable to SQL injection"
    )


_CALL_CHECKS = (_weak_hash_issue, _shell_true_issue, _sql_injection_issue)


def security_issues(root, path, source, only_lines):
    """Every security-category issue findable from a single AST pass over
    `root` (the parsed tree-sitter root node for a JS/TS file).
    """
    issues = []
    for node in _iter_kind(root, "call_expression"):
        if not _in_scope(node, only_lines):
            continue
        for check in _CALL_CHECKS:
            issue = check(node, path, source)
            if issue is not None:
                issues.append(issue)
    for node in _iter_kind(root, "new_expression"):
        if not _in_scope(node, only_lines):
            continue
        issue = _new_function_issue(node, path, source)
        if issue is not None:
            issues.append(issue)
    return issues

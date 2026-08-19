"""AST-precise security checks for Go, via tree-sitter.

Same posture as `js_security.py` (see that module's docstring for why this
doesn't duplicate `generic_analyzer.py`'s line-level regex scan): Go gets
its own module rather than sharing JS's because `selector_expression`'s
field names (`operand`/`field`, not `object`/`property`) and Go's specific
stdlib idioms for each check differ enough that a shared implementation
would be more branching than code reuse.

- `weak-hash`: `crypto/md5`/`crypto/sha1`'s `New()`/`Sum()` functions --
  unlike JS's `crypto.createHash('md5')`, the weak algorithm is which
  *package* is imported/called, not a runtime string argument.
- `shell-true`: `os/exec`'s `Command`/`CommandContext` invoked with a
  shell binary (`sh`, `bash`, `cmd`, `powershell`, ...) as one of its
  string-literal arguments. Go has no `shell=True`-style flag (Python) or
  always-shell function (Node's `exec`) -- the *only* way a Go program
  runs something through a shell is by explicitly naming the shell
  binary, so that's the signal.
- `sql-injection-risk`: `<db/tx>.Query/QueryContext/QueryRow/
  QueryRowContext/Exec/ExecContext(...)` whose first argument (the query
  text) is built with `fmt.Sprintf(...)` or `+` concatenation. Unlike the
  JS/Python versions, this doesn't require *exactly one* argument --
  `db.Query(query, id)` (a literal query string plus separately-passed
  bind parameters, the safe parameterized idiom) is correctly not
  flagged, since only argument 0 is inspected for how it was built.

No Go equivalent of `dangerous-eval` is included: Go has no idiomatic
stdlib way to compile and run a string as code (no `eval`, no bare
`Function`-constructor analogue), so there's nothing for that rule to
detect here.

Reuses the same rule symbols `python_security.py`/`js_security.py` emit
(`weak-hash`, `shell-true`, `sql-injection-risk`) -- already registered in
`rules.py` with CWE/OWASP tags -- so `codequality compliance`/`explain`/
scoring pick up Go findings with no registry changes.
"""

from codequality.analyzers._ts_helpers import iter_kind as _iter_kind
from codequality.analyzers._ts_helpers import node_line as _line
from codequality.analyzers._ts_helpers import node_text as _text
from codequality.analyzers.base import Issue

_SQL_EXEC_METHODS = {"Query", "QueryContext", "QueryRow", "QueryRowContext", "Exec", "ExecContext"}
_WEAK_HASH_PACKAGES = {"md5", "sha1"}
_WEAK_HASH_FUNCS = {"New", "Sum"}
_WEAK_HASH_IMPORT_REQUIRED = {
    "md5": "crypto/md5",
    "sha1": "crypto/sha1",
}
_SHELL_BINARIES = {
    "sh", "bash", "zsh", "ksh", "csh", "dash",
    "/bin/sh", "/bin/bash", "/bin/zsh", "/usr/bin/env",
    "cmd", "cmd.exe", "powershell", "powershell.exe",
}
_STRING_LITERAL_KINDS = {"interpreted_string_literal", "raw_string_literal"}


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    start, end = node.start_point.row + 1, node.end_point.row + 1
    return any(start <= ln <= end for ln in only_lines)


def _string_literal_value(node, source_bytes):
    if node.type not in _STRING_LITERAL_KINDS:
        return None
    return _text(node, source_bytes).strip('`"')


def _selector_parts(node, source_bytes):
    """(operand_text, field_text) for a `selector_expression`, e.g.
    `md5.New` -> ("md5", "New"), or None if `node` isn't one.
    """
    if node.type != "selector_expression":
        return None
    operand = node.child_by_field_name("operand")
    field = node.child_by_field_name("field")
    if operand is None or field is None:
        return None
    return _text(operand, source_bytes), _text(field, source_bytes)


def _call_args(call_node):
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return []
    return [args_node.named_child(i) for i in range(args_node.named_child_count)]


def _imported_packages(root, source_bytes):
    """Set of Go package path strings imported by this file
    (e.g. {'crypto/md5', 'fmt', 'database/sql'}).
    """
    packages = set()
    for imp_decl in _iter_kind(root, "import_declaration"):
        for spec in _iter_kind(imp_decl, "import_spec"):
            path_node = spec.child_by_field_name("path")
            if path_node is not None:
                packages.add(_text(path_node, source_bytes).strip('"`'))
    return packages


def _weak_hash_issue(node, path, source_bytes, imports):
    func = node.child_by_field_name("function")
    if func is None:
        return None
    parts = _selector_parts(func, source_bytes)
    if parts is None:
        return None
    pkg, fn = parts
    if pkg not in _WEAK_HASH_PACKAGES or fn not in _WEAK_HASH_FUNCS:
        return None
    # Only flag if the file actually imports the weak crypto package -- prevents
    # false positives from locally-defined types that share the package name.
    if _WEAK_HASH_IMPORT_REQUIRED.get(pkg) not in imports:
        return None
    return Issue(
        path, _line(node), "security", "warn", "weak-hash",
        f"crypto/{pkg} is cryptographically broken -- use crypto/sha256 or better"
    )


def _shell_true_issue(node, path, source_bytes, imports):
    """`exec.Command`/`exec.CommandContext` is the only way a Go program
    invokes a shell; the signal is any string-literal argument naming a
    shell binary, checked positionally-agnostic since `CommandContext`'s
    first argument is a `context.Context`, not the command name.
    """
    func = node.child_by_field_name("function")
    if func is None:
        return None
    parts = _selector_parts(func, source_bytes)
    if parts is None:
        return None
    pkg, fn = parts
    if pkg != "exec" or fn not in ("Command", "CommandContext"):
        return None
    for arg in _call_args(node):
        value = _string_literal_value(arg, source_bytes)
        if value is not None and value.lower() in _SHELL_BINARIES:
            return Issue(
                path, _line(node), "security", "error", "shell-true",
                f"exec.{fn}(...) invokes '{value}' directly -- any argument built from untrusted "
                f"input is interpreted by that shell, not passed as a literal argument"
            )
    return None


def _is_dynamic_string_expr(node, source_bytes):
    """True if `node` builds a string dynamically (fmt.Sprintf(...) call,
    or `+` concatenation) rather than being a plain literal -- mirrors
    `python_security._is_dynamic_string_expr`/`js_security`'s version.
    """
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        parts = _selector_parts(func, source_bytes) if func is not None else None
        return parts is not None and parts == ("fmt", "Sprintf")
    if node.type == "binary_expression":
        op = node.child_by_field_name("operator")
        return op is not None and _text(op, source_bytes) == "+"
    return False


def _query_arg_index(method):
    """Position of the query-text argument: `Query`/`Exec`(...) take it
    first, but the `...Context` variants take a `context.Context` first
    (`QueryContext(ctx, query, ...)`), shifting it to index 1.
    """
    return 1 if method.endswith("Context") else 0


def _sql_injection_issue(node, path, source_bytes, imports):
    func = node.child_by_field_name("function")
    if func is None:
        return None
    parts = _selector_parts(func, source_bytes)
    if parts is None:
        return None
    _receiver, method = parts
    if method not in _SQL_EXEC_METHODS:
        return None
    # Only flag if the file imports a recognised SQL/DB package -- prevents
    # false positives from unrelated types that happen to define .Query() etc.
    if not any("sql" in p or p == "database/sql" for p in imports):
        return None
    args = _call_args(node)
    idx = _query_arg_index(method)
    if len(args) <= idx or not _is_dynamic_string_expr(args[idx], source_bytes):
        return None
    return Issue(
        path, _line(node), "security", "error", "sql-injection-risk",
        f"Query string passed to .{method}() is built with fmt.Sprintf/+ concatenation instead of "
        f"parameter placeholders -- vulnerable to SQL injection"
    )


_CALL_CHECKS = (_weak_hash_issue, _shell_true_issue, _sql_injection_issue)


def security_issues(root, path, source, only_lines):
    """Every security-category issue findable from a single AST pass over
    `root` (the parsed tree-sitter root node for a Go file).
    """
    source_bytes = source.encode("utf-8", errors="replace")
    imports = _imported_packages(root, source_bytes)
    issues = []
    for node in _iter_kind(root, "call_expression"):
        if not _in_scope(node, only_lines):
            continue
        for check in _CALL_CHECKS:
            issue = check(node, path, source_bytes, imports)
            if issue is not None:
                issues.append(issue)
    return issues

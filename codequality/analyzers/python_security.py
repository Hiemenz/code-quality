"""Security checks split out of `python_analyzer.py`: eval/exec, shell=True,
unsafe pickle/yaml deserialization, hardcoded-secret-looking assignments,
SQL built via string interpolation, and logging a secret-looking variable.
Pulled into its own module purely to keep `python_analyzer.py` from growing
past a size that would itself trip the long-file check -- these checks are
otherwise fully part of the same AST-based analysis pass.
"""

import ast

from codequality.analyzers.base import Issue
from codequality.analyzers.secrets import SECRET_NAME_RE, is_placeholder
from codequality.property_scaffold import is_test_file

_SHELL_CALLS = {"subprocess.run", "subprocess.call", "subprocess.Popen", "subprocess.check_call",
                "subprocess.check_output"}

# Dangerous calls whose mere presence is the issue -- symbol, severity, message.
_DANGEROUS_CALLS = {
    "eval": ("dangerous-eval", "error", "Use of 'eval()' can execute arbitrary code"),
    "exec": ("dangerous-eval", "error", "Use of 'exec()' can execute arbitrary code"),
    "os.system": ("shell-exec", "warn", "os.system() runs a shell command; prefer subprocess with a list of args"),
    "pickle.load": ("unsafe-deserialization", "warn",
                     "pickle.load() can execute arbitrary code from untrusted input"),
    "pickle.loads": ("unsafe-deserialization", "warn",
                      "pickle.loads() can execute arbitrary code from untrusted input"),
    "hashlib.md5": ("weak-hash", "warn",
                    "MD5 is cryptographically broken -- use hashlib.sha256() or better for security purposes"),
    "hashlib.sha1": ("weak-hash", "warn",
                     "SHA-1 is cryptographically broken -- use hashlib.sha256() or better for security purposes"),
    "tempfile.mktemp": ("insecure-tempfile", "warn",
                        "tempfile.mktemp() has a TOCTOU race condition -- use mkstemp() or NamedTemporaryFile() instead"),
}


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


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


_SQL_EXEC_METHODS = {"execute", "executemany", "raw"}


def _last_attr_or_name(node):
    """Last dotted segment of a Call's callee: 'execute' for both
    `cursor.execute(...)` and a bare `execute(...)`.
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_dynamic_string_expr(node):
    """True if `node` looks like a Python string built via f-string
    interpolation, `%`/`+` formatting, or `.format()` -- as opposed to a
    plain string literal, or a query string combined with parameters passed
    *separately* (the standard, safe parameterized-query idiom this check
    deliberately doesn't flag -- see `_sql_injection_issue`).
    """
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return True
    return False


def _sql_injection_issue(node, path):
    """Flags `cursor.execute(...)`/`.executemany(...)`/`QuerySet.raw(...)`
    called with exactly one dynamically-built string argument -- no
    separate params tuple/list/kwarg, meaning whatever was interpolated
    into the query text is not escaped by the driver. The safe,
    parameterized form (`execute("... WHERE x=%s", (value,))`) is
    deliberately not flagged: it's the second, separate argument that
    matters, not whether the query text itself contains a placeholder.
    """
    if _last_attr_or_name(node) not in _SQL_EXEC_METHODS:
        return None
    if len(node.args) != 1 or node.keywords:
        return None
    if not _is_dynamic_string_expr(node.args[0]):
        return None
    return Issue(
        path, node.lineno, "security", "error", "sql-injection-risk",
        "Query string is built with f-string/%/+/.format() interpolation instead of passing "
        "parameters separately -- vulnerable to SQL injection"
    )


_LOG_METHOD_NAMES = {"debug", "info", "warning", "warn", "error", "critical", "exception", "log"}


def _is_log_or_print_call(node):
    if isinstance(node.func, ast.Name) and node.func.id == "print":
        return True
    return isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHOD_NAMES


def _names_referenced_in(expr_node):
    return {n.id for n in ast.walk(expr_node) if isinstance(n, ast.Name)}


def _sensitive_logging_issue(node, path):
    """Flags a `logger.<level>(...)`/`print(...)` call that references a
    variable whose *name* looks like a secret (same `SECRET_NAME_RE` used
    for `hardcoded-secret`) -- logging a credential is a common way secrets
    leak into log aggregators/terminals even when the value itself is never
    hardcoded anywhere.
    """
    if not _is_log_or_print_call(node):
        return None
    names = set()
    for arg in list(node.args) + [kw.value for kw in node.keywords]:
        names |= _names_referenced_in(arg)
    leaked = sorted(n for n in names if SECRET_NAME_RE.search(n))
    if not leaked:
        return None
    return Issue(
        path, node.lineno, "security", "warn", "sensitive-data-logging",
        f"Logs '{leaked[0]}', whose name looks like a secret/credential -- avoid logging it directly"
    )


def _hardcoded_secret_issue(node, path):
    if not (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)):
        return None
    name = node.targets[0].id
    if not SECRET_NAME_RE.search(name):
        return None
    if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
        return None
    if is_placeholder(node.value.value):
        return None
    return Issue(path, node.lineno, "security", "error", "hardcoded-secret",
                 f"'{name}' looks like a hardcoded secret")


def _fstring_log_issue(node, path):
    """Flags logger.<level>(f"...") calls where the f-string has actual
    interpolations.  Passing an already-formatted string to the logger
    defeats lazy evaluation: the string is always built even when the log
    level is disabled, and the structured-logging %-style API is bypassed.
    The fix is ``logger.debug("...", value)`` instead of
    ``logger.debug(f"... {value}")``.
    Skips print() -- only structured logging loggers benefit from lazy eval.
    """
    if not (isinstance(node.func, ast.Attribute) and node.func.attr in _LOG_METHOD_NAMES):
        return None
    for arg in node.args:
        if isinstance(arg, ast.JoinedStr) and any(isinstance(v, ast.FormattedValue) for v in arg.values):
            return Issue(
                path, node.lineno, "style", "warn", "fstring-log-arg",
                "f-string passed directly to logger defeats lazy evaluation -- "
                "use logger.method('...%s', value) instead"
            )
    return None


_CALL_CHECKS = (_security_call_issue, _sql_injection_issue, _sensitive_logging_issue, _fstring_log_issue)


def assert_validation_issues(tree, path, only_lines):
    """Flag assert statements outside test files: assert is silently stripped
    when Python runs with -O (optimised mode), so any validation that relies on
    it vanishes in production. Use an explicit `if not ...: raise` instead.
    """
    if is_test_file(path):
        return []
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert) and _in_scope(node, only_lines):
            issues.append(Issue(
                path, node.lineno, "correctness", "info", "assert-as-validation",
                "assert is disabled by 'python -O' and cannot be relied on for validation "
                "-- use an explicit if/raise instead"
            ))
    return issues


def security_issues(tree, path, only_lines):
    """Every security-category issue in `tree`: dangerous calls, SQL built
    via string interpolation, logging a secret-looking variable, and
    hardcoded-secret-looking assignments.
    """
    issues = []
    for node in ast.walk(tree):
        if not _in_scope(node, only_lines):
            continue
        if isinstance(node, ast.Call):
            for check in _CALL_CHECKS:
                issue = check(node, path)
                if issue is not None:
                    issues.append(issue)
        elif isinstance(node, ast.Assign):
            issue = _hardcoded_secret_issue(node, path)
            if issue is not None:
                issues.append(issue)
    return issues

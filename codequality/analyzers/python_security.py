"""Security checks split out of `python_analyzer.py`: eval/exec, shell=True,
unsafe pickle/yaml deserialization, and hardcoded-secret-looking
assignments. Pulled into its own module purely to keep `python_analyzer.py`
from growing past a size that would itself trip the long-file check --
these checks are otherwise fully part of the same AST-based analysis pass.
"""

import ast
import re

from codequality.analyzers.base import Issue

_SECRET_NAME_RE = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key)", re.IGNORECASE)
_SECRET_PLACEHOLDER_RE = re.compile(
    r"^(|changeme|xxx+|todo|<.*>|\.\.\.|example|test|dummy|fake|placeholder)$", re.IGNORECASE
)

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


def security_issues(tree, path, only_lines):
    """Every security-category issue in `tree`: dangerous calls and
    hardcoded-secret-looking assignments.
    """
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

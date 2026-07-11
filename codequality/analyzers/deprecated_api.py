"""Deprecated/removed stdlib API detection, from a fixed table -- no
version sniffing, no import execution, just names.

Why this matters for LLM-generated code specifically: a model trained on
an older corpus keeps emitting the APIs that were idiomatic when that
corpus was written -- `datetime.utcnow()`, `imp`, `distutils`,
`ssl.wrap_socket(...)`, unittest's `assertEquals` -- long after they were
deprecated or physically removed from Python. Human codebases accumulate
these too, but a fresh LLM-written file using `imp.load_source` is a
strong "trained on stale data, not checked against a current runtime"
signal. Severity encodes the difference:

- **`warn`** -- the API is *removed* in a current CPython release
  (3.12/3.13): the code crashes with ImportError/AttributeError on a
  modern interpreter.
- **`info`** -- deprecated but still working: a maintenance liability and
  a staleness signal, not a crash today.

Detection is deliberately shallow, same "no type resolution" tradeoff as
`async_await.py`:

- module table -- matched against the *top* dotted segment of every
  `import x.y` / `from x.y import z`;
- attribute-call table -- `receiver.attr(...)` where the receiver's last
  dotted segment matches too (`datetime.utcnow()` and
  `datetime.datetime.utcnow()` both present `utcnow` on a `datetime`
  receiver; a same-named attribute on an unrelated object is a
  false-positive this check accepts and documents);
- bare-method table -- unittest's removed aliases (`assertEquals`,
  `failUnless`, ...), matched on the method name alone since those names
  are distinctive enough to carry the match by themselves.

Only *calls* are matched by the last two tables -- a bare reference like
`handler = ssl.wrap_socket` is rarer and skipping it keeps the walk
simple and the false-positive surface small.
"""

import ast

from codequality.analyzers.base import Issue

SYMBOL = "deprecated-api"

# module (top dotted segment) -> (message, severity)
_MODULES = {
    "imp": ("removed in Python 3.12 -- use importlib", "warn"),
    "distutils": ("removed in Python 3.12 -- use setuptools/sysconfig/shutil", "warn"),
    "asyncore": ("removed in Python 3.12 -- use asyncio", "warn"),
    "asynchat": ("removed in Python 3.12 -- use asyncio", "warn"),
    "smtpd": ("removed in Python 3.12 -- use aiosmtpd", "warn"),
    "cgi": ("removed in Python 3.13 -- parse form data with urllib/email instead", "warn"),
    "cgitb": ("removed in Python 3.13", "warn"),
    "telnetlib": ("removed in Python 3.13 -- use telnetlib3 or paramiko", "warn"),
    "pipes": ("removed in Python 3.13 -- use shlex", "warn"),
    "crypt": ("removed in Python 3.13 -- use hashlib or a passlib-style library", "warn"),
    "nntplib": ("removed in Python 3.13", "warn"),
    "nis": ("removed in Python 3.13", "warn"),
    "spwd": ("removed in Python 3.13", "warn"),
    "msilib": ("removed in Python 3.13", "warn"),
    "ossaudiodev": ("removed in Python 3.13", "warn"),
    "audioop": ("removed in Python 3.13", "warn"),
    "sndhdr": ("removed in Python 3.13 -- use a third-party filetype library", "warn"),
    "imghdr": ("removed in Python 3.13 -- use a third-party filetype library", "warn"),
    "sunau": ("removed in Python 3.13", "warn"),
    "aifc": ("removed in Python 3.13", "warn"),
    "uu": ("removed in Python 3.13 -- use base64", "warn"),
    "xdrlib": ("removed in Python 3.13", "warn"),
    "chunk": ("removed in Python 3.13", "warn"),
    "mailcap": ("removed in Python 3.13 -- use mimetypes", "warn"),
    "pkg_resources": ("deprecated -- use importlib.metadata / importlib.resources", "info"),
}

# (receiver last segment, attr) -> (message, severity)
_ATTR_CALLS = {
    ("datetime", "utcnow"): (
        "deprecated since Python 3.12 -- returns a naive datetime; use datetime.now(timezone.utc)", "info"),
    ("datetime", "utcfromtimestamp"): (
        "deprecated since Python 3.12 -- returns a naive datetime; use "
        "datetime.fromtimestamp(ts, timezone.utc)", "info"),
    ("ssl", "wrap_socket"): (
        "removed in Python 3.12 -- create an SSLContext and use context.wrap_socket(...)", "warn"),
    ("asyncio", "get_event_loop"): (
        "deprecated outside a running loop -- use asyncio.run(...) at the entry point or "
        "asyncio.get_running_loop() inside a coroutine", "info"),
    ("locale", "getdefaultlocale"): (
        "deprecated since Python 3.11 -- use locale.getlocale() / locale.setlocale(...)", "info"),
}

# unittest aliases removed in Python 3.12, matched on method name alone
_BARE_METHODS = {
    "assertEquals": "assertEqual",
    "assertNotEquals": "assertNotEqual",
    "assert_": "assertTrue",
    "failUnless": "assertTrue",
    "failIf": "assertFalse",
    "failUnlessEqual": "assertEqual",
    "failIfEqual": "assertNotEqual",
    "failUnlessRaises": "assertRaises",
    "failUnlessAlmostEqual": "assertAlmostEqual",
    "failIfAlmostEqual": "assertNotAlmostEqual",
    "assertRegexpMatches": "assertRegex",
    "assertNotRegexpMatches": "assertNotRegex",
    "assertRaisesRegexp": "assertRaisesRegex",
}


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _top_segment(dotted):
    return dotted.split(".", 1)[0] if dotted else None


def _last_segment(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _issue(path, node, display, message, severity):
    return Issue(path, node.lineno, "correctness", severity, SYMBOL, f"'{display}' is {message}")


def _import_issues(node, path):
    issues = []
    if isinstance(node, ast.Import):
        names = [(alias.name, _top_segment(alias.name)) for alias in node.names]
    elif isinstance(node, ast.ImportFrom) and node.level == 0:
        names = [(node.module, _top_segment(node.module))]
    else:
        return issues
    for display, top in names:
        if top in _MODULES:
            message, severity = _MODULES[top]
            issues.append(_issue(path, node, f"import {display}", message, severity))
    return issues


def _call_issues(node, path):
    func = node.func
    attr = _last_segment(func)
    if attr is None:
        return []
    if attr in _BARE_METHODS:
        message = f"a unittest alias removed in Python 3.12 -- use {_BARE_METHODS[attr]}"
        return [_issue(path, node, attr, message, "warn")]
    if isinstance(func, ast.Attribute):
        receiver = _last_segment(func.value)
        entry = _ATTR_CALLS.get((receiver, attr))
        if entry is not None:
            message, severity = entry
            return [_issue(path, node, f"{receiver}.{attr}(...)", message, severity)]
    return []


def deprecated_api_issues(tree, path, only_lines=None):
    """Every deprecated-api issue in `tree` -- see the module docstring for
    the three tables and what severity means here.
    """
    issues = []
    for node in ast.walk(tree):
        if not _in_scope(node, only_lines):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            issues.extend(_import_issues(node, path))
        elif isinstance(node, ast.Call):
            issues.extend(_call_issues(node, path))
    return issues

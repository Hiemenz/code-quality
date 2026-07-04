"""Assertion-free test detection: a `test_*` function that runs code but
never checks anything passes no matter what that code does. This is
distinct from coverage (which only asks "did anything call this") and
mutation testing (which needs to actually run mutants) -- it's a static,
instant check for the purest form of test theater: no `assert`, no
`self.assertX`, no `pytest.raises`/`warns`, nothing that could ever fail.
"""

import ast

from codequality.analyzers.base import Issue

_ASSERTION_LIKE_ATTRS_PREFIX = "assert"
_ASSERTION_LIKE_NAMES = {"raises", "warns", "fail"}
_SKIP_DECORATOR_NAMES = {"skip", "skipif", "skipUnless"}


def _callee_name(func):
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _is_assertion_like_call(node):
    if not isinstance(node, ast.Call):
        return False
    name = _callee_name(node.func)
    if name is None:
        return False
    return name.startswith(_ASSERTION_LIKE_ATTRS_PREFIX) or name in _ASSERTION_LIKE_NAMES


def _has_assertion(fn_node):
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Assert) or _is_assertion_like_call(node):
            return True
    return False


def _decorator_name(dec):
    target = dec.func if isinstance(dec, ast.Call) else dec
    return _callee_name(target) if isinstance(target, (ast.Attribute, ast.Name)) else None


def _is_skipped(fn_node):
    return any(_decorator_name(dec) in _SKIP_DECORATOR_NAMES for dec in fn_node.decorator_list)


def is_test_function(fn_node):
    return fn_node.name.startswith("test_")


def assertion_free_test_issues(fn_node, path):
    """[]/[Issue] for one already-identified function node, if it looks like
    a test (`test_*` name) with no assertion anywhere in its body.
    """
    if not is_test_function(fn_node) or _is_skipped(fn_node) or _has_assertion(fn_node):
        return []
    return [
        Issue(path, fn_node.lineno, "correctness", "warn", "assertion-free-test",
              f"Test '{fn_node.name}' has no assertion -- it will pass no matter what the code under test does")
    ]

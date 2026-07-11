"""Test-theater detection, two escalating forms:

- **`assertion-free-test`** -- a `test_*` function that runs code but never
  checks anything: no `assert`, no `self.assertX`, no
  `pytest.raises`/`warns`, nothing that could ever fail. Distinct from
  coverage (which only asks "did anything call this") and mutation testing
  (which needs to actually run mutants) -- a static, instant check.

- **`tautological-test`** -- the sneakier sibling: the test *has*
  assertions, but every single one is trivially true (`assert True`,
  `assertEqual(x, x)`, `assert a == a`), so it still can't fail no matter
  what the code under test does. Common in LLM-written tests that were
  generated to satisfy a "write tests" instruction rather than to verify
  behavior. Only fires when ALL of a test's assertion-like statements are
  tautological -- one `assert True` next to a real assertion is odd but
  harmless, and flagging it would be noise. Comparing two textually
  identical expressions (`assert f(x) == f(x)`) is treated as tautological
  even though a side-effecting `f` could technically differ between calls;
  a test relying on that is theater of a different kind.

- **`mock-only-test`** -- the test's every assertion is a mock-interaction
  assertion (`m.assert_called_once_with(...)`, `assert_awaited`,
  `assert_has_calls`, ...): it verifies that mocks were poked in the
  expected order, but never checks a single real output or state change.
  Interaction-testing is a legitimate style when the interaction *is* the
  contract (a notification went out, a transaction was committed), so this
  is `info`, not `warn` -- a distribution-level signal rather than a
  per-test verdict: a test suite where a large share of tests assert only
  on mocks is well-documented LLM output behavior ("mock everything, then
  assert the mocks are mocks"), and a human reviewing the list can tell
  the two apart in seconds. Fires only when all assertions are
  mock-interaction calls; a single real assertion clears the test, and a
  test with no assertions at all is `assertion-free-test`'s finding.
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


_SAME_ARGS_METHODS = {
    "assertEqual", "assertEquals", "assertIs", "assertAlmostEqual", "assertSequenceEqual",
    "assertListEqual", "assertDictEqual", "assertTupleEqual", "assertSetEqual", "assertCountEqual",
}
_TAUTOLOGICAL_COMPARE_OPS = (ast.Eq, ast.LtE, ast.GtE, ast.Is)


def _same_expression(a, b):
    return ast.dump(a) == ast.dump(b)


def _is_tautological_expr(expr):
    # A truthy constant (`assert True`, `assert 1`) can never fail; a falsy
    # one (`assert False`) is a deliberate always-fail, which is not this
    # pattern.
    if isinstance(expr, ast.Constant) and bool(expr.value):
        return True
    if isinstance(expr, ast.Compare) and len(expr.comparators) == 1:
        return isinstance(expr.ops[0], _TAUTOLOGICAL_COMPARE_OPS) and _same_expression(expr.left, expr.comparators[0])
    return False


def _is_tautological_call(node):
    name = _callee_name(node.func)
    args = node.args
    if not args:
        return False
    head = args[0]
    if name in ("assertTrue", "assert_"):
        return isinstance(head, ast.Constant) and bool(head.value)
    if name == "assertFalse":
        return isinstance(head, ast.Constant) and not head.value
    if name in _SAME_ARGS_METHODS and len(args) >= 2:
        return _same_expression(head, args[1])
    if name == "assertIsNone":
        return isinstance(head, ast.Constant) and head.value is None
    if name == "assertIsNotNone":
        return isinstance(head, ast.Constant) and head.value is not None
    return False


def _is_tautological_assertion(node):
    if isinstance(node, ast.Assert):
        return _is_tautological_expr(node.test)
    return _is_tautological_call(node)


def tautological_test_issues(fn_node, path):
    """[]/[Issue] for one already-identified function node, if it looks
    like a test (`test_*` name) whose every assertion is trivially true --
    see the module docstring. A test with zero assertions is
    `assertion-free-test`'s finding, not this one.
    """
    if not is_test_function(fn_node) or _is_skipped(fn_node):
        return []
    assertions = [
        node for node in ast.walk(fn_node)
        if isinstance(node, ast.Assert) or _is_assertion_like_call(node)
    ]
    if not assertions or not all(_is_tautological_assertion(node) for node in assertions):
        return []
    return [
        Issue(path, fn_node.lineno, "correctness", "warn", "tautological-test",
              f"Test '{fn_node.name}' only makes trivially-true assertions (e.g. 'assert True', "
              f"comparing an expression to itself) -- it can never fail")
    ]


# unittest.mock's Mock assertion methods (Mock.assert_called_with and
# friends) -- the exhaustive current set, matched by method name.
_MOCK_ASSERT_PREFIXES = ("assert_called", "assert_awaited", "assert_not_called", "assert_not_awaited")
_MOCK_ASSERT_NAMES = {"assert_any_call", "assert_any_await", "assert_has_calls", "assert_has_awaits"}


def _is_mock_assertion(node):
    if not isinstance(node, ast.Call):
        return False
    name = _callee_name(node.func)
    if name is None:
        return False
    return name.startswith(_MOCK_ASSERT_PREFIXES) or name in _MOCK_ASSERT_NAMES


def mock_only_test_issues(fn_node, path):
    """[]/[Issue] for one already-identified function node, if it looks
    like a test (`test_*` name) whose every assertion is a
    mock-interaction assertion -- see the module docstring for why this is
    `info` severity.
    """
    if not is_test_function(fn_node) or _is_skipped(fn_node):
        return []
    assertions = [
        node for node in ast.walk(fn_node)
        if isinstance(node, ast.Assert) or _is_assertion_like_call(node)
    ]
    if not assertions or not all(_is_mock_assertion(node) for node in assertions):
        return []
    return [
        Issue(path, fn_node.lineno, "correctness", "info", "mock-only-test",
              f"Test '{fn_node.name}' asserts only on mock interactions -- no real output or state "
              f"is ever checked")
    ]

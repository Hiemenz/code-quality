"""Tests for the _apply_missing_tests scanner check."""

from codequality.analyzers.base import FileMetrics, FunctionMetrics
from codequality.scanner import _apply_missing_tests


def _fm(path, language="python", complexity=5):
    fm = FileMetrics(path=path, language=language, total_lines=50, loc=40)
    if complexity:
        fm.functions = [FunctionMetrics(
            file=path, name="foo", lineno=1, end_lineno=20,
            complexity=complexity, length=20, nesting=1, params=1, has_docstring=True,
        )]
    return fm


def test_missing_test_flagged():
    metrics = {"foo.py": _fm("foo.py")}
    _apply_missing_tests(metrics)
    syms = [i.symbol for i in metrics["foo.py"].issues]
    assert "missing-test-file" in syms


def test_with_test_file_not_flagged():
    metrics = {"foo.py": _fm("foo.py"), "test_foo.py": _fm("test_foo.py", complexity=2)}
    _apply_missing_tests(metrics)
    syms = [i.symbol for i in metrics["foo.py"].issues]
    assert "missing-test-file" not in syms


def test_stem_test_suffix_counts():
    metrics = {"foo.py": _fm("foo.py"), "foo_test.py": _fm("foo_test.py", complexity=2)}
    _apply_missing_tests(metrics)
    syms = [i.symbol for i in metrics["foo.py"].issues]
    assert "missing-test-file" not in syms


def test_low_complexity_not_flagged():
    metrics = {"foo.py": _fm("foo.py", complexity=1)}
    _apply_missing_tests(metrics)
    syms = [i.symbol for i in metrics["foo.py"].issues]
    assert "missing-test-file" not in syms


def test_test_file_itself_not_flagged():
    metrics = {"test_foo.py": _fm("test_foo.py")}
    _apply_missing_tests(metrics)
    assert metrics["test_foo.py"].issues == []


def test_non_python_not_flagged():
    fm = FileMetrics(path="foo.js", language="javascript", total_lines=50, loc=40)
    metrics = {"foo.js": fm}
    _apply_missing_tests(metrics)
    assert fm.issues == []


def test_no_functions_not_flagged():
    fm = FileMetrics(path="foo.py", language="python", total_lines=5, loc=4)
    metrics = {"foo.py": fm}
    _apply_missing_tests(metrics)
    assert fm.issues == []

"""Tests for the fstring-log-arg check in python_security."""

import ast

from codequality.analyzers.python_security import security_issues


def _symbols(source, path="test.py"):
    tree = ast.parse(source)
    return [i.symbol for i in security_issues(tree, path, only_lines=None)]


def test_fstring_log_debug():
    src = 'import logging\nlogger = logging.getLogger(__name__)\nlogger.debug(f"value is {x}")\n'
    assert "fstring-log-arg" in _symbols(src)


def test_fstring_log_info():
    src = 'logger.info(f"hello {name}")\n'
    assert "fstring-log-arg" in _symbols(src)


def test_percent_format_log_not_flagged():
    src = 'logger.debug("value is %s", x)\n'
    assert "fstring-log-arg" not in _symbols(src)


def test_plain_string_not_flagged():
    src = 'logger.info("static message")\n'
    assert "fstring-log-arg" not in _symbols(src)


def test_fstring_no_interpolation_not_flagged():
    src = 'logger.info(f"no interpolation here")\n'
    assert "fstring-log-arg" not in _symbols(src)


def test_print_fstring_not_flagged():
    # fstring-log-arg is logger-only; print gets sensitive-data-logging, not this
    src = 'print(f"value is {x}")\n'
    assert "fstring-log-arg" not in _symbols(src)


def test_only_lines_filter():
    src = 'logger.debug(f"val {x}")\n'
    tree = ast.parse(src)
    issues = security_issues(tree, "test.py", only_lines={99})
    assert all(i.symbol != "fstring-log-arg" for i in issues)

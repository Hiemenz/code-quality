"""Tests for python_import_order: future-import-order and relative-before-absolute."""

import ast

import pytest

from codequality.analyzers.python_import_order import import_order_issues


def _issues(source):
    tree = ast.parse(source)
    return import_order_issues(tree, "test.py")


def _symbols(source):
    return [i.symbol for i in _issues(source)]


def test_clean_imports():
    src = "import os\nimport sys\n"
    assert _symbols(src) == []


def test_future_first_is_fine():
    src = "from __future__ import annotations\nimport os\n"
    assert _symbols(src) == []


def test_future_after_stdlib():
    src = "import os\nfrom __future__ import annotations\n"
    assert "future-import-order" in _symbols(src)


def test_future_after_relative():
    src = "from .foo import bar\nfrom __future__ import annotations\n"
    assert "future-import-order" in _symbols(src)


def test_relative_after_absolute_ok():
    src = "import os\nfrom .foo import bar\n"
    assert _symbols(src) == []


def test_relative_before_absolute_flagged():
    src = "from .foo import bar\nimport os\n"
    assert "relative-before-absolute" in _symbols(src)


def test_leading_docstring_ignored():
    src = '"""Module docstring."""\nimport os\nfrom __future__ import annotations\n'
    assert "future-import-order" in _symbols(src)


def test_only_relative_no_flag():
    src = "from .foo import bar\nfrom .baz import qux\n"
    assert _symbols(src) == []


def test_clean_only_lines_filter():
    src = "from .foo import bar\nimport os\n"
    tree = ast.parse(src)
    issues = import_order_issues(tree, "test.py", only_lines={99})
    assert issues == []


def test_only_lines_in_scope():
    src = "from .foo import bar\nimport os\n"
    tree = ast.parse(src)
    issues = import_order_issues(tree, "test.py", only_lines={1})
    syms = [i.symbol for i in issues]
    assert "relative-before-absolute" in syms

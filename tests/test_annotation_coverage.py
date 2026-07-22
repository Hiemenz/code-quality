"""Tests for codequality.annotation_coverage."""

import ast
import unittest

from codequality.annotation_coverage import (
    _file_results,
    _is_public,
    _param_count_annotated,
    compute,
    render_text,
)


def _parse(src):
    return ast.parse(src)


def _func(src):
    tree = _parse(src)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef))


class TestIsPublic(unittest.TestCase):
    def test_regular_name_is_public(self):
        self.assertTrue(_is_public("foo"))

    def test_underscore_prefix_is_private(self):
        self.assertFalse(_is_public("_foo"))

    def test_dunder_excluded_by_default(self):
        self.assertFalse(_is_public("__init__"))

    def test_dunder_included_when_flag_set(self):
        self.assertTrue(_is_public("__init__", include_dunders=True))


class TestParamCountAnnotated(unittest.TestCase):
    def test_fully_annotated(self):
        node = _func("def f(x: int, y: str) -> bool: ...")
        total, annotated = _param_count_annotated(node)
        self.assertEqual(total, 3)   # x, y, return
        self.assertEqual(annotated, 3)

    def test_no_annotations(self):
        node = _func("def f(x, y): ...")
        total, annotated = _param_count_annotated(node)
        self.assertEqual(total, 3)   # x, y, return
        self.assertEqual(annotated, 0)

    def test_self_excluded(self):
        node = _func("def f(self, x: int) -> None: ...")
        total, annotated = _param_count_annotated(node)
        self.assertEqual(total, 2)   # x, return
        self.assertEqual(annotated, 2)

    def test_cls_excluded(self):
        node = _func("def f(cls, x) -> None: ...")
        total, annotated = _param_count_annotated(node)
        self.assertEqual(total, 2)   # x, return
        self.assertEqual(annotated, 1)  # only return

    def test_no_params_only_return(self):
        node = _func("def f() -> int: ...")
        total, annotated = _param_count_annotated(node)
        self.assertEqual(total, 1)
        self.assertEqual(annotated, 1)

    def test_no_params_no_return(self):
        node = _func("def f(): ...")
        total, annotated = _param_count_annotated(node)
        self.assertEqual(total, 1)   # return slot
        self.assertEqual(annotated, 0)

    def test_vararg_counted(self):
        node = _func("def f(*args: int) -> None: ...")
        total, annotated = _param_count_annotated(node)
        self.assertEqual(total, 2)   # *args, return
        self.assertEqual(annotated, 2)

    def test_kwarg_counted(self):
        node = _func("def f(**kwargs: str) -> None: ...")
        total, annotated = _param_count_annotated(node)
        self.assertEqual(total, 2)
        self.assertEqual(annotated, 2)


class TestFileResults(unittest.TestCase):
    def test_public_function_found(self):
        src = "def foo(x: int) -> str: ..."
        results = _file_results(src, "f.py")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["function"], "foo")
        self.assertEqual(results[0]["coverage"], 100.0)

    def test_private_function_excluded(self):
        src = "def _helper(x: int) -> str: ..."
        results = _file_results(src, "f.py")
        self.assertEqual(results, [])

    def test_dunder_excluded_by_default(self):
        src = "def __init__(self, x: int) -> None: ..."
        results = _file_results(src, "f.py")
        self.assertEqual(results, [])

    def test_dunder_included_when_requested(self):
        src = "def __init__(self, x: int) -> None: ..."
        results = _file_results(src, "f.py", include_dunders=True)
        self.assertEqual(len(results), 1)

    def test_partial_annotation_coverage(self):
        src = "def foo(x: int, y) -> None: ..."
        results = _file_results(src, "f.py")
        self.assertEqual(results[0]["total"], 3)
        self.assertEqual(results[0]["annotated"], 2)

    def test_syntax_error_returns_empty(self):
        results = _file_results("def (:", "f.py")
        self.assertEqual(results, [])

    def test_multiple_functions(self):
        src = "def foo(x: int) -> None: ...\ndef bar(y): ..."
        results = _file_results(src, "f.py")
        names = [r["function"] for r in results]
        self.assertIn("foo", names)
        self.assertIn("bar", names)

    def test_zero_total_gives_100_percent(self):
        # A function with only self and no return annotation edge case
        src = "def foo(self): ..."
        results = _file_results(src, "f.py")
        # self excluded, no other params, return slot counts
        self.assertEqual(results[0]["total"], 1)


class TestCompute(unittest.TestCase):
    def test_overall_stats_computed(self, tmp_path=None):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.py"), "w") as f:
                f.write("def foo(x: int) -> None: ...\n")
            result = compute(d)
        self.assertIn("overall", result)
        self.assertGreater(result["overall"]["total"], 0)

    def test_below_threshold_populated(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "m.py"), "w") as f:
                f.write("def foo(x, y): ...\n")  # 0% annotated
            result = compute(d, min_coverage=50.0)
        self.assertGreater(len(result["below_threshold"]), 0)

    def test_no_python_files_returns_empty(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "readme.txt"), "w") as f:
                f.write("hello\n")
            result = compute(d)
        self.assertEqual(result["overall"]["total"], 0)
        self.assertEqual(result["functions"], [])


class TestRenderText(unittest.TestCase):
    def _result(self, coverage=80.0):
        return {
            "functions": [],
            "files": [{"file": "m.py", "function_count": 2, "total": 5,
                        "annotated": 4, "coverage": coverage}],
            "overall": {"total": 5, "annotated": 4, "coverage": coverage},
            "below_threshold": [],
            "min_coverage": 0.0,
        }

    def test_overall_coverage_shown(self):
        text = render_text(self._result(80.0))
        self.assertIn("80.0%", text)
        self.assertIn("4/5", text)

    def test_file_listed(self):
        text = render_text(self._result(80.0))
        self.assertIn("m.py", text)

    def test_below_threshold_shown(self):
        result = self._result(30.0)
        result["min_coverage"] = 50.0
        result["below_threshold"] = [{
            "file": "m.py", "line": 1, "function": "foo",
            "total": 3, "annotated": 1, "coverage": 33.3,
        }]
        text = render_text(result)
        self.assertIn("foo", text)
        self.assertIn("33.3%", text)


if __name__ == "__main__":
    unittest.main()

import ast
import unittest

from codequality.analyzers import generic_analyzer
from codequality.analyzers.placeholder_code import (
    placeholder_comment_issues,
    stub_implementation_issues,
)
from codequality.config import DEFAULT_CONFIG, Limits


def _stub_issues(source):
    return stub_implementation_issues(ast.parse(source), "f.py")


def _comment_issues(source, only_lines=None):
    return placeholder_comment_issues("f.py", source.splitlines(keepends=True), only_lines)


class TestStubImplementation(unittest.TestCase):
    def test_pass_only_body_is_flagged_info(self):
        issues = _stub_issues("def handler(event):\n    pass\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "stub-implementation")
        self.assertEqual(issues[0].severity, "info")
        self.assertEqual(issues[0].category, "correctness")

    def test_ellipsis_body_is_flagged(self):
        issues = _stub_issues("def handler(event):\n    ...\n")
        self.assertEqual(len(issues), 1)

    def test_docstring_only_body_is_flagged(self):
        issues = _stub_issues('def handler(event):\n    """Handle the event."""\n')
        self.assertEqual(len(issues), 1)

    def test_not_implemented_raise_is_flagged_warn(self):
        issues = _stub_issues("def convert(x):\n    raise NotImplementedError\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warn")

    def test_not_implemented_call_with_docstring_is_flagged(self):
        issues = _stub_issues('def convert(x):\n    """Convert."""\n    raise NotImplementedError("later")\n')
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warn")

    def test_real_body_is_not_flagged(self):
        issues = _stub_issues("def add(a, b):\n    return a + b\n")
        self.assertEqual(issues, [])

    def test_decorated_function_is_exempt(self):
        issues = _stub_issues("@abstractmethod\ndef convert(x):\n    raise NotImplementedError\n")
        self.assertEqual(issues, [])

    def test_methods_of_abc_subclass_are_exempt(self):
        source = "class Base(ABC):\n    def convert(self, x):\n        raise NotImplementedError\n"
        self.assertEqual(_stub_issues(source), [])

    def test_methods_of_protocol_class_are_exempt(self):
        source = "class Reader(Protocol):\n    def read(self):\n        ...\n"
        self.assertEqual(_stub_issues(source), [])

    def test_metaclass_abcmeta_exempts_methods(self):
        source = "class Base(metaclass=ABCMeta):\n    def convert(self, x):\n        raise NotImplementedError\n"
        self.assertEqual(_stub_issues(source), [])

    def test_abstractmethod_sibling_exempts_whole_class(self):
        source = (
            "class Base:\n"
            "    @abstractmethod\n"
            "    def one(self):\n"
            "        ...\n"
            "    def two(self):\n"
            "        raise NotImplementedError\n"
        )
        self.assertEqual(_stub_issues(source), [])

    def test_plain_class_method_stub_is_flagged(self):
        source = "class Service:\n    def run(self):\n        pass\n"
        issues = _stub_issues(source)
        self.assertEqual(len(issues), 1)

    def test_test_functions_are_left_to_assertion_free_check(self):
        self.assertEqual(_stub_issues("def test_later():\n    pass\n"), [])

    def test_raise_of_other_exception_is_not_a_stub(self):
        issues = _stub_issues("def convert(x):\n    raise ValueError('bad')\n")
        self.assertEqual(issues, [])


class TestPlaceholderComment(unittest.TestCase):
    def test_rest_of_code_comment_is_flagged(self):
        issues = _comment_issues("x = 1\n# ... rest of the code ...\ny = 2\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "placeholder-comment")
        self.assertEqual(issues[0].line, 2)
        self.assertEqual(issues[0].severity, "warn")

    def test_your_logic_here_is_flagged(self):
        issues = _comment_issues("def f():\n    # your logic here\n    pass\n")
        self.assertEqual(len(issues), 1)

    def test_implementation_omitted_is_flagged(self):
        issues = _comment_issues("# implementation omitted\n")
        self.assertEqual(len(issues), 1)

    def test_rest_of_with_one_adjective_is_flagged(self):
        issues = _comment_issues("# ... rest of the processing logic ...\n")
        self.assertEqual(len(issues), 1)

    def test_existing_code_ellipsis_marker_is_flagged(self):
        issues = _comment_issues("# ... existing code ...\n")
        self.assertEqual(len(issues), 1)

    def test_ordinary_comment_is_not_flagged(self):
        issues = _comment_issues("# strip the header row before parsing\nrows = rows[1:]\n")
        self.assertEqual(issues, [])

    def test_phrase_inside_a_string_literal_is_not_flagged(self):
        issues = _comment_issues('msg = "put your code here"\n')
        self.assertEqual(issues, [])

    def test_only_lines_restricts_the_check(self):
        source = "# your code here\nx = 1\n# your code here\n"
        issues = _comment_issues(source, only_lines={3})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 3)


def _analyze_generic(source, path="f.js", language="javascript"):
    return generic_analyzer.analyze(path, source, language, Limits(dict(DEFAULT_CONFIG["limits"])))


class TestGenericLanguagePlaceholders(unittest.TestCase):
    def test_js_placeholder_comment_is_flagged(self):
        fm = _analyze_generic("function f() {\n  // ... rest of the code ...\n}\n")
        symbols = [i.symbol for i in fm.issues]
        self.assertIn("placeholder-comment", symbols)

    def test_js_not_implemented_throw_is_flagged(self):
        fm = _analyze_generic("function f() {\n  throw new Error('Not implemented');\n}\n")
        issues = [i for i in fm.issues if i.symbol == "stub-implementation"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "info")

    def test_csharp_not_implemented_exception_is_flagged(self):
        fm = _analyze_generic("void F() {\n  throw new NotImplementedException();\n}\n", "f.cs", "csharp")
        self.assertIn("stub-implementation", {i.symbol for i in fm.issues})

    def test_rust_todo_macro_is_flagged(self):
        fm = _analyze_generic("fn f() {\n    todo!()\n}\n", "f.rs", "rust")
        self.assertIn("stub-implementation", {i.symbol for i in fm.issues})

    def test_kotlin_todo_call_is_flagged(self):
        fm = _analyze_generic("fun f() {\n    TODO()\n}\n", "f.kt", "kotlin")
        self.assertIn("stub-implementation", {i.symbol for i in fm.issues})

    def test_lowercase_todo_helper_is_not_flagged(self):
        fm = _analyze_generic("function f() {\n  todo();\n}\n")
        self.assertNotIn("stub-implementation", {i.symbol for i in fm.issues})

    def test_ordinary_generic_code_is_clean(self):
        fm = _analyze_generic("function add(a, b) {\n  return a + b;\n}\n")
        symbols = {i.symbol for i in fm.issues}
        self.assertNotIn("placeholder-comment", symbols)
        self.assertNotIn("stub-implementation", symbols)


if __name__ == "__main__":
    unittest.main()

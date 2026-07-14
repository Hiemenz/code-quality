import ast
import unittest

from codequality.analyzers.python_idioms import (
    boolean_trap_issues,
    comparison_idiom_issues,
    f_string_no_placeholder_issues,
    long_lambda_issues,
    magic_number_issues,
    mutable_class_attribute_issues,
    nested_comprehension_issues,
    redundant_else_issues,
    shadowed_builtin_issues,
)


def _tree(source):
    return ast.parse(source)


class TestComparisonIdioms(unittest.TestCase):
    def test_eq_none_flagged(self):
        issues = comparison_idiom_issues(_tree("if x == None: pass\n"), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "comparison-to-none")
        self.assertIn("is None", issues[0].message)

    def test_neq_none_flagged(self):
        issues = comparison_idiom_issues(_tree("if x != None: pass\n"), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertIn("is not None", issues[0].message)

    def test_none_on_left_flagged(self):
        issues = comparison_idiom_issues(_tree("if None == x: pass\n"), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "comparison-to-none")

    def test_is_none_not_flagged(self):
        self.assertEqual(comparison_idiom_issues(_tree("if x is None: pass\n"), "f.py"), [])

    def test_eq_true_flagged(self):
        issues = comparison_idiom_issues(_tree("if x == True: pass\n"), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "comparison-to-true")
        self.assertIn("True", issues[0].message)

    def test_eq_false_flagged(self):
        issues = comparison_idiom_issues(_tree("if x == False: pass\n"), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertIn("False", issues[0].message)

    def test_eq_integer_not_flagged(self):
        self.assertEqual(comparison_idiom_issues(_tree("if x == 1: pass\n"), "f.py"), [])

    def test_is_true_not_flagged(self):
        self.assertEqual(comparison_idiom_issues(_tree("if x is True: pass\n"), "f.py"), [])

    def test_only_lines_restricts(self):
        src = "a = 1\nif x == None: pass\n"
        issues = comparison_idiom_issues(_tree(src), "f.py", only_lines={1})
        self.assertEqual(issues, [])

    def test_only_lines_includes(self):
        src = "a = 1\nif x == None: pass\n"
        issues = comparison_idiom_issues(_tree(src), "f.py", only_lines={2})
        self.assertEqual(len(issues), 1)


class TestShadowedBuiltin(unittest.TestCase):
    def test_assignment_to_list_flagged(self):
        issues = shadowed_builtin_issues(_tree("list = []\n"), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "shadowed-builtin")
        self.assertIn("list", issues[0].message)

    def test_assignment_to_dict_flagged(self):
        issues = shadowed_builtin_issues(_tree("dict = {}\n"), "f.py")
        self.assertEqual(len(issues), 1)

    def test_for_loop_target_flagged(self):
        issues = shadowed_builtin_issues(_tree("for id in items: pass\n"), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertIn("id", issues[0].message)

    def test_function_name_flagged(self):
        issues = shadowed_builtin_issues(_tree("def list(): pass\n"), "f.py")
        self.assertEqual(len(issues), 1)

    def test_class_name_flagged(self):
        issues = shadowed_builtin_issues(_tree("class dict: pass\n"), "f.py")
        self.assertEqual(len(issues), 1)

    def test_normal_name_not_flagged(self):
        self.assertEqual(shadowed_builtin_issues(_tree("my_list = []\n"), "f.py"), [])

    def test_exception_name_not_in_set(self):
        # Assigning to ValueError (an exception class name) should NOT be flagged --
        # exception classes are excluded from the builtin set.
        self.assertEqual(shadowed_builtin_issues(_tree("ValueError = 'oops'\n"), "f.py"), [])

    def test_annotated_assignment_flagged(self):
        issues = shadowed_builtin_issues(_tree("list: int = 1\n"), "f.py")
        self.assertEqual(len(issues), 1)


class TestMutableClassAttribute(unittest.TestCase):
    def test_class_level_list_flagged(self):
        src = "class Foo:\n    items = []\n"
        issues = mutable_class_attribute_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "mutable-class-attribute")
        self.assertIn("items", issues[0].message)

    def test_class_level_dict_flagged(self):
        src = "class Foo:\n    mapping = {}\n"
        issues = mutable_class_attribute_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)

    def test_class_level_set_flagged(self):
        src = "class Foo:\n    names = set()\n"
        # set() is a Call, not an ast.Set literal -- should NOT be flagged
        # (we only flag literal {} [] set literals, not set() calls)
        issues = mutable_class_attribute_issues(ast.parse(src), "f.py")
        self.assertEqual(issues, [])

    def test_class_level_set_literal_flagged(self):
        src = "class Foo:\n    names = {1, 2}\n"
        issues = mutable_class_attribute_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)

    def test_instance_attribute_not_flagged(self):
        src = "class Foo:\n    def __init__(self):\n        self.items = []\n"
        self.assertEqual(mutable_class_attribute_issues(ast.parse(src), "f.py"), [])

    def test_string_attribute_not_flagged(self):
        src = "class Foo:\n    name = 'hello'\n"
        self.assertEqual(mutable_class_attribute_issues(ast.parse(src), "f.py"), [])

    def test_integer_attribute_not_flagged(self):
        src = "class Foo:\n    count = 0\n"
        self.assertEqual(mutable_class_attribute_issues(ast.parse(src), "f.py"), [])


class TestFStringNoPlaceholder(unittest.TestCase):
    def test_f_string_without_placeholder_flagged(self):
        issues = f_string_no_placeholder_issues(_tree('x = f"hello"\n'), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "f-string-no-placeholder")

    def test_f_string_with_placeholder_not_flagged(self):
        issues = f_string_no_placeholder_issues(_tree('x = f"hello {name}"\n'), "f.py")
        self.assertEqual(issues, [])

    def test_regular_string_not_flagged(self):
        issues = f_string_no_placeholder_issues(_tree('x = "hello"\n'), "f.py")
        self.assertEqual(issues, [])

    def test_empty_f_string_flagged(self):
        issues = f_string_no_placeholder_issues(_tree('x = f""\n'), "f.py")
        self.assertEqual(len(issues), 1)

    def test_format_spec_inner_joinedstr_not_flagged(self):
        # f"{label:<28}" has a nested JoinedStr for the "<28" format spec.
        # That inner node contains only a Constant, not a FormattedValue, but
        # it must NOT be flagged -- only the outer f-string matters, and it
        # does have a placeholder ({label}).
        issues = f_string_no_placeholder_issues(_tree('x = f"{label:<28}"\n'), "f.py")
        self.assertEqual(issues, [])


class TestRedundantElse(unittest.TestCase):
    def test_else_after_return_flagged(self):
        src = "def f():\n    if x:\n        return 1\n    else:\n        return 2\n"
        issues = redundant_else_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "redundant-else")

    def test_else_after_raise_flagged(self):
        src = "def f():\n    if not x:\n        raise ValueError\n    else:\n        pass\n"
        issues = redundant_else_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)

    def test_no_else_not_flagged(self):
        src = "def f():\n    if x:\n        return 1\n    return 2\n"
        self.assertEqual(redundant_else_issues(ast.parse(src), "f.py"), [])

    def test_if_without_return_not_flagged(self):
        src = "def f():\n    if x:\n        do_something()\n    else:\n        return 2\n"
        self.assertEqual(redundant_else_issues(ast.parse(src), "f.py"), [])

    def test_elif_chain_outer_not_flagged(self):
        # The outer `if` has `elif` (not a true `else`), so should not be flagged
        src = "def f():\n    if a:\n        return 1\n    elif b:\n        return 2\n"
        self.assertEqual(redundant_else_issues(ast.parse(src), "f.py"), [])

    def test_else_after_continue_flagged(self):
        src = "for x in items:\n    if bad(x):\n        continue\n    else:\n        process(x)\n"
        issues = redundant_else_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)


class TestBooleanTrap(unittest.TestCase):
    def test_two_bool_annotated_params_flagged(self):
        src = "def f(a, reverse: bool, verbose: bool): pass\n"
        issues = boolean_trap_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "boolean-trap")
        self.assertIn("reverse", issues[0].message)

    def test_two_bool_default_params_flagged(self):
        src = "def sort(lst, ascending=True, stable=False): pass\n"
        issues = boolean_trap_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)

    def test_one_bool_param_not_flagged(self):
        src = "def download(url, verify=True): pass\n"
        self.assertEqual(boolean_trap_issues(ast.parse(src), "f.py"), [])

    def test_keyword_only_bool_params_not_flagged(self):
        src = "def f(a, *, ascending=True, stable=False): pass\n"
        self.assertEqual(boolean_trap_issues(ast.parse(src), "f.py"), [])

    def test_self_is_excluded(self):
        src = "class C:\n    def m(self, a: bool, b: bool): pass\n"
        issues = boolean_trap_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertIn("a", issues[0].message)

    def test_no_bool_params_not_flagged(self):
        src = "def f(a, b, c): pass\n"
        self.assertEqual(boolean_trap_issues(ast.parse(src), "f.py"), [])


class TestMagicNumber(unittest.TestCase):
    def test_magic_number_in_comparison_flagged(self):
        src = "def f(x):\n    if x > 42:\n        pass\n"
        issues = magic_number_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "magic-number")
        self.assertIn("42", issues[0].message)

    def test_magic_number_in_binop_flagged(self):
        src = "def f(x):\n    return x * 73\n"
        issues = magic_number_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)

    def test_safe_numbers_not_flagged(self):
        for n in (0, 1, 2, 10, 100, 1000, -1):
            src = f"def f(x):\n    return x + {n}\n"
            self.assertEqual(magic_number_issues(ast.parse(src), "f.py"), [],
                             msg=f"safe number {n} should not be flagged")

    def test_bool_not_flagged(self):
        src = "def f(x):\n    return x == True\n"
        self.assertEqual(magic_number_issues(ast.parse(src), "f.py"), [])

    def test_module_level_not_flagged(self):
        # Module-level BinOps not inside a function are not checked
        src = "LIMIT = 42\n"
        self.assertEqual(magic_number_issues(ast.parse(src), "f.py"), [])

    def test_augassign_magic_flagged(self):
        src = "def f(x):\n    x += 99\n    return x\n"
        issues = magic_number_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)


class TestNestedComprehension(unittest.TestCase):
    def test_three_for_clauses_flagged(self):
        src = "x = [a+b+c for a in A for b in B for c in C]\n"
        issues = nested_comprehension_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "nested-comprehension")
        self.assertIn("3", issues[0].message)

    def test_two_for_clauses_not_flagged(self):
        src = "x = [a+b for a in A for b in B]\n"
        self.assertEqual(nested_comprehension_issues(ast.parse(src), "f.py"), [])

    def test_dict_comp_flagged(self):
        src = "x = {k: v for k in K for v in V for _ in Z}\n"
        issues = nested_comprehension_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertIn("dict comprehension", issues[0].message)

    def test_generator_expression_flagged(self):
        src = "x = sum(a*b*c for a in A for b in B for c in C)\n"
        issues = nested_comprehension_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)


class TestLongLambda(unittest.TestCase):
    def test_complex_lambda_flagged(self):
        src = "f = lambda x, y: x * y + x / y\n"
        issues = long_lambda_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "long-lambda")

    def test_ternary_lambda_with_call_flagged(self):
        src = "f = lambda x: max(x, 0) if x is not None else 0\n"
        issues = long_lambda_issues(ast.parse(src), "f.py")
        self.assertEqual(len(issues), 1)

    def test_simple_attribute_lambda_not_flagged(self):
        src = "f = lambda x: x.strip()\n"
        self.assertEqual(long_lambda_issues(ast.parse(src), "f.py"), [])

    def test_simple_comparison_lambda_not_flagged(self):
        src = "f = lambda x: x > 0\n"
        self.assertEqual(long_lambda_issues(ast.parse(src), "f.py"), [])

    def test_identity_lambda_not_flagged(self):
        src = "f = lambda x: x\n"
        self.assertEqual(long_lambda_issues(ast.parse(src), "f.py"), [])


if __name__ == "__main__":
    unittest.main()

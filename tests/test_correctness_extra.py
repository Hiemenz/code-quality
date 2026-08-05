import ast
import unittest

from codequality.analyzers.python_correctness_extra import (
    float_equality_issues,
    mutable_global_issues,
)


def _fe(source, only_lines=None):
    return float_equality_issues(ast.parse(source), "f.py", only_lines)


def _mg(source, only_lines=None):
    return mutable_global_issues(ast.parse(source), "f.py", only_lines)


class TestFloatEquality(unittest.TestCase):
    def test_equality_to_float_flagged(self):
        issues = _fe("x == 1.5\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "float-equality")
        self.assertEqual(issues[0].category, "correctness")

    def test_inequality_to_float_flagged(self):
        self.assertEqual(len(_fe("x != 2.0\n")), 1)

    def test_equality_to_zero_float_flagged(self):
        self.assertEqual(len(_fe("x == 0.0\n")), 1)

    def test_negative_float_literal_flagged(self):
        self.assertEqual(len(_fe("x == -2.5\n")), 1)

    def test_float_on_left_side_flagged(self):
        self.assertEqual(len(_fe("3.14 == x\n")), 1)

    def test_int_equality_not_flagged(self):
        self.assertEqual(_fe("x == 1\n"), [])

    def test_ordering_comparison_not_flagged(self):
        self.assertEqual(_fe("x < 1.5\n"), [])
        self.assertEqual(_fe("x >= 2.0\n"), [])

    def test_one_finding_per_comparison(self):
        self.assertEqual(len(_fe("1.0 == x == 2.0\n")), 1)

    def test_only_lines_scoping(self):
        self.assertEqual(len(_fe("x == 1.5\ny == 2.5\n", only_lines={2})), 1)


class TestMutableGlobal(unittest.TestCase):
    def test_global_assignment_flagged(self):
        issues = _mg("def f():\n    global C\n    C = 1\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "mutable-global")
        self.assertEqual(issues[0].severity, "info")

    def test_global_read_only_not_flagged(self):
        self.assertEqual(_mg("def f():\n    global C\n    return C\n"), [])

    def test_local_assignment_not_flagged(self):
        self.assertEqual(_mg("def f():\n    x = 1\n    return x\n"), [])

    def test_tuple_unpack_global_flagged(self):
        self.assertEqual(len(_mg("def f():\n    global A, B\n    A, B = 1, 2\n")), 1)

    def test_augassign_global_flagged(self):
        self.assertEqual(len(_mg("def f():\n    global C\n    C += 1\n")), 1)

    def test_message_lists_names(self):
        issues = _mg("def f():\n    global CACHE\n    CACHE = {}\n")
        self.assertIn("CACHE", issues[0].message)


if __name__ == "__main__":
    unittest.main()

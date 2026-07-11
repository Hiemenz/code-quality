import ast
import unittest

from codequality.analyzers.python_loop_perf import string_concat_in_loop_issues


def _issues(source, only_lines=None):
    return string_concat_in_loop_issues(ast.parse(source), "f.py", only_lines)


class TestStringConcatInLoop(unittest.TestCase):
    def test_string_literal_concat_in_for_loop_flagged(self):
        src = "for i in items:\n    result += 'x'\n"
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "string-concat-in-loop")
        self.assertIn("result", issues[0].message)

    def test_f_string_concat_in_for_loop_flagged(self):
        src = "for i in items:\n    result += f'{i}'\n"
        issues = _issues(src)
        self.assertEqual(len(issues), 1)

    def test_concat_in_while_loop_flagged(self):
        src = "while True:\n    s += 'x'\n    break\n"
        issues = _issues(src)
        self.assertEqual(len(issues), 1)

    def test_numeric_augassign_not_flagged(self):
        src = "for i in items:\n    total += 1\n"
        self.assertEqual(_issues(src), [])

    def test_variable_augassign_not_flagged(self):
        # Without type info, variable += variable is not flagged
        src = "for i in items:\n    result += other\n"
        self.assertEqual(_issues(src), [])

    def test_string_concat_outside_loop_not_flagged(self):
        src = "result += 'x'\n"
        self.assertEqual(_issues(src), [])

    def test_nested_loop_attributed_to_inner(self):
        src = "for i in outer:\n    for j in inner:\n        s += 'x'\n"
        issues = _issues(src)
        # Should only be flagged once (for the inner loop)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 3)

    def test_concat_in_outer_loop_body_flagged(self):
        src = "for i in outer:\n    s += 'outer'\n    for j in inner:\n        pass\n"
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 2)  # outer loop body

    def test_only_lines_restricts(self):
        src = "for i in items:\n    result += 'x'\n"
        issues = _issues(src, only_lines={1})  # loop line but not the augassign line
        self.assertEqual(issues, [])

    def test_only_lines_includes(self):
        src = "for i in items:\n    result += 'x'\n"
        issues = _issues(src, only_lines={2})
        self.assertEqual(len(issues), 1)

    def test_concat_in_if_inside_loop_flagged(self):
        src = "for i in items:\n    if condition:\n        result += 'x'\n"
        issues = _issues(src)
        self.assertEqual(len(issues), 1)


if __name__ == "__main__":
    unittest.main()

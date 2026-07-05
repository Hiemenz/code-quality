import unittest

from codequality.analyzers.complexity_regression import compare_functions


class TestComplexityRegression(unittest.TestCase):
    def _symbols(self, old_source, new_source, threshold=5):
        issues = compare_functions(old_source, new_source, "mod.py", threshold=threshold)
        return {i.symbol for i in issues}

    def test_no_old_source_returns_nothing(self):
        self.assertEqual(compare_functions(None, "def f(a): pass\n", "mod.py"), [])

    def test_large_complexity_jump_is_flagged_with_old_and_new_numbers(self):
        old = "def f(x):\n    return x\n"
        new_lines = ["def f(x):"]
        # 7 if-branches => complexity 1 + 7 = 8, an increase of 7 over the old
        # complexity of 1 -- past the default threshold of 5.
        for i in range(7):
            new_lines.append(f"    if x == {i}:")
            new_lines.append(f"        return {i}")
        new_lines.append("    return -1")
        new = "\n".join(new_lines) + "\n"

        issues = compare_functions(old, new, "mod.py")
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue.symbol, "complexity-regression")
        self.assertIn("1 to 8", issue.message)

    def test_small_increase_under_threshold_is_not_flagged(self):
        old = "def f(x):\n    if x:\n        return 1\n    return 0\n"
        new = "def f(x):\n    if x:\n        return 1\n    elif not x:\n        return 2\n    return 0\n"
        self.assertEqual(self._symbols(old, new), set())

    def test_function_that_got_simpler_is_not_flagged(self):
        old = (
            "def f(x):\n"
            "    if x == 1:\n"
            "        return 1\n"
            "    elif x == 2:\n"
            "        return 2\n"
            "    elif x == 3:\n"
            "        return 3\n"
            "    elif x == 4:\n"
            "        return 4\n"
            "    elif x == 5:\n"
            "        return 5\n"
            "    elif x == 6:\n"
            "        return 6\n"
            "    elif x == 7:\n"
            "        return 7\n"
            "    return -1\n"
        )
        new = "def f(x):\n    return x\n"
        self.assertEqual(self._symbols(old, new), set())

    def test_brand_new_function_has_no_old_counterpart_to_compare(self):
        old = "def f(a): pass\n"
        new_lines = ["def f(a): pass", "def g(x):"]
        for i in range(7):
            new_lines.append(f"    if x == {i}:")
            new_lines.append(f"        return {i}")
        new_lines.append("    return -1")
        new = "\n".join(new_lines) + "\n"
        self.assertEqual(self._symbols(old, new), set())

    def test_private_function_is_exempt(self):
        old = "def _helper(x):\n    return x\n"
        new_lines = ["def _helper(x):"]
        for i in range(7):
            new_lines.append(f"    if x == {i}:")
            new_lines.append(f"        return {i}")
        new_lines.append("    return -1")
        new = "\n".join(new_lines) + "\n"
        self.assertEqual(self._symbols(old, new), set())

    def test_unchanged_function_is_silent(self):
        source = "def f(x):\n    if x:\n        return 1\n    return 0\n"
        self.assertEqual(self._symbols(source, source), set())

    def test_syntax_error_returns_nothing(self):
        self.assertEqual(compare_functions("def f(:\n", "def f(a): pass\n", "mod.py"), [])

    def test_custom_threshold_is_respected(self):
        old = "def f(x):\n    if x:\n        return 1\n    return 0\n"
        new = "def f(x):\n    if x:\n        return 1\n    elif not x:\n        return 2\n    return 0\n"
        # complexity goes from 2 to 3, a +1 increase -- flagged only once the
        # threshold is lowered below that.
        self.assertEqual(self._symbols(old, new, threshold=5), set())
        self.assertIn("complexity-regression", self._symbols(old, new, threshold=0))

    def test_method_of_public_class_is_checked(self):
        old = "class C:\n    def m(self, x):\n        return x\n"
        new_lines = ["class C:", "    def m(self, x):"]
        for i in range(7):
            new_lines.append(f"        if x == {i}:")
            new_lines.append(f"            return {i}")
        new_lines.append("        return -1")
        new = "\n".join(new_lines) + "\n"
        self.assertIn("complexity-regression", self._symbols(old, new))


if __name__ == "__main__":
    unittest.main()

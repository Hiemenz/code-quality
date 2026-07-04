import unittest

from codequality.analyzers.signature_diff import signature_diff_issues


class TestSignatureDiff(unittest.TestCase):
    def _symbols(self, old_source, new_source):
        issues = signature_diff_issues(old_source, new_source, "mod.py")
        return {i.symbol for i in issues}

    def test_no_old_source_returns_nothing(self):
        self.assertEqual(signature_diff_issues(None, "def f(a): pass\n", "mod.py"), [])

    def test_unchanged_signature_is_silent(self):
        source = "def f(a, b): pass\n"
        self.assertEqual(self._symbols(source, source), set())

    def test_removed_parameter_is_flagged(self):
        old = "def f(a, b): pass\n"
        new = "def f(a): pass\n"
        self.assertIn("breaking-signature-change", self._symbols(old, new))

    def test_new_required_parameter_is_flagged(self):
        old = "def f(a): pass\n"
        new = "def f(a, b): pass\n"
        self.assertIn("breaking-signature-change", self._symbols(old, new))

    def test_new_optional_parameter_is_silent(self):
        old = "def f(a): pass\n"
        new = "def f(a, b=1): pass\n"
        self.assertEqual(self._symbols(old, new), set())

    def test_reordered_parameters_are_flagged(self):
        old = "def f(a, b): pass\n"
        new = "def f(b, a): pass\n"
        self.assertIn("breaking-signature-change", self._symbols(old, new))

    def test_private_function_is_ignored(self):
        old = "def _helper(a, b): pass\n"
        new = "def _helper(a): pass\n"
        self.assertEqual(self._symbols(old, new), set())

    def test_method_of_public_class_is_checked(self):
        old = "class C:\n    def m(self, a, b): pass\n"
        new = "class C:\n    def m(self, a): pass\n"
        self.assertIn("breaking-signature-change", self._symbols(old, new))

    def test_syntax_error_returns_nothing(self):
        self.assertEqual(signature_diff_issues("def f(:\n", "def f(a): pass\n", "mod.py"), [])

    def test_new_function_has_no_old_counterpart_to_compare(self):
        old = "def f(a): pass\n"
        new = "def f(a): pass\ndef g(b): pass\n"
        self.assertEqual(self._symbols(old, new), set())


if __name__ == "__main__":
    unittest.main()

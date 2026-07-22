"""Tests for codequality.fixer -- the mechanical style-rule auto-fix engine."""

import textwrap
import unittest

from codequality.fixer import (
    FIXABLE_RULES,
    AppliedFix,
    FixResult,
    SkippedFix,
    _fix_comparison_to_none,
    _fix_comparison_to_true,
    _fix_fstring_no_placeholder,
    _fix_redundant_else,
    _fix_trailing_whitespace,
    fix_issues,
    render_text,
)


def _lines(src):
    """splitlines(keepends=True) helper."""
    return src.splitlines(keepends=True)


# ---------------------------------------------------------------------------
# trailing-whitespace
# ---------------------------------------------------------------------------

class TestFixTrailingWhitespace(unittest.TestCase):
    def _apply(self, src, lineno):
        lines = _lines(src)
        ok = _fix_trailing_whitespace(lines, lineno)
        return ok, "".join(line for line in lines if line is not None)

    def test_strips_trailing_spaces(self):
        ok, result = self._apply("x = 1   \ny = 2\n", 1)
        self.assertTrue(ok)
        self.assertEqual(result, "x = 1\ny = 2\n")

    def test_strips_trailing_tab(self):
        ok, result = self._apply("x = 1\t\ny = 2\n", 1)
        self.assertTrue(ok)
        self.assertEqual(result, "x = 1\ny = 2\n")

    def test_no_trailing_whitespace_unchanged(self):
        ok, result = self._apply("x = 1\ny = 2\n", 1)
        self.assertFalse(ok)
        self.assertEqual(result, "x = 1\ny = 2\n")

    def test_preserves_newline(self):
        lines = ["x   \n"]
        _fix_trailing_whitespace(lines, 1)
        self.assertEqual(lines[0], "x\n")

    def test_crlf_preserved(self):
        lines = ["x   \r\n"]
        _fix_trailing_whitespace(lines, 1)
        self.assertEqual(lines[0], "x\r\n")

    def test_line_out_of_range(self):
        lines = ["x\n"]
        ok = _fix_trailing_whitespace(lines, 99)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# f-string-no-placeholder
# ---------------------------------------------------------------------------

class TestFixFstringNoPlaceholder(unittest.TestCase):
    def _apply(self, src, lineno):
        lines = _lines(src)
        ok = _fix_fstring_no_placeholder(lines, lineno)
        return ok, "".join(line for line in lines if line is not None)

    def test_removes_f_prefix_double_quote(self):
        ok, result = self._apply('x = f"hello"\n', 1)
        self.assertTrue(ok)
        self.assertIn('"hello"', result)
        self.assertNotIn('f"', result)

    def test_removes_f_prefix_single_quote(self):
        ok, result = self._apply("x = f'hello'\n", 1)
        self.assertTrue(ok)
        self.assertNotIn("f'", result)

    def test_removes_F_prefix(self):
        ok, result = self._apply('x = F"hello"\n', 1)
        self.assertTrue(ok)
        self.assertNotIn('F"', result)

    def test_removes_f_from_rf_prefix(self):
        ok, result = self._apply('x = rf"raw no placeholder"\n', 1)
        self.assertTrue(ok)
        result_line = result.strip()
        self.assertTrue(result_line.startswith('x = r"'), result_line)

    def test_removes_f_from_fr_prefix(self):
        ok, result = self._apply('x = fr"raw no placeholder"\n', 1)
        self.assertTrue(ok)
        result_line = result.strip()
        self.assertTrue(result_line.startswith('x = r"'), result_line)

    def test_removes_triple_quote_f_string(self):
        ok, result = self._apply('x = f"""hello"""\n', 1)
        self.assertTrue(ok)
        self.assertNotIn('f"""', result)

    def test_plain_string_unchanged(self):
        ok, result = self._apply('x = "hello"\n', 1)
        self.assertFalse(ok)

    def test_only_first_occurrence_fixed(self):
        ok, result = self._apply('x = f"a"; y = f"b"\n', 1)
        self.assertTrue(ok)
        # First f-string should be fixed, second unchanged
        self.assertIn('f"b"', result)


# ---------------------------------------------------------------------------
# comparison-to-none
# ---------------------------------------------------------------------------

class TestFixComparisonToNone(unittest.TestCase):
    def _apply(self, src, lineno):
        lines = _lines(src)
        ok = _fix_comparison_to_none(lines, lineno)
        return ok, "".join(line for line in lines if line is not None)

    def test_eq_none_becomes_is_none(self):
        ok, result = self._apply("if x == None:\n", 1)
        self.assertTrue(ok)
        self.assertIn("is None", result)
        self.assertNotIn("== None", result)

    def test_neq_none_becomes_is_not_none(self):
        ok, result = self._apply("if x != None:\n", 1)
        self.assertTrue(ok)
        self.assertIn("is not None", result)

    def test_fixes_both_on_same_line(self):
        ok, result = self._apply("if x == None and y != None:\n", 1)
        self.assertTrue(ok)
        self.assertIn("x is None", result)
        self.assertIn("y is not None", result)

    def test_is_none_already_unchanged(self):
        ok, result = self._apply("if x is None:\n", 1)
        self.assertFalse(ok)

    def test_spacing_variants(self):
        ok, result = self._apply("if x ==  None:\n", 1)
        self.assertTrue(ok)
        self.assertIn("is None", result)


# ---------------------------------------------------------------------------
# comparison-to-true
# ---------------------------------------------------------------------------

class TestFixComparisonToTrue(unittest.TestCase):
    def _apply(self, src, lineno):
        lines = _lines(src)
        ok, reason = _fix_comparison_to_true(lines, lineno)
        return ok, reason, "".join(line for line in lines if line is not None)

    def test_x_eq_true_becomes_x(self):
        ok, _, result = self._apply("if result == True:\n", 1)
        self.assertTrue(ok)
        self.assertIn("if result:", result)

    def test_x_neq_true_becomes_not_x(self):
        ok, _, result = self._apply("if result != True:\n", 1)
        self.assertTrue(ok)
        self.assertIn("if not result:", result)

    def test_x_eq_false_becomes_not_x(self):
        ok, _, result = self._apply("if result == False:\n", 1)
        self.assertTrue(ok)
        self.assertIn("if not result:", result)

    def test_x_neq_false_becomes_x(self):
        ok, _, result = self._apply("if result != False:\n", 1)
        self.assertTrue(ok)
        self.assertIn("if result:", result)

    def test_true_eq_x(self):
        ok, _, result = self._apply("if True == result:\n", 1)
        self.assertTrue(ok)
        self.assertIn("if result:", result)

    def test_false_eq_x(self):
        ok, _, result = self._apply("if False == result:\n", 1)
        self.assertTrue(ok)
        self.assertIn("if not result:", result)

    def test_dotted_attribute(self):
        ok, _, result = self._apply("if self.flag == True:\n", 1)
        self.assertTrue(ok)
        self.assertIn("if self.flag:", result)

    def test_complex_expression_skipped(self):
        ok, reason, _ = self._apply("if foo() == True:\n", 1)
        self.assertFalse(ok)
        self.assertIsNotNone(reason)

    def test_no_bool_comparison_unchanged(self):
        ok, _, _ = self._apply("if result:\n", 1)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# redundant-else
# ---------------------------------------------------------------------------

class TestFixRedundantElse(unittest.TestCase):
    def _apply(self, src, else_lineno):
        lines = _lines(src)
        ok = _fix_redundant_else(lines, else_lineno)
        return ok, "".join(line for line in lines if line is not None)

    def test_removes_else_and_dedents_body(self):
        src = textwrap.dedent("""\
            def f(x):
                if x:
                    return x
                else:
                    y = x + 1
                    return y
        """)
        ok, result = self._apply(src, 4)
        self.assertTrue(ok)
        self.assertNotIn("else:", result)
        self.assertIn("    y = x + 1\n", result)
        self.assertIn("    return y\n", result)

    def test_removes_else_with_nested_block(self):
        src = textwrap.dedent("""\
            def f(x):
                if x > 0:
                    return x
                else:
                    for i in range(x):
                        print(i)
                    return -1
        """)
        ok, result = self._apply(src, 4)
        self.assertTrue(ok)
        self.assertNotIn("else:", result)
        # for loop should now be at 4-space indent
        self.assertIn("    for i in range(x):\n", result)

    def test_blank_lines_in_else_preserved(self):
        src = textwrap.dedent("""\
            def f(x):
                if x:
                    return x
                else:
                    a = 1

                    b = 2
                    return b
        """)
        ok, result = self._apply(src, 4)
        self.assertTrue(ok)
        self.assertNotIn("else:", result)
        self.assertIn("    a = 1\n", result)
        self.assertIn("    b = 2\n", result)

    def test_single_statement_else(self):
        src = textwrap.dedent("""\
            def f(x):
                if x:
                    return 1
                else:
                    return 2
        """)
        ok, result = self._apply(src, 4)
        self.assertTrue(ok)
        self.assertNotIn("else:", result)
        self.assertIn("    return 2\n", result)

    def test_raise_in_if_branch(self):
        src = textwrap.dedent("""\
            def f(x):
                if not x:
                    raise ValueError
                else:
                    return x * 2
        """)
        ok, result = self._apply(src, 4)
        self.assertTrue(ok)
        self.assertNotIn("else:", result)

    def test_out_of_range_line(self):
        ok, _ = self._apply("def f():\n    pass\n", 99)
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# fix_issues (integration)
# ---------------------------------------------------------------------------

class TestFixIssues(unittest.TestCase):
    def _run(self, content, issues, dry_run=False, tmp_path=None):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "test.py")
            with open(p, "w") as f:
                f.write(content)
            # Make issues relative to root d
            adjusted = [{**i, "file": "test.py"} for i in issues]
            results = fix_issues(d, adjusted, dry_run=dry_run)
            if not dry_run:
                with open(p) as f:
                    new_content = f.read()
            else:
                new_content = results[0].new_text if results else content
        return results, new_content

    def test_trailing_whitespace_fixed(self):
        results, new = self._run(
            "x = 1   \n",
            [{"file": "test.py", "line": 1, "symbol": "trailing-whitespace"}],
        )
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].changed)
        self.assertEqual(new, "x = 1\n")

    def test_dry_run_does_not_write(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "test.py")
            with open(p, "w") as f:
                f.write("x = 1   \n")
            issues = [{"file": "test.py", "line": 1, "symbol": "trailing-whitespace"}]
            fix_issues(d, issues, dry_run=True)
            with open(p) as f:
                content = f.read()
        self.assertEqual(content, "x = 1   \n")  # unchanged

    def test_unknown_rules_ignored(self):
        results, new = self._run(
            "x = 1\n",
            [{"file": "test.py", "line": 1, "symbol": "some-unknown-rule"}],
        )
        self.assertEqual(results, [])

    def test_fstring_fixed(self):
        results, new = self._run(
            'x = f"hello"\n',
            [{"file": "test.py", "line": 1, "symbol": "f-string-no-placeholder"}],
        )
        self.assertNotIn('f"', new)

    def test_diff_available_in_dry_run(self):
        results, _ = self._run(
            "x = 1   \n",
            [{"file": "test.py", "line": 1, "symbol": "trailing-whitespace"}],
            dry_run=True,
        )
        self.assertIn("---", results[0].diff)
        self.assertIn("+++", results[0].diff)


class TestRenderText(unittest.TestCase):
    def test_no_results(self):
        self.assertIn("No fixable issues", render_text([]))

    def test_shows_applied(self):
        r = FixResult(path="f.py", applied=[AppliedFix(1, "trailing-whitespace")],
                      original_text="x   \n", new_text="x\n")
        text = render_text([r])
        self.assertIn("f.py:1", text)
        self.assertIn("trailing-whitespace", text)
        self.assertIn("Fixed 1", text)

    def test_shows_skipped(self):
        r = FixResult(path="f.py", skipped=[SkippedFix(3, "comparison-to-true", "too complex")],
                      original_text="x\n", new_text="x\n")
        text = render_text([r])
        self.assertIn("skip", text)
        self.assertIn("too complex", text)

    def test_dry_run_label(self):
        r = FixResult(path="f.py", applied=[AppliedFix(1, "trailing-whitespace")],
                      original_text="x   \n", new_text="x\n")
        text = render_text([r], dry_run=True)
        self.assertIn("Would fix", text)


class TestFixableRules(unittest.TestCase):
    def test_all_five_present(self):
        self.assertIn("trailing-whitespace", FIXABLE_RULES)
        self.assertIn("f-string-no-placeholder", FIXABLE_RULES)
        self.assertIn("comparison-to-none", FIXABLE_RULES)
        self.assertIn("comparison-to-true", FIXABLE_RULES)
        self.assertIn("redundant-else", FIXABLE_RULES)


if __name__ == "__main__":
    unittest.main()

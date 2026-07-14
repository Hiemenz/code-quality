import unittest

from codequality.analyzers.python_token_checks import implicit_string_concat_issues


def _issues(source, only_lines=None):
    return implicit_string_concat_issues(source, "f.py", only_lines)


class TestImplicitStringConcat(unittest.TestCase):
    def test_adjacent_strings_on_same_line_flagged(self):
        issues = _issues('"hello" "world"\n')
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "implicit-string-concat")

    def test_adjacent_strings_across_lines_in_list_flagged(self):
        # Inside [...] a missing comma is the likely bug -- flag it.
        src = '[\n    "hello"\n    "world"\n]\n'
        issues = _issues(src)
        self.assertEqual(len(issues), 1)

    def test_adjacent_strings_across_lines_in_parens_not_flagged(self):
        # Inside (...) across lines is intentional string splitting -- don't flag.
        src = '(\n    "hello"\n    "world"\n)\n'
        issues = _issues(src)
        self.assertEqual(issues, [])

    def test_explicit_plus_not_flagged(self):
        self.assertEqual(_issues('"hello" + "world"\n'), [])

    def test_single_string_not_flagged(self):
        self.assertEqual(_issues('"hello"\n'), [])

    def test_list_with_comma_not_flagged(self):
        self.assertEqual(_issues('["hello", "world"]\n'), [])

    def test_list_missing_comma_flagged(self):
        src = '[\n    "hello"\n    "world"\n]\n'
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertIn("comma", issues[0].message)

    def test_three_adjacent_strings_flagged_twice(self):
        issues = _issues('"a" "b" "c"\n')
        self.assertEqual(len(issues), 2)

    def test_only_lines_restricts(self):
        # "world" is on line 3 inside brackets -- a real missing-comma case
        src = '[\n    "hello"\n    "world"\n]\n'
        issues = _issues(src, only_lines={3})
        self.assertEqual(len(issues), 1)

    def test_only_lines_excludes(self):
        src = '"hello" "world"\n'
        issues = _issues(src, only_lines={99})
        self.assertEqual(issues, [])

    def test_f_strings_adjacent_flagged(self):
        issues = _issues('f"hello {x}" f"world {y}"\n')
        self.assertEqual(len(issues), 1)

    def test_comment_between_strings_breaks_adjacency(self):
        src = '"hello"  # a comment\n"world"\n'
        # Comment breaks the adjacency
        issues = _issues(src)
        self.assertEqual(issues, [])

    def test_invalid_syntax_returns_empty(self):
        self.assertEqual(_issues("def def\n"), [])


if __name__ == "__main__":
    unittest.main()

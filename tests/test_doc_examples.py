import unittest

from codequality.analyzers.doc_examples import check_markdown_source, extract_python_blocks


class TestDocExamples(unittest.TestCase):
    def test_valid_python_block_produces_no_issue(self):
        source = (
            "# Title\n"
            "\n"
            "```python\n"
            "def add(a, b):\n"
            "    return a + b\n"
            "```\n"
        )
        self.assertEqual(check_markdown_source("README.md", source), [])

    def test_broken_python_block_is_flagged_with_correct_line(self):
        # Mismatched parens: the fenced block starts at line 3 (line 1 is
        # the fence, so the first code line is line 4).
        source = (
            "# Title\n"
            "\n"
            "```python\n"
            "def broken(:\n"
            "    return 1\n"
            "```\n"
        )
        issues = check_markdown_source("README.md", source)
        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue.symbol, "broken-doc-example")
        self.assertEqual(issue.category, "documentation")
        self.assertEqual(issue.severity, "warn")
        self.assertEqual(issue.file, "README.md")
        self.assertEqual(issue.line, 4)
        self.assertIn("no longer parses", issue.message)

    def test_python2_only_syntax_is_flagged(self):
        source = (
            "```python\n"
            "print 'hello'\n"
            "```\n"
        )
        issues = check_markdown_source("doc.md", source)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "broken-doc-example")
        self.assertEqual(issues[0].line, 2)

    def test_non_python_fences_are_ignored(self):
        source = (
            "```bash\n"
            "echo (unbalanced\n"
            "```\n"
            "\n"
            "```json\n"
            "{not valid json or python either\n"
            "```\n"
        )
        self.assertEqual(check_markdown_source("README.md", source), [])

    def test_markdown_with_no_code_blocks_produces_nothing(self):
        source = "# Just some prose\n\nNo code here, just words about the project.\n"
        self.assertEqual(check_markdown_source("README.md", source), [])

    def test_multiple_blocks_checked_independently(self):
        source = (
            "```python\n"
            "x = 1 + 1\n"
            "```\n"
            "\n"
            "Some text in between.\n"
            "\n"
            "```python\n"
            "def broken(:\n"
            "```\n"
            "\n"
            "```python\n"
            "y = 2 + 2\n"
            "```\n"
        )
        issues = check_markdown_source("README.md", source)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 8)

    def test_py_fence_alias_is_recognized(self):
        source = "```py\nprint 'still python 2'\n```\n"
        issues = check_markdown_source("doc.md", source)
        self.assertEqual(len(issues), 1)

    def test_extract_python_blocks_tracks_start_line(self):
        source = "intro\n\n```python\na = 1\nb = 2\n```\n"
        blocks = extract_python_blocks(source)
        self.assertEqual(len(blocks), 1)
        start_line, code = blocks[0]
        self.assertEqual(start_line, 4)
        self.assertEqual(code, "a = 1\nb = 2")

    def test_empty_code_block_produces_no_issue(self):
        source = "```python\n```\n"
        self.assertEqual(check_markdown_source("README.md", source), [])


if __name__ == "__main__":
    unittest.main()

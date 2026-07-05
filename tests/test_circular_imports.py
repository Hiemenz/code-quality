import unittest

from codequality.analyzers.circular_imports import circular_import_issues


class TestCircularImports(unittest.TestCase):
    def test_two_file_cycle_detected_for_both_files(self):
        sources = {
            "a.py": "import b\n",
            "b.py": "import a\n",
        }
        issues = circular_import_issues(sources)
        self.assertEqual({i.file for i in issues}, {"a.py", "b.py"})
        for issue in issues:
            self.assertEqual(issue.symbol, "circular-import")
            self.assertEqual(issue.category, "structure")
            self.assertEqual(issue.severity, "warn")
            self.assertEqual(issue.line, 1)
            self.assertEqual(issue.message, "Circular import: a.py -> b.py -> a.py")

    def test_three_file_cycle_not_reported_redundantly(self):
        sources = {
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "import a\n",
        }
        issues = circular_import_issues(sources)
        # One issue per participating file...
        self.assertEqual({i.file for i in issues}, {"a.py", "b.py", "c.py"})
        self.assertEqual(len(issues), 3)
        # ...but every issue describes the same logical cycle, normalized
        # to start from the same (lexicographically-first) file, so it
        # doesn't read as three different cycles.
        messages = {i.message for i in issues}
        self.assertEqual(messages, {"Circular import: a.py -> b.py -> c.py -> a.py"})

    def test_one_directional_chain_is_not_a_cycle(self):
        sources = {
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "x = 1\n",
        }
        self.assertEqual(circular_import_issues(sources), [])

    def test_stdlib_and_third_party_imports_are_ignored(self):
        sources = {
            "a.py": "import os\nimport json\nfrom collections import OrderedDict\nimport requests\n",
        }
        # Should not crash trying to resolve these as internal modules, and
        # obviously can't form a cycle against files that aren't scanned.
        self.assertEqual(circular_import_issues(sources), [])

    def test_relative_import_cycle_is_resolved(self):
        sources = {
            "pkg/__init__.py": "",
            "pkg/a.py": "from . import b\n",
            "pkg/b.py": "from .a import x\n",
        }
        issues = circular_import_issues(sources)
        self.assertEqual({i.file for i in issues}, {"pkg/a.py", "pkg/b.py"})

    def test_relative_dotdot_import_cycle_is_resolved(self):
        sources = {
            "pkg/__init__.py": "",
            "pkg/b.py": "from .sub.a import y\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/a.py": "from .. import b\n",
        }
        issues = circular_import_issues(sources)
        self.assertEqual({i.file for i in issues}, {"pkg/b.py", "pkg/sub/a.py"})

    def test_no_imports_produces_nothing(self):
        sources = {"a.py": "x = 1\n", "b.py": "y = 2\n"}
        self.assertEqual(circular_import_issues(sources), [])

    def test_syntax_error_is_skipped_not_fatal(self):
        sources = {"a.py": "def f(:\n", "b.py": "import a\n"}
        self.assertEqual(circular_import_issues(sources), [])


if __name__ == "__main__":
    unittest.main()

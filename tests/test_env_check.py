import os
import tempfile
import unittest

from codequality import env_check


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


class TestUndocumentedEnvVar(unittest.TestCase):
    def test_getenv_with_no_env_example_flags_undocumented(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "app.py", 'import os\nkey = os.getenv("API_KEY")\n')
            issues = env_check.check(root)
            undoc = [i for i in issues if i.symbol == "undocumented-env-var"]
            self.assertEqual(len(undoc), 1)
            self.assertEqual(undoc[0].file, "app.py")
            self.assertIn("API_KEY", undoc[0].message)
            self.assertEqual(undoc[0].category, "documentation")
            self.assertEqual(undoc[0].severity, "info")

    def test_os_environ_subscript_detected(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "app.py", 'import os\nkey = os.environ["API_KEY"]\n')
            issues = env_check.check(root)
            undoc = [i for i in issues if i.symbol == "undocumented-env-var"]
            self.assertEqual(len(undoc), 1)
            self.assertEqual(undoc[0].line, 2)

    def test_os_environ_get_call_detected(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "app.py", 'import os\nkey = os.environ.get("API_KEY")\n')
            issues = env_check.check(root)
            undoc = [i for i in issues if i.symbol == "undocumented-env-var"]
            self.assertEqual(len(undoc), 1)


class TestUnusedDocumentedEnvVar(unittest.TestCase):
    def test_env_example_var_never_referenced_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, ".env.example", "UNUSED_VAR=\nOTHER_VAR=some_value\n")
            _write(root, "app.py", "print('hello')\n")
            issues = env_check.check(root)
            unused = [i for i in issues if i.symbol == "unused-documented-env-var"]
            names = {i.message.split()[0] for i in unused}
            self.assertIn("UNUSED_VAR", names)
            self.assertIn("OTHER_VAR", names)
            self.assertEqual(len(unused), 2)


class TestUsedAndDocumented(unittest.TestCase):
    def test_var_used_and_documented_produces_no_issue(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, ".env.example", "API_KEY=\n")
            _write(root, "app.py", 'import os\nkey = os.getenv("API_KEY")\n')
            issues = env_check.check(root)
            self.assertEqual(issues, [])


class TestNoUsageNoDocs(unittest.TestCase):
    def test_repo_with_neither_produces_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "app.py", "print('hello')\n")
            issues = env_check.check(root)
            self.assertEqual(issues, [])

    def test_empty_repo_does_not_crash(self):
        with tempfile.TemporaryDirectory() as root:
            issues = env_check.check(root)
            self.assertEqual(issues, [])


class TestReadmeDocumentedVars(unittest.TestCase):
    def test_fenced_env_block_in_readme_counts_as_documented(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "README.md",
                "# Project\n\n## Config\n\n```\nAPI_KEY=changeme\nDB_URL=postgres://localhost\n```\n",
            )
            _write(root, "app.py", 'import os\nkey = os.getenv("API_KEY")\n')
            issues = env_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "undocumented-env-var"], [])
            unused = [i for i in issues if i.symbol == "unused-documented-env-var"]
            self.assertEqual(len(unused), 1)
            self.assertIn("DB_URL", unused[0].message)

    def test_markdown_table_in_readme_counts_as_documented(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "README.md",
                "# Project\n\n"
                "| Environment Variable | Description |\n"
                "|---|---|\n"
                "| `API_KEY` | secret key |\n",
            )
            _write(root, "app.py", 'import os\nkey = os.getenv("API_KEY")\n')
            issues = env_check.check(root)
            self.assertEqual(issues, [])


class TestGenericLanguageFallback(unittest.TestCase):
    def test_js_process_env_detected_as_usage(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "app.js", "const key = process.env.API_KEY;\n")
            issues = env_check.check(root)
            undoc = [i for i in issues if i.symbol == "undocumented-env-var"]
            self.assertEqual(len(undoc), 1)
            self.assertEqual(undoc[0].file, "app.js")


if __name__ == "__main__":
    unittest.main()

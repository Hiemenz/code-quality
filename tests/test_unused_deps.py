import os
import tempfile
import unittest

from codequality.analyzers.unused_deps import unused_dependency_issues


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


class TestUnusedDeps(unittest.TestCase):
    def test_imported_package_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests==2.0\n")
            _write(root, "main.py", "import requests\nrequests.get('/')\n")
            results = unused_dependency_issues(root, {"main.py": "import requests\n"})
            issues = [i for lst in results.values() for i in lst]
            self.assertEqual(issues, [])

    def test_unimported_package_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests==2.0\n")
            results = unused_dependency_issues(root, {"main.py": "import os\n"})
            issues = [i for lst in results.values() for i in lst]
            self.assertEqual(len(issues), 1)
            self.assertEqual(issues[0].symbol, "unused-dependency")
            self.assertIn("requests", issues[0].message)

    def test_tool_packages_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "black==22.0\nflake8==5.0\nmypy==1.0\n")
            results = unused_dependency_issues(root, {"main.py": "x = 1\n"})
            issues = [i for lst in results.values() for i in lst]
            self.assertEqual(issues, [])

    def test_alias_package_matched(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "Pillow==9.0\n")
            results = unused_dependency_issues(root, {"main.py": "from PIL import Image\n"})
            issues = [i for lst in results.values() for i in lst]
            self.assertEqual(issues, [])

    def test_pyyaml_alias_matched(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "pyyaml==6.0\n")
            results = unused_dependency_issues(root, {"main.py": "import yaml\n"})
            issues = [i for lst in results.values() for i in lst]
            self.assertEqual(issues, [])

    def test_comment_lines_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "# production deps\nrequests==2.0\n")
            results = unused_dependency_issues(root, {"main.py": "import requests\n"})
            issues = [i for lst in results.values() for i in lst]
            self.assertEqual(issues, [])

    def test_vcs_url_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "git+https://github.com/foo/bar.git\n")
            results = unused_dependency_issues(root, {"main.py": "x = 1\n"})
            issues = [i for lst in results.values() for i in lst]
            self.assertEqual(issues, [])

    def test_no_requirements_file_returns_empty(self):
        with tempfile.TemporaryDirectory() as root:
            results = unused_dependency_issues(root, {"main.py": "import os\n"})
            self.assertEqual(results, {})

    def test_dash_underscore_normalization(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "python-dateutil==2.8\n")
            results = unused_dependency_issues(root, {"main.py": "import dateutil\n"})
            issues = [i for lst in results.values() for i in lst]
            self.assertEqual(issues, [])

    def test_findings_attached_to_requirements_file(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "boto3==1.0\n")
            results = unused_dependency_issues(root, {"main.py": "x = 1\n"})
            self.assertIn("requirements.txt", results)


if __name__ == "__main__":
    unittest.main()

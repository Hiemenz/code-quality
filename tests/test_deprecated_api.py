import ast
import unittest

from codequality.analyzers.deprecated_api import deprecated_api_issues


def _issues(source, only_lines=None):
    return deprecated_api_issues(ast.parse(source), "f.py", only_lines)


class TestDeprecatedApi(unittest.TestCase):
    def test_removed_module_import_is_flagged_warn(self):
        issues = _issues("import imp\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "deprecated-api")
        self.assertEqual(issues[0].category, "correctness")
        self.assertEqual(issues[0].severity, "warn")
        self.assertIn("importlib", issues[0].message)

    def test_from_import_matches_top_segment(self):
        issues = _issues("from distutils.core import setup\n")
        self.assertEqual(len(issues), 1)
        self.assertIn("distutils", issues[0].message)

    def test_dotted_import_matches_top_segment(self):
        issues = _issues("import distutils.core\n")
        self.assertEqual(len(issues), 1)

    def test_pkg_resources_is_info_not_warn(self):
        issues = _issues("import pkg_resources\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "info")

    def test_relative_import_is_ignored(self):
        self.assertEqual(_issues("from . import imp\n"), [])

    def test_utcnow_call_is_flagged(self):
        issues = _issues("from datetime import datetime\nts = datetime.utcnow()\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "info")
        self.assertIn("timezone.utc", issues[0].message)

    def test_fully_dotted_utcnow_is_flagged(self):
        issues = _issues("import datetime\nts = datetime.datetime.utcnow()\n")
        self.assertEqual(len(issues), 1)

    def test_utcnow_on_unrelated_receiver_is_not_flagged(self):
        self.assertEqual(_issues("ts = clock.utcnow()\n"), [])

    def test_ssl_wrap_socket_is_warn(self):
        issues = _issues("import ssl\ns = ssl.wrap_socket(sock)\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warn")

    def test_bare_reference_without_call_is_not_flagged(self):
        self.assertEqual(_issues("import ssl\nhandler = ssl.wrap_socket\n"), [])

    def test_unittest_alias_is_flagged_with_replacement(self):
        issues = _issues("class T(unittest.TestCase):\n    def test_x(self):\n        self.assertEquals(1, 2)\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warn")
        self.assertIn("assertEqual", issues[0].message)

    def test_modern_equivalents_are_not_flagged(self):
        source = (
            "import importlib\n"
            "from datetime import datetime, timezone\n"
            "ts = datetime.now(timezone.utc)\n"
            "self.assertEqual(1, 1)\n"
        )
        self.assertEqual(_issues(source), [])

    def test_only_lines_restricts_the_check(self):
        source = "import imp\nimport imp\n"
        issues = _issues(source, only_lines={2})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 2)


if __name__ == "__main__":
    unittest.main()

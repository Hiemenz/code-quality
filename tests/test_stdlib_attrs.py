import ast
import unittest

from codequality.analyzers.stdlib_attrs import stdlib_attribute_issues


def _issues(source, only_lines=None):
    return stdlib_attribute_issues(ast.parse(source), "f.py", only_lines)


class TestStdlibAttrs(unittest.TestCase):
    def test_hallucinated_os_path_function_is_flagged(self):
        issues = _issues("import os\nos.path.exists_dir('/tmp')\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "unresolved-attribute")
        self.assertEqual(issues[0].severity, "warn")
        self.assertIn("exists_dir", issues[0].message)

    def test_real_os_path_function_is_not_flagged(self):
        self.assertEqual(_issues("import os\nos.path.exists('/tmp')\n"), [])

    def test_hallucinated_from_import_is_flagged(self):
        issues = _issues("from json import dumpss\n")
        self.assertEqual(len(issues), 1)
        self.assertIn("dumpss", issues[0].message)

    def test_real_from_import_is_not_flagged(self):
        self.assertEqual(_issues("from json import dumps\n"), [])

    def test_from_import_of_submodule_is_not_flagged(self):
        self.assertEqual(_issues("from os import path\n"), [])

    def test_class_method_chain_is_verified(self):
        issues = _issues("import datetime\ndatetime.datetime.utcnowww()\n")
        self.assertEqual(len(issues), 1)
        self.assertIn("utcnowww", issues[0].message)

    def test_instance_attributes_stop_verification(self):
        # sys.stdout is an instance; whatever comes after it is not checked.
        self.assertEqual(_issues("import sys\nsys.stdout.wrrrite('x')\n"), [])

    def test_third_party_modules_are_ignored(self):
        self.assertEqual(_issues("import requests\nrequests.gettt('http://x')\n"), [])

    def test_shadowed_name_is_skipped(self):
        self.assertEqual(_issues("import os\nos = object()\nos.frobnicate()\n"), [])

    def test_monkeypatched_attribute_is_skipped(self):
        self.assertEqual(_issues("import os\nos.custom_thing = 1\nprint(os.custom_thing)\n"), [])

    def test_aliased_import_is_verified(self):
        issues = _issues("import json as j\nj.dumpss({})\n")
        self.assertEqual(len(issues), 1)

    def test_from_bound_submodule_is_verified(self):
        issues = _issues("from os import path\npath.existss('/tmp')\n")
        self.assertEqual(len(issues), 1)

    def test_star_import_from_stdlib_does_not_crash(self):
        self.assertEqual(_issues("from os.path import *\n"), [])

    def test_relative_import_is_ignored(self):
        self.assertEqual(_issues("from . import helpers\n"), [])

    def test_side_effect_modules_are_never_imported(self):
        # 'this' prints the Zen of Python at import time; the denylist must
        # keep it out even when the scanned code imports it.
        self.assertEqual(_issues("import this\nthis.nonsense()\n"), [])

    def test_deep_chain_flags_only_once(self):
        issues = _issues("import os\nx = os.path.exists_dir\n")
        self.assertEqual(len(issues), 1)

    def test_only_lines_restricts_the_check(self):
        source = "import os\nos.path.exists_dir('/a')\nos.path.exists_dir('/b')\n"
        issues = _issues(source, only_lines={3})
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 3)


if __name__ == "__main__":
    unittest.main()

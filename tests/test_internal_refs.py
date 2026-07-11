import unittest

from codequality.analyzers.internal_refs import internal_reference_issues


def _issues(file_sources):
    flat = []
    for issues in internal_reference_issues(file_sources).values():
        flat.extend(issues)
    return flat


class TestFromImport(unittest.TestCase):
    def test_missing_name_in_repo_module_is_flagged(self):
        issues = _issues({
            "utils.py": "def helper():\n    return 1\n",
            "app.py": "from utils import frobnicate\n",
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "unresolved-internal-import")
        self.assertEqual(issues[0].severity, "warn")
        self.assertEqual(issues[0].file, "app.py")

    def test_existing_name_is_not_flagged(self):
        issues = _issues({
            "utils.py": "def helper():\n    return 1\n",
            "app.py": "from utils import helper\n",
        })
        self.assertEqual(issues, [])

    def test_reexported_name_counts_as_defined(self):
        issues = _issues({
            "pkg/__init__.py": "from pkg.impl import helper\n",
            "pkg/impl.py": "def helper():\n    return 1\n",
            "app.py": "from pkg import helper\n",
        })
        self.assertEqual(issues, [])

    def test_importing_a_submodule_from_package_is_not_flagged(self):
        issues = _issues({
            "pkg/__init__.py": "",
            "pkg/impl.py": "def helper():\n    return 1\n",
            "app.py": "from pkg import impl\n",
        })
        self.assertEqual(issues, [])

    def test_conditionally_defined_name_counts(self):
        source = "try:\n    import fastjson as json\nexcept ImportError:\n    import json\n"
        issues = _issues({
            "compat.py": source,
            "app.py": "from compat import json\n",
        })
        self.assertEqual(issues, [])

    def test_star_importing_module_is_not_checkable(self):
        issues = _issues({
            "utils.py": "from os.path import *\n",
            "app.py": "from utils import join\n",
        })
        self.assertEqual(issues, [])

    def test_module_getattr_is_not_checkable(self):
        issues = _issues({
            "lazy.py": "def __getattr__(name):\n    return name\n",
            "app.py": "from lazy import anything\n",
        })
        self.assertEqual(issues, [])

    def test_relative_import_is_resolved(self):
        issues = _issues({
            "pkg/__init__.py": "",
            "pkg/a.py": "from .b import missing\n",
            "pkg/b.py": "def present():\n    return 1\n",
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].file, "pkg/a.py")

    def test_external_module_is_ignored(self):
        issues = _issues({"app.py": "from os.path import nonexistent_thing\n"})
        self.assertEqual(issues, [])

    def test_from_dot_import_of_existing_sibling_is_fine(self):
        issues = _issues({
            "pkg/__init__.py": "",
            "pkg/a.py": "from . import b\n",
            "pkg/b.py": "def go():\n    return 1\n",
        })
        self.assertEqual(issues, [])

    def test_from_dot_import_of_missing_sibling_is_flagged(self):
        issues = _issues({
            "pkg/__init__.py": "",
            "pkg/a.py": "from . import nope\n",
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "unresolved-internal-import")

    def test_syntax_error_file_neither_crashes_nor_verifies(self):
        issues = _issues({
            "broken.py": "def f(:\n",
            "app.py": "from broken import anything\n",
        })
        self.assertEqual(issues, [])

    def test_annassign_and_augassign_names_count_as_defined(self):
        issues = _issues({
            "consts.py": "LIMIT: int = 5\nTOTAL = 0\nTOTAL += 1\n",
            "app.py": "from consts import LIMIT, TOTAL\n",
        })
        self.assertEqual(issues, [])

    def test_scan_repo_integration(self):
        import os
        import shutil
        import tempfile
        from codequality.config import Config
        from codequality.scanner import scan_repo

        root = tempfile.mkdtemp(prefix="cq-iref-")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        with open(os.path.join(root, "utils.py"), "w") as f:
            f.write("def helper():\n    return 1\n")
        with open(os.path.join(root, "app.py"), "w") as f:
            f.write("from utils import frobnicate\n")
        config = Config.load(root, explicit_path=None, overrides={})
        metrics = scan_repo(root, config)
        symbols = {i.symbol for fm in metrics for i in fm.issues}
        self.assertIn("unresolved-internal-import", symbols)


class TestAttributeAccess(unittest.TestCase):
    def test_missing_attribute_is_flagged_info(self):
        issues = _issues({
            "utils.py": "def helper():\n    return 1\n",
            "app.py": "import utils\nutils.frobnicate()\n",
        })
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "unresolved-internal-attribute")
        self.assertEqual(issues[0].severity, "info")

    def test_existing_attribute_is_not_flagged(self):
        issues = _issues({
            "utils.py": "def helper():\n    return 1\n",
            "app.py": "import utils\nutils.helper()\n",
        })
        self.assertEqual(issues, [])

    def test_dotted_package_chain_resolves_to_submodule(self):
        issues = _issues({
            "pkg/__init__.py": "",
            "pkg/sub.py": "def go():\n    return 1\n",
            "app.py": "import pkg.sub\npkg.sub.go()\npkg.sub.gone()\n",
        })
        self.assertEqual(len(issues), 1)
        self.assertIn("gone", issues[0].message)

    def test_locally_assigned_attribute_is_not_flagged(self):
        issues = _issues({
            "utils.py": "def helper():\n    return 1\n",
            "app.py": "import utils\nutils.cache = {}\nprint(utils.cache)\n",
        })
        self.assertEqual(issues, [])

    def test_shadowed_name_is_skipped(self):
        issues = _issues({
            "utils.py": "def helper():\n    return 1\n",
            "app.py": "import utils\nutils = object()\nutils.frobnicate()\n",
        })
        self.assertEqual(issues, [])

    def test_aliased_import_is_tracked(self):
        issues = _issues({
            "utils.py": "def helper():\n    return 1\n",
            "app.py": "import utils as u\nu.frobnicate()\n",
        })
        self.assertEqual(len(issues), 1)

    def test_module_imported_via_from_is_tracked(self):
        issues = _issues({
            "pkg/__init__.py": "",
            "pkg/sub.py": "def go():\n    return 1\n",
            "app.py": "from pkg import sub\nsub.gone()\n",
        })
        self.assertEqual(len(issues), 1)


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest

from codequality import arch_conformance
from codequality.config import Config


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def _config(layers):
    return Config({"architecture": {"layers": layers}})


_LAYERS = [
    {"name": "api", "modules": ["myapp.api"]},
    {"name": "service", "modules": ["myapp.service"]},
    {"name": "data", "modules": ["myapp.data"]},
]


class TestNoConfig(unittest.TestCase):
    def test_no_layers_configured_returns_no_issues(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/data/models.py", "import myapp.service.orders\n")
            issues = arch_conformance.check(root, Config({}))
            self.assertEqual(issues, [])


class TestLayerViolations(unittest.TestCase):
    def test_lower_layer_importing_higher_layer_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/data/models.py", "import myapp.service.orders\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            found = [i for i in issues if i.symbol == "layering-violation"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].file, os.path.join("myapp", "data", "models.py"))
            self.assertIn("myapp.service.orders", found[0].message)

    def test_higher_layer_importing_lower_layer_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/api/views.py", "import myapp.service.orders\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            self.assertEqual([i for i in issues if i.symbol == "layering-violation"], [])

    def test_from_import_of_higher_layer_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/data/models.py", "from myapp.api import serializers\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            found = [i for i in issues if i.symbol == "layering-violation"]
            self.assertEqual(len(found), 1)

    def test_same_layer_import_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/service/orders.py", "import myapp.service.billing\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            self.assertEqual([i for i in issues if i.symbol == "layering-violation"], [])

    def test_relative_import_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/data/models.py", "from . import helpers\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            self.assertEqual([i for i in issues if i.symbol == "layering-violation"], [])

    def test_unclassified_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "scripts/one_off.py", "import myapp.service.orders\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            self.assertEqual(issues, [])

    def test_import_of_unclassified_module_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/data/models.py", "import requests\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            self.assertEqual([i for i in issues if i.symbol == "layering-violation"], [])

    def test_init_file_resolves_to_package_module_name(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/data/__init__.py", "import myapp.api.views\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            found = [i for i in issues if i.symbol == "layering-violation"]
            self.assertEqual(len(found), 1)


class TestRenderText(unittest.TestCase):
    def test_no_issues_renders_clean_message(self):
        self.assertIn("No issues found", arch_conformance.render_text([]))

    def test_issues_are_rendered_with_file_and_symbol(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "myapp/data/models.py", "import myapp.service.orders\n")
            issues = arch_conformance.check(root, _config(_LAYERS))
            text = arch_conformance.render_text(issues)
            self.assertIn("models.py", text)
            self.assertIn("layering-violation", text)


if __name__ == "__main__":
    unittest.main()

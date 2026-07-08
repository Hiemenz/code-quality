import os
import tempfile
import unittest

from codequality import config_drift


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


class TestNoConfigFilesRepo(unittest.TestCase):
    def test_repo_with_no_comparable_files_returns_no_issues(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "main.py", "print('hi')\n")
            self.assertEqual(config_drift.check(root), [])

    def test_single_env_file_returns_no_issues(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, ".env", "FOO=1\n")
            self.assertEqual(config_drift.check(root), [])


class TestRootEnvFiles(unittest.TestCase):
    def test_missing_key_in_one_env_file_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, ".env.example", "FOO=1\nBAR=2\n")
            _write(root, ".env.production", "FOO=1\n")
            issues = config_drift.check(root)
            found = [i for i in issues if i.symbol == "config-drift"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].file, ".env.production")
            self.assertIn("BAR", found[0].message)
            self.assertIn(".env.example", found[0].message)

    def test_identical_env_files_are_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, ".env.example", "FOO=1\nBAR=2\n")
            _write(root, ".env.production", "FOO=9\nBAR=8\n")
            self.assertEqual(config_drift.check(root), [])

    def test_envrc_is_not_treated_as_an_env_variant(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, ".env.example", "FOO=1\n")
            _write(root, ".envrc", "export PATH_ADD=./bin\nuse flake\n")
            self.assertEqual(config_drift.check(root), [])

    def test_export_prefixed_lines_are_parsed(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, ".env.example", "export FOO=1\nexport BAR=2\n")
            _write(root, ".env.production", "export FOO=1\n")
            issues = config_drift.check(root)
            self.assertEqual(len(issues), 1)
            self.assertIn("BAR", issues[0].message)


class TestConfigDirGroups(unittest.TestCase):
    def test_missing_key_across_yaml_siblings_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "config/dev.yaml", "feature_x: true\ntimeout: 5\n")
            _write(root, "config/production.yaml", "feature_x: true\n")
            issues = config_drift.check(root)
            found = [i for i in issues if i.symbol == "config-drift"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].file, os.path.join("config", "production.yaml"))
            self.assertIn("timeout", found[0].message)

    def test_nested_yaml_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "config/dev.yaml", "database:\n  host: localhost\n  port: 5432\n")
            _write(root, "config/production.yaml", "database:\n  host: prod-db\n")
            self.assertEqual(config_drift.check(root), [])

    def test_yaml_and_json_siblings_are_not_cross_compared(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "config/dev.yaml", "feature_x: true\n")
            _write(root, "config/base.json", '{"feature_y": true}\n')
            self.assertEqual(config_drift.check(root), [])

    def test_missing_key_across_json_siblings_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "config/dev.json", '{"a": 1, "b": 2}\n')
            _write(root, "config/prod.json", '{"a": 1}\n')
            issues = config_drift.check(root)
            found = [i for i in issues if i.symbol == "config-drift"]
            self.assertEqual(len(found), 1)
            self.assertIn("b", found[0].message)

    def test_invalid_json_sibling_is_skipped_not_crashed(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "config/dev.json", '{"a": 1}\n')
            _write(root, "config/broken.json", "not valid json\n")
            self.assertEqual(config_drift.check(root), [])

    def test_single_file_in_config_dir_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "config/dev.yaml", "feature_x: true\n")
            self.assertEqual(config_drift.check(root), [])


class TestRenderText(unittest.TestCase):
    def test_no_issues_renders_clean_message(self):
        text = config_drift.render_text([])
        self.assertIn("No issues found", text)

    def test_issues_are_rendered_with_file_and_symbol(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, ".env.example", "FOO=1\nBAR=2\n")
            _write(root, ".env.production", "FOO=1\n")
            issues = config_drift.check(root)
            text = config_drift.render_text(issues)
            self.assertIn(".env.production", text)
            self.assertIn("config-drift", text)


if __name__ == "__main__":
    unittest.main()

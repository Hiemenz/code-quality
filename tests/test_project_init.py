"""Tests for codequality.project_init."""

import os
import tempfile
import unittest

from codequality.project_init import init, render_text


class TestInit(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._td, ignore_errors=True)

    def test_creates_config_file(self):
        results = init(self._td)
        config = os.path.join(self._td, ".codequality.toml")
        self.assertTrue(os.path.isfile(config))
        statuses = [s for _, s in results]
        self.assertIn("created", statuses)

    def test_creates_workflow_file(self):
        init(self._td)
        wf = os.path.join(self._td, ".github", "workflows", "codequality.yml")
        self.assertTrue(os.path.isfile(wf))

    def test_config_contains_fail_under(self):
        init(self._td, fail_under=80)
        config_path = os.path.join(self._td, ".codequality.toml")
        with open(config_path) as f:
            content = f.read()
        self.assertIn("80", content)
        self.assertIn("fail_under", content)

    def test_workflow_contains_fail_under(self):
        init(self._td, fail_under=75)
        wf = os.path.join(self._td, ".github", "workflows", "codequality.yml")
        with open(wf) as f:
            content = f.read()
        self.assertIn("75", content)

    def test_existing_config_skipped(self):
        config_path = os.path.join(self._td, ".codequality.toml")
        with open(config_path, "w") as f:
            f.write("original\n")
        results = init(self._td)
        status_map = dict(results)
        self.assertEqual(status_map[config_path], "skipped")
        with open(config_path) as f:
            self.assertEqual(f.read(), "original\n")

    def test_existing_config_overwritten_with_force(self):
        config_path = os.path.join(self._td, ".codequality.toml")
        with open(config_path, "w") as f:
            f.write("original\n")
        results = init(self._td, force=True)
        status_map = dict(results)
        self.assertEqual(status_map[config_path], "overwritten")
        with open(config_path) as f:
            self.assertNotEqual(f.read(), "original\n")

    def test_config_only_skips_workflow(self):
        init(self._td, config_only=True)
        wf = os.path.join(self._td, ".github", "workflows", "codequality.yml")
        self.assertFalse(os.path.isfile(wf))

    def test_ci_only_skips_config(self):
        init(self._td, ci_only=True)
        config = os.path.join(self._td, ".codequality.toml")
        self.assertFalse(os.path.isfile(config))

    def test_creates_github_workflows_dir(self):
        init(self._td)
        wf_dir = os.path.join(self._td, ".github", "workflows")
        self.assertTrue(os.path.isdir(wf_dir))


class TestRenderText(unittest.TestCase):
    def test_created_status_shown(self):
        results = [("/repo/.codequality.toml", "created")]
        text = render_text(results, "/repo")
        self.assertIn("[+]", text)
        self.assertIn("created", text)

    def test_skipped_shown(self):
        results = [("/repo/.codequality.toml", "skipped")]
        text = render_text(results, "/repo")
        self.assertIn("[!]", text)
        self.assertIn("--force", text)

    def test_overwritten_shown(self):
        results = [("/repo/.codequality.toml", "overwritten")]
        text = render_text(results, "/repo")
        self.assertIn("[~]", text)

    def test_scan_hint_when_files_created(self):
        results = [("/repo/.codequality.toml", "created")]
        text = render_text(results, "/repo")
        self.assertIn("codequality scan", text)


if __name__ == "__main__":
    unittest.main()

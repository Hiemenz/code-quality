import os
import tempfile
import unittest

from codequality import orphaned_config


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


class TestNoConfigFilesRepo(unittest.TestCase):
    def test_repo_with_none_of_the_config_kinds_returns_no_issues(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "main.py", "print('hi')\n")
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])


class TestGithubActionsWorkflows(unittest.TestCase):
    def test_missing_script_referenced_via_leading_dot_slash_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, ".github/workflows/ci.yml",
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - name: Deploy\n"
                "        run: ./scripts/deploy.sh\n",
            )
            issues = orphaned_config.check(root)
            found = [i for i in issues if i.symbol == "orphaned-config-reference"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].file, os.path.join(".github", "workflows", "ci.yml"))
            self.assertIn("scripts/deploy.sh", found[0].message)
            self.assertEqual(found[0].category, "documentation")
            self.assertEqual(found[0].severity, "warn")

    def test_existing_script_referenced_via_interpreter_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "scripts/deploy.sh", "#!/bin/sh\necho hi\n")
            _write(
                root, ".github/workflows/ci.yml",
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - name: Deploy\n"
                "        run: bash scripts/deploy.sh\n",
            )
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])

    def test_block_scalar_run_step_is_scanned_line_by_line(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, ".github/workflows/ci.yml",
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - name: Multi-step\n"
                "        run: |\n"
                "          echo setup\n"
                "          ./scripts/missing.sh\n"
                "      - name: Next step\n"
                "        run: echo done\n",
            )
            issues = orphaned_config.check(root)
            found = [i for i in issues if i.symbol == "orphaned-config-reference"]
            self.assertEqual(len(found), 1)
            self.assertIn("scripts/missing.sh", found[0].message)
            self.assertEqual(found[0].line, 10)

    def test_ambiguous_bare_command_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, ".github/workflows/ci.yml",
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: pytest -q\n"
                "      - run: pip install .\n",
            )
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])

    def test_url_reference_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, ".github/workflows/ci.yml",
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: curl https://example.com/install.sh | bash\n",
            )
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])

    def test_variable_expansion_reference_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, ".github/workflows/ci.yml",
                "name: CI\n"
                "on: [push]\n"
                "jobs:\n"
                "  build:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: bash ${{ github.workspace }}/scripts/x.sh\n",
            )
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])


class TestDockerCompose(unittest.TestCase):
    def test_missing_dockerfile_via_context_and_dockerfile_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "frontend/placeholder.txt", "keep dir\n")
            _write(
                root, "docker-compose.yml",
                "services:\n"
                "  web:\n"
                "    build:\n"
                "      context: ./frontend\n"
                "      dockerfile: Dockerfile.prod\n",
            )
            issues = orphaned_config.check(root)
            found = [i for i in issues if i.symbol == "orphaned-config-reference"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].file, "docker-compose.yml")
            self.assertIn("Dockerfile.prod", found[0].message)

    def test_existing_dockerfile_via_context_and_dockerfile_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "frontend/Dockerfile.prod", "FROM scratch\n")
            _write(
                root, "docker-compose.yml",
                "services:\n"
                "  web:\n"
                "    build:\n"
                "      context: ./frontend\n"
                "      dockerfile: Dockerfile.prod\n",
            )
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])

    def test_build_shorthand_missing_context_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "docker-compose.yml",
                "services:\n"
                "  web:\n"
                "    build: ./missing-dir\n",
            )
            issues = orphaned_config.check(root)
            found = [i for i in issues if i.symbol == "orphaned-config-reference"]
            self.assertEqual(len(found), 1)
            self.assertIn("missing-dir", found[0].message)

    def test_missing_env_file_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "docker-compose.yml",
                "services:\n"
                "  web:\n"
                "    env_file: ./config/missing.env\n",
            )
            issues = orphaned_config.check(root)
            found = [i for i in issues if i.symbol == "orphaned-config-reference"]
            self.assertEqual(len(found), 1)
            self.assertIn("missing.env", found[0].message)

    def test_missing_bind_mount_volume_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "docker-compose.yml",
                "services:\n"
                "  web:\n"
                "    volumes:\n"
                "      - ./data:/var/lib/data\n",
            )
            issues = orphaned_config.check(root)
            found = [i for i in issues if i.symbol == "orphaned-config-reference"]
            self.assertEqual(len(found), 1)
            self.assertIn("./data", found[0].message)

    def test_named_volume_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "docker-compose.yml",
                "services:\n"
                "  web:\n"
                "    volumes:\n"
                "      - dbdata:/var/lib/data\n",
            )
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])


class TestMakefile(unittest.TestCase):
    def test_missing_script_invoked_from_recipe_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "Makefile",
                "deploy:\n"
                "\t./scripts/deploy.sh\n",
            )
            issues = orphaned_config.check(root)
            found = [i for i in issues if i.symbol == "orphaned-config-reference"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].file, "Makefile")
            self.assertIn("scripts/deploy.sh", found[0].message)

    def test_existing_script_invoked_from_recipe_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "scripts/deploy.sh", "#!/bin/sh\necho hi\n")
            _write(
                root, "Makefile",
                "deploy:\n"
                "\tbash scripts/deploy.sh\n",
            )
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])

    def test_silent_recipe_prefix_is_handled(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "Makefile",
                "deploy:\n"
                "\t@./scripts/missing.sh\n",
            )
            issues = orphaned_config.check(root)
            found = [i for i in issues if i.symbol == "orphaned-config-reference"]
            self.assertEqual(len(found), 1)
            self.assertIn("scripts/missing.sh", found[0].message)

    def test_bare_command_recipe_line_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "Makefile",
                "test:\n"
                "\tpytest -q\n",
            )
            issues = orphaned_config.check(root)
            self.assertEqual(issues, [])


class TestRenderText(unittest.TestCase):
    def test_no_issues_renders_clean_message(self):
        text = orphaned_config.render_text([])
        self.assertIn("No issues found", text)

    def test_issues_are_rendered_with_file_and_symbol(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "Makefile",
                "deploy:\n"
                "\t./scripts/deploy.sh\n",
            )
            issues = orphaned_config.check(root)
            text = orphaned_config.render_text(issues)
            self.assertIn("Makefile", text)
            self.assertIn("orphaned-config-reference", text)


if __name__ == "__main__":
    unittest.main()

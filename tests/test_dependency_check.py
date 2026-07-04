import os
import tempfile
import unittest

from codequality import dependency_check


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


class TestFindManifests(unittest.TestCase):
    def test_no_manifests_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(dependency_check.find_manifests(root), [])

    def test_finds_requirements_variants_and_pyproject_and_package_json(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests==2.0\n")
            _write(root, "requirements-dev.txt", "pytest==7.0\n")
            _write(root, "pyproject.toml", "[project]\nname='x'\n")
            _write(root, "package.json", "{}\n")
            found = dependency_check.find_manifests(root)
            self.assertIn("requirements.txt", found)
            self.assertIn("requirements-dev.txt", found)
            self.assertIn("pyproject.toml", found)
            self.assertIn("package.json", found)


class TestNoManifestsRepo(unittest.TestCase):
    def test_repo_with_no_manifests_returns_no_issues(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "main.py", "print('hi')\n")
            issues = dependency_check.check(root)
            self.assertEqual(issues, [])


class TestInconsistentPinning(unittest.TestCase):
    def test_mostly_pinned_requirements_flags_the_unpinned_minority(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "requirements.txt",
                "a==1.0\nb==1.0\nc==1.0\nd==1.0\ne>=1.0\n",
            )
            issues = dependency_check.check(root)
            pinning_issues = [i for i in issues if i.symbol == "inconsistent-pinning"]
            self.assertEqual(len(pinning_issues), 1)
            self.assertEqual(pinning_issues[0].file, "requirements.txt")
            self.assertIn("e", pinning_issues[0].message)

    def test_mostly_unpinned_requirements_flags_the_pinned_minority(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "requirements.txt",
                "a>=1.0\nb>=1.0\nc>=1.0\nd>=1.0\ne==1.0\n",
            )
            issues = dependency_check.check(root)
            pinning_issues = [i for i in issues if i.symbol == "inconsistent-pinning"]
            self.assertEqual(len(pinning_issues), 1)
            self.assertIn("e", pinning_issues[0].message)

    def test_small_manifest_below_threshold_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "a==1.0\nb>=1.0\n")
            issues = dependency_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "inconsistent-pinning"], [])

    def test_evenly_mixed_pinning_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "a==1.0\nb==1.0\nc==1.0\nd>=1.0\ne>=1.0\nf>=1.0\n")
            issues = dependency_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "inconsistent-pinning"], [])


class TestDuplicateDependency(unittest.TestCase):
    def test_same_package_pinned_differently_across_files_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests==2.0\n")
            _write(root, "requirements-dev.txt", "requests==3.0\n")
            issues = dependency_check.check(root)
            dupes = [i for i in issues if i.symbol == "duplicate-dependency"]
            self.assertEqual(len(dupes), 2)  # flagged once per file it appears in
            files = {i.file for i in dupes}
            self.assertEqual(files, {"requirements.txt", "requirements-dev.txt"})

    def test_same_package_same_spec_across_files_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests==2.0\n")
            _write(root, "requirements-dev.txt", "requests==2.0\n")
            issues = dependency_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "duplicate-dependency"], [])

    def test_package_declared_in_only_one_file_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests==2.0\n")
            issues = dependency_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "duplicate-dependency"], [])

    def test_name_normalization_matches_underscore_and_dash_variants(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "my_package==1.0\n")
            _write(root, "requirements-dev.txt", "my-package==2.0\n")
            issues = dependency_check.check(root)
            dupes = [i for i in issues if i.symbol == "duplicate-dependency"]
            self.assertEqual(len(dupes), 2)


class TestUnpinnedInLockfileRepo(unittest.TestCase):
    def test_bare_dependency_flagged_when_lockfile_present(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests\n")
            _write(root, "poetry.lock", "")
            issues = dependency_check.check(root)
            lock_issues = [i for i in issues if i.symbol == "unpinned-in-lockfile-repo"]
            self.assertEqual(len(lock_issues), 1)
            self.assertIn("requests", lock_issues[0].message)

    def test_bare_dependency_not_flagged_without_lockfile(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests\n")
            issues = dependency_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "unpinned-in-lockfile-repo"], [])

    def test_constrained_dependency_not_flagged_even_with_lockfile(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests>=2.0\n")
            _write(root, "package-lock.json", "{}")
            issues = dependency_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "unpinned-in-lockfile-repo"], [])


class TestPyprojectOnlyRepo(unittest.TestCase):
    def test_pyproject_dependencies_and_optional_dependencies_parse(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "pyproject.toml",
                "[project]\n"
                "name = 'demo'\n"
                "dependencies = [\"a==1.0\", \"b==1.0\", \"c==1.0\", \"d==1.0\", \"e>=1.0\"]\n"
                "\n"
                "[project.optional-dependencies]\n"
                "dev = [\"pytest==7.0\"]\n",
            )
            issues = dependency_check.check(root)
            pinning_issues = [i for i in issues if i.symbol == "inconsistent-pinning"]
            self.assertEqual(len(pinning_issues), 1)
            self.assertEqual(pinning_issues[0].file, "pyproject.toml")
            self.assertIn("e", pinning_issues[0].message)

    def test_pyproject_without_dependencies_key_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "pyproject.toml", "[project]\nname = 'demo'\n")
            issues = dependency_check.check(root)
            self.assertEqual(issues, [])


class TestPackageJson(unittest.TestCase):
    def test_dependencies_and_dev_dependencies_parse(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "package.json",
                '{"dependencies": {"left-pad": "1.0.0", "react": "^18.0.0"}, '
                '"devDependencies": {"jest": "~29.0.0"}}',
            )
            issues = dependency_check.check(root)
            # Nothing crashes and parsing succeeds even though no rule threshold is hit.
            self.assertIsInstance(issues, list)

    def test_malformed_package_json_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "package.json", "{not valid json")
            issues = dependency_check.check(root)
            self.assertEqual(issues, [])


class TestTomllibUnavailable(unittest.TestCase):
    """Mirrors codequality/config.py's own graceful-skip behavior when
    tomllib isn't available (Python < 3.11): pyproject.toml is simply
    skipped, not a crash.
    """

    def test_pyproject_is_skipped_without_raising(self):
        original = dependency_check.tomllib
        dependency_check.tomllib = None
        try:
            with tempfile.TemporaryDirectory() as root:
                _write(
                    root, "pyproject.toml",
                    "[project]\nname = 'demo'\ndependencies = [\"a==1.0\", \"b>=1.0\"]\n",
                )
                issues = dependency_check.check(root)
                self.assertEqual(issues, [])
        finally:
            dependency_check.tomllib = original


class TestRenderText(unittest.TestCase):
    def test_no_issues_renders_clean_message(self):
        text = dependency_check.render_text([])
        self.assertIn("No issues found", text)

    def test_issues_are_rendered_with_file_and_symbol(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "requirements.txt", "requests\n")
            _write(root, "poetry.lock", "")
            issues = dependency_check.check(root)
            text = dependency_check.render_text(issues)
            self.assertIn("requirements.txt", text)
            self.assertIn("unpinned-in-lockfile-repo", text)


if __name__ == "__main__":
    unittest.main()

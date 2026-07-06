import os
import tempfile
import unittest

from codequality import dependency_risk


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


def _build_fixture_repo(root):
    """A repo with:
    - `alpha`: heavily imported (5x) AND inconsistently pinned -> should
      rank highest (risk_score == usage_count, nonzero).
    - `beta`: heavily imported (4x) but cleanly/consistently declared ->
      should rank 0 despite heavy use.
    - `gamma`: barely imported (1x) but flagged by a pinning issue ->
      should rank low-but-nonzero.
    5 of 7 declared deps are exactly pinned (>= the 70% threshold
    dependency_check.PINNING_THRESHOLD requires), so the two unpinned
    outliers -- `alpha` and `gamma` -- both get flagged
    `inconsistent-pinning`; `beta` is pinned and never an outlier.
    """
    _write(
        root, "requirements.txt",
        "alpha>=1.0\n"
        "beta==2.0\n"
        "delta==1.0\n"
        "epsilon==1.0\n"
        "zeta==1.0\n"
        "eta==1.0\n"
        "gamma>=1.0\n",
    )
    for i in range(5):
        _write(root, f"pkg_alpha_{i}.py", "import alpha\n")
    for i in range(4):
        _write(root, f"pkg_beta_{i}.py", "from beta import thing\n")
    _write(root, "pkg_gamma.py", "import gamma.sub\n")


class TestComputeRanking(unittest.TestCase):
    def test_used_and_flagged_package_ranks_highest(self):
        with tempfile.TemporaryDirectory() as root:
            _build_fixture_repo(root)
            rows = dependency_risk.compute(root)
            by_name = {r["package"]: r for r in rows}

            alpha = by_name["alpha"]
            self.assertEqual(alpha["usage_count"], 5)
            self.assertIn("inconsistent-pinning", alpha["issue_types"])
            self.assertEqual(alpha["risk_score"], 5)
            self.assertEqual(rows[0]["package"], "alpha")

    def test_heavily_used_but_clean_package_ranks_zero(self):
        with tempfile.TemporaryDirectory() as root:
            _build_fixture_repo(root)
            rows = dependency_risk.compute(root)
            by_name = {r["package"]: r for r in rows}

            beta = by_name["beta"]
            self.assertEqual(beta["usage_count"], 4)
            self.assertEqual(beta["issue_types"], [])
            self.assertEqual(beta["risk_score"], 0)

    def test_barely_used_but_flagged_package_ranks_low_but_nonzero(self):
        with tempfile.TemporaryDirectory() as root:
            _build_fixture_repo(root)
            rows = dependency_risk.compute(root)
            by_name = {r["package"]: r for r in rows}

            gamma = by_name["gamma"]
            self.assertEqual(gamma["usage_count"], 1)
            self.assertIn("inconsistent-pinning", gamma["issue_types"])
            self.assertEqual(gamma["risk_score"], 1)

            # alpha (usage 5, flagged) must outrank gamma (usage 1, flagged).
            alpha_rank = next(i for i, r in enumerate(rows) if r["package"] == "alpha")
            gamma_rank = next(i for i, r in enumerate(rows) if r["package"] == "gamma")
            self.assertLess(alpha_rank, gamma_rank)

    def test_rows_sorted_by_risk_score_descending(self):
        with tempfile.TemporaryDirectory() as root:
            _build_fixture_repo(root)
            rows = dependency_risk.compute(root)
            scores = [r["risk_score"] for r in rows]
            self.assertEqual(scores, sorted(scores, reverse=True))


class TestNoManifestRepo(unittest.TestCase):
    def test_repo_with_no_manifest_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "main.py", "import os\n")
            rows = dependency_risk.compute(root)
            self.assertEqual(rows, [])


class TestImportCounting(unittest.TestCase):
    def test_counts_plain_import_and_from_import(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "a.py", "import requests\n")
            _write(root, "b.py", "from requests.auth import HTTPBasicAuth\n")
            _write(root, "c.py", "import requests.sessions\n")
            counts = dependency_risk.count_python_imports(root)
            self.assertEqual(counts["requests"], 3)

    def test_relative_imports_are_not_counted(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "pkg"))
            _write(root, "pkg/__init__.py", "")
            _write(root, "pkg/mod.py", "from . import sibling\nfrom ..other import thing\n")
            counts = dependency_risk.count_python_imports(root)
            self.assertEqual(sum(counts.values()), 0)

    def test_file_with_syntax_error_is_skipped_not_a_crash(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "broken.py", "def f(:\n")
            counts = dependency_risk.count_python_imports(root)
            self.assertEqual(counts, {})


class TestNpmPackagesNotUsageCounted(unittest.TestCase):
    def test_npm_only_dependency_has_zero_usage_count(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "package.json",
                '{"dependencies": {"left-pad": "1.0.0", "react": "1.0.0", "lodash": "1.0.0", '
                '"chalk": "1.0.0", "axios": "1.0.0", "moment": "^1.0.0"}}',
            )
            rows = dependency_risk.compute(root)
            by_name = {r["package"]: r for r in rows}
            self.assertEqual(by_name["moment"]["usage_count"], 0)
            self.assertEqual(by_name["moment"]["ecosystem"], "npm")


class TestRenderText(unittest.TestCase):
    def test_empty_rows_renders_no_dependencies_message(self):
        text = dependency_risk.render_text([])
        self.assertIn("No declared dependencies found", text)

    def test_rows_rendered_with_package_and_risk_score(self):
        with tempfile.TemporaryDirectory() as root:
            _build_fixture_repo(root)
            rows = dependency_risk.compute(root)
            text = dependency_risk.render_text(rows)
            self.assertIn("alpha", text)
            self.assertIn("inconsistent-pinning", text)
            self.assertIn("not staleness/CVE detection", text)

    def test_top_n_caps_rendered_rows(self):
        with tempfile.TemporaryDirectory() as root:
            _build_fixture_repo(root)
            rows = dependency_risk.compute(root)
            text = dependency_risk.render_text(rows, top_n=1)
            # only the single highest-ranked row's package name should appear
            # in the table body (rank 1 == alpha).
            lines = [ln for ln in text.splitlines() if ln.strip() and ln.strip()[0].isdigit()]
            self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()

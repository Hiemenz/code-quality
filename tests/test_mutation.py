import os
import tempfile
import unittest

from codequality import mutation


class TestMutationScore(unittest.TestCase):
    """Pure computation, no mutmut required."""

    def test_score_is_killed_over_total(self):
        self.assertEqual(mutation.mutation_score({"killed": 2, "total": 4}), 50.0)

    def test_score_is_none_when_no_mutants(self):
        self.assertIsNone(mutation.mutation_score({"killed": 0, "total": 0}))

    def test_render_text_handles_missing_mutants(self):
        text = mutation.render_text({"killed": 0, "survived": 0, "total": 0})
        self.assertIn("n/a", text)


class TestIsConfigured(unittest.TestCase):
    def test_missing_pyproject_is_not_configured(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(mutation.is_configured(root))

    def test_pyproject_without_mutmut_section_is_not_configured(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "pyproject.toml"), "w") as f:
                f.write("[tool.other]\nkey = 1\n")
            self.assertFalse(mutation.is_configured(root))

    def test_pyproject_with_mutmut_section_is_configured(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "pyproject.toml"), "w") as f:
                f.write('[tool.mutmut]\npaths_to_mutate = ["pkg"]\n')
            self.assertTrue(mutation.is_configured(root))


if __name__ == "__main__":
    unittest.main()

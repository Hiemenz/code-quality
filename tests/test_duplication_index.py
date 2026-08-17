import os
import tempfile
import unittest

from codequality.analyzers import duplication

_BLOCK = [f"line number {i} of a shared block" for i in range(6)]


class TestLoadSaveIndex(unittest.TestCase):
    def test_load_missing_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(duplication.load_index(os.path.join(root, "nope.json")), {})

    def test_load_corrupt_file_returns_empty_dict(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "index.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            self.assertEqual(duplication.load_index(path), {})

    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "sub", "index.json")
            index = {"blockkey": [["proj-a", "a.py", 0]]}
            duplication.save_index(path, index)
            self.assertEqual(duplication.load_index(path), index)

    def test_save_caps_at_max_entries(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "index.json")
            index = {f"k{i}": [["p", "a.py", i]] for i in range(10)}
            duplication.save_index(path, index, cap=5)
            self.assertEqual(len(duplication.load_index(path)), 5)


class TestFindCrossProjectDuplicates(unittest.TestCase):
    def test_no_persisted_index_finds_no_matches(self):
        file_lines = {"a.py": list(_BLOCK)}
        matches, updated = duplication.find_cross_project_duplicates(file_lines, {}, "proj-a")
        self.assertEqual(matches, {})
        self.assertTrue(updated)  # this project's own blocks got recorded

    def test_matches_block_from_a_different_project(self):
        with tempfile.TemporaryDirectory() as root:
            index_path = os.path.join(root, "index.json")

            file_lines_a = {"a.py": list(_BLOCK)}
            _, updated_a = duplication.find_cross_project_duplicates(file_lines_a, {}, "proj-a")
            duplication.save_index(index_path, updated_a)

            persisted = duplication.load_index(index_path)
            file_lines_b = {"b.py": list(_BLOCK)}
            matches, _ = duplication.find_cross_project_duplicates(file_lines_b, persisted, "proj-b")

            self.assertIn("b.py", matches)
            line_start, other_project, other_path = matches["b.py"][0]
            self.assertEqual(line_start, 0)
            self.assertEqual(other_project, "proj-a")
            self.assertEqual(other_path, "a.py")

    def test_same_project_id_is_not_a_cross_project_match(self):
        file_lines = {"a.py": list(_BLOCK), "b.py": list(_BLOCK)}
        _, updated = duplication.find_cross_project_duplicates(file_lines, {}, "proj-a")
        matches, _ = duplication.find_cross_project_duplicates(file_lines, updated, "proj-a")
        self.assertEqual(matches, {})

    def test_short_files_are_skipped(self):
        file_lines = {"a.py": ["one line"]}
        matches, updated = duplication.find_cross_project_duplicates(file_lines, {}, "proj-a")
        self.assertEqual(matches, {})
        self.assertEqual(updated, {})


class TestDefaultIndexPath(unittest.TestCase):
    def test_respects_xdg_cache_home(self):
        old = os.environ.get("XDG_CACHE_HOME")
        try:
            os.environ["XDG_CACHE_HOME"] = "/tmp/xdgtest"
            self.assertEqual(
                duplication.default_index_path(), "/tmp/xdgtest/codequality/duplication_index.json"
            )
        finally:
            if old is None:
                os.environ.pop("XDG_CACHE_HOME", None)
            else:
                os.environ["XDG_CACHE_HOME"] = old


if __name__ == "__main__":
    unittest.main()

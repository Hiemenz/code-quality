import unittest

from codequality.git_utils import parse_added_lines

SAMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index 1111111..2222222 100644
--- a/foo.py
+++ b/foo.py
@@ -10,2 +10,3 @@ def f():
-old line
+new line one
+new line two
@@ -30,0 +32,2 @@ def g():
+added line a
+added line b
diff --git a/bar.py b/bar.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/bar.py
@@ -0,0 +1,2 @@
+line 1
+line 2
diff --git a/removed.py b/removed.py
deleted file mode 100644
index 4444444..0000000
--- a/removed.py
+++ /dev/null
@@ -1,2 +0,0 @@
-gone 1
-gone 2
"""


class TestDiffParsing(unittest.TestCase):
    def test_parses_added_line_numbers_per_file(self):
        result = parse_added_lines(SAMPLE_DIFF)
        self.assertEqual(result["foo.py"], {10, 11, 32, 33})
        self.assertEqual(result["bar.py"], {1, 2})

    def test_deleted_file_is_not_present(self):
        result = parse_added_lines(SAMPLE_DIFF)
        self.assertNotIn("removed.py", result)

    def test_no_diff_returns_empty(self):
        self.assertEqual(parse_added_lines(""), {})

    def test_deterministic(self):
        results = [parse_added_lines(SAMPLE_DIFF) for _ in range(5)]
        self.assertTrue(all(r == results[0] for r in results))


if __name__ == "__main__":
    unittest.main()

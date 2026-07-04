import json
import os
import subprocess
import tempfile
import unittest

from codequality import complexity_trend
from codequality.config import Config


def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, check=True, capture_output=True, text=True)


def _write(cwd, path, content):
    with open(os.path.join(cwd, path), "w") as f:
        f.write(content)


class TestDiffReport(unittest.TestCase):
    """Unit tests against hand-built snapshot entries -- no git/scan
    involved, just the comparison/report logic.
    """

    def test_function_present_in_both_shows_correct_delta(self):
        first = {"timestamp": "2026-01-01T00:00:00+00:00", "commit": "aaa", "functions": {"a.py::f": 3}}
        last = {"timestamp": "2026-01-02T00:00:00+00:00", "commit": "bbb", "functions": {"a.py::f": 9}}

        rows = complexity_trend.diff_report([first, last])

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["name"], "a.py::f")
        self.assertEqual(row["first_complexity"], 3)
        self.assertEqual(row["last_complexity"], 9)
        self.assertEqual(row["delta"], 6)

    def test_function_only_in_one_snapshot_is_excluded(self):
        first = {"timestamp": "2026-01-01T00:00:00+00:00", "commit": "aaa", "functions": {"a.py::f": 3}}
        last = {
            "timestamp": "2026-01-02T00:00:00+00:00", "commit": "bbb",
            "functions": {"a.py::f": 9, "a.py::only_new": 5},
        }

        rows = complexity_trend.diff_report([first, last])

        names = [r["name"] for r in rows]
        self.assertIn("a.py::f", names)
        self.assertNotIn("a.py::only_new", names)

    def test_snapshots_are_order_independent(self):
        """diff_report must sort by timestamp itself, not trust file/list
        order -- passing the newer snapshot first should give the same
        result as passing it last.
        """
        older = {"timestamp": "2026-01-01T00:00:00+00:00", "commit": "aaa", "functions": {"a.py::f": 2}}
        newer = {"timestamp": "2026-01-05T00:00:00+00:00", "commit": "bbb", "functions": {"a.py::f": 8}}

        rows_forward = complexity_trend.diff_report([older, newer])
        rows_backward = complexity_trend.diff_report([newer, older])

        self.assertEqual(rows_forward, rows_backward)
        self.assertEqual(rows_forward[0]["delta"], 6)

    def test_sorted_by_biggest_increase_first(self):
        first = {
            "timestamp": "2026-01-01T00:00:00+00:00", "commit": "aaa",
            "functions": {"a.py::small_increase": 4, "a.py::big_increase": 2, "a.py::decrease": 10},
        }
        last = {
            "timestamp": "2026-01-02T00:00:00+00:00", "commit": "bbb",
            "functions": {"a.py::small_increase": 5, "a.py::big_increase": 15, "a.py::decrease": 3},
        }

        rows = complexity_trend.diff_report([first, last])

        self.assertEqual([r["name"] for r in rows], ["a.py::big_increase", "a.py::small_increase", "a.py::decrease"])

    def test_top_n_truncates(self):
        first = {"timestamp": "t1", "commit": None, "functions": {f"a.py::f{i}": 1 for i in range(5)}}
        last = {"timestamp": "t2", "commit": None, "functions": {f"a.py::f{i}": i + 2 for i in range(5)}}

        rows = complexity_trend.diff_report([first, last], top_n=2)

        self.assertEqual(len(rows), 2)

    def test_empty_snapshot_list_produces_empty_report(self):
        self.assertEqual(complexity_trend.diff_report([]), [])

    def test_single_snapshot_produces_empty_report_not_a_crash(self):
        only = {"timestamp": "2026-01-01T00:00:00+00:00", "commit": "aaa", "functions": {"a.py::f": 3}}
        self.assertEqual(complexity_trend.diff_report([only]), [])


class TestRenderText(unittest.TestCase):
    def test_empty_report_renders_a_message_not_a_crash(self):
        text = complexity_trend.render_text([])
        self.assertIn("2 snapshots", text)

    def test_nonempty_report_renders_name_and_delta(self):
        rows = [{"name": "a.py::f", "first_complexity": 3, "last_complexity": 9, "delta": 6}]
        text = complexity_trend.render_text(rows)
        self.assertIn("a.py::f", text)
        self.assertIn("+6", text)


class TestSnapshotAppendRead(unittest.TestCase):
    """append_snapshot/read_snapshots round-trip, plus a real snapshot()
    call against a tiny git repo to make sure the scan-integration wiring
    (qualified names, git sha) actually works end to end.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        _git(["init", "-q"], self.repo)
        _git(["config", "user.email", "t@example.com"], self.repo)
        _git(["config", "user.name", "T"], self.repo)

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_and_read_round_trip(self):
        path = os.path.join(self.repo, "snap.jsonl")
        entry1 = {"timestamp": "t1", "commit": "aaa", "functions": {"a.py::f": 1}}
        entry2 = {"timestamp": "t2", "commit": "bbb", "functions": {"a.py::f": 2}}

        complexity_trend.append_snapshot(path, entry1)
        complexity_trend.append_snapshot(path, entry2)

        snapshots = complexity_trend.read_snapshots(path)
        self.assertEqual(snapshots, [entry1, entry2])

    def test_append_writes_one_json_line_per_call(self):
        path = os.path.join(self.repo, "snap.jsonl")
        complexity_trend.append_snapshot(path, {"timestamp": "t1", "commit": None, "functions": {}})
        with open(path) as f:
            lines = f.readlines()
        self.assertEqual(len(lines), 1)
        json.loads(lines[0])  # must be valid JSON on its own

    def test_snapshot_records_function_complexity_and_commit_sha(self):
        _write(
            self.repo, "a.py",
            "def simple():\n    return 1\n\n\n"
            "def branchy(x):\n"
            "    if x:\n"
            "        return 1\n"
            "    elif x == 2:\n"
            "        return 2\n"
            "    else:\n"
            "        return 3\n",
        )
        _git(["add", "."], self.repo)
        _git(["commit", "-q", "-m", "initial"], self.repo)

        config = Config.load(self.repo)
        entry = complexity_trend.snapshot(self.repo, config)

        self.assertIn("a.py::simple", entry["functions"])
        self.assertIn("a.py::branchy", entry["functions"])
        self.assertGreater(entry["functions"]["a.py::branchy"], entry["functions"]["a.py::simple"])
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(entry["commit"], sha)

    def test_two_real_snapshots_show_increasing_complexity(self):
        _write(self.repo, "a.py", "def f(x):\n    return x\n")
        _git(["add", "."], self.repo)
        _git(["commit", "-q", "-m", "initial"], self.repo)
        config = Config.load(self.repo)
        snap_path = os.path.join(self.repo, "snap.jsonl")

        entry1 = complexity_trend.snapshot(self.repo, config)
        complexity_trend.append_snapshot(snap_path, entry1)

        _write(
            self.repo, "a.py",
            "def f(x):\n"
            "    if x > 0:\n"
            "        if x > 10:\n"
            "            return 'big'\n"
            "        return 'small positive'\n"
            "    elif x < 0:\n"
            "        return 'negative'\n"
            "    return 'zero'\n",
        )
        _git(["add", "."], self.repo)
        _git(["commit", "-q", "-m", "add branches"], self.repo)

        entry2 = complexity_trend.snapshot(self.repo, config)
        complexity_trend.append_snapshot(snap_path, entry2)

        snapshots = complexity_trend.read_snapshots(snap_path)
        rows = complexity_trend.diff_report(snapshots)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "a.py::f")
        self.assertGreater(rows[0]["delta"], 0)


if __name__ == "__main__":
    unittest.main()

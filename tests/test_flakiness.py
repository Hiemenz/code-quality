"""Tests for `codequality flakiness`.

Most of this is tested without ever spawning a real subprocess: the
parsing functions are unit-tested against canned sample output strings
(the hard, format-specific part), and the flip-detection/aggregation
logic is unit-tested against pre-parsed per-run result dicts directly (no
real test suite needed to prove "does the comparison logic notice a
result that differs across runs").

`run()` itself is tested with `subprocess.run` mocked out (same
convention as `tests/test_llm_judge.py`'s `mock.patch.object` use for the
other subprocess/network-touching optional feature), so we can feed it
canned per-run output without actually invoking a test runner.

One real, non-mocked integration test drives an actual `python -m
unittest discover -s tests -v` subprocess against a tiny generated test
suite that is genuinely (but deterministically) flaky -- a test that
reads a counter file and alternates pass/fail on every invocation, rather
than anything based on real timing/randomness, so this test itself can't
flake in CI.
"""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from codequality import flakiness
from codequality.cli import main


class TestParseUnittestOutput(unittest.TestCase):
    def test_all_passing(self):
        text = (
            "test_a (pkg.mod.T.test_a) ... ok\n"
            "test_b (pkg.mod.T.test_b) ... ok\n"
        )
        self.assertEqual(
            flakiness.parse_unittest_output(text),
            {"pkg.mod.T.test_a": "pass", "pkg.mod.T.test_b": "pass"},
        )

    def test_fail_and_error_and_skip(self):
        text = (
            "test_a (pkg.mod.T.test_a) ... FAIL\n"
            "test_b (pkg.mod.T.test_b) ... ERROR\n"
            "test_c (pkg.mod.T.test_c) ... skipped 'not ready'\n"
        )
        result = flakiness.parse_unittest_output(text)
        self.assertEqual(result["pkg.mod.T.test_a"], "fail")
        self.assertEqual(result["pkg.mod.T.test_b"], "error")
        self.assertEqual(result["pkg.mod.T.test_c"], "skip")

    def test_ignores_traceback_and_summary_lines(self):
        text = (
            "test_a (pkg.mod.T.test_a) ... FAIL\n"
            "\n"
            "======================================================================\n"
            "FAIL: test_a (pkg.mod.T.test_a)\n"
            "----------------------------------------------------------------------\n"
            "Traceback (most recent call last):\n"
            '  File "pkg/mod.py", line 3, in test_a\n'
            "    self.assertTrue(False)\n"
            "AssertionError\n"
            "\n"
            "----------------------------------------------------------------------\n"
            "Ran 1 test in 0.001s\n"
            "\n"
            "FAILED (failures=1)\n"
        )
        self.assertEqual(flakiness.parse_unittest_output(text), {"pkg.mod.T.test_a": "fail"})

    def test_pre_311_paren_form_without_repeated_name(self):
        """Older unittest verbose output doesn't repeat the test name inside
        the parens -- still needs a usable, unique-enough test id.
        """
        text = "test_a (pkg.mod.T) ... ok\n"
        self.assertEqual(flakiness.parse_unittest_output(text), {"pkg.mod.T.test_a": "pass"})


class TestParsePytestOutput(unittest.TestCase):
    def test_passed_and_failed(self):
        text = (
            "tests/test_bar.py::TestBar::test_foo PASSED\n"
            "tests/test_bar.py::TestBar::test_baz FAILED\n"
        )
        result = flakiness.parse_pytest_output(text)
        self.assertEqual(result["tests/test_bar.py::TestBar::test_foo"], "pass")
        self.assertEqual(result["tests/test_bar.py::TestBar::test_baz"], "fail")

    def test_progress_suffix_is_ignored(self):
        text = "tests/test_bar.py::test_foo PASSED [ 50%]\n"
        result = flakiness.parse_pytest_output(text)
        self.assertEqual(result["tests/test_bar.py::test_foo"], "pass")


class TestParseTestOutput(unittest.TestCase):
    def test_falls_back_when_nothing_recognized(self):
        results, parsed = flakiness.parse_test_output("some custom runner output\nOK: 3 passed\n")
        self.assertFalse(parsed)
        self.assertEqual(results, {})

    def test_prefers_unittest_format_when_present(self):
        results, parsed = flakiness.parse_test_output("test_a (pkg.T.test_a) ... ok\n")
        self.assertTrue(parsed)
        self.assertEqual(results, {"pkg.T.test_a": "pass"})


class TestFindFlaky(unittest.TestCase):
    def test_stable_results_are_not_flaky(self):
        runs = [{"t1": "pass", "t2": "fail"}] * 5
        self.assertEqual(flakiness.find_flaky(runs), {})

    def test_a_test_that_flips_is_flaky(self):
        runs = [
            {"t1": "pass", "t2": "pass"},
            {"t1": "fail", "t2": "pass"},
            {"t1": "pass", "t2": "pass"},
        ]
        flaky = flakiness.find_flaky(runs)
        self.assertEqual(set(flaky), {"t1"})
        self.assertEqual(flaky["t1"]["statuses"], ["pass", "fail", "pass"])
        self.assertEqual(flaky["t1"]["flips"], 2)

    def test_missing_from_a_run_counts_as_a_distinct_status(self):
        runs = [{"t1": "pass"}, {}, {"t1": "pass"}]
        flaky = flakiness.find_flaky(runs)
        self.assertIn("t1", flaky)
        self.assertEqual(flaky["t1"]["statuses"], ["pass", "missing", "pass"])
        self.assertEqual(flaky["t1"]["flips"], 2)

    def test_no_tests_at_all_is_not_flaky(self):
        self.assertEqual(flakiness.find_flaky([{}, {}]), {})


class TestBuildCommand(unittest.TestCase):
    def test_unittest_gets_verbose_flag_appended(self):
        cmd = flakiness._build_command("unittest discover -s tests")
        self.assertEqual(cmd[1:], ["-m", "unittest", "discover", "-s", "tests", "-v"])

    def test_unittest_verbose_flag_not_duplicated(self):
        cmd = flakiness._build_command("unittest discover -s tests -v")
        self.assertEqual(cmd.count("-v"), 1)

    def test_pytest_gets_verbose_flag_appended(self):
        cmd = flakiness._build_command("pytest -q tests")
        self.assertIn("-v", cmd)

    def test_unrecognized_runner_is_left_alone(self):
        cmd = flakiness._build_command("nose2")
        self.assertEqual(cmd[1:], ["-m", "nose2"])


class TestRunWithMockedSubprocess(unittest.TestCase):
    """`subprocess.run` is mocked so these exercise `run()`'s orchestration
    (looping N times, building overall stats, calling into find_flaky)
    without needing a real test suite on disk.
    """

    def _fake_completed(self, stderr, returncode=0):
        proc = mock.Mock()
        proc.stdout = ""
        proc.stderr = stderr
        proc.returncode = returncode
        return proc

    def test_stable_suite_reports_zero_flaky(self):
        stable_output = "test_a (pkg.T.test_a) ... ok\ntest_b (pkg.T.test_b) ... ok\n"
        with mock.patch.object(flakiness.subprocess, "run", return_value=self._fake_completed(stable_output)):
            result = flakiness.run("/fake/root", runs=3)
        self.assertTrue(result["parsed"])
        self.assertEqual(result["runs"], 3)
        self.assertEqual(result["tests_seen"], 2)
        self.assertEqual(result["flaky"], {})

    def test_flipping_suite_is_reported_as_flaky(self):
        outputs = [
            "test_a (pkg.T.test_a) ... ok\n",
            "test_a (pkg.T.test_a) ... FAIL\n",
            "test_a (pkg.T.test_a) ... ok\n",
        ]
        procs = [self._fake_completed(o, returncode=(0 if "FAIL" not in o else 1)) for o in outputs]
        with mock.patch.object(flakiness.subprocess, "run", side_effect=procs):
            result = flakiness.run("/fake/root", runs=3)
        self.assertIn("pkg.T.test_a", result["flaky"])
        self.assertEqual(result["flaky"]["pkg.T.test_a"]["statuses"], ["pass", "fail", "pass"])
        self.assertEqual([o["passed"] for o in result["overall"]], [True, False, True])

    def test_unparseable_output_falls_back_to_overall_only(self):
        with mock.patch.object(
            flakiness.subprocess, "run", return_value=self._fake_completed("custom runner: 3 ok\n")
        ):
            result = flakiness.run("/fake/root", runs=2)
        self.assertFalse(result["parsed"])
        self.assertEqual(result["tests_seen"], 0)
        self.assertEqual(result["flaky"], {})
        self.assertEqual(len(result["overall"]), 2)

    def test_default_test_command_used_when_none_given(self):
        with mock.patch.object(flakiness.subprocess, "run", return_value=self._fake_completed("")) as run_mock:
            flakiness.run("/fake/root", runs=1)
        called_cmd = run_mock.call_args.args[0]
        self.assertIn("unittest", called_cmd)
        self.assertIn("discover", called_cmd)


class TestRenderText(unittest.TestCase):
    def test_clean_summary(self):
        result = {"runs": 5, "parsed": True, "overall": [], "tests_seen": 10, "flaky": {}}
        text = flakiness.render_text(result)
        self.assertIn("10 tests, 5 runs, 0 flaky", text)

    def test_flaky_summary_lists_each_test(self):
        result = {
            "runs": 3,
            "parsed": True,
            "overall": [],
            "tests_seen": 2,
            "flaky": {"pkg.T.test_a": {"statuses": ["pass", "fail", "pass"], "flips": 2}},
        }
        text = flakiness.render_text(result)
        self.assertIn("1 flaky", text)
        self.assertIn("pkg.T.test_a", text)
        self.assertIn("2 flip", text)

    def test_unparsed_fallback_message(self):
        result = {
            "runs": 2,
            "parsed": False,
            "overall": [{"passed": True, "returncode": 0}, {"passed": False, "returncode": 1}],
            "tests_seen": 0,
            "flaky": {},
        }
        text = flakiness.render_text(result)
        self.assertIn("Could not parse", text)
        self.assertIn("1/2", text)


class TestCliWiring(unittest.TestCase):
    """Confirms the subcommand is wired up end to end, with flakiness.run
    itself mocked out so this doesn't spawn a real test suite.
    """

    def _run(self, args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(args)
        return code, buf.getvalue()

    def test_flakiness_subcommand_renders_text(self):
        fake_result = {"runs": 5, "parsed": True, "overall": [], "tests_seen": 3, "flaky": {}}
        with mock.patch.object(flakiness, "run", return_value=fake_result) as run_mock:
            code, out = self._run(["flakiness", "."])
        self.assertEqual(code, 0)
        self.assertIn("3 tests, 5 runs, 0 flaky", out)
        self.assertEqual(run_mock.call_args.kwargs["runs"], 5)

    def test_flakiness_subcommand_accepts_runs_and_test_command_and_json(self):
        fake_result = {"runs": 8, "parsed": True, "overall": [], "tests_seen": 1, "flaky": {}}
        with mock.patch.object(flakiness, "run", return_value=fake_result) as run_mock:
            code, out = self._run(
                ["flakiness", ".", "--runs", "8", "--test-command", "pytest -q", "--format", "json"]
            )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["runs"], 8)
        run_mock.assert_called_once()
        self.assertEqual(run_mock.call_args.kwargs["test_command"], "pytest -q")
        self.assertEqual(run_mock.call_args.kwargs["runs"], 8)


_ALT_TEST_SOURCE = '''
import json
import os
import unittest

COUNTER_FILE = os.path.join(os.path.dirname(__file__), "counter.json")


def _next_count():
    if os.path.exists(COUNTER_FILE):
        with open(COUNTER_FILE) as f:
            n = json.load(f)
    else:
        n = 0
    n += 1
    with open(COUNTER_FILE, "w") as f:
        json.dump(n, f)
    return n


class AlternatingTest(unittest.TestCase):
    def test_alternates(self):
        # Deterministically alternates pass/fail across successive process
        # invocations -- genuinely flaky across `flakiness.run()`'s reruns,
        # but never flaky *in CI* since it isn't based on timing/randomness.
        n = _next_count()
        self.assertEqual(n % 2, 0)

    def test_stable(self):
        self.assertTrue(True)
'''


class TestRunIntegration(unittest.TestCase):
    """The one real (non-mocked) subprocess-driving test: a genuinely
    flaky-but-deterministic tiny suite, run for real via `python -m
    unittest discover -s tests -v`.
    """

    def test_detects_a_genuinely_flaky_test(self):
        with tempfile.TemporaryDirectory() as root:
            tests_dir = os.path.join(root, "tests")
            os.makedirs(tests_dir)
            open(os.path.join(tests_dir, "__init__.py"), "w").close()
            with open(os.path.join(tests_dir, "test_alt.py"), "w") as f:
                f.write(_ALT_TEST_SOURCE)

            result = flakiness.run(root, runs=4)

        self.assertTrue(result["parsed"])
        self.assertEqual(result["tests_seen"], 2)

        flaky_names = {test_id.rsplit(".", 1)[-1] for test_id in result["flaky"]}
        self.assertIn("test_alternates", flaky_names)
        self.assertNotIn("test_stable", flaky_names)


if __name__ == "__main__":
    unittest.main()

"""Flaky-test detection: reruns the target repo's own test suite N times
and diffs each test's pass/fail/error result across runs. No judgment
involved -- just repeated execution and comparison, so unlike the
`--llm-review` feature elsewhere in this tool, this stays fully
deterministic in method (though not necessarily in *outcome*, since a
genuinely flaky test's result can differ run to run by definition).

Like `coverage_check.py`, this is the "run the target repo's own code"
trust boundary: it shells out to the repo's own test command (never
through a shell -- split into argv the same way `coverage_check.py` and
`mutation.py` do) `runs` times and parses each run's verbose per-test
output. That's why it's its own explicit subcommand (`codequality
flakiness`) rather than folded into `scan`/`diff`.

Parsing per-test results is necessarily best-effort: there is no single
machine-readable format shared by every Python test runner. This module
recognizes two output shapes well enough to extract a per-test-id
pass/fail/error/skip result:

- `python -m unittest ... -v` verbose output (this repo's own
  convention, and `coverage_check.DEFAULT_TEST_COMMAND`'s default), e.g.:
      test_foo (tests.test_bar.TestBar.test_foo) ... ok
      test_baz (tests.test_bar.TestBar.test_baz) ... FAIL
      test_slow (tests.test_bar.TestBar.test_slow) ... skipped 'reason'
  Note unittest's verbose per-test lines go to *stderr*, not stdout.
- `pytest -v` verbose output, e.g.:
      tests/test_bar.py::TestBar::test_foo PASSED
      tests/test_bar.py::TestBar::test_baz FAILED

`-v`/`--verbose` is appended automatically when the command's first
token is recognizably `unittest` or `pytest` and it isn't already
present. Any other runner (nose2, a custom Makefile wrapper, ...)
degrades gracefully: per-test results can't be extracted, so `run()`
reports `"parsed": False` plus only the overall pass/fail per run,
rather than crashing or fabricating per-test data. Writing a fully
general test-output parser for every runner in existence is out of
scope here -- this is meant to work well for `unittest discover`/pytest
and fail safe (not silently wrong) for anything else.
"""

import re
import subprocess
import sys

DEFAULT_TEST_COMMAND = "unittest discover -s tests"
DEFAULT_RUNS = 5

# "test_foo (pkg.mod.Cls.test_foo) ... ok" / "... FAIL" / "... ERROR" /
# "... skipped 'reason'" / "... expected failure" / "... unexpected success"
# Also matches the pre-3.11 form where the parens don't repeat the test
# name ("test_foo (pkg.mod.Cls) ... ok").
_UNITTEST_LINE = re.compile(
    r"^(?P<name>\S+)\s+\((?P<path>[\w.]+)\)\s*.*?\.\.\.\s*"
    r"(?P<status>ok|FAIL|ERROR|skipped(?:\s+'.*')?|expected failure|unexpected success)\s*$"
)

# "tests/test_bar.py::TestBar::test_foo PASSED" (optionally followed by a
# "[ 12%]" progress suffix, which we don't care about).
_PYTEST_LINE = re.compile(r"^(?P<nodeid>\S+::\S+)\s+(?P<status>PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)\b")

_UNITTEST_STATUS_MAP = {
    "ok": "pass",
    "FAIL": "fail",
    "ERROR": "error",
    "skipped": "skip",
    "expected failure": "pass",
    "unexpected success": "fail",
}
_PYTEST_STATUS_MAP = {
    "PASSED": "pass",
    "FAILED": "fail",
    "ERROR": "error",
    "SKIPPED": "skip",
    "XFAIL": "pass",
    "XPASS": "fail",
}


def _build_command(test_command):
    """Split `test_command` the same way coverage_check.py/mutation.py do
    (no shell=True) and prefix it with `python -m`, adding a verbose flag
    when the runner is recognizable so per-test parsing has a chance.
    """
    parts = test_command.split()
    if not parts:
        return [sys.executable, "-m"]
    if parts[0] == "unittest" and "-v" not in parts and "--verbose" not in parts:
        parts = parts + ["-v"]
    elif parts[0] == "pytest" and "-v" not in parts and "--verbose" not in parts:
        parts = parts + ["-v"]
    return [sys.executable, "-m"] + parts


def parse_unittest_output(text):
    """Parse `python -m unittest -v` output (stderr, normally) into
    {test_id: "pass"|"fail"|"error"|"skip"}. Lines that don't match
    (tracebacks, the summary line, ...) are ignored.
    """
    results = {}
    for line in text.splitlines():
        m = _UNITTEST_LINE.match(line)
        if not m:
            continue
        name, path, status = m.group("name"), m.group("path"), m.group("status")
        test_id = path if path.endswith("." + name) else f"{path}.{name}"
        key = "skipped" if status.startswith("skipped") else status
        results[test_id] = _UNITTEST_STATUS_MAP.get(key, "unknown")
    return results


def parse_pytest_output(text):
    """Parse `pytest -v` output into {test_id: "pass"|"fail"|"error"|"skip"}."""
    results = {}
    for line in text.splitlines():
        m = _PYTEST_LINE.match(line)
        if not m:
            continue
        results[m.group("nodeid")] = _PYTEST_STATUS_MAP.get(m.group("status"), "unknown")
    return results


def parse_test_output(text):
    """Try both known formats against one run's combined stdout+stderr.
    Returns (results, parsed) -- `parsed` is False when neither format
    produced anything, signalling the caller to fall back to
    overall-pass/fail-only reporting.
    """
    unittest_results = parse_unittest_output(text)
    if unittest_results:
        return unittest_results, True
    pytest_results = parse_pytest_output(text)
    if pytest_results:
        return pytest_results, True
    return {}, False


def find_flaky(per_run_results):
    """`per_run_results`: a list of {test_id: status} dicts, one per run
    (as produced by `parse_test_output`). Returns {test_id: {"statuses":
    [...], "flips": N}} for every test whose status differs across at
    least two of the runs -- a test missing from a run's results (e.g. it
    didn't exist yet, or the run crashed before reaching it) is treated as
    a distinct "missing" status, since that's itself a form of
    instability worth surfacing.
    """
    all_ids = set()
    for r in per_run_results:
        all_ids.update(r)

    flaky = {}
    for test_id in sorted(all_ids):
        statuses = [r.get(test_id, "missing") for r in per_run_results]
        if len(set(statuses)) > 1:
            flips = sum(1 for i in range(1, len(statuses)) if statuses[i] != statuses[i - 1])
            flaky[test_id] = {"statuses": statuses, "flips": flips}
    return flaky


def run(root, test_command=None, runs=DEFAULT_RUNS):
    """Run the repo's own test suite `runs` times and diff per-test
    results across runs.

    Returns:
        {
          "runs": int,
          "parsed": bool,        # could per-test results be extracted at all
          "overall": [{"passed": bool, "returncode": int}, ...],  # one per run
          "tests_seen": int,     # distinct test ids seen (0 if not parsed)
          "flaky": {test_id: {"statuses": [...], "flips": int}, ...},
        }
    """
    test_command = test_command or DEFAULT_TEST_COMMAND
    cmd = _build_command(test_command)

    per_run_results = []
    overall = []
    for _ in range(runs):
        result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        parsed_results, was_parsed = parse_test_output(combined)
        per_run_results.append(parsed_results if was_parsed else {})
        overall.append({"passed": result.returncode == 0, "returncode": result.returncode})

    parsed = any(per_run_results)
    if not parsed:
        return {"runs": runs, "parsed": False, "overall": overall, "tests_seen": 0, "flaky": {}}

    tests_seen = len({test_id for r in per_run_results for test_id in r})
    return {
        "runs": runs,
        "parsed": True,
        "overall": overall,
        "tests_seen": tests_seen,
        "flaky": find_flaky(per_run_results),
    }


def render_text(result):
    """Render the dict from `run()` as a short human-readable summary."""
    lines = ["Flakiness Check", ""]
    lines.append(f"  Runs: {result['runs']}")

    if not result["parsed"]:
        passed = sum(1 for o in result["overall"] if o["passed"])
        lines.append(
            "  Could not parse per-test results from the test command's output "
            "(unrecognized test runner format) -- reporting overall run status only."
        )
        lines.append(f"  Overall: {passed}/{result['runs']} run(s) fully passed")
        return "\n".join(lines)

    flaky = result["flaky"]
    if not flaky:
        lines.append(f"  {result['tests_seen']} tests, {result['runs']} runs, 0 flaky")
        return "\n".join(lines)

    lines.append(f"  {result['tests_seen']} tests, {result['runs']} runs, {len(flaky)} flaky")
    lines.append("")
    for test_id, info in sorted(flaky.items()):
        lines.append(f"  FLAKY  {test_id}  ({info['flips']} flip(s): {' -> '.join(info['statuses'])})")
    return "\n".join(lines)

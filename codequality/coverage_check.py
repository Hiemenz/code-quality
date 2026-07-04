"""Test coverage as a real trust signal: did anything actually exercise
this code? Wraps `coverage.py`, running the target repo's own test suite
in a subprocess -- there's no way to know whether code is covered without
running something against it, so unlike every other check in this tool,
this one executes the target repo's code.

Optional (`pip install codequality[coverage]`) and opt-in (--check-coverage)
for exactly that reason, plus it depends on knowing how to run the repo's
tests, which only the caller knows -- see --test-command in the README.
"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile

# Invoked via subprocess (`python -m coverage ...`) below, not through its
# Python API in-process -- find_spec just confirms the extra is installed
# without importing it, the same check used for --check-imports.
AVAILABLE = importlib.util.find_spec("coverage") is not None

DEFAULT_TEST_COMMAND = "unittest discover -s tests"


def _run_tests_under_coverage(root, test_command, data_file):
    cmd = [sys.executable, "-m", "coverage", "run", "--branch", f"--data-file={data_file}", "-m"]
    cmd += test_command.split()
    subprocess.run(cmd, cwd=root, capture_output=True, text=True)


def _write_json_report(root, data_file, json_file):
    cmd = [sys.executable, "-m", "coverage", "json", f"--data-file={data_file}", "-o", json_file, "-i"]
    result = subprocess.run(cmd, cwd=root, capture_output=True, text=True)
    return result.returncode == 0 and os.path.isfile(json_file)


def run(root, test_command=DEFAULT_TEST_COMMAND):
    """Run the repo's own test suite under coverage.

    Returns dict[relative_path] -> {"covered": set(line numbers),
    "missing": set(line numbers)}, or None if no report could be produced
    (the extra isn't installed, or the test command itself failed to
    produce results).
    """
    if not AVAILABLE:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        data_file = os.path.join(tmp, ".coverage")
        json_file = os.path.join(tmp, "coverage.json")
        _run_tests_under_coverage(root, test_command, data_file)
        if not _write_json_report(root, data_file, json_file):
            return None
        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

    return {
        rel_path: {"covered": set(info["executed_lines"]), "missing": set(info["missing_lines"])}
        for rel_path, info in data.get("files", {}).items()
    }


def ratio(lines, only_lines=None):
    """Executed/executable ratio (0.0-1.0, or None if there's nothing to
    measure) from a {"covered", "missing"} entry, restricted to
    `only_lines` when given -- that's how diff mode measures "patch
    coverage" (just the lines that changed) instead of whole-file coverage.
    """
    covered, missing = lines["covered"], lines["missing"]
    if only_lines is not None:
        covered = covered & only_lines
        missing = missing & only_lines
    total = len(covered) + len(missing)
    return len(covered) / total if total else None

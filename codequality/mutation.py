"""Wraps `mutmut` for real mutation testing: does the test suite actually
notice when the code's behavior changes? A low kill rate means the tests
are theater -- they run the code without asserting anything that would
catch a behavior change, which is exactly the failure mode of narrow,
example-based tests that check "did it run" rather than "is the answer
right" (a well-documented weak spot in LLM-written test suites).

Optional (`pip install codequality[mutation]`) and always a separate,
explicit command (`codequality mutation`), never folded into `scan` --
mutmut reruns the target repo's test suite once per mutant, so even a
modest codebase can take minutes. mutmut 3.x's configuration lives
entirely in the target repo's pyproject.toml (`[tool.mutmut]`); this
module deliberately never writes to that file on the caller's behalf --
if it's missing, it reports setup instructions instead of guessing.
"""

import importlib.util
import json
import os
import subprocess
import sys

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    tomllib = None

AVAILABLE = importlib.util.find_spec("mutmut") is not None

SETUP_HINT = """No [tool.mutmut] section found in pyproject.toml. mutmut needs to know
what to mutate and how to run your tests; add something like:

    [tool.mutmut]
    paths_to_mutate = ["your_package"]
    runner = "python -m pytest"

then re-run `codequality mutation`."""


def is_configured(root):
    path = os.path.join(root, "pyproject.toml")
    if tomllib is None or not os.path.isfile(path):
        return False
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return "mutmut" in data.get("tool", {})


def run(root):
    """Runs mutmut over `root` (which must already have [tool.mutmut]
    configured) and returns the kill-rate stats dict, or None if no
    results could be produced.
    """
    if not AVAILABLE:
        return None
    subprocess.run([sys.executable, "-m", "mutmut", "run"], cwd=root, capture_output=True, text=True)
    result = subprocess.run(
        [sys.executable, "-m", "mutmut", "export-cicd-stats"], cwd=root, capture_output=True, text=True
    )
    stats_path = os.path.join(root, "mutants", "mutmut-cicd-stats.json")
    if result.returncode != 0 or not os.path.isfile(stats_path):
        return None
    with open(stats_path, "r", encoding="utf-8") as f:
        return json.load(f)


def mutation_score(stats):
    total = stats.get("total", 0)
    return stats["killed"] / total * 100 if total else None


def render_text(stats):
    """Render mutmut's `export-cicd-stats` dict as a short kill-rate summary."""
    score = mutation_score(stats)
    score_line = f"{score:.1f}%" if score is not None else "n/a (no mutants generated)"
    return (
        "Mutation Testing\n\n"
        f"  Killed:   {stats.get('killed', 0)}\n"
        f"  Survived: {stats.get('survived', 0)}\n"
        f"  Total:    {stats.get('total', 0)}\n"
        f"  Score:    {score_line}"
    )

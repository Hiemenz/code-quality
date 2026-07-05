"""Complexity x test-presence risk: cross per-file complexity with whether
a file has *any* corresponding test file at all -- a different pairing than
`codequality hotspots` (complexity x change frequency, see
`codequality/hotspots.py`), but the same underlying idea: complexity alone
is an incomplete risk signal, and crossing it with a second axis produces a
much better prioritized to-do list than either number alone.

A highly complex file with zero test coverage of any kind is a much
stronger risk signal than complexity alone would suggest -- nobody has so
much as written a test file for it, let alone exercised every branch. A
simple, low-complexity file with no test is comparatively low-risk: there's
not much for a missing test to be protecting against. And a complex file
that *does* have a matching test file is, for the purposes of this
specific check, no longer the priority -- something is at least nominally
guarding it, even though this check can't tell you how good that test is.

This is deliberately **structural test presence**, not actual line
coverage: it never runs anything, so it can't tell you the test that
exists actually exercises the risky branches -- that's what
`--check-coverage` is for (see the README's "Test coverage" section), and
that check is opt-in specifically because it executes the target repo's
own code, a different trust boundary than everything else in this tool.
This feature stays in the same no-execution category as
`hotspots`/`dependency-check`/`ownership`: it only reads file paths and
parses source, never runs a test suite.

Two numbers already computed elsewhere are recombined here:

- **`complexity`** -- the same "max cyclomatic complexity of any function
  in the file" convention `hotspots.py` uses (see that module's docstring
  for the full reasoning): one deeply-tangled function is the real risk in
  a file regardless of how many trivial helpers share it, and max isn't
  diluted by file size the way an average would be. A file with no
  functions at all scores 0 and can never be a risk, no matter how
  untested it is -- there's nothing complex to protect.
- **`has_test`** -- whether *some* file in the scanned set looks like a
  test for this one, by filename convention only: `foo.py` counts as
  tested if `test_foo.py` or `foo_test.py` exists anywhere in the scanned
  repo (same `test_*`/`*_test` naming convention
  `codequality.property_scaffold.is_test_file` already uses, reused here
  rather than redefined). This is deliberately a blunt, purely-by-stem
  check -- no import graph, no "does the test actually import this
  module," same "simple, heuristic, expect some noise" tradeoff every
  other check in this tool makes (see e.g. `dead-code`'s and
  `dependency-check`'s module docstrings). False negatives are possible
  (a test file with an unconventional name) and false positives are
  possible (`test_foo.py` exists but tests something else entirely); this
  is a cheap first-pass signal, not a guarantee.

Files that are themselves test files (again via
`property_scaffold.is_test_file`, which additionally treats anything under
a `tests/`/`test/` directory as a test regardless of its own name) are
skipped entirely -- a test file doesn't need a test of its own, and
including it would just be noise in a report about what to test next.

`risk_score` is `complexity` for a file with no matching test, and `0` for
a file that has one -- chosen over `None` so the score sorts and formats
like every other numeric column here without special-casing; `has_test`
is reported as its own boolean column right alongside it precisely so the
`0` is never ambiguous with "measured zero risk" (there's no such case:
every file with `complexity == 0` also can never rank, since it has no
functions to be risky in the first place). Both raw signals stay visible
next to the composite score -- the same "auditable, not a black box"
convention as `hotspots.py`/`scorer.py`.
"""

import os

from codequality import property_scaffold
from codequality.scanner import scan_repo


def _file_complexity(fm):
    """Max cyclomatic complexity of any function in this file (same
    convention as `hotspots._file_complexity`); 0 for a file with no
    functions.
    """
    if not fm.functions:
        return 0
    return max(fn.complexity for fn in fm.functions)


def _test_name_stem(rel_path):
    """If `rel_path`'s basename follows the `test_foo.py`/`foo_test.py`
    naming convention, return the stem it's testing (`foo`); otherwise
    None. Extension-agnostic on purpose -- this is a filename-stem check,
    not a same-language requirement.
    """
    base = os.path.basename(rel_path)
    name, _ext = os.path.splitext(base)
    if name.startswith("test_"):
        return name[len("test_"):]
    if name.endswith("_test"):
        return name[: -len("_test")]
    return None


def _tested_stems(all_rel_paths):
    """Set of stems that have a `test_<stem>`/`<stem>_test` file anywhere
    in the scanned set, regardless of directory -- purely a filename-stem
    correspondence, no path/import matching (see module docstring).
    """
    stems = set()
    for rel_path in all_rel_paths:
        stem = _test_name_stem(rel_path)
        if stem:
            stems.add(stem)
    return stems


def compute(root, config):
    """Run a full scan and return a list of {file, complexity, has_test,
    risk_score} for every non-test source file, sorted by risk_score
    descending (ties broken by file path for determinism). Test files
    themselves (per `property_scaffold.is_test_file`) are excluded -- see
    module docstring.
    """
    file_metrics = scan_repo(root, config)
    all_paths = [fm.path for fm in file_metrics]
    tested_stems = _tested_stems(all_paths)

    rows = []
    for fm in file_metrics:
        if property_scaffold.is_test_file(fm.path):
            continue
        complexity = _file_complexity(fm)
        stem, _ext = os.path.splitext(os.path.basename(fm.path))
        has_test = stem in tested_stems
        risk_score = 0 if has_test else complexity
        rows.append({
            "file": fm.path,
            "complexity": complexity,
            "has_test": has_test,
            "risk_score": risk_score,
        })
    rows.sort(key=lambda r: (-r["risk_score"], r["file"]))
    return rows


def render_text(rows, top_n=25):
    """Render the top `top_n` rows from `compute()` as a rank/file/
    complexity/has_test/risk_score table. Files with complexity 0 (no
    functions at all -- never a risk, see module docstring) are dropped
    before ranking/capping, so a repo full of trivial no-function files
    doesn't crowd out the files actually worth showing.
    """
    shown = [r for r in rows if r["complexity"] > 0][:top_n]
    if not shown:
        return "No files found."
    lines = ["Complexity x Test Presence Risk (what to test first)", ""]
    lines.append(f"  {'#':>4}  {'File':<60}{'Complexity':>12}{'Has Test':>10}{'Risk':>8}")
    for i, r in enumerate(shown, start=1):
        lines.append(
            f"  {i:>4}  {r['file']:<60}{r['complexity']:>12}{str(r['has_test']):>10}{r['risk_score']:>8}"
        )
    return "\n".join(lines)

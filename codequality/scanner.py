"""File discovery and orchestration: walks the repo (or a restricted set
of paths for diff mode), dispatches each file to the right analyzer, and
runs duplicate-block detection across the resulting set.
"""

import fnmatch
import os

from codequality import coverage_check, generated_code, git_utils, suppress, typecheck
from codequality.analyzers import (
    circular_imports, complexity_regression, dead_code, doc_examples, duplication, generic_analyzer, internal_refs,
    python_analyzer, scope_check, signature_diff, treesitter_analyzer, unused_deps,
)
from codequality.analyzers.base import FileMetrics, Issue
from codequality.config import DEFAULT_IGNORE_DIRS, GENERIC_EXTENSIONS, PYTHON_EXTENSIONS
from codequality.property_scaffold import is_test_file


def _is_excluded(rel_path, patterns):
    return any(fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(os.path.basename(rel_path), pat) for pat in patterns)


def discover_files(root, exclude_patterns, include_generic=True):
    """Walk `root` and return a sorted list of (relative_path, language) for supported files."""
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.endswith(".egg-info")]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            ext = os.path.splitext(fn)[1]
            if ext in PYTHON_EXTENSIONS:
                lang = "python"
            elif include_generic and ext in GENERIC_EXTENSIONS:
                lang = GENERIC_EXTENSIONS[ext]
            else:
                continue
            if _is_excluded(rel, exclude_patterns):
                continue
            results.append((rel, lang))
    return sorted(results)


def _read_source(root, rel_path):
    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _run_analyzer(rel_path, source, language, config, only_lines):
    if language == "python":
        return python_analyzer.analyze(
            rel_path, source, config.limits, only_lines=only_lines, check_imports=config.check_imports
        )
    if treesitter_analyzer.AVAILABLE and language in treesitter_analyzer.LANGUAGES:
        return treesitter_analyzer.analyze(rel_path, source, language, config.limits, only_lines=only_lines)
    return generic_analyzer.analyze(rel_path, source, language, config.limits, only_lines=only_lines)


def analyze_file(root, rel_path, language, config, only_lines=None):
    """Dispatch to the right analyzer for `language`: Python's `ast`-based one,
    tree-sitter if the optional dependency is installed and supports this
    language, or the line-heuristic fallback otherwise. Applies inline
    `codequality: ignore` suppression to the result either way.

    Returns `None` (same as an unreadable file) for an auto-detected
    generated file, unless `config.include_generated` opts back in -- see
    `codequality/generated_code.py`. This is the one place both `scan_repo`
    and `scan_changed` funnel through, so the exclusion applies identically
    to a full scan and a diff.
    """
    source = _read_source(root, rel_path)
    if source is None:
        return None
    if not config.include_generated and generated_code.is_generated(rel_path, source):
        return None
    fm = _run_analyzer(rel_path, source, language, config, only_lines)
    suppressions = suppress.parse(source)
    fm.issues, fm.suppressed_count = suppress.filter_issues(fm.issues, suppressions)
    suppress.annotate_functions(fm.functions, suppressions)
    return fm


def _apply_duplication(root, file_metrics_by_path):
    file_lines = {}
    for rel_path, fm in file_metrics_by_path.items():
        source = _read_source(root, rel_path)
        if source is not None:
            file_lines[rel_path] = source.splitlines()
    dup_map = duplication.find_duplicate_lines(file_lines)
    for rel_path, idx_set in dup_map.items():
        fm = file_metrics_by_path.get(rel_path)
        if fm is not None:
            fm.duplicate_lines = len(idx_set)


def _python_file_sources(root, metrics_by_path):
    file_sources = {}
    for rel_path, fm in metrics_by_path.items():
        if fm.language != "python":
            continue
        source = _read_source(root, rel_path)
        if source is not None:
            file_sources[rel_path] = source
    return file_sources


def _apply_circular_imports(root, metrics_by_path):
    """Whole-graph property, but scoped to whatever's actually in
    `metrics_by_path` -- same shape as `_apply_duplication`. In `diff` mode
    this only sees the changed files, so a cycle only surfaces there if
    every file in it happens to be part of the change; that's an accepted
    narrowing (duplication is scoped the same way) rather than re-walking
    the whole repo on every diff.
    """
    file_sources = _python_file_sources(root, metrics_by_path)
    for issue in circular_imports.circular_import_issues(file_sources):
        fm = metrics_by_path.get(issue.file)
        if fm is not None:
            fm.issues.append(issue)


def _apply_dead_code(root, metrics_by_path):
    """Cross-file dead-code detection: needs every Python file's source at
    once to know whether a top-level function/class is referenced
    *anywhere* in the repo, so -- like duplication -- this only makes
    sense on a full scan, never a diff (a diff has no view of the rest of
    the repo to check references against).
    """
    file_sources = _python_file_sources(root, metrics_by_path)
    for rel_path, issues in dead_code.find_dead_code(file_sources).items():
        fm = metrics_by_path.get(rel_path)
        if fm is not None:
            fm.issues.extend(issues)


def _apply_internal_refs(root, metrics_by_path):
    """Cross-file unresolved-internal-reference detection: `from utils
    import frobnicate` where repo-local `utils` defines no `frobnicate` --
    see analyzers/internal_refs.py. Needs every Python file's source at
    once to build the module map, so like dead code it's full-scan only.
    """
    file_sources = _python_file_sources(root, metrics_by_path)
    for rel_path, issues in internal_refs.internal_reference_issues(file_sources).items():
        fm = metrics_by_path.get(rel_path)
        if fm is not None:
            fm.issues.extend(issues)


def _apply_unused_deps(root, metrics, metrics_by_path):
    """Cross-file unused-dependency detection: packages listed in
    requirements files that are never imported in any Python source file.
    Full-scan-only, like dead-code: a diff has no view of all other imports.
    Issues are attached to the requirements/pyproject file; a new FileMetrics
    entry is created for it if it isn't already in metrics_by_path (the same
    approach as _apply_doc_examples for Markdown files).
    """
    file_sources = _python_file_sources(root, metrics_by_path)
    for rel_path, issues in unused_deps.unused_dependency_issues(root, file_sources).items():
        fm = metrics_by_path.get(rel_path)
        if fm is None:
            try:
                with open(os.path.join(root, rel_path), "r", encoding="utf-8") as f:
                    line_count = sum(1 for _ in f)
            except OSError:
                line_count = 1
            fm = FileMetrics(path=rel_path, language="requirements", total_lines=line_count, loc=line_count)
            metrics.append(fm)
            metrics_by_path[rel_path] = fm
        fm.issues.extend(issues)


def _discover_markdown_files(root, exclude_patterns):
    """Sorted list of relative paths to every `.md` file under `root`. Kept
    separate from `discover_files`: Markdown isn't a source language with
    functions/complexity to score, so it doesn't belong in the Python/
    generic-language dispatch table above -- it gets its own small walk,
    applying the same DEFAULT_IGNORE_DIRS/exclude-pattern rules.
    """
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.endswith(".egg-info")]
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            if _is_excluded(rel, exclude_patterns):
                continue
            results.append(rel)
    return sorted(results)


def _apply_doc_examples(root, config, metrics, metrics_by_path):
    """Validate that fenced ```python/```py Markdown code blocks still
    parse -- see analyzers/doc_examples.py. Full-scan only, not `diff`:
    unlike the line-level style checks, "does this repo's documentation
    still parse" isn't scoped to whichever lines a diff happened to touch
    (a signature change three files away is exactly the case a README
    example rots from, with the README itself untouched by that diff), so
    there's no meaningful "just the changed lines" version of this check
    the way `diff` mode has for everything else. It's per-file rather than
    cross-file like `_apply_duplication`/`_apply_dead_code` above, but it
    shares their reasoning for being full-scan-only.
    """
    for rel_path in _discover_markdown_files(root, config.exclude):
        source = _read_source(root, rel_path)
        if source is None:
            continue
        issues = doc_examples.check_markdown_source(rel_path, source)
        if not issues:
            continue
        fm = metrics_by_path.get(rel_path)
        if fm is None:
            line_count = len(source.splitlines())
            fm = FileMetrics(path=rel_path, language="markdown", total_lines=line_count, loc=line_count)
            metrics.append(fm)
            metrics_by_path[rel_path] = fm
        fm.issues.extend(issues)


def _apply_type_checking(root, config, metrics_by_path, changed_files=None):
    """Run mypy once over the whole root (it needs full-project context for
    real cross-file inference) and distribute its findings onto the
    matching FileMetrics. In diff mode, only findings on changed lines
    count, same scoping rule every other check follows.
    """
    if not config.check_types or not typecheck.AVAILABLE:
        return
    for rel_path, issues in typecheck.run(root).items():
        fm = metrics_by_path.get(rel_path)
        if fm is None:
            continue
        for issue in issues:
            if changed_files is not None and issue.line not in changed_files.get(rel_path, set()):
                continue
            fm.issues.append(issue)


def _apply_coverage(root, config, metrics_by_path, changed_files=None):
    """Run the repo's own test suite under coverage.py once and attach a
    per-file ratio to each matching FileMetrics -- this executes the
    target repo's code, unlike every other check here, which is why it's
    opt-in (--check-coverage). In diff mode, the ratio is "patch coverage"
    (just the added lines), not whole-file coverage.
    """
    if not config.check_coverage or not coverage_check.AVAILABLE:
        return
    coverage_by_file = coverage_check.run(root, config.test_command)
    if coverage_by_file is None:
        return
    for rel_path, lines in coverage_by_file.items():
        fm = metrics_by_path.get(rel_path)
        if fm is None:
            continue
        only_lines = changed_files.get(rel_path) if changed_files is not None else None
        computed = coverage_check.ratio(lines, only_lines)
        if computed is not None:
            fm.coverage_ratio = computed


def _apply_signature_diff(root, metrics_by_path, base):
    """Compare each changed Python file's old (at `base`) vs. new function
    signatures, flagging breaking API changes. Diff-only, always-on --
    pure AST comparison, no environment dependency, no opt-in needed.
    """
    if base is None:
        return
    for rel_path, fm in metrics_by_path.items():
        if fm.language != "python":
            continue
        old_source = git_utils.get_file_at_ref(base, rel_path, root)
        new_source = _read_source(root, rel_path)
        if new_source is None:
            continue
        fm.issues.extend(signature_diff.signature_diff_issues(old_source, new_source, rel_path))


def _apply_complexity_regression(root, metrics_by_path, base):
    """Compare each changed Python file's old (at `base`) vs. new per-function
    cyclomatic complexity, flagging functions that got significantly more
    complex. Diff-only, always-on -- pure AST comparison via
    analyzers/complexity_regression.py, same precedent as
    _apply_signature_diff above (and shares the same `base` ref).
    """
    if base is None:
        return
    for rel_path, fm in metrics_by_path.items():
        if fm.language != "python":
            continue
        old_source = git_utils.get_file_at_ref(base, rel_path, root)
        new_source = _read_source(root, rel_path)
        if new_source is None:
            continue
        fm.issues.extend(complexity_regression.compare_functions(old_source, new_source, rel_path))


def _apply_scope_check(metrics_by_path, task_description):
    """Flag changed files that share no keyword with `task_description`
    while another changed file elsewhere does -- see analyzers/scope_check.py.
    Diff-only (there's no single "the task" for a whole-repo scan) and
    always-on like signature-diff: pure string matching, no environment
    dependency, no opt-in needed.
    """
    if not task_description:
        return
    issues_by_path = scope_check.scope_mismatch_issues(task_description, metrics_by_path.keys())
    for rel_path, issues in issues_by_path.items():
        metrics_by_path[rel_path].issues.extend(issues)


_MISSING_TEST_MIN_COMPLEXITY = 3


def _apply_missing_tests(metrics_by_path):
    """Flag non-test Python source files that have meaningful complexity but no
    corresponding test file anywhere in the scanned set. Only runs on full
    scans (not diff) because a diff has no view of the rest of the repo's test
    files.

    A file is considered tested if a path matching ``test_<stem>.py``,
    ``<stem>_test.py``, or ``tests/<stem>.py`` (case-insensitive) appears
    anywhere in `metrics_by_path`.  Files with max cyclomatic complexity below
    `_MISSING_TEST_MIN_COMPLEXITY` are skipped -- tiny helpers rarely warrant
    their own test file.
    """
    tested_stems = set()
    for rel_path in metrics_by_path:
        base = os.path.basename(rel_path)
        name, _ = os.path.splitext(base)
        nl = name.lower()
        if nl.startswith("test_"):
            tested_stems.add(nl[5:])
        elif nl.endswith("_test"):
            tested_stems.add(nl[:-5])

    for rel_path, fm in metrics_by_path.items():
        if fm.language != "python":
            continue
        if is_test_file(rel_path):
            continue
        if not fm.functions:
            continue
        max_cc = max(fn.complexity for fn in fm.functions)
        if max_cc < _MISSING_TEST_MIN_COMPLEXITY:
            continue
        stem, _ = os.path.splitext(os.path.basename(rel_path).lower())
        if stem in tested_stems:
            continue
        fm.issues.append(Issue(
            rel_path, 1, "correctness", "info", "missing-test-file",
            f"No test file found for '{os.path.basename(rel_path)}' "
            f"(max complexity {max_cc}) -- consider adding tests"
        ))


def scan_repo(root, config):
    """Full-repo scan: every supported file, in full."""
    files = discover_files(root, config.exclude, config.include_generic_languages)
    metrics = []
    metrics_by_path = {}
    for rel_path, lang in files:
        fm = analyze_file(root, rel_path, lang, config)
        if fm is None:
            continue
        metrics.append(fm)
        metrics_by_path[rel_path] = fm
    _apply_duplication(root, metrics_by_path)
    _apply_circular_imports(root, metrics_by_path)
    _apply_dead_code(root, metrics_by_path)
    _apply_internal_refs(root, metrics_by_path)
    _apply_unused_deps(root, metrics, metrics_by_path)
    _apply_type_checking(root, config, metrics_by_path)
    _apply_coverage(root, config, metrics_by_path)
    _apply_doc_examples(root, config, metrics, metrics_by_path)
    _apply_missing_tests(metrics_by_path)
    return metrics


def scan_changed(root, config, changed_files, base=None, task_description=None):
    """Diff-scoped scan.

    `changed_files`: dict[rel_path] -> set of 1-based added line numbers
    (from git_utils.parse_added_lines). Only these files are analyzed,
    and analyzers are told which lines actually changed so complexity/
    structure/style checks grade the changed logic, not the whole file.

    `base`, when given, is the git ref being diffed against -- it's what
    lets signature-diff and complexity-regression (and, opt-in,
    behavior-diff) fetch each changed file's *old* content to compare
    against.

    `task_description`, when given, feeds the scope-mismatch check (see
    analyzers/scope_check.py) -- typically the commit subject.
    """
    all_files = dict(discover_files(root, config.exclude, config.include_generic_languages))
    metrics = []
    metrics_by_path = {}
    for rel_path, added_lines in changed_files.items():
        lang = all_files.get(rel_path)
        if lang is None:
            continue  # not a supported/tracked extension, or excluded
        fm = analyze_file(root, rel_path, lang, config, only_lines=added_lines)
        if fm is None:
            continue
        metrics.append(fm)
        metrics_by_path[rel_path] = fm
    _apply_duplication(root, metrics_by_path)
    _apply_circular_imports(root, metrics_by_path)
    _apply_type_checking(root, config, metrics_by_path, changed_files=changed_files)
    _apply_coverage(root, config, metrics_by_path, changed_files=changed_files)
    _apply_signature_diff(root, metrics_by_path, base)
    _apply_complexity_regression(root, metrics_by_path, base)
    _apply_scope_check(metrics_by_path, task_description)
    return metrics

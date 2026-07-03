"""File discovery and orchestration: walks the repo (or a restricted set
of paths for diff mode), dispatches each file to the right analyzer, and
runs duplicate-block detection across the resulting set.
"""

import fnmatch
import os

from codequality.analyzers import duplication, generic_analyzer, python_analyzer
from codequality.config import DEFAULT_IGNORE_DIRS, GENERIC_EXTENSIONS, PYTHON_EXTENSIONS


def _is_excluded(rel_path, patterns):
    return any(fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(os.path.basename(rel_path), pat) for pat in patterns)


def discover_files(root, exclude_patterns, include_generic=True):
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


def analyze_file(root, rel_path, language, config, only_lines=None):
    source = _read_source(root, rel_path)
    if source is None:
        return None
    if language == "python":
        return python_analyzer.analyze(rel_path, source, config.limits, only_lines=only_lines)
    return generic_analyzer.analyze(rel_path, source, language, config.limits, only_lines=only_lines)


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
    return metrics


def scan_changed(root, config, changed_files):
    """Diff-scoped scan.

    `changed_files`: dict[rel_path] -> set of 1-based added line numbers
    (from git_utils.parse_added_lines). Only these files are analyzed,
    and analyzers are told which lines actually changed so complexity/
    structure/style checks grade the changed logic, not the whole file.
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
    return metrics

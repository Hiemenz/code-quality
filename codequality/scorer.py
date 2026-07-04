"""Turns raw metrics (from the analyzers) into 0-100 scores.

Every number here comes from counting things in the AST/text -- there is
no model call, no subjective judgment. The formulas are deliberately
simple and documented inline so the score is auditable: given the same
inputs, this always produces the same output.
"""

from dataclasses import dataclass

from codequality import suppress


def grade(score):
    """Map a 0-100 score to a letter grade (A/B/C/D/F)."""
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _complexity_penalty(cc):
    if cc <= 5:
        return 0.0
    if cc <= 10:
        return (cc - 5) * 2
    if cc <= 20:
        return 10 + (cc - 10) * 4
    return 50 + (cc - 20) * 6


def score_complexity(functions, limits):
    """Average complexity penalty across functions, skipping any with a
    suppressed `high-complexity` check.
    """
    if not functions:
        return 100.0
    penalties = [
        0.0 if suppress.is_suppressed(f.suppressed, "high-complexity") else _complexity_penalty(f.complexity)
        for f in functions
    ]
    avg_penalty = sum(penalties) / len(penalties)
    return _clamp(100 - avg_penalty)


def score_structure(functions, file_metrics_list, limits):
    """Score function length, nesting depth, and file length as one category."""
    penalties = []
    for f in functions:
        p = 0.0
        if f.length > limits.max_function_lines and not suppress.is_suppressed(f.suppressed, "long-function"):
            over = f.length - limits.max_function_lines
            p += min(40.0, over * 0.5)
        if f.nesting > limits.max_nesting and not suppress.is_suppressed(f.suppressed, "deep-nesting"):
            over = f.nesting - limits.max_nesting
            p += min(30.0, over * 8)
        penalties.append(p)
    for fm in file_metrics_list:
        if fm.total_lines > limits.max_file_lines:
            over = fm.total_lines - limits.max_file_lines
            penalties.append(min(40.0, over * 0.05))
    if not penalties:
        return 100.0
    return _clamp(100 - sum(penalties) / len(penalties))


def score_duplication(file_metrics_list):
    total_lines = sum(fm.total_lines for fm in file_metrics_list)
    dup_lines = sum(fm.duplicate_lines for fm in file_metrics_list)
    if total_lines == 0:
        return 100.0
    ratio = dup_lines / total_lines
    return _clamp(100 - ratio * 100 * 2.5)


def _docstring_ratio(items, is_documented):
    if not items:
        return 1.0
    return sum(1 for x in items if is_documented(x)) / len(items)


def score_documentation(functions, file_metrics_list):
    """Score docstring coverage on public functions (75%) and modules (25%)."""
    def _is_documented(f):
        return f.has_docstring or suppress.is_suppressed(f.suppressed, "missing-docstring")

    scored_functions = [f for f in functions if f.is_public and f.length > 0]
    func_score = _docstring_ratio(scored_functions, _is_documented) * 100

    py_files = [fm for fm in file_metrics_list if fm.language == "python"]
    if not py_files:
        return _clamp(func_score)

    mod_score = _docstring_ratio(py_files, lambda fm: fm.has_module_docstring) * 100
    return _clamp(func_score * 0.75 + mod_score * 0.25)


def score_style(file_metrics_list):
    """Score style/hygiene issues, weighted by severity, per 100 lines of code."""
    weights = {
        "long-line": 1,
        "trailing-whitespace": 1,
        "tab-indent": 1,
        "todo-marker": 0.5,
        "bare-except": 4,
        "star-import": 3,
        "mutable-default-arg": 4,
        "unused-import": 2,
        "unused-variable": 2,
        "bad-function-name": 1,
        "bad-class-name": 1,
    }
    total_loc = sum(fm.loc for fm in file_metrics_list)
    if total_loc == 0:
        return 100.0
    weighted = 0.0
    for fm in file_metrics_list:
        for issue in fm.issues:
            if issue.category == "style":
                weighted += weights.get(issue.symbol, 1)
    density_per_100 = weighted / total_loc * 100
    return _clamp(100 - density_per_100 * 8)


def score_security(file_metrics_list):
    """Score security-sensitive findings, weighted by severity, per 100 lines of code."""
    weights = {
        "dangerous-eval": 15,
        "shell-true": 15,
        "hardcoded-secret": 20,
        "unsafe-deserialization": 10,
        "unsafe-yaml-load": 8,
        "shell-exec": 6,
    }
    total_loc = sum(fm.loc for fm in file_metrics_list)
    if total_loc == 0:
        return 100.0
    weighted = 0.0
    for fm in file_metrics_list:
        for issue in fm.issues:
            if issue.category == "security":
                weighted += weights.get(issue.symbol, 10)
    density_per_100 = weighted / total_loc * 100
    return _clamp(100 - density_per_100 * 8)


@dataclass
class CategoryResult:
    score: float
    weight: float


@dataclass
class ScoreResult:
    overall: float
    grade: str
    categories: dict  # name -> CategoryResult


def compute_scores(file_metrics_list, config):
    """Combine all five category scores into a weighted overall ScoreResult."""
    functions = [f for fm in file_metrics_list for f in fm.functions]

    raw = {
        "complexity": score_complexity(functions, config.limits),
        "structure": score_structure(functions, file_metrics_list, config.limits),
        "duplication": score_duplication(file_metrics_list),
        "documentation": score_documentation(functions, file_metrics_list),
        "style": score_style(file_metrics_list),
        "security": score_security(file_metrics_list),
    }

    weights = config.weights
    total_weight = sum(weights.values()) or 1
    overall = sum(raw[name] * weights.get(name, 0) for name in raw) / total_weight

    categories = {name: CategoryResult(score=round(raw[name], 1), weight=weights.get(name, 0)) for name in raw}

    return ScoreResult(overall=round(overall, 1), grade=grade(overall), categories=categories)


def score_single_file(file_metrics):
    """Convenience per-file score, used for the "worst files" report table."""

    class _Cfg:
        pass

    from codequality.config import DEFAULT_CONFIG, Limits

    cfg = _Cfg()
    cfg.limits = Limits(DEFAULT_CONFIG["limits"])
    cfg.weights = DEFAULT_CONFIG["weights"]
    result = compute_scores([file_metrics], cfg)
    return result.overall

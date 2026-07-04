"""Orchestrates a full quality pipeline out of tools that already exist:
format check -> lint -> static analysis -> security -> tests -> coverage ->
complexity -> benchmark. `codequality` itself already covers static
analysis, security, complexity, and (opt-in) coverage in one deterministic
pass -- see scanner.py/scorer.py. Formatting, linting, and benchmarking are
deliberately not reimplemented here: every repo already has a preferred
tool for those (black/prettier/ruff/eslint/pytest-benchmark/...), so this
module just runs whichever external commands the target repo's
`[pipeline]` config points at, runs codequality's own scan/diff alongside
them, and folds everything into one report + exit code for CI to gate on.

Each external step is run via `subprocess.run(shlex.split(command), ...)`,
never `shell=True` -- config-file content is untrusted input as far as
shell injection is concerned, same reasoning as the dangerous-shell-true
security check this tool applies to everyone else's code.
"""

import shlex
import subprocess
import time
from dataclasses import dataclass, field

from codequality.git_utils import get_changed_files, is_git_repo, resolve_default_base
from codequality.report import build_summary
from codequality.scanner import scan_changed, scan_repo
from codequality.scorer import compute_scores

_OUTPUT_TAIL_LINES = 20


class PipelineError(RuntimeError):
    """Usage error running the pipeline (e.g. diff mode outside a git repo)."""


@dataclass
class StepResult:
    name: str
    command: str
    passed: bool
    exit_code: int = None
    duration: float = 0.0
    allow_failure: bool = False
    output_tail: str = ""
    skipped: bool = False


@dataclass
class PipelineResult:
    steps: list = field(default_factory=list)
    passed: bool = True
    codequality_summary: dict = None


def _tail(text):
    lines = text.splitlines()
    return "\n".join(lines[-_OUTPUT_TAIL_LINES:])


def _run_external_step(step, root):
    """Runs one configured external command and grades it: exit code 0
    passes outright, a non-zero exit only passes if the step is marked
    allow_failure (e.g. a lint step the repo hasn't fully cleaned up yet).
    """
    start = time.monotonic()
    try:
        proc = subprocess.run(shlex.split(step.command), cwd=root, capture_output=True, text=True)
        exit_code = proc.returncode
        output = (proc.stdout or "") + (proc.stderr or "")
    except OSError as e:
        exit_code = None
        output = str(e)
    duration = time.monotonic() - start
    succeeded = exit_code == 0
    return StepResult(
        name=step.name,
        command=step.command,
        passed=succeeded or step.allow_failure,
        exit_code=exit_code,
        duration=duration,
        allow_failure=step.allow_failure,
        output_tail=_tail(output),
    )


def _skipped_step(step):
    return StepResult(
        name=step.name, command=step.command, passed=False, allow_failure=step.allow_failure, skipped=True
    )


def _run_codequality_scan(root, config, fail_under):
    file_metrics = scan_repo(root, config)
    score_result = compute_scores(file_metrics, config)
    return build_summary(file_metrics, score_result, "scan", root, fail_under=fail_under)


def _run_codequality_diff(root, config, fail_under, codequality_args):
    if not is_git_repo(root):
        raise PipelineError(f"{root} is not a git repository (mode='diff' requires git)")
    base = codequality_args.get("base")
    head = codequality_args.get("head")
    auto_head = None
    if base is None:
        base, auto_head = resolve_default_base(root)
    head = head if head is not None else auto_head

    changed_files = get_changed_files(base, head, root)
    diff_info = {
        "base": base,
        "head": head,
        "changed_files": sorted(changed_files.keys()),
        "changed_lines_count": sum(len(v) for v in changed_files.values()),
    }
    file_metrics = scan_changed(root, config, changed_files) if changed_files else []
    score_result = compute_scores(file_metrics, config)
    return build_summary(file_metrics, score_result, "diff", root, diff_info=diff_info, fail_under=fail_under)


def _run_codequality_step(root, config, mode, fail_under, codequality_args):
    """Runs codequality's own scan/diff in-process (not shelling out to
    itself) and wraps the result as one more StepResult, so the pipeline's
    own quality bar shows up in the same report as every external step.
    """
    start = time.monotonic()
    if mode == "diff":
        summary = _run_codequality_diff(root, config, fail_under, codequality_args or {})
    else:
        summary = _run_codequality_scan(root, config, fail_under)
    duration = time.monotonic() - start
    passed = summary["threshold"]["passed"]
    overall = summary["overall"]
    step = StepResult(
        name="codequality",
        command=f"codequality {mode}",
        passed=passed,
        exit_code=0 if passed else 1,
        duration=duration,
        output_tail=f"score={overall['score']}/100 (grade {overall['grade']})",
    )
    return step, summary


def run(root, config, mode="scan", fail_under=None, continue_on_failure=False, codequality_args=None):
    """Runs every configured `[pipeline]` step in order, then codequality's
    own scan/diff as one final step, returning a PipelineResult.

    Stops at the first failing step (leaving the rest marked skipped)
    unless that step is `allow_failure`, so a broken format-check fails
    fast instead of burning CI time on a lint pass over code that's known
    to be unformatted -- unless `continue_on_failure` is set, which runs
    every step regardless so the report shows the full picture at once.
    """
    result = PipelineResult()
    stopped_early = False

    for step_cfg in config.pipeline_steps:
        if stopped_early and not continue_on_failure:
            result.steps.append(_skipped_step(step_cfg))
            continue
        step_result = _run_external_step(step_cfg, root)
        result.steps.append(step_result)
        if not step_result.passed:
            result.passed = False
            stopped_early = True

    if stopped_early and not continue_on_failure:
        return result

    cq_step, result.codequality_summary = _run_codequality_step(root, config, mode, fail_under, codequality_args)
    result.steps.append(cq_step)
    if not cq_step.passed:
        result.passed = False
    return result


def to_dict(result):
    """JSON-serializable form of a PipelineResult, for --format json."""
    return {
        "tool": "codequality-pipeline",
        "passed": result.passed,
        "steps": [
            {
                "name": s.name,
                "command": s.command,
                "passed": s.passed,
                "exit_code": s.exit_code,
                "duration_seconds": round(s.duration, 3),
                "allow_failure": s.allow_failure,
                "skipped": s.skipped,
                "output_tail": s.output_tail,
            }
            for s in result.steps
        ],
        "codequality": result.codequality_summary,
    }


def render_text(result):
    """Render a PipelineResult as a human-readable terminal report: one
    line per step (status, exit code, duration), then the codequality
    score/grade if it ran, then the overall verdict.
    """
    lines = ["Pipeline Report", ""]
    for step in result.steps:
        if step.skipped:
            lines.append(f"  [SKIP] {step.name}  (not run: an earlier step failed)")
            continue
        status = "PASS" if step.passed else "FAIL"
        note = " (allowed to fail)" if step.allow_failure and step.exit_code != 0 else ""
        lines.append(f"  [{status}] {step.name}  ({step.duration:.2f}s, exit={step.exit_code}){note}")
        if not step.passed and step.output_tail:
            for output_line in step.output_tail.splitlines():
                lines.append(f"        {output_line}")

    if result.codequality_summary is not None:
        overall = result.codequality_summary["overall"]
        lines.append("")
        lines.append(f"codequality score: {overall['score']}/100 (grade {overall['grade']})")

    lines.append("")
    lines.append("PASS" if result.passed else "FAIL")
    return "\n".join(lines)

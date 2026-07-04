"""Optional, explicitly opt-in LLM-based review: architecture/readability
scores plus "did this diff do what the task asked, and nothing more."

This is the one module in `codequality` that makes a network call, costs
money, and isn't reproducible run-to-run -- everything else in this tool is
static analysis with a fixed, deterministic formula (see README). To keep
that promise intact for the rest of the tool, this module:

- is never imported/called by `scan`/`diff` unless `--llm-review` is passed
  (see cli.py) -- there is no code path that reaches this module by default;
- never contributes to the 0-100 deterministic score computed by scorer.py;
  its output is reported as its own top-level `llm_review` section (see
  report.py), never folded into `categories`;
- requires the optional `codequality[llm]` extra (the `anthropic` package)
  to even be reachable, following the same AVAILABLE-flag pattern as
  typecheck.py (mypy) and coverage_check.py (coverage.py);
- reads its API key only from the ANTHROPIC_API_KEY environment variable,
  never from a config file, so a key can't end up committed to the target
  repo's .codequality.toml.
"""

import os

try:
    import anthropic
    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when the extra isn't installed
    anthropic = None
    AVAILABLE = False

DEFAULT_MODEL = "claude-haiku-4-5"
MODEL_ENV_VAR = "CODEQUALITY_LLM_MODEL"
ANTHROPIC_KEY_ENV_VAR = "ANTHROPIC_API_KEY"

_TOOL_NAME = "submit_review"

_TOOL_SCHEMA = {
    "name": _TOOL_NAME,
    "description": "Submit the architecture/readability/instruction-adherence review of the reviewed code.",
    "input_schema": {
        "type": "object",
        "properties": {
            "architecture_score": {
                "type": "integer",
                "description": "0-10 rating of the code's architecture and design quality.",
            },
            "readability_score": {
                "type": "integer",
                "description": "0-10 rating of how readable and clear the code is.",
            },
            "instruction_adherence_score": {
                "type": ["integer", "null"],
                "description": (
                    "0-10 rating of whether the change does what the task description asked, "
                    "and nothing more or less. null if no task description was provided."
                ),
            },
            "rationale": {
                "type": "string",
                "description": "A short (2-6 sentence) explanation of the scores above.",
            },
        },
        "required": ["architecture_score", "readability_score", "instruction_adherence_score", "rationale"],
        "additionalProperties": False,
    },
}


class LLMJudgeError(RuntimeError):
    """Raised when the LLM review can't be produced -- missing package,
    missing API key, or a failed/unparseable API call. `judge()` never
    fabricates a score in place of raising this.
    """


def _format_content(diff_text_or_files):
    if isinstance(diff_text_or_files, dict):
        return "\n\n".join(f"--- {path} ---\n{text}" for path, text in diff_text_or_files.items())
    return diff_text_or_files


def _build_prompt(content, task_description):
    parts = []
    if task_description:
        parts.append(
            "The code below was written to implement this task:\n" + task_description.strip() + "\n"
        )
    else:
        parts.append("No task description was provided for this code.\n")
    parts.append("Code to review (a unified diff, or full file contents):\n")
    parts.append(content)
    parts.append("")
    if task_description:
        parts.append(
            "Rate architecture (0-10) and readability (0-10). Also rate instruction_adherence "
            "(0-10): does the change do what the task asked -- and nothing more, nothing less?"
        )
    else:
        parts.append(
            "Rate architecture (0-10) and readability (0-10). No task description was given, "
            "so do not invent one to grade against -- set instruction_adherence_score to null."
        )
    parts.append("Call the submit_review tool with your scores and a short rationale.")
    return "\n".join(parts)


def _extract_tool_input(response):
    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == _TOOL_NAME:
            return block.input
    return None


def judge(diff_text_or_files, task_description=None, model=None, api_key=None):
    """Run the opt-in LLM review.

    `diff_text_or_files`: either a unified diff (str) or a dict of
    {relative_path: file_contents} for a scan-equivalent whole-file review.
    `task_description`: the prompt/task text the code was supposed to
    implement, if known. When omitted, `instruction_adherence_score` is
    `None` rather than a fabricated number -- there's nothing to grade
    adherence against.
    `model`/`api_key`: override the model (default `DEFAULT_MODEL`, also
    overridable via the CODEQUALITY_LLM_MODEL env var) and the API key
    (default: ANTHROPIC_API_KEY env var only -- never read from config).

    Returns a dict: {"architecture_score": int, "readability_score": int,
    "instruction_adherence_score": int or None, "rationale": str}.

    Raises LLMJudgeError -- never returns a fabricated score -- when the
    `anthropic` package isn't installed, no API key is available, or the
    request fails or comes back in an unexpected shape.
    """
    if not AVAILABLE:
        raise LLMJudgeError(
            "The 'anthropic' package is not installed. Install it with "
            "`pip install codequality[llm]` to use --llm-review."
        )

    resolved_key = api_key or os.environ.get(ANTHROPIC_KEY_ENV_VAR)
    if not resolved_key:
        raise LLMJudgeError(
            f"No API key found. Set the {ANTHROPIC_KEY_ENV_VAR} environment variable to use --llm-review."
        )

    resolved_model = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
    prompt = _build_prompt(_format_content(diff_text_or_files), task_description)

    client = anthropic.Anthropic(api_key=resolved_key)
    try:
        response = client.messages.create(
            model=resolved_model,
            max_tokens=1024,
            tools=[_TOOL_SCHEMA],
            tool_choice={"type": "tool", "name": _TOOL_NAME},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        raise LLMJudgeError(f"LLM review request failed: {exc}") from exc

    result = _extract_tool_input(response)
    if result is None:
        raise LLMJudgeError("LLM review response did not include the expected tool call.")

    return {
        "model": resolved_model,
        "architecture_score": result.get("architecture_score"),
        "readability_score": result.get("readability_score"),
        "instruction_adherence_score": result.get("instruction_adherence_score") if task_description else None,
        "rationale": result.get("rationale", ""),
    }

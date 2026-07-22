"""Scaffold a .codequality.toml config and a GitHub Actions CI workflow.

``codequality init`` writes two files into the target repository:

1. ``.codequality.toml`` -- minimal config with fail_under and commented
   placeholders for the most commonly customised settings.
2. ``.github/workflows/codequality.yml`` -- a two-step workflow that runs a
   full repo scan on every push, plus a diff-gated changed-code scan on pull
   requests, with a sticky PR comment.

Both files are skipped (with a notice) if they already exist. Pass ``--force``
to overwrite.
"""

import os

_CONFIG_TEMPLATE = """\
# codequality configuration
# Documentation: https://github.com/Hiemenz/code-quality

[tool.codequality]

# Exit non-zero from `codequality scan` when the overall score drops below
# this value (0–100). Start conservative and raise it once the codebase is
# in better shape.
fail_under = {fail_under}

# Glob patterns to exclude from scanning (relative to this file's directory).
# Useful for generated code, fixtures, or vendored libraries.
# exclude = ["generated/**", "tests/fixtures/**"]

# Optional: adjust category weights (these are the built-in defaults).
# [tool.codequality.weights]
# style       = 12
# correctness = 15
# security    = 15
# complexity  = 15
# coverage    = 15
# structure   = 10
# duplication = 10
# documentation = 8
"""

_WORKFLOW_TEMPLATE = """\
name: Code Quality

on:
  pull_request:
  push:
    branches: [main]

jobs:
  code-quality:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # full history so `diff` can find the merge-base

      - uses: actions/setup-python@v5
        with:
          python-version: "3.x"

      - name: Install codequality
        run: pip install codequality

      # Full-repo scan (overall health — track over time via badges/history).
      - name: Full repo scan
        run: codequality scan . --format text --fail-under {fail_under}

      # Changed-code gate — only runs on pull requests.
      - name: Changed-code scan (PR gate)
        if: github.event_name == 'pull_request'
        run: |
          codequality diff . \\
            --format json --output cq-diff.json --fail-under {fail_under}
          codequality diff . --format text

      # Sticky PR comment with the diff report.
      - name: Post PR comment
        if: github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            if (!fs.existsSync('cq-diff.json')) {{ process.exit(0); }}
            const marker = '<!-- codequality-report -->';
            const body = marker + '\\n```\\n' +
              require('child_process').execSync('codequality diff . --format text 2>&1').toString() +
              '\\n```';
            const {{ data: comments }} = await github.rest.issues.listComments({{
              owner: context.repo.owner, repo: context.repo.repo,
              issue_number: context.issue.number,
            }});
            const existing = comments.find(c => c.body.includes(marker));
            if (existing) {{
              await github.rest.issues.updateComment({{
                owner: context.repo.owner, repo: context.repo.repo,
                comment_id: existing.id, body,
              }});
            }} else {{
              await github.rest.issues.createComment({{
                owner: context.repo.owner, repo: context.repo.repo,
                issue_number: context.issue.number, body,
              }});
            }}
"""


def init(
    root,
    fail_under=70,
    config_only=False,
    ci_only=False,
    force=False,
):
    """Scaffold config and/or CI workflow in `root`.

    Returns a list of ``(path, status)`` pairs where status is one of
    ``"created"``, ``"skipped"`` (already exists), or ``"overwritten"``.
    """
    results = []

    if not ci_only:
        config_path = os.path.join(root, ".codequality.toml")
        exists = os.path.isfile(config_path)
        if exists and not force:
            results.append((config_path, "skipped"))
        else:
            content = _CONFIG_TEMPLATE.format(fail_under=fail_under)
            with open(config_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            results.append((config_path, "overwritten" if exists else "created"))

    if not config_only:
        workflows_dir = os.path.join(root, ".github", "workflows")
        os.makedirs(workflows_dir, exist_ok=True)
        workflow_path = os.path.join(workflows_dir, "codequality.yml")
        exists = os.path.isfile(workflow_path)
        if exists and not force:
            results.append((workflow_path, "skipped"))
        else:
            content = _WORKFLOW_TEMPLATE.format(fail_under=fail_under)
            with open(workflow_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            results.append((workflow_path, "overwritten" if exists else "created"))

    return results


def render_text(results, root):
    """Human-readable summary of what init created."""
    lines = []
    for path, status in results:
        rel = os.path.relpath(path, root)
        icon = {"created": "+", "overwritten": "~", "skipped": "!"}.get(status, "?")
        lines.append(f"  [{icon}] {rel}  ({status})")
    lines.append("")
    created = sum(1 for _, s in results if s in ("created", "overwritten"))
    skipped = sum(1 for _, s in results if s == "skipped")
    if created:
        lines.append(f"Created {created} file(s). Run `codequality scan .` to check your project.")
    if skipped:
        lines.append(f"Skipped {skipped} existing file(s). Use --force to overwrite.")
    return "\n".join(lines)

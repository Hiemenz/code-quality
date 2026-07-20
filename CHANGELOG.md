# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project follows semantic versioning.

## [Unreleased]

### Added

- `codequality explain <rule>` — look up any rule symbol from the terminal
  (`--list` enumerates all rules).
- `--format badge` on `scan`/`diff` — emits shields.io endpoint JSON so a
  repo can embed its live score as a README badge.
- `--jobs N` on `scan`/`diff` — parallel per-file analysis (deterministic:
  results are ordered the same regardless of worker count).
- `rule` field on JSON issue objects (duplicate of `symbol`, which stays
  for backward compatibility) so integrations can use the conventional
  field name.
- Pre-commit hooks (`.pre-commit-hooks.yaml`): `codequality-diff` and
  `codequality-scan` for use with the pre-commit framework.
- Composite GitHub Action (`action.yml`): one `uses:` line gets install +
  scan + PR diff gate + sticky score comment.
- Release workflow: version tags publish to PyPI via trusted publishing
  and create a GitHub release.
- CI now runs the test suite across Python 3.9 / 3.11 / 3.13 (previously
  only the self-scan ran in CI).

### Changed

- PR score comments are now sticky: re-pushes update the existing comment
  instead of posting a new one per run.
- README split: per-subcommand references moved to `docs/`, README keeps
  the short version.

## [0.5.0] - 2026-07-13

Baseline for this changelog. Highlights of the 0.x line so far:

- `scan` and `diff` modes with text/JSON/markdown/SARIF/HTML output and
  `--fail-under` gating.
- 60+ deterministic checks across complexity, structure, duplication,
  documentation, style, security, correctness, and coverage categories.
- AI-authorship-aware analyses (hallucinated imports, edit-distance
  survival, rework rate) via git blame — no LLM calls.
- Standalone subcommands: hotspots, ownership, todo-age, history-secrets,
  dependency-check/risk, config-drift, migration-check, feature-flags,
  arch-conformance, mutation, flakiness, pipeline, and more.
- Baseline mode, suppression comments, generated-file auto-exclusion,
  repo-convention detection.

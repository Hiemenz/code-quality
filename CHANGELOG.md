# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project follows semantic versioning.

## [Unreleased]

### Fixed

- `score_correctness` now treats `dead-code` and `unused-method` as weight-0
  findings, matching the documented guarantee that these info-severity signals
  don't affect the Correctness score. Previously the missing dict entries fell
  back to the default weight (12), silently tanking the score on any repo with
  unreferenced public API surface.
- `go_taint_flow.py`: taint propagation through multi-target Go assignments now
  works correctly when a non-identifier LHS (index or selector expression) is
  mixed with an identifier target — e.g. `x[0], q = "a", r.FormValue("id")`
  now correctly taints `q`. Previously the identifier filter mismatched the RHS
  list length, so `_apply_binding` was never called and the taint was silently
  dropped.
- `dead_code_ast.py`: two same-named top-level definitions (a redefinition
  pattern) are now each reported independently as dead code. Previously the
  first was silently overwritten in the candidates dict and never flagged.
- `dead_code_ast.py`: methods inside a decorated class (e.g.
  `@register class Foo: def bar(self): ...`) are no longer flagged as
  `unused-method`. The class-level decorator implies framework dispatch of all
  methods, the same reasoning that already exempts decorated top-level defs.
- `go_security.py`: `weak-hash` now requires the file to actually import
  `crypto/md5` or `crypto/sha1` before flagging, preventing false positives
  from locally-defined types that share the package name (e.g.
  `type md5 struct{}`). `sql-injection-risk` similarly now requires a
  SQL-related import, preventing false positives from unrelated types that
  happen to define a `.Query()` method.
- `dead_code_confidence.py`: the full AST cross-reference pass is no longer
  run twice per invocation. Dead-code issues are now reused from the results
  already computed by `scan_repo()` instead of calling `find_dead_code_ast()`
  a second time over the same files.

### Performance

- `go_security.py`, `go_taint_flow.py`, `js_security.py`, `js_taint_flow.py`:
  the source string is now encoded to UTF-8 bytes once per file at the entry
  point, instead of once per AST node. This removes an O(nodes × file-size)
  redundant encode loop that the original in-module `_text()` implementations
  incurred on every call.
- `scripts/scan_repos.py`: repo clone and scan operations now run in parallel
  via `ThreadPoolExecutor`. Wall time is now approximately equal to the slowest
  single repo instead of the sum of all repos.

### Refactored

- `analyzers/_ts_helpers.py` (new): `node_text`, `node_line`, and `iter_kind`
  extracted from four separate module copies (`go_security`, `go_taint_flow`,
  `js_security`, `js_taint_flow`) into one shared module. The copies were
  already diverging; a bug fix now only needs to be applied once.
- `dead_code_ast.py` now imports its exemption helpers (`_is_dunder`,
  `_is_exempt_name`, `_dunder_all_names`, `_DEF_TYPES`, `_TEST_HOOKS`)
  directly from `dead_code.py` rather than duplicating them. `dead_code.py`
  serves as the canonical source of these shared constants.

### Added

- `codequality fix` gains four AST-guided rules — twelve total, up from
  eight. Unlike the existing line-edit rules, these parse the file once
  and splice at exact node offsets, so a lookalike inside a string
  literal or comment can never be rewritten: `mutable-default-arg`
  (`def f(x=[])` → `def f(x=None)` plus an `if x is None: x = []` guard
  at the top of the body, after the docstring if there is one),
  `lost-exception-context` (append ` from err` to a raise that discards
  its handler's exception), `unsafe-yaml-load` (`yaml.load(x)` /
  `yaml.load(x, Loader=...)` → `yaml.safe_load(x)`), and
  `future-import-order` (move a misplaced `from __future__ import ...`
  above the other imports). A file with no AST-rule issues is never
  parsed, and one that doesn't parse skips only its AST-rule issues —
  its text-level fixes still apply.
- Go gets the same real security detection JavaScript/TypeScript has
  (with the `treesitter` extra installed): `weak-hash` for
  `crypto/md5`/`crypto/sha1`, `shell-true` for `exec.Command`/
  `exec.CommandContext` invoked with a shell binary, `sql-injection-risk`
  for `Query`/`Exec`/...`Context` calls built with `fmt.Sprintf`/`+`, and
  intraprocedural taint tracking into SQL sinks from `.FormValue`/
  `.PostFormValue`/`.Header.Get`/`.URL.Query().Get`/`os.Getenv`/
  `os.Args`. Same rule symbols/CWE tags as the Python and JS/TS checks,
  so `codequality compliance` and scoring need no registry changes
  (`codequality/analyzers/go_security.py`, `go_taint_flow.py`).
- `diff --check-coverage` now surfaces patch coverage as its own report
  section instead of only folding it into the Coverage category's 0-100
  score: a `Patch coverage: X% (N/M changed lines covered)` line plus a
  `file:44-51`-style listing of which changed lines the test suite
  doesn't reach, in `text`/`markdown`/`html`, and a `patch_coverage`
  object (`ratio`, `covered_lines`, `total_lines`, `uncovered`) in
  `--format json`.
- `codequality fix` gains three more auto-fixable rules: `bare-except`
  (`except:` → `except Exception:`), `tab-indent` (expand leading tabs to
  4 spaces), and `unused-import` (delete a top-level, single-name import
  statement) — eight rules total, up from five.
- Cross-file dead-code detection now runs on AST references instead of a
  whole-word regex (`analyzers/dead_code_ast.py`), so a name that merely
  appears inside a comment or string literal no longer suppresses a real
  finding. It also adds `unused-method`: a public class method never
  referenced as an attribute access anywhere in the repo.
- `scripts/scan_repos.py` — scan a set of external repos and render a
  cross-repo rule hit-rate comparison, for sanity-checking a new rule's
  false-positive rate against real-world code.
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
- CI now runs the test suite across Python 3.11 / 3.12 / 3.13 (previously
  only the self-scan ran in CI).

### Changed

- Minimum Python bumped to 3.11. The `>=3.9` claim was never actually
  functional: `tomllib` (pyproject parsing) and `sys.stdlib_module_names`
  (the hallucinated-stdlib-attribute check) require 3.11 and 3.10
  respectively, so several checks silently no-op'd on 3.9/3.10. Requiring
  3.11 keeps the tool dependency-free rather than adding a `tomli`
  backport.

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

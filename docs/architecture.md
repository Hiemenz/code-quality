# Architecture

`codequality` is a stateless CLI tool. Given a repository path it reads
source files, runs deterministic analyzers, applies a fixed scoring model,
and writes a report. Nothing is persisted between runs except for the two
files a user explicitly opts into (`--record-history`, `--baseline`). No
daemon, no server, no network calls — every invocation is a complete,
independent computation.

---

## High-level data flow

```
CLI args
   │
   ▼
Config.load()          .codequality.toml / .json / pyproject.toml
   │
   ▼
discover_files()       walk repo; classify Python / tree-sitter / generic
   │                   filter excluded globs + auto-detected generated files
   ▼
analyze_file() × N     per-file dispatch (see "Analyzer layer" below)
   │                   inline suppression applied here
   ▼
cross-file passes      duplication, dead code, circular imports,
   │                   internal refs, unused deps, doc examples,
   │                   diff-only: signature diff, complexity regression,
   │                   scope check
   ▼
compute_scores()       8-category weighted formula → 0-100 + grade
   │
   ▼
build_summary()        format-agnostic dict
   │
   ▼
render_*()             text / JSON / markdown / SARIF / GitLab / HTML / badge
   │
   ▼
stdout / file
```

Diff mode (`codequality diff`) inserts a `get_changed_files()` step
between `Config.load` and `analyze_file`: git's diff output restricts both
which files are walked and which line numbers inside each file are scored.

---

## Module map

```
codequality/
├── cli.py                   Argument parsing, subcommand dispatch
├── config.py                Config dataclass + TOML/JSON loader
├── scanner.py               File discovery, per-file dispatch, cross-file passes
├── scorer.py                0-100 category formulas
├── report.py                build_summary + all render_*() functions
├── rules.py                 Central rule registry (95 symbols)
├── suppress.py              codequality: ignore parser + filter
├── generated_code.py        Auto-detect generated files
├── git_utils.py             Thin wrappers around git CLI calls
├── history.py               --record-history JSONL append/read
├── baseline.py              Baseline snapshot + forgiveness logic
├── fixer.py                 In-place auto-fix engine (8 rules)
├── report_compare.py        compare subcommand: delta between two JSON reports
├── annotation_coverage.py   annotation-coverage subcommand
├── suppression_debt.py      suppression-debt subcommand (git blame aging)
├── project_init.py          init subcommand (.codequality.toml + CI workflow)
│
├── analyzers/
│   ├── base.py              Issue, FileMetrics, FunctionMetrics dataclasses
│   ├── python_analyzer.py   Main Python AST orchestrator (imports all checks)
│   ├── python_idioms.py     Style/idiom checks (f-string, redundant-else, …)
│   ├── python_security.py   Security checks (eval, shell=True, secrets, …)
│   ├── python_test_quality.py  assertion-free, tautological, mock-only tests
│   ├── python_correctness_extra.py  float-equality, mutable-global
│   ├── python_unreachable.py  unreachable-code
│   ├── python_loop_perf.py  string-concat-in-loop
│   ├── python_import_order.py  import ordering
│   ├── python_token_checks.py  implicit string concat
│   ├── python_docstring_drift.py  stale docstring parameters
│   ├── naive_datetime.py    Timezone-naive datetime constructors
│   ├── redos.py             Catastrophic-backtracking regex detection
│   ├── async_await.py       Unawaited coroutine calls
│   ├── db_query_in_loop.py  N+1 query pattern
│   ├── resource_lifecycle.py  Unclosed file/socket/urlopen
│   ├── deprecated_api.py    Removed/deprecated stdlib APIs
│   ├── placeholder_code.py  stub-implementation, placeholder-comment
│   ├── secrets.py           Hardcoded-secret regex
│   ├── signature_diff.py    breaking-signature-change (diff mode only)
│   ├── complexity_regression.py  per-function complexity delta (diff mode only)
│   ├── scope_check.py       scope-mismatch (diff mode only)
│   ├── duplication.py       6-line sliding-window hash (cross-file)
│   ├── circular_imports.py  import-cycle detection (cross-file)
│   ├── dead_code.py         Cross-file unreferenced top-level names (regex refs; used by dead-code-confidence)
│   ├── dead_code_ast.py     Cross-file unreferenced top-level names + methods (AST refs; wired into scan/diff)
│   ├── internal_refs.py     Cross-file unresolved internal imports/attrs
│   ├── unused_deps.py       Unused declared dependencies
│   ├── doc_examples.py      Broken Markdown code-block syntax check
│   ├── stdlib_attrs.py      Hallucinated stdlib attributes (--check-imports)
│   ├── treesitter_analyzer.py  Optional per-function analysis for non-Python
│   └── generic_analyzer.py  Heuristic fallback (no parser required)
│
└── (standalone subcommand modules)
    ai_report.py  api_diff.py  arch_conformance.py  churn.py
    commit_lint.py  complexity_coverage_risk.py  complexity_regression_diff.py
    complexity_trend.py  config_drift.py  config_validate.py  conventions.py
    coverage_check.py  dead_code_confidence.py  dependency_check.py
    dependency_risk.py  edit_distance.py  env_check.py  feature_flags.py
    flakiness.py  hallucination_metrics.py  history_secrets.py  hotspots.py
    large_files.py  migration_check.py  mutation.py  orphaned_config.py
    ownership.py  pipeline.py  property_scaffold.py  todo_age.py  typecheck.py
```

---

## Config layer

`config.py` contains `Config`, a plain dataclass built by merging
`DEFAULT_CONFIG` with whatever `.codequality.toml` / `.codequality.json` /
`[tool.codequality]` in `pyproject.toml` provides. The file is found with
`_find_config_file(root)`; `_read_config_file(path)` unwraps the
`[tool.codequality]` table only for `pyproject.toml` — plain
`.codequality.toml` files are read verbatim. CLI flags (`--exclude`,
`--fail-under`, etc.) are applied on top as overrides after loading.

Key fields on `Config`:

| Field | Default | Purpose |
|---|---|---|
| `fail_under` | 60 | Score below which `scan`/`diff` exits 1 |
| `weights` | see scorer | Per-category weights summing to ~100 |
| `limits` | see scorer | Per-check numeric thresholds |
| `exclude` | `[]` | Glob patterns for `discover_files` |
| `include_generic_languages` | `True` | Enable non-Python heuristic analysis |
| `check_imports` / `check_types` / `check_coverage` | `False` | Opt-in expensive checks |
| `pipeline.steps` | `[]` | External commands for `codequality pipeline` |
| `architecture.layers` | `[]` | Layer definitions for `arch-conformance` |

---

## Analyzer layer

### Python (always available)

`python_analyzer.analyze()` takes a file path + source string + `Limits` and
returns a `FileMetrics`. It:

1. Parses with `ast.parse` (returns empty metrics on `SyntaxError`).
2. Walks the AST with `_FunctionVisitor` to collect `FunctionMetrics`
   (cyclomatic complexity, cognitive complexity, nesting, length, docstrings,
   parameter counts, suppression annotations).
3. Calls ~20 separate issue-finding functions imported from the sibling
   modules listed above. Each takes `(tree, path, only_lines)` and returns
   `list[Issue]`.
4. `only_lines` is `None` for a full scan, or a `set[int]` of added line
   numbers in diff mode — this is the mechanism that makes diff scoring
   operate at line granularity rather than file granularity.

### Tree-sitter (optional)

`treesitter_analyzer.py` wraps `tree-sitter-language-pack` and provides the
same `analyze(path, source, language, limits, only_lines)` interface as the
Python path. It supports `.js .jsx .ts .tsx .java .go .c .cpp .cs .rb .php
.rs .kt .swift .scala`. Availability is detected at import time
(`AVAILABLE` flag); `scanner.py` falls back to the generic analyzer when
the extra is not installed or the language isn't supported.

For JavaScript/TypeScript specifically, `analyze()` also runs
`codequality/analyzers/js_security.py` (weak-hash, shell-true,
sql-injection-risk, and `new Function(...)` as dangerous-eval -- deliberately
*not* bare `eval`/`exec` or hardcoded-secret, both already covered by the
generic line-level regex path below for every tree-sitter language) and
`codequality/analyzers/js_taint_flow.py` (intraprocedural-only taint
tracking into SQL sinks, sources being `req.query`/`req.params`/`req.body`/
`req.headers`/`req.cookies`/`process.env`/`process.argv` member access --
ported from `codequality/analyzers/taint_flow.py`'s Python design onto
tree-sitter nodes; see that module's docstring for the "why SQL only, why
intraprocedural" rationale, which carries over unchanged). Both reuse the
same rule symbols (`weak-hash`, `shell-true`, `sql-injection-risk`,
`dangerous-eval`, `tainted-data-flow`) the Python checks emit, so
`codequality compliance` and scoring treat JS/TS findings identically with
no registry changes. No other tree-sitter language (Go, Java, Rust, ...)
gets this treatment yet.

### Generic heuristic fallback

`generic_analyzer.py` requires no parser. It measures:
- Line count, LOC (non-blank, non-comment lines)
- Complexity approximated from branching-keyword density (`if`/`else`/`for`/
  `while`/`switch`/`case`/`catch`)
- Nesting approximated from indentation depth
- Line-level checks (long lines, trailing whitespace, TODO markers, bare
  `catch`/`except`, hardcoded-looking secrets, `eval`/`exec` calls,
  `shell=True`, placeholder comments)

### Cross-file passes (full scan only)

After all per-file `FileMetrics` are collected, `scanner.scan_repo` runs four
passes that need the whole repo at once:

| Pass | Module | What it checks |
|---|---|---|
| Duplication | `analyzers/duplication.py` | 6-line sliding-window SHA-256 hash; cross-file duplicate blocks |
| Circular imports | `analyzers/circular_imports.py` | Directed import graph; SCC detection |
| Dead code | `analyzers/dead_code_ast.py` | Public top-level names (AST refs) and public methods never referenced anywhere else |
| Internal refs | `analyzers/internal_refs.py` | `from mod import name` where `mod` exists but has no `name` |
| Unused deps | `analyzers/unused_deps.py` | Packages in requirements files never imported |
| Doc examples | `analyzers/doc_examples.py` | Fenced ` ```python ` blocks in Markdown that fail `ast.parse` |

---

## Scoring model

`scorer.compute_scores(file_metrics_list, config)` returns a `ScoreResult`
containing an `overall` float (0-100) and per-category `CategoryScore`
objects.

Each category has an independent formula operating on the flat list of
`FileMetrics` (and their nested `FunctionMetrics`). All formulas are
"defect density" shaped — issues and penalties are normalised by lines of
code — so large files and small files compete on equal terms.

| Category | Weight | Formula sketch |
|---|---|---|
| **Complexity** | 15 | Average per-function penalty: 0 for CC ≤ 5, linear rise to 50 at CC 20, steeper above; `100 - avg_penalty` |
| **Structure** | 10 | Per-function penalties for over-limit length and nesting, per-file penalty for over-limit total lines; `100 - avg_penalty` |
| **Duplication** | 10 | `100 - (dup_lines / total_lines) × 250` |
| **Documentation** | 8 | `func_coverage × 0.75 + module_coverage × 0.25`, each 0-100 |
| **Style** | 12 | Severity-weighted issue count per 100 LOC; `100 - density × 8` |
| **Security** | 15 | Same density formula; higher per-issue weights (hardcoded secret = 20, shell=True = 15) |
| **Correctness** | 15 | Same density formula; highest weights (unresolved-import = 20, type-error = 12) |
| **Coverage** | 15 | Line-coverage % from `coverage.py`; always 100 until `--check-coverage` is enabled |

**Overall**: `Σ (category_score × weight) / Σ weights`, then clamped 0-100.

**Grade**: A ≥ 90 · B ≥ 80 · C ≥ 70 · D ≥ 60 · F < 60.

---

## Report layer

`report.py` has two parts:

**`build_summary()`** — assembles a language-agnostic dict from the list of
`FileMetrics` and the `ScoreResult`. This dict is the single intermediate
representation consumed by all renderers, and what `scan --format json`
serialises directly.

**`render_*(summary)`** — one function per output format:

| Function | Format | Use case |
|---|---|---|
| `render_text` | Coloured terminal | Human at a terminal |
| `render_json` | Machine-readable JSON | CI artefacts, piping, `compare` |
| `render_markdown` | GitHub-flavoured Markdown | PR comments |
| `render_sarif` | SARIF 2.1.0 | GitHub code-scanning / Security tab |
| `render_gitlab` | GitLab Code Quality JSON | GitLab MR widget |
| `render_html` | Self-contained HTML | Local browsing |
| `render_badge` | Shields.io-compatible JSON | README badge |

---

## Suppression system

`suppress.py` is the only module that touches `codequality: ignore` markers.
`scanner.analyze_file()` calls it after the analyzer returns so the same
logic applies regardless of language.

`parse(source)` scans every line for `codequality: ignore` (blanket) or
`codequality: ignore[rule1,rule2]` (scoped). It returns a
`dict[int, set[str] | None]` mapping line number to suppressed rule set
(`None` = blanket, suppresses everything).

`filter_issues(issues, suppressions)` removes suppressed issues and returns
`(filtered_list, suppressed_count)`. Suppressed issues still contribute
their suppressed count to the summary (`"suppressed": N` in JSON) so they
stay visible.

`annotate_functions(functions, suppressions)` attaches suppression state to
`FunctionMetrics` so the scorer can skip suppressed complexity/length/nesting
penalties.

---

## Fixer

`fixer.py` implements eight rules with a single, unambiguous correct
rewrite:

| Rule | Transform |
|---|---|
| `trailing-whitespace` | Strip trailing whitespace, preserve line ending |
| `f-string-no-placeholder` | Remove `f`/`F` prefix from f-strings whose content has no real `{…}` placeholder |
| `comparison-to-none` | `== None` → `is None`, `!= None` → `is not None` |
| `comparison-to-true` | `x == True` → `x`, `x == False` → `not x`, etc. (simple names/attributes only) |
| `redundant-else` | Remove the `else:` line and dedent its body by one level |
| `bare-except` | `except:` → `except Exception:` |
| `tab-indent` | Expand tabs found in a line's *leading* whitespace to 4 spaces (tabs elsewhere on the line are left alone) |
| `unused-import` | Delete a top-level (zero-indent), single-name `import`/`from ... import` statement |

The engine processes issues bottom-to-top within each file so that the
`redundant-else`/`unused-import` line removals don't shift the line
numbers of earlier issues. Files are read and written with `newline=""`
to preserve CRLF endings. `--dry-run` produces a unified diff without
writing any files.

---

## Standalone subcommands

These live in top-level modules and do not feed into the main scan/diff
pipeline. They are invoked directly from `cli.py` and produce their own
output.

### Git-history analysis

| Subcommand | Module | What it does |
|---|---|---|
| `churn` | `churn.py` | Rework rate per file (lines added then reverted), AI vs. human |
| `edit-distance` | `edit_distance.py` | % of lines a commit added that still exist at HEAD |
| `commit-lint` | `commit_lint.py` | Subject line quality (length, specificity, placeholder detection) |
| `hallucination-rate` | `hallucination_metrics.py` | `--check-imports`/`--check-types` findings per 1,000 lines, AI vs. human |
| `ai-report` | `ai_report.py` | Dashboard: churn + edit-distance + commit-lint + hallucination-rate |
| `ownership` | `ownership.py` | Per-file bus factor (git blame concentration + AI-commit fraction) |
| `todo-age` | `todo_age.py` | Age TODO/FIXME/HACK markers via git blame |
| `dead-code-confidence` | `dead_code_confidence.py` | Dead-code findings aged via git blame |
| `history-secrets` | `history_secrets.py` | Secrets ever committed (even since removed) via `git log -S` |
| `suppression-debt` | `suppression_debt.py` | Age blanket `# noqa` / `# type: ignore` / `codequality: ignore` via git blame |

### Cross-reference / structural analysis

| Subcommand | Module | What it does |
|---|---|---|
| `api-diff` | `api_diff.py` | Public API changes between any two git refs |
| `complexity-regression` | `complexity_regression_diff.py` | Per-function complexity delta between any two git refs |
| `hotspots` | `hotspots.py` | Complexity × change frequency (highest-risk refactor targets) |
| `complexity-coverage-risk` | `complexity_coverage_risk.py` | Complexity × no-test-file (what to test first) |
| `dead-code-confidence` | `dead_code_confidence.py` | Cross-file unreferenced names aged via git blame |
| `dependency-check` | `dependency_check.py` | requirements.txt/pyproject.toml structural consistency |
| `dependency-risk` | `dependency_risk.py` | Import frequency × dependency-check flags |
| `orphaned-config` | `orphaned_config.py` | CI/Makefile/docker-compose references to non-existent paths |
| `arch-conformance` | `arch_conformance.py` | Config-driven import-direction check across named layers |
| `conventions` | `conventions.py` | Repo's own dominant style conventions; files that deviate |
| `annotation-coverage` | `annotation_coverage.py` | % of public function params/returns with type annotations |

### Environment / runtime checks

| Subcommand | Module | What it does |
|---|---|---|
| `env-check` | `env_check.py` | `os.environ` reads vs. `.env.example`/README documented vars |
| `config-drift` | `config_drift.py` | Sibling env config files whose key sets diverge |
| `config-check` | `config_validate.py` | Validate `.codequality.toml` itself |
| `migration-check` | `migration_check.py` | Up-only Django/Alembic/SQL migrations (no rollback) |
| `feature-flags` | `feature_flags.py` | Age feature-flag references via git blame |
| `large-files` | `large_files.py` | Binary blobs or oversized files committed to git |

### Test quality

| Subcommand | Module | What it does |
|---|---|---|
| `mutation` | `mutation.py` | Mutation testing (`mutmut` under the hood) |
| `flakiness` | `flakiness.py` | Run test suite N times; find non-deterministic results |
| `scaffold-properties` | `property_scaffold.py` | Property-based test gaps + Hypothesis stub generation |

### Workflow

| Subcommand | Module | What it does |
|---|---|---|
| `trend` | `history.py` | Sparkline + delta table from `--record-history` file |
| `complexity-trend` | `complexity_trend.py` | Per-function complexity snapshots over time |
| `baseline` | `baseline.py` | Snapshot current issues for baseline-forgiveness mode |
| `pipeline` | `pipeline.py` | Orchestrate external format/lint/test steps + own scan |
| `init` | `project_init.py` | Scaffold `.codequality.toml` + GitHub Actions CI workflow |
| `compare` | `report_compare.py` | Delta between two `scan --format json` reports |
| `fix` | `fixer.py` | Apply the 5 auto-fixable style rules in-place |
| `explain` | `rules.py` | Look up a rule symbol by name |

---

## CLI dispatch

`cli.py` is the only module that touches `argparse`. It is structured as:

1. **Parser builders** (`_add_*_subparser`) — one per subcommand, each wires
   up the arguments for that command. All share `_add_common_args` for
   `path`, `--config`, `--format`, `--output`, `--fail-under`, `--no-color`.
2. **Command handlers** (`cmd_*`) — one per subcommand. Each loads config,
   calls the appropriate module, and calls `_render`/`_emit`.
3. **`_COMMANDS` dict** — maps subcommand name → handler; `main()` looks
   the handler up and calls it, returning its exit code.
4. **`_render(summary, fmt)`** — dispatches to the right `render_*` function
   in `report.py`.
5. **`_emit(text, output_path)`** — writes to a file or prints to stdout.

Exit codes: `0` = passed, `1` = below threshold or regression, `2` = usage
or git error.

---

## Rules registry

`rules.py` contains `RULES` — a `dict[str, dict]` mapping every rule symbol
(95 total) to its category, scope (`scan` / `diff` / `api-diff` / …), and
human-readable description. `codequality explain <symbol>` reads from this
dict. The registry is the canonical list; adding a new check means wiring in
the Issue emission **and** adding the symbol here so `explain` can find it.

---

## Adding a new check

1. **Write the issue-finding function** in the appropriate analyzer module.
   Signature: `foo_issues(tree, path, only_lines=None) → list[Issue]`.
   Only use `ast.walk`; never execute code from the scanned repo.

2. **Wire it into `python_analyzer.analyze()`** by importing it and adding
   it to the concatenated issue list near line 840.

3. **Register the symbol** in `rules.py` under the right category.

4. **Add tests** in `tests/test_*.py` covering the true-positive and
   false-negative cases; run `python -m pytest`.

For a new standalone subcommand, additionally:
- Add a module at `codequality/<name>.py` with a `compute()` + `render_text()` pair.
- Add `_add_<name>_subparser()` and `cmd_<name>()` in `cli.py`.
- Wire the name into `_COMMANDS`.

---

## Invariants

- **No LLM calls.** Every number is derived from parsing or git commands.
- **No network access.** Dependency checks are structural (manifest files),
  not registry lookups.
- **No code execution** (except the two opt-in passes: `--check-coverage`
  runs the repo's test suite; `--check-imports` imports stdlib modules only,
  never third-party packages from the scanned repo).
- **Same input → same output.** All randomness is excluded; the score is
  deterministic and safe to gate CI on.
- **Defect density, not raw counts.** All scoring formulas normalise by lines
  of code so large and small files compete fairly.

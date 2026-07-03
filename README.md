# codequality

A deterministic, programmatic code quality scanner. Think "Grammarly for
code": it reads your repo and gives you a 0-100 score with a letter grade,
broken down by category, plus a list of specific issues with file:line
locations.

No LLM calls, no network access, no required external dependencies. Every
number comes from parsing the code (Python via `ast`, other languages via
`tree-sitter` if installed, otherwise heuristics) and running it through
fixed, documented formulas. The same input always produces the same
output — safe to gate a CI pipeline on.

Two modes:

- **`scan`** — score the whole repository. Use this to track overall
  codebase health over time.
- **`diff`** — score only the code that changed (via `git diff`). Use this
  to gate pull requests without requiring the entire repo to be clean
  first.

Both work in a pipeline (JSON/markdown output, exit codes for gating) and
standalone on your machine (colored terminal output).

## Install

```bash
pip install .
```

Or run it with zero install, straight from a checkout:

```bash
python3 -m codequality scan .
```

### Installing it into a different repo

`codequality` isn't published to PyPI; install it straight from git into
whatever repo you want scored:

```bash
pip install "git+https://github.com/Hiemenz/code-quality.git"

# for real per-function parsing of JS/TS/Go/Java/C/C++/C#/Ruby/PHP/Rust/
# Kotlin/Swift/Scala instead of the heuristic fallback (see "Language
# support" below):
pip install "git+https://github.com/Hiemenz/code-quality.git#egg=codequality[treesitter]"
```

Then, in the target repo:

1. **Add a config file** at the repo root — `.codequality.toml`:
   ```toml
   [thresholds]
   fail_under = 60   # start low for an existing codebase; raise it over time

   [limits]
   exclude = ["vendor/*", "migrations/*", "node_modules/*"]
   ```
   (See [Configuration](#configuration) below for every field.)

2. **Copy the CI workflow** — `.github/workflows/code-quality.yml` in this
   repo is a ready-to-use template: full `scan` on every push to main
   (tracked as an artifact, not gated), plus a `diff` scan gated at
   `--fail-under` on every PR with the result posted as a PR comment. Copy
   it into the target repo and adjust the branch name/threshold. Not on
   GitHub Actions? The same two `codequality scan`/`codequality diff`
   commands work in any CI system — see [CI pipeline example](#ci-pipeline-example).

3. **Run it locally once** to see where the repo stands:
   ```bash
   codequality scan . --fail-under 0   # no gating, just the report
   ```
   The `worst_files` list in `--format json` (or the "Lowest-scoring files"
   table in the terminal report) is your prioritized to-do list.

4. **Track improvement over time** — see [Tracking score history](#tracking-score-history).

## Usage

```bash
# Full repo, human-readable report in the terminal
codequality scan .

# Only what changed vs. main, as markdown (e.g. for a PR comment)
codequality diff . --base origin/main --format markdown

# Only what changed vs. the last commit (default when the working tree is
# clean), or vs. HEAD when there are uncommitted changes -- handy for
# "did I just make this worse?" while you're still editing
codequality diff .

# CI gate: non-zero exit if the score is below the threshold
codequality scan . --fail-under 70
echo $?

# GitHub code-scanning annotations
codequality scan . --format sarif --output results.sarif

# Track the score over time, then look at the trend
codequality scan . --record-history history.jsonl
codequality trend history.jsonl
```

`scan` and `diff` share the same flags:

| Flag | Meaning |
|---|---|
| `path` | Root directory to analyze (default `.`) |
| `--format` | `text` (default, colored), `json`, `markdown`, or `sarif` |
| `--output FILE` | Write the report to a file instead of stdout |
| `--fail-under N` | Exit 1 if the overall score is below N |
| `--config PATH` | Explicit config file (see below) |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--no-generic` | Only analyze Python; skip the analyzer for other languages |

`diff` additionally takes `--base REF` and `--head REF` (default: auto-detect,
see above).

`scan` additionally takes `--record-history FILE`, which appends this run's
overall/category scores as one JSON line to `FILE` — see
[Tracking score history](#tracking-score-history).

Exit codes: `0` = passed threshold, `1` = below threshold, `2` = usage/git error.

## How the score is built

Six categories, each 0-100, combined by weight into the overall score:

| Category | Default weight | What it measures |
|---|---|---|
| Complexity | 25 | Cyclomatic complexity per function (McCabe-style: branches, loops, boolean operators, comprehensions, `except` clauses) |
| Structure | 15 | Function length, nesting depth, file length |
| Duplication | 15 | Copy-pasted blocks (6+ line sliding-window hash, cross-file) |
| Documentation | 10 | Docstring coverage on public functions and modules |
| Style | 15 | Long lines, trailing whitespace, TODO markers, bare `except:`, wildcard imports, mutable default arguments, unused imports/variables, non-conventional naming |
| Security | 20 | `eval`/`exec`, `shell=True`, unsafe deserialization (`pickle`, `yaml.load`), hardcoded-looking secrets |

Each category is scored from *defect density*, not raw counts — a 3,000-line
file isn't unfairly punished the same as a 30-line file for having one long
function. See `codequality/scorer.py` for the exact formulas; they're
simple arithmetic on purpose, not a black box.

Python-only checks (no equivalent yet for other languages): unused
imports/variables, `snake_case`/`PascalCase` naming, `pickle`/`yaml.load`
deserialization checks. Hardcoded-secret and `eval`/`exec` detection run
for every language via a line-level regex.

### Language support

**Python gets full analysis**: real functions, real complexity, real
nesting, via `ast` — no extra install required.

**Other languages** get real per-function analysis too, *if* the optional
`tree-sitter` extra is installed:

```bash
pip install "codequality[treesitter]"
```

This adds real function/method boundaries, real cyclomatic complexity, and
real nesting depth for `.js .jsx .ts .tsx .java .go .c .cpp .cs .rb .php
.rs .kt .swift .scala` via [`tree-sitter-language-pack`](https://github.com/xberg-io/tree-sitter-language-pack)
grammars — the same idea as the Python path, just driven by a table of
node-kind names per language instead of Python's `ast` module.

**Without the extra installed**, those languages fall back to a lighter
heuristic pass: no parser, so complexity is approximated from
branching-keyword density and nesting from indentation, at file
granularity rather than per-function. Treat those scores as directional.
The extra is optional (not a base dependency) specifically so the default
install stays dependency-free and installable anywhere `pip` runs.

### Diff mode is scoped to the actual change, not just the changed files

`codequality diff` doesn't just re-run the full-file analysis on whatever
files changed. It parses the unified diff to find exactly which lines were
added, then:

- only counts a function toward complexity/structure/nesting scoring if
  the change actually touches that function;
- only runs line-level checks (long lines, TODOs, trailing whitespace, bare
  except, ...) on the added lines themselves.

So editing one line in a 40-function file scores that one line's context,
not the other 39 functions you didn't touch.

## Configuration

Optional `.codequality.toml` (or `.codequality.json`, or a
`[tool.codequality]` table in `pyproject.toml`) at the repo root:

```toml
[thresholds]
fail_under = 70

[weights]
complexity = 25
structure = 15
duplication = 15
documentation = 10
style = 15
security = 20

[limits]
max_line_length = 120
max_function_lines = 60
max_file_lines = 600
max_complexity = 10
max_nesting = 4
docstring_min_lines = 8   # don't demand docstrings on tiny helpers

exclude = ["migrations/*", "vendor/*"]
include_generic_languages = true
```

All fields are optional and merge over the built-in defaults.

## CI pipeline example

See `.github/workflows/code-quality.yml`: it runs a full `scan` on every
push (tracked as an artifact), and a `diff` scan gated at `--fail-under 70`
on every PR, posting the markdown report as a PR comment. Adapt the same
two commands to any other CI system — it's a plain CLI with JSON/markdown
output and a real exit code.

For GitHub's native code-scanning UI (inline annotations on the Security
tab instead of a PR comment), add a SARIF step:

```yaml
- run: codequality scan . --format sarif --output results.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

## Tracking score history

`scan --record-history FILE` appends one JSON line per run (timestamp,
overall score, per-category scores) to `FILE`. `codequality trend FILE`
renders a sparkline and a score/grade/delta table across every recorded
run:

```bash
codequality scan . --record-history codequality-history.jsonl
codequality trend codequality-history.jsonl
```

The cheapest way to persist `FILE` across CI runs is to just commit it to
the repo (add a step to your main-branch workflow that commits the updated
`codequality-history.jsonl`) — it's a small, append-only file, so `git log`
on it doubles as an audit trail, and `trend` works the same whether you run
it in CI or locally.

## Development

```bash
python3 -m unittest discover -s tests
```

The tool dogfoods itself — `codequality scan .` on this repo is part of
sanity-checking any change to the analyzers or scorer.

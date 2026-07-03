# codequality

A deterministic, programmatic code quality scanner. Think "Grammarly for
code": it reads your repo and gives you a 0-100 score with a letter grade,
broken down by category, plus a list of specific issues with file:line
locations.

No LLM calls, no network access, no external dependencies. Every number
comes from parsing the code (Python via `ast`, other languages via
heuristics) and running it through fixed, documented formulas. The same
input always produces the same output — safe to gate a CI pipeline on.

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
```

`scan` and `diff` share the same flags:

| Flag | Meaning |
|---|---|
| `path` | Root directory to analyze (default `.`) |
| `--format` | `text` (default, colored), `json`, or `markdown` |
| `--output FILE` | Write the report to a file instead of stdout |
| `--fail-under N` | Exit 1 if the overall score is below N |
| `--config PATH` | Explicit config file (see below) |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--no-generic` | Only analyze Python; skip the lighter heuristic analyzer for other languages |

`diff` additionally takes `--base REF` and `--head REF` (default: auto-detect,
see above).

Exit codes: `0` = passed threshold, `1` = below threshold, `2` = usage/git error.

## How the score is built

Five categories, each 0-100, combined by weight into the overall score:

| Category | Default weight | What it measures |
|---|---|---|
| Complexity | 30 | Cyclomatic complexity per function (McCabe-style: branches, loops, boolean operators, comprehensions, `except` clauses) |
| Structure | 20 | Function length, nesting depth, file length |
| Duplication | 15 | Copy-pasted blocks (6+ line sliding-window hash, cross-file) |
| Documentation | 15 | Docstring coverage on public functions and modules |
| Style | 20 | Long lines, trailing whitespace, TODO markers, bare `except:`, wildcard imports, mutable default arguments |

Each category is scored from *defect density*, not raw counts — a 3,000-line
file isn't unfairly punished the same as a 30-line file for having one long
function. See `codequality/scorer.py` for the exact formulas; they're
simple arithmetic on purpose, not a black box.

**Python gets full analysis**: real functions, real complexity, real nesting,
via `ast`. **Other languages** (`.js .ts .java .go .c .cpp .cs .rb .php .rs
.kt .swift .scala`, ...) get a lighter pass: no parser, so complexity is
approximated from branching-keyword density and nesting from indentation,
at file granularity rather than per-function. This is clearly weaker than
the Python path — treat those scores as directional. Closing this gap
(e.g. with `tree-sitter`) is the natural v2 step; it was left out of v1 to
keep the tool dependency-free and installable anywhere `pip` runs.

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
complexity = 30
structure = 20
duplication = 15
documentation = 15
style = 20

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

## Development

```bash
python3 -m unittest discover -s tests
```

The tool dogfoods itself — `codequality scan .` on this repo is part of
sanity-checking any change to the analyzers or scorer.

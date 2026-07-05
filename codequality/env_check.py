"""Environment variable documentation drift.

Compares environment variables actually *referenced* in the codebase
against the ones that are actually *documented*, and flags the mismatch in
either direction:

- **`undocumented-env-var`** -- read somewhere in the code but not found in
  any documented source.
- **`unused-documented-env-var`** -- documented, but never referenced
  anywhere in the scanned code.

Both are reported `category="documentation"`, `severity="info"` -- this is
a heuristic signal, not a hard rule. Detection for Python is real (`ast`,
walking for the three standard read forms: `os.environ["X"]`,
`os.environ.get("X")`, `os.getenv("X")`); everything else uses a
line-level regex fallback, the same "no full parser for every language, so
approximate" tradeoff `analyzers/generic_analyzer.py` already makes
elsewhere in this tool. Expect noise, especially from the non-Python
fallback (a `getenv("X")`-shaped call that isn't actually reading a process
environment variable, a `.env.example` value that's dead as documentation
but still valid, etc.).

"Documented" is read from whichever of these actually exist -- no
guessing beyond that:

- `.env.example` / `.env.sample` / `.env.template` at the repo root,
  simple `KEY=value` line format.
- A fenced code block in `README.md` that looks like a `.env` file (lines
  mostly matching `KEY=value`, keys `UPPER_SNAKE_CASE`-shaped), or a
  markdown table with a header cell that looks like an environment
  variable reference (e.g. "Environment Variable", "Env Var").

If neither exists, the documented side is simply empty -- every code
usage is reported as `undocumented-env-var` (nothing to guess at), and no
`unused-documented-env-var` can ever fire (there's nothing documented to
be unused). This is a purely textual, offline check like everything else
in this tool: no execution of target code, no network access.
"""

import ast
import os
import re

from codequality.analyzers.base import Issue
from codequality.scanner import discover_files

ENV_EXAMPLE_NAMES = (".env.example", ".env.sample", ".env.template")

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# One regex per non-Python "read an env var" shape this tool knows about.
# Deliberately best-effort: matches the common idiom per language/ecosystem,
# not a real parse of that language's syntax.
_GENERIC_ENV_PATTERNS = [
    re.compile(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)"),                       # JS/TS: process.env.FOO
    re.compile(r"process\.env\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"),      # JS/TS: process.env["FOO"]
    re.compile(r"\bgetenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),               # C/PHP/Ruby-Kernel: getenv("FOO")
    re.compile(r"\bos\.Getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),           # Go: os.Getenv("FOO")
    re.compile(r"\bENV\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]"),             # Ruby: ENV["FOO"]
    re.compile(r"\bENV\.fetch\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),           # Ruby: ENV.fetch("FOO")
    re.compile(r"\bSystem\.getenv\(\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]"),       # Java: System.getenv("FOO")
]


class EnvUsage:
    def __init__(self, name, file, line):
        self.name = name
        self.file = file
        self.line = line


class EnvDoc:
    def __init__(self, name, file, line):
        self.name = name
        self.file = file
        self.line = line


def _read_source(root, rel_path):
    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _is_os_environ_attr(node):
    """True for the AST node `os.environ` (an Attribute `environ` on Name `os`)."""
    return (
        isinstance(node, ast.Attribute) and node.attr == "environ"
        and isinstance(node.value, ast.Name) and node.value.id == "os"
    )


def _is_os_name(node, name):
    """True for the AST node `os.<name>` (an Attribute on Name `os`)."""
    return (
        isinstance(node, ast.Attribute) and node.attr == name
        and isinstance(node.value, ast.Name) and node.value.id == "os"
    )


def _string_const(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _PythonEnvVisitor(ast.NodeVisitor):
    """Walks a Python AST for the three standard "read an env var" shapes:
    `os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")`.
    """

    def __init__(self, rel_path):
        self.rel_path = rel_path
        self.usages = []

    def visit_Subscript(self, node):
        """Catches `os.environ["X"]`."""
        if _is_os_environ_attr(node.value):
            key = node.slice
            # Python < 3.9 wraps the subscript in an ast.Index; handle both.
            if isinstance(key, ast.Index):
                key = key.value
            name = _string_const(key)
            if name is not None:
                self.usages.append(EnvUsage(name, self.rel_path, node.lineno))
        self.generic_visit(node)

    def visit_Call(self, node):
        """Catches `os.environ.get("X")` and `os.getenv("X")`."""
        func = node.func
        is_environ_get = (
            isinstance(func, ast.Attribute) and func.attr == "get" and _is_os_environ_attr(func.value)
        )
        is_getenv = _is_os_name(func, "getenv")
        if is_environ_get or is_getenv:
            if node.args:
                name = _string_const(node.args[0])
                if name is not None:
                    self.usages.append(EnvUsage(name, self.rel_path, node.lineno))
        self.generic_visit(node)


def _python_env_usages(rel_path, source):
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    visitor = _PythonEnvVisitor(rel_path)
    visitor.visit(tree)
    return visitor.usages


def _generic_env_usages(rel_path, source):
    """Heuristic, line-level fallback for non-Python source (JS, Go, Ruby,
    PHP, Java, C, ...) -- see module docstring. Best-effort; expect noise.
    """
    usages = []
    for i, line in enumerate(source.splitlines(), start=1):
        for pattern in _GENERIC_ENV_PATTERNS:
            for m in pattern.finditer(line):
                usages.append(EnvUsage(m.group(1), rel_path, i))
    return usages


def find_env_usages(root, config=None):
    """Every environment-variable read found across the scanned codebase.

    Python files get a real `ast` walk; every other supported (generic)
    extension gets the regex fallback above.
    """
    exclude = config.exclude if config is not None else []
    include_generic = config.include_generic_languages if config is not None else True
    files = discover_files(root, exclude, include_generic=include_generic)

    usages = []
    for rel_path, lang in files:
        source = _read_source(root, rel_path)
        if source is None:
            continue
        if lang == "python":
            usages.extend(_python_env_usages(rel_path, source))
        else:
            usages.extend(_generic_env_usages(rel_path, source))
    return usages


def _parse_env_file(root, rel_path):
    """Parses a simple `.env`-style file: `KEY=value` per line, `#`
    comments and blank lines ignored, optional leading `export `.
    """
    source = _read_source(root, rel_path)
    if source is None:
        return []
    docs = []
    for i, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if _ENV_KEY_RE.match(key):
            docs.append(EnvDoc(key, rel_path, i))
    return docs


def find_env_example_file(root):
    """First `.env.example`-style file found at the repo root, or None."""
    for name in ENV_EXAMPLE_NAMES:
        if os.path.isfile(os.path.join(root, name)):
            return name
    return None


_FENCE_RE = re.compile(r"^\s*```")


def _find_fenced_blocks(lines):
    """Yields (lineno_of_first_body_line, block_lines) for each ``` ... ```
    fenced code block in `lines`.
    """
    i = 0
    while i < len(lines):
        if not _FENCE_RE.match(lines[i]):
            i += 1
            continue
        start = i + 1
        j = start
        while j < len(lines) and not _FENCE_RE.match(lines[j]):
            j += 1
        yield start + 1, lines[start:j]
        i = j + 1


def _env_lines_in_block(start_lineno, block_lines):
    """(lineno, line) for each non-blank/non-comment line in a fenced block."""
    return [
        (start_lineno + k, ln) for k, ln in enumerate(block_lines)
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _readme_fenced_env_blocks(rel_path, source):
    """Fenced code blocks in a README that look like a `.env` file: most
    non-blank/non-comment lines are `KEY=value` with an upper-snake-case key.
    """
    docs = []
    lines = source.splitlines()
    for start_lineno, block_lines in _find_fenced_blocks(lines):
        candidate = _env_lines_in_block(start_lineno, block_lines)
        if not candidate:
            continue
        matches = [(lineno, ln) for lineno, ln in candidate if re.match(r"^[A-Z_][A-Z0-9_]*=", ln.strip())]
        if len(matches) < max(1, len(candidate) * 0.6):
            continue
        for lineno, ln in matches:
            key = ln.strip().split("=", 1)[0].strip()
            docs.append(EnvDoc(key, rel_path, lineno))
    return docs


_ENV_TABLE_HEADER_RE = re.compile(r"env(?:ironment)?[\s_-]*var(?:iable)?s?", re.IGNORECASE)


def _is_table_separator_row(line):
    return "-" in line and "|" in line


def _table_rows_after(lines, header_idx):
    """(lineno, cells) for each markdown table row following the header +
    separator at `header_idx`/`header_idx + 1`.
    """
    rows = []
    j = header_idx + 2
    while j < len(lines) and lines[j].strip().startswith("|"):
        cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
        rows.append((j + 1, cells))
        j += 1
    return rows


def _readme_table_env_vars(rel_path, source):
    """Markdown table whose header row has a cell like "Environment
    Variable"/"Env Var" -- first column of each following row is treated
    as the variable name (backticks/whitespace stripped).
    """
    docs = []
    lines = source.splitlines()
    for i, line in enumerate(lines):
        if "|" not in line or not _ENV_TABLE_HEADER_RE.search(line):
            continue
        if i + 1 >= len(lines) or not _is_table_separator_row(lines[i + 1]):
            continue
        for lineno, cells in _table_rows_after(lines, i):
            if not cells:
                continue
            key = cells[0].strip("`").strip()
            if _ENV_KEY_RE.match(key):
                docs.append(EnvDoc(key, rel_path, lineno))
    return docs


def find_readme_env_vars(root):
    """Best-effort documented env vars found in README.md -- fenced `.env`-
    style blocks and/or a markdown table with an env-var-looking header.
    Empty if README.md doesn't exist or has neither shape; this is
    deliberately not a general markdown parser.
    """
    for name in ("README.md", "readme.md"):
        source = _read_source(root, name)
        if source is not None:
            return _readme_fenced_env_blocks(name, source) + _readme_table_env_vars(name, source)
    return []


def find_documented_env_vars(root):
    """Every documented env var found in `root`, from whichever of
    `.env.example`-style files and/or README.md actually exist. Empty (not
    a guess) if neither is present or neither has a recognizable shape.
    """
    docs = []
    env_file = find_env_example_file(root)
    if env_file is not None:
        docs.extend(_parse_env_file(root, env_file))
    docs.extend(find_readme_env_vars(root))
    return docs


def _undocumented_issue(usage):
    return Issue(
        file=usage.file, line=usage.line, category="documentation", severity="info",
        symbol="undocumented-env-var",
        message=(
            f"{usage.name} is read here but isn't documented in .env.example/.env.sample/.env.template "
            f"or a recognizable README.md env-var section"
        ),
    )


def _unused_documented_issue(doc):
    return Issue(
        file=doc.file, line=doc.line, category="documentation", severity="info",
        symbol="unused-documented-env-var",
        message=(
            f"{doc.name} is documented here but never referenced in the scanned code "
            f"(heuristic -- non-Python usage detection is best-effort, see README)"
        ),
    )


def check(root, config=None):
    """Runs the env-var documentation drift check against `root` and
    returns a flat list[Issue]. Never raises; returns [] if there's
    nothing to flag either way.
    """
    usages = find_env_usages(root, config)
    docs = find_documented_env_vars(root)

    used_names = {u.name for u in usages}
    documented_names = {d.name for d in docs}

    issues = [_undocumented_issue(u) for u in usages if u.name not in documented_names]
    issues.extend(_unused_documented_issue(d) for d in docs if d.name not in used_names)
    return issues


def render_text(issues):
    if not issues:
        return "Environment Variable Check\n\nNo issues found."
    lines = [f"Environment Variable Check ({len(issues)} issue(s))", ""]
    for issue in sorted(issues, key=lambda i: (i.file, i.line, i.symbol)):
        lines.append(f"  {issue.file}:{issue.line} [{issue.symbol}] {issue.message}")
    return "\n".join(lines)

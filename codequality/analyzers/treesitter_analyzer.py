"""Real per-function analysis for non-Python languages, via tree-sitter.

This is the accuracy upgrade `generic_analyzer` explicitly calls out as v1's
known gap: instead of whole-file keyword-density heuristics, it parses a
real syntax tree, finds actual function/method boundaries, and computes
cyclomatic complexity and nesting depth from real AST node kinds -- the
same idea as `python_analyzer`, just driven by a table of node-kind names
per language instead of Python's `ast` module.

`tree-sitter-language-pack` is an *optional* dependency (`pip install
codequality[treesitter]`) so the base install stays dependency-free, as
documented in the README. When it isn't installed, `AVAILABLE` is False and
`scanner.py` falls back to `generic_analyzer`.

The per-language node-kind tables below were derived by parsing small code
samples with each grammar and inspecting the resulting tree (see the
project's dev notes) rather than from each grammar's formal spec -- treat
them as a good-faith mapping tuned for common code, not exhaustive
coverage of every language construct.
"""

import re

from codequality.analyzers.base import FileMetrics, FunctionMetrics, Issue
from codequality.analyzers.generic_analyzer import (
    LINE_COMMENT_PREFIXES,
    _scan_line,
)

try:
    from tree_sitter_language_pack import get_parser as _get_ts_parser
    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when the extra isn't installed
    _get_ts_parser = None
    AVAILABLE = False

_BOOL_OPS_C_STYLE = re.compile(r"&&|\|\|")
_BOOL_OPS_RUBY = re.compile(r"&&|\|\||\band\b|\bor\b")

LANGUAGES = {
    "javascript": dict(
        function_kinds={"function_declaration", "function_expression", "generator_function_declaration",
                         "generator_function", "arrow_function", "method_definition"},
        complexity_kinds={"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
                           "catch_clause", "ternary_expression", "switch_case"},
        nesting_kinds={"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
                        "catch_clause", "switch_statement", "try_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "typescript": dict(
        function_kinds={"function_declaration", "function_expression", "generator_function_declaration",
                         "generator_function", "arrow_function", "method_definition"},
        complexity_kinds={"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
                           "catch_clause", "ternary_expression", "switch_case"},
        nesting_kinds={"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
                        "catch_clause", "switch_statement", "try_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "go": dict(
        function_kinds={"function_declaration", "method_declaration", "func_literal"},
        complexity_kinds={"if_statement", "for_statement", "expression_case", "type_case", "communication_case"},
        nesting_kinds={"if_statement", "for_statement", "expression_switch_statement", "type_switch_statement",
                        "select_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "java": dict(
        function_kinds={"method_declaration", "constructor_declaration"},
        complexity_kinds={"if_statement", "for_statement", "enhanced_for_statement", "while_statement",
                           "do_statement", "catch_clause", "ternary_expression", "switch_label"},
        nesting_kinds={"if_statement", "for_statement", "enhanced_for_statement", "while_statement", "do_statement",
                        "catch_clause", "switch_expression", "switch_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "c": dict(
        function_kinds={"function_definition"},
        complexity_kinds={"if_statement", "for_statement", "while_statement", "do_statement", "case_statement"},
        nesting_kinds={"if_statement", "for_statement", "while_statement", "do_statement", "switch_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "cpp": dict(
        function_kinds={"function_definition"},
        complexity_kinds={"if_statement", "for_statement", "while_statement", "do_statement", "case_statement",
                           "catch_clause"},
        nesting_kinds={"if_statement", "for_statement", "while_statement", "do_statement", "switch_statement",
                        "try_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "csharp": dict(
        function_kinds={"method_declaration", "constructor_declaration", "local_function_statement"},
        complexity_kinds={"if_statement", "for_statement", "foreach_statement", "while_statement", "do_statement",
                           "catch_clause", "conditional_expression", "switch_section"},
        nesting_kinds={"if_statement", "for_statement", "foreach_statement", "while_statement", "do_statement",
                        "switch_statement", "try_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "ruby": dict(
        function_kinds={"method", "singleton_method"},
        complexity_kinds={"if", "unless", "while", "until", "for", "rescue", "when"},
        nesting_kinds={"if", "unless", "while", "until", "for", "begin", "case"},
        bool_op_re=_BOOL_OPS_RUBY,
    ),
    "php": dict(
        function_kinds={"function_definition", "method_declaration"},
        complexity_kinds={"if_statement", "for_statement", "foreach_statement", "while_statement", "do_statement",
                           "catch_clause", "conditional_expression", "case_statement"},
        nesting_kinds={"if_statement", "for_statement", "foreach_statement", "while_statement", "do_statement",
                        "switch_statement", "try_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "rust": dict(
        function_kinds={"function_item"},
        complexity_kinds={"if_expression", "if_let_expression", "for_expression", "while_expression",
                           "while_let_expression", "match_arm"},
        nesting_kinds={"if_expression", "if_let_expression", "for_expression", "while_expression",
                        "while_let_expression", "match_expression"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "kotlin": dict(
        function_kinds={"function_declaration"},
        complexity_kinds={"if_expression", "for_statement", "while_statement", "do_while_statement", "catch_block",
                           "when_entry"},
        nesting_kinds={"if_expression", "for_statement", "while_statement", "do_while_statement", "when_expression",
                        "try_expression"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "swift": dict(
        function_kinds={"function_declaration"},
        complexity_kinds={"if_statement", "for_statement", "while_statement", "repeat_while_statement",
                           "catch_clause", "switch_entry", "guard_statement"},
        nesting_kinds={"if_statement", "for_statement", "while_statement", "repeat_while_statement",
                        "switch_statement", "do_statement"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
    "scala": dict(
        function_kinds={"function_definition"},
        complexity_kinds={"if_expression", "for_expression", "while_expression", "do_while_expression",
                           "catch_clause", "case_clause"},
        nesting_kinds={"if_expression", "for_expression", "while_expression", "do_while_expression",
                        "match_expression", "try_expression"},
        bool_op_re=_BOOL_OPS_C_STYLE,
    ),
}

_PARAM_KIND_HINT = "parameter"
_NAME_LEAF_KINDS = {"identifier", "field_identifier", "simple_identifier", "type_identifier"}

_CAMEL_RE = re.compile(r"^_?[a-z][a-zA-Z0-9]*$")
_PASCAL_RE = re.compile(r"^_?[A-Z][a-zA-Z0-9]*$")
_SNAKE_RE = re.compile(r"^_{0,2}[a-z][a-z0-9_]*$")
_MIXED_CAPS_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9]*$")

_STYLE_LABEL = {"camel": "camelCase", "pascal": "PascalCase", "snake": "snake_case",
                "mixedcaps": "MixedCaps (no underscores)"}

# Only languages with one well-established, low-ambiguity convention for
# function/method names -- C, C++, and PHP are deliberately left out since
# real-world style there is too mixed to check without a lot of noise.
_NAME_CHECK = {
    "javascript": ("camel", _CAMEL_RE),
    "typescript": ("camel", _CAMEL_RE),
    "java": ("camel", _CAMEL_RE),
    "csharp": ("pascal", _PASCAL_RE),
    "go": ("mixedcaps", _MIXED_CAPS_RE),
    "ruby": ("snake", _SNAKE_RE),
    "rust": ("snake", _SNAKE_RE),
    "kotlin": ("camel", _CAMEL_RE),
    "swift": ("camel", _CAMEL_RE),
    "scala": ("camel", _CAMEL_RE),
}

# Constructors are conventionally named after the class (PascalCase), not
# like an ordinary method -- checking them against the method-naming rule
# would flag every single constructor.
_CONSTRUCTOR_KINDS = {"constructor_declaration"}


def _naming_issue(fn, node, language, path):
    check = _NAME_CHECK.get(language)
    if check is None or node.kind() in _CONSTRUCTOR_KINDS or fn.name == "<anonymous>":
        return None
    style, pattern = check
    if pattern.match(fn.name):
        return None
    return Issue(path, fn.lineno, "style", "info", "bad-function-name",
                 f"Function '{fn.name}' should be {_STYLE_LABEL[style]} ({language} convention)")

_parser_cache = {}


def _get_parser(grammar):
    if grammar not in _parser_cache:
        _parser_cache[grammar] = _get_ts_parser(grammar)
    return _parser_cache[grammar]


def _grammar_for(language, path):
    if language == "typescript" and path.endswith(".tsx"):
        return "tsx"
    return language


def _node_text(node, source):
    return source[node.start_byte():node.end_byte()]


def _iter_named(node):
    yield node
    for i in range(node.named_child_count()):
        yield from _iter_named(node.named_child(i))


def _in_scope(lineno, end_lineno, only_lines):
    if only_lines is None:
        return True
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _extract_name(node, source):
    name_field = node.child_by_field_name("name")
    if name_field is not None:
        return _node_text(name_field, source)
    declarator = node.child_by_field_name("declarator")
    for _ in range(5):
        if declarator is None:
            break
        if declarator.kind() in _NAME_LEAF_KINDS:
            return _node_text(declarator, source)
        nested = declarator.child_by_field_name("declarator")
        if nested is None and declarator.named_child_count() > 0:
            nested = declarator.named_child(0)
        declarator = nested
    return "<anonymous>"


def _count_params(node):
    for i in range(node.named_child_count()):
        child = node.named_child(i)
        if _PARAM_KIND_HINT in child.kind():
            return child.named_child_count()
    return 0


def _function_stats(fn_node, cfg, source):
    """Cyclomatic complexity + max nesting depth for one function's subtree.

    Mirrors `python_analyzer`'s `_ComplexityVisitor`/`_NestingVisitor`: stops
    at nested function boundaries so a closure doesn't inflate its parent's
    score (that nested function is scored separately, on its own).
    """
    complexity = 1
    max_nesting = 0

    def _walk(node, depth):
        nonlocal complexity, max_nesting
        for i in range(node.named_child_count()):
            child = node.named_child(i)
            kind = child.kind()
            if kind in cfg["function_kinds"]:
                continue
            next_depth = depth
            if kind in cfg["complexity_kinds"]:
                complexity += 1
            if kind in cfg["nesting_kinds"]:
                next_depth = depth + 1
                max_nesting = max(max_nesting, next_depth)
            _walk(child, next_depth)

    _walk(fn_node, 0)
    complexity += len(cfg["bool_op_re"].findall(_node_text(fn_node, source)))
    return complexity, max_nesting


def _has_preceding_comment(lines, lineno, comment_prefix):
    if lineno < 2:
        return False
    prev = lines[lineno - 2].rstrip("\n").strip()
    return prev.startswith(comment_prefix) or prev.endswith("*/") or prev.startswith("/**")


def _build_function_metrics(fn_node, cfg, path, source, lines, comment_prefix):
    start = fn_node.start_position().row + 1
    end = fn_node.end_position().row + 1
    complexity, nesting = _function_stats(fn_node, cfg, source)
    return FunctionMetrics(
        file=path,
        name=_extract_name(fn_node, source),
        lineno=start,
        end_lineno=end,
        complexity=complexity,
        length=end - start + 1,
        nesting=nesting,
        params=_count_params(fn_node),
        has_docstring=_has_preceding_comment(lines, start, comment_prefix),
        is_public=True,
    )


def _function_issues(fn, node, language, path, limits):
    issues = []
    if fn.complexity > limits.max_complexity:
        severity = "error" if fn.complexity > limits.max_complexity * 2 else "warn"
        issues.append(
            Issue(path, fn.lineno, "complexity", severity, "high-complexity",
                  f"Function '{fn.name}' has cyclomatic complexity {fn.complexity} (limit {limits.max_complexity})")
        )
    if fn.length > limits.max_function_lines:
        issues.append(
            Issue(path, fn.lineno, "structure", "warn", "long-function",
                  f"Function '{fn.name}' is {fn.length} lines long (limit {limits.max_function_lines})")
        )
    if fn.nesting > limits.max_nesting:
        issues.append(
            Issue(path, fn.lineno, "structure", "warn", "deep-nesting",
                  f"Function '{fn.name}' nests {fn.nesting} levels deep (limit {limits.max_nesting})")
        )
    naming_issue = _naming_issue(fn, node, language, path)
    if naming_issue is not None:
        issues.append(naming_issue)
    return issues


def _process_functions(root, cfg, language, path, source, lines, comment_prefix, limits, only_lines, fm):
    for node in _iter_named(root):
        if node.kind() not in cfg["function_kinds"]:
            continue
        start = node.start_position().row + 1
        end = node.end_position().row + 1
        if not _in_scope(start, end, only_lines):
            continue
        fn = _build_function_metrics(node, cfg, path, source, lines, comment_prefix)
        fm.functions.append(fn)
        fm.issues.extend(_function_issues(fn, node, language, path, limits))


def _process_lines(lines, path, comment_prefix, limits, only_lines, fm):
    comment_lines = 0
    for i, raw in enumerate(lines, start=1):
        if raw.rstrip("\n").lstrip().startswith(comment_prefix):
            comment_lines += 1
        if only_lines is not None and i not in only_lines:
            continue
        issues, _is_comment, _indent, _hits = _scan_line(path, i, raw, comment_prefix, limits)
        fm.issues.extend(issues)
    return comment_lines


def analyze(path, source, language, limits, only_lines=None):
    """Parse `source` with the tree-sitter grammar for `language` and score
    it the same way `python_analyzer` scores Python: real function
    boundaries, real complexity, real nesting.
    """
    grammar = _grammar_for(language, path)
    cfg = LANGUAGES.get(language)
    parser = _get_parser(grammar)
    lines = source.splitlines(keepends=True)
    total_lines = len(lines)
    loc = sum(1 for l in lines if l.strip())
    comment_prefix = LINE_COMMENT_PREFIXES.get(language, "//")

    tree = parser.parse(source)
    root = tree.root_node()

    fm = FileMetrics(path=path, language=language, total_lines=total_lines, loc=loc)
    _process_functions(root, cfg, language, path, source, lines, comment_prefix, limits, only_lines, fm)

    if total_lines > limits.max_file_lines and only_lines is None:
        msg = f"File is {total_lines} lines long (limit {limits.max_file_lines})"
        fm.issues.append(Issue(path, 1, "structure", "info", "long-file", msg))

    comment_lines = _process_lines(lines, path, comment_prefix, limits, only_lines, fm)

    fm.comment_lines = comment_lines
    comment_ratio = comment_lines / max(loc, 1)
    fm.has_module_docstring = comment_lines > 0 and comment_ratio > 0.03
    return fm

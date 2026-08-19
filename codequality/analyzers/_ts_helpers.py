"""Shared tree-sitter helper functions used by language-specific analyzers.

_text, _line, and _iter_kind are identical across go_security, go_taint_flow,
js_security, and js_taint_flow.  Centralising them means a fix to byte-offset
handling or the recursive walk only needs to be made once.

Performance note: tree-sitter node offsets are *byte* offsets, not str
codepoint indices.  Each module's entry point must encode the source string
to bytes exactly once and pass the bytes through every helper call.  This
avoids the O(calls x file-size) re-encoding that in-module _text()
implementations incur when called at every AST node.
"""


def node_text(node, source_bytes):
    """UTF-8 text for a tree-sitter node from pre-encoded source bytes."""
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def node_line(node):
    """1-indexed line number of a tree-sitter node."""
    return node.start_point.row + 1


def iter_kind(node, kind):
    """Depth-first generator of all nodes (including node itself) whose type equals kind."""
    if node.type == kind:
        yield node
    for i in range(node.named_child_count):
        yield from iter_kind(node.named_child(i), kind)

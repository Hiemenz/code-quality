"""Shared data model used by every analyzer and the scorer."""

from dataclasses import dataclass, field


@dataclass
class Issue:
    file: str
    line: int
    category: str  # complexity | structure | duplication | documentation | style | security | correctness
    severity: str  # info | warn | error
    symbol: str
    message: str

    def to_dict(self):
        """JSON-serializable form of this issue.

        `rule` and `symbol` are the same value: `rule` is the
        conventional field name for integrations, `symbol` predates it
        and stays for backward compatibility.
        """
        return {
            "file": self.file,
            "line": self.line,
            "category": self.category,
            "severity": self.severity,
            "rule": self.symbol,
            "symbol": self.symbol,
            "message": self.message,
        }


@dataclass
class FunctionMetrics:
    file: str
    name: str
    lineno: int
    end_lineno: int
    complexity: int
    length: int
    nesting: int
    params: int
    has_docstring: bool
    is_public: bool = True
    # Sonar-style cognitive complexity (nesting-weighted); 0 for analyzers
    # that don't compute it (generic/tree-sitter paths).
    cognitive: int = 0
    suppressed: frozenset = field(default_factory=frozenset)  # symbols suppressed at this function's line

    @property
    def touched(self):
        return True


@dataclass
class FileMetrics:
    path: str
    language: str
    total_lines: int
    loc: int  # non-blank lines counted toward density metrics
    functions: list = field(default_factory=list)  # list[FunctionMetrics]
    issues: list = field(default_factory=list)  # list[Issue]
    has_module_docstring: bool = False
    comment_lines: int = 0
    duplicate_lines: int = 0
    suppressed_count: int = 0
    coverage_ratio: float = None  # 0.0-1.0, or None if coverage wasn't measured for this file
    parse_error: str = None


def is_public_name(name: str) -> bool:
    return not name.startswith("_")

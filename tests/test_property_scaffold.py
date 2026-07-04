import ast
import os
import tempfile
import unittest

from codequality import property_scaffold


class TestPropertyScaffold(unittest.TestCase):
    def test_finds_candidate_functions_with_simple_signatures(self):
        """Private functions and zero-arg functions aren't useful property-test targets."""
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "lib.py"), "w") as f:
                f.write(
                    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
                    "def _private(a: int) -> int:\n    return a\n\n\n"
                    "def no_args() -> int:\n    return 1\n"
                )
            candidates = property_scaffold.find_candidates(root, ["lib.py"])
        names = [node.name for _rel, node in candidates]
        self.assertEqual(names, ["add"])

    def test_test_files_are_excluded_from_candidates(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "test_lib.py"), "w") as f:
                f.write("def helper(a: int) -> int:\n    return a\n")
            candidates = property_scaffold.find_candidates(root, ["test_lib.py"])
        self.assertEqual(candidates, [])

    def test_detects_existing_given_decorated_tests(self):
        """The usage signal should count real @given tests already in the repo."""
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "test_lib.py"), "w") as f:
                f.write(
                    "from hypothesis import given\nimport hypothesis.strategies as st\n\n\n"
                    "@given(st.integers())\ndef test_prop(x):\n    pass\n"
                )
            found = property_scaffold.scan_existing_property_tests(root, ["test_lib.py"])
        self.assertEqual(found, {"test_lib.py": 1})

    def test_generated_stub_file_is_valid_python(self):
        """A generated stub must parse, even with an unsupported (untyped) parameter."""
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "lib.py"), "w") as f:
                f.write(
                    "def add(a: int, b: int) -> int:\n    return a + b\n\n\n"
                    "def mystery(a, b) -> int:\n    return a\n"  # untyped params -> unsupported strategy
                )
            candidates = property_scaffold.find_candidates(root, ["lib.py"])
            stub_source = property_scaffold.render_stub_file(candidates)
        tree = ast.parse(stub_source)  # raises SyntaxError if the scaffold ever produces broken code
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.assertEqual(len(names), len(set(names)), "stub function names must be unique")

    def test_same_named_functions_in_different_modules_get_distinct_names_and_aliases(self):
        """Two `analyze` functions from different modules must not collide
        -- either as duplicate def names, or (worse) as duplicate import
        bindings that silently shadow each other in the flat stub file.
        """
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "pkg"))
            open(os.path.join(root, "pkg", "__init__.py"), "w").close()
            with open(os.path.join(root, "pkg", "a.py"), "w") as f:
                f.write("def analyze(x: int) -> int:\n    return x\n")
            with open(os.path.join(root, "pkg", "b.py"), "w") as f:
                f.write("def analyze(x: int) -> int:\n    return x\n")
            candidates = property_scaffold.find_candidates(root, ["pkg/a.py", "pkg/b.py"])
            stub_source = property_scaffold.render_stub_file(candidates)
        tree = ast.parse(stub_source)
        def_names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        self.assertEqual(len(def_names), len(set(def_names)))
        imported_names = [
            alias.asname or alias.name
            for n in tree.body if isinstance(n, ast.ImportFrom)
            for alias in n.names
        ]
        self.assertEqual(len(imported_names), len(set(imported_names)))


if __name__ == "__main__":
    unittest.main()

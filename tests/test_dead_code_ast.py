import unittest

from codequality.analyzers.dead_code_ast import find_dead_code_ast


class TestDeadCodeAst(unittest.TestCase):
    def _issues(self, file_sources):
        return find_dead_code_ast(file_sources)

    def _symbols(self, file_sources):
        issues = self._issues(file_sources)
        return {(path, i.symbol) for path, lst in issues.items() for i in lst}

    def _flagged_names(self, file_sources):
        issues = self._issues(file_sources)
        return {i.message.split("'")[1] for lst in issues.values() for i in lst}

    # ------------------------------------------------------------------
    # Top-level dead-code (same semantics as regex-based scanner)
    # ------------------------------------------------------------------

    def test_unreferenced_function_is_flagged(self):
        sources = {
            "a.py": "def unused_helper():\n    return 1\n",
            "b.py": "x = 1\n",
        }
        self.assertIn(("a.py", "dead-code"), self._symbols(sources))

    def test_function_referenced_in_other_file_not_flagged(self):
        sources = {
            "a.py": "def used():\n    return 1\n",
            "b.py": "from a import used\nused()\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_function_referenced_in_same_file_not_flagged(self):
        sources = {
            "a.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "def caller():\n"
                "    return helper()\n"
            ),
        }
        self.assertNotIn("helper", self._flagged_names(sources))

    def test_class_unreferenced_is_flagged(self):
        sources = {
            "a.py": "class Orphan:\n    pass\n",
            "b.py": "x = 1\n",
        }
        self.assertIn(("a.py", "dead-code"), self._symbols(sources))

    def test_dunder_all_export_exempt(self):
        sources = {
            "a.py": (
                "__all__ = ['pub']\n"
                "\n"
                "def pub():\n"
                "    return 1\n"
            ),
            "b.py": "x = 1\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_decorated_function_exempt(self):
        sources = {
            "a.py": (
                "def route(p):\n"
                "    def wrap(fn): return fn\n"
                "    return wrap\n"
                "\n"
                "@route('/x')\n"
                "def handler():\n"
                "    return 1\n"
            ),
            "b.py": "x = 1\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_private_function_not_a_candidate(self):
        sources = {
            "a.py": "def _internal():\n    pass\n",
            "b.py": "x = 1\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_main_exempt(self):
        sources = {
            "a.py": "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_test_hooks_exempt(self):
        sources = {
            "a.py": "def setUp():\n    pass\n\ndef test_foo():\n    pass\n",
            "b.py": "x = 1\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_test_class_exempt(self):
        sources = {"test_a.py": "class TestThing:\n    def test_it(self):\n        pass\n"}
        self.assertEqual(self._symbols(sources), set())

    def test_name_in_comment_does_not_prevent_flagging(self):
        # Regex-based scanner would NOT flag this because the name appears in
        # text.  AST-based correctly ignores the comment.
        sources = {
            "a.py": "def compute():\n    return 1\n",
            "b.py": "# TODO: call compute() someday\nx = 1\n",
        }
        self.assertIn(("a.py", "dead-code"), self._symbols(sources))

    def test_name_in_string_does_not_prevent_flagging(self):
        sources = {
            "a.py": "def compute():\n    return 1\n",
            "b.py": "msg = 'compute is not yet wired up'\n",
        }
        self.assertIn(("a.py", "dead-code"), self._symbols(sources))

    def test_syntax_error_file_skipped_gracefully(self):
        sources = {
            "bad.py": "def (\n",
            "good.py": "def unused():\n    pass\n",
        }
        # Should not raise; bad.py is skipped; good.py may or may not be flagged
        result = self._issues(sources)
        self.assertNotIn("bad.py", result)

    # ------------------------------------------------------------------
    # Unused-method detection (new capability)
    # ------------------------------------------------------------------

    def test_unreferenced_method_flagged(self):
        sources = {
            "a.py": (
                "class Foo:\n"
                "    def orphan_method(self):\n"
                "        pass\n"
            ),
            "b.py": "from a import Foo\nFoo()\n",
        }
        self.assertIn(("a.py", "unused-method"), self._symbols(sources))

    def test_method_called_externally_not_flagged(self):
        sources = {
            "a.py": "class Foo:\n    def process(self):\n        pass\n",
            "b.py": "from a import Foo\nFoo().process()\n",
        }
        self.assertNotIn(("a.py", "unused-method"), self._symbols(sources))

    def test_method_called_internally_not_flagged(self):
        sources = {
            "a.py": (
                "class Foo:\n"
                "    def helper(self):\n"
                "        pass\n"
                "    def run(self):\n"
                "        self.helper()\n"
            ),
        }
        self.assertNotIn("helper", self._flagged_names(sources))

    def test_dunder_method_not_a_candidate(self):
        sources = {
            "a.py": "class Foo:\n    def __str__(self):\n        return 'Foo'\n",
            "b.py": "from a import Foo\nFoo()\n",
        }
        self.assertNotIn(("a.py", "unused-method"), self._symbols(sources))

    def test_private_method_not_a_candidate(self):
        sources = {
            "a.py": "class Foo:\n    def _helper(self):\n        pass\n",
            "b.py": "from a import Foo\nFoo()\n",
        }
        self.assertNotIn(("a.py", "unused-method"), self._symbols(sources))

    def test_decorated_method_not_a_candidate(self):
        sources = {
            "a.py": "class Foo:\n    @staticmethod\n    def make():\n        return Foo()\n",
            "b.py": "from a import Foo\nFoo()\n",
        }
        self.assertNotIn(("a.py", "unused-method"), self._symbols(sources))

    def test_http_verb_methods_exempt(self):
        for verb in ("get", "post", "put", "patch", "delete", "head", "options"):
            with self.subTest(verb=verb):
                sources = {
                    "view.py": f"class MyView:\n    def {verb}(self, request):\n        pass\n",
                    "urls.py": "from view import MyView\nroutes = [MyView]\n",
                }
                self.assertNotIn(("view.py", "unused-method"), self._symbols(sources))

    def test_method_of_dead_class_not_double_reported(self):
        # If the class itself is dead, its methods should not be additionally
        # flagged with unused-method (cascading noise suppression).
        sources = {
            "a.py": "class Orphan:\n    def some_method(self):\n        pass\n",
            "b.py": "x = 1\n",
        }
        symbols = self._symbols(sources)
        # dead-code for the class is expected
        self.assertIn(("a.py", "dead-code"), symbols)
        # unused-method should NOT also fire since the class is dead
        self.assertNotIn(("a.py", "unused-method"), symbols)

    def test_async_method_flagged(self):
        sources = {
            "a.py": "class Worker:\n    async def run_task(self):\n        pass\n",
            "b.py": "from a import Worker\nWorker()\n",
        }
        self.assertIn(("a.py", "unused-method"), self._symbols(sources))

    def test_classmethod_name_referenced_as_attribute_not_flagged(self):
        sources = {
            "a.py": "class Repo:\n    def from_url(self, url):\n        pass\n",
            "b.py": "from a import Repo\nRepo().from_url('x')\n",
        }
        self.assertNotIn(("a.py", "unused-method"), self._symbols(sources))


if __name__ == "__main__":
    unittest.main()

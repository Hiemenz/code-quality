import unittest

from codequality.analyzers.dead_code import find_dead_code


class TestDeadCode(unittest.TestCase):
    def _symbols(self, file_sources):
        issues = find_dead_code(file_sources)
        return {(path, i.symbol) for path, issue_list in issues.items() for i in issue_list}

    def _flagged_names(self, file_sources):
        """Set of names flagged as dead code, extracted from each issue's
        message (e.g. "Function 'helper' is defined ...") -- lets tests
        assert on one specific name without caring about unrelated
        candidates elsewhere in the same fixture.
        """
        issues = find_dead_code(file_sources)
        return {i.message.split("'")[1] for issue_list in issues.values() for i in issue_list}

    def test_function_referenced_only_at_definition_is_flagged(self):
        sources = {
            "a.py": "def unused_helper():\n    return 1\n",
            "b.py": "x = 1\n",
        }
        self.assertIn(("a.py", "dead-code"), self._symbols(sources))

    def test_function_called_from_another_file_is_not_flagged(self):
        sources = {
            "a.py": "def used_helper():\n    return 1\n",
            "b.py": "from a import used_helper\n\nused_helper()\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_function_called_elsewhere_in_same_file_is_not_flagged(self):
        # `helper` is called from `caller`, further down in the same file --
        # that call should count as a reference (only the definition line
        # itself is excluded). `caller` itself is never called anywhere, so
        # it's legitimately flagged; the assertion only cares about `helper`.
        sources = {
            "a.py": (
                "def helper():\n"
                "    return 1\n"
                "\n"
                "\n"
                "def caller():\n"
                "    return helper()\n"
            ),
        }
        self.assertNotIn("helper", self._flagged_names(sources))

    def test_class_referenced_only_at_definition_is_flagged(self):
        sources = {
            "a.py": "class UnusedThing:\n    pass\n",
            "b.py": "x = 1\n",
        }
        self.assertIn(("a.py", "dead-code"), self._symbols(sources))

    def test_dunder_all_export_is_exempt(self):
        sources = {
            "a.py": (
                "__all__ = ['exported_thing']\n"
                "\n"
                "\n"
                "def exported_thing():\n"
                "    return 1\n"
            ),
            "b.py": "x = 1\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_decorated_function_is_exempt(self):
        sources = {
            "a.py": (
                "def route(path):\n"
                "    def wrap(fn):\n"
                "        return fn\n"
                "    return wrap\n"
                "\n"
                "\n"
                "@route('/x')\n"
                "def handler():\n"
                "    return 1\n"
            ),
            "b.py": "x = 1\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_dunder_method_is_exempt(self):
        # `Foo` is referenced elsewhere (so only `__init__` is under test);
        # `__init__` was never a candidate in the first place (dunder, and
        # not top-level to begin with -- it's inside the class body), so
        # neither name should be flagged.
        sources = {
            "a.py": "class Foo:\n    def __init__(self):\n        pass\n",
            "b.py": "from a import Foo\n\nFoo()\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_test_hook_names_are_exempt(self):
        sources = {
            "a.py": (
                "def setUp():\n"
                "    pass\n"
                "\n"
                "\n"
                "def test_something():\n"
                "    pass\n"
            ),
            "b.py": "x = 1\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_unittest_style_class_is_exempt(self):
        # unittest/pytest discover `Test*`-prefixed classes by naming
        # convention alone, never by direct reference.
        sources = {
            "test_a.py": "class TestSomething:\n    def test_it(self):\n        pass\n",
        }
        self.assertEqual(self._symbols(sources), set())

    def test_main_entry_point_is_exempt(self):
        sources = {
            "a.py": (
                "def main():\n"
                "    pass\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    main()\n"
            ),
        }
        self.assertEqual(self._symbols(sources), set())

    def test_private_function_is_not_a_candidate(self):
        sources = {
            "a.py": "def _private_helper():\n    return 1\n",
            "b.py": "x = 1\n",
        }
        self.assertEqual(self._symbols(sources), set())


if __name__ == "__main__":
    unittest.main()

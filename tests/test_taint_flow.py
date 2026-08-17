import ast
import unittest

from codequality.analyzers.taint_flow import taint_issues


def _issues(src, only_lines=None):
    tree = ast.parse(src)
    return taint_issues(tree, "a.py", only_lines)


class TestDirectFlow(unittest.TestCase):
    def test_tainted_var_into_execute_flags(self):
        src = (
            "def handler(request, cursor):\n"
            "    user_id = request.args.get('id')\n"
            "    query = f'SELECT * FROM users WHERE id = {user_id}'\n"
            "    cursor.execute(query)\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "tainted-data-flow")
        self.assertEqual(issues[0].line, 4)

    def test_input_source_into_execute_flags(self):
        src = (
            "def run(cursor):\n"
            "    name = input('name: ')\n"
            "    q = 'SELECT * FROM t WHERE name = ' + name\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_sys_argv_into_execute_flags(self):
        src = (
            "import sys\n"
            "def run(cursor):\n"
            "    q = sys.argv[1]\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_multi_hop_reassignment_still_flags(self):
        src = (
            "def run(request, cursor):\n"
            "    a = request.args.get('x')\n"
            "    b = a\n"
            "    c = b\n"
            "    cursor.execute(c)\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 5)


class TestBranching(unittest.TestCase):
    def test_taint_from_if_branch_reaches_sink_after(self):
        src = (
            "def run(request, cursor, flag):\n"
            "    q = 'safe'\n"
            "    if flag:\n"
            "        q = request.args.get('x')\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_taint_from_for_loop_body_reaches_sink_after(self):
        src = (
            "def run(request, cursor, items):\n"
            "    q = 'safe'\n"
            "    for item in items:\n"
            "        q = request.args.get('x')\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(len(_issues(src)), 1)


class TestNegatives(unittest.TestCase):
    def test_literal_argument_does_not_flag(self):
        src = "def run(cursor):\n    cursor.execute('SELECT 1')\n"
        self.assertEqual(_issues(src), [])

    def test_reassigned_to_literal_before_sink_clears_taint(self):
        src = (
            "def run(request, cursor):\n"
            "    q = request.args.get('x')\n"
            "    q = 'SELECT 1'\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(_issues(src), [])

    def test_unrelated_untainted_variable_does_not_flag(self):
        src = (
            "def run(request, cursor):\n"
            "    user_id = request.args.get('id')\n"
            "    query = 'SELECT 1'\n"
            "    cursor.execute(query)\n"
        )
        self.assertEqual(_issues(src), [])

    def test_parameterized_query_with_separate_params_not_flagged(self):
        src = (
            "def run(request, cursor):\n"
            "    user_id = request.args.get('id')\n"
            "    cursor.execute('SELECT * FROM t WHERE id = %s', (user_id,))\n"
        )
        self.assertEqual(_issues(src), [])

    def test_nested_function_scope_is_independent(self):
        src = (
            "def outer(request, cursor):\n"
            "    q = request.args.get('x')\n"
            "    def inner(cursor2):\n"
            "        cursor2.execute('SELECT 1')\n"
            "    inner(cursor)\n"
            "    return q\n"
        )
        self.assertEqual(_issues(src), [])

    def test_only_lines_filters_to_diff_scope(self):
        src = (
            "def run(request, cursor):\n"
            "    q = request.args.get('x')\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(_issues(src, only_lines={1, 2}), [])
        self.assertEqual(len(_issues(src, only_lines={3})), 1)


if __name__ == "__main__":
    unittest.main()

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


class TestWidenedSources(unittest.TestCase):
    def test_request_headers_get_is_a_source(self):
        src = (
            "def run(request, cursor):\n"
            "    q = request.headers.get('X-Query')\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_request_query_params_get_is_a_source(self):
        src = (
            "def run(request, cursor):\n"
            "    q = request.query_params.get('x')\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_django_request_meta_get_is_a_source(self):
        src = (
            "def run(request, cursor):\n"
            "    q = request.META.get('HTTP_X_FOO')\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_request_data_attribute_is_a_source(self):
        src = (
            "def run(request, cursor):\n"
            "    q = request.data\n"
            "    cursor.execute(q)\n"
        )
        self.assertEqual(len(_issues(src)), 1)


class TestRouteHandlerParameters(unittest.TestCase):
    def test_fastapi_get_handler_param_is_tainted(self):
        src = (
            "@app.get('/items/{item_id}')\n"
            "def get_item(item_id, cursor):\n"
            "    query = f'SELECT * FROM items WHERE id = {item_id}'\n"
            "    cursor.execute(query)\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_flask_route_handler_param_is_tainted(self):
        src = (
            "@app.route('/items/<item_id>')\n"
            "def get_item(item_id, cursor):\n"
            "    query = f'SELECT * FROM items WHERE id = {item_id}'\n"
            "    cursor.execute(query)\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_non_route_function_param_is_not_tainted(self):
        src = (
            "def get_item(item_id, cursor):\n"
            "    query = f'SELECT * FROM items WHERE id = {item_id}'\n"
            "    cursor.execute(query)\n"
        )
        self.assertEqual(_issues(src), [])

    def test_depends_default_param_is_excluded(self):
        src = (
            "@app.get('/items/{item_id}')\n"
            "def get_item(item_id, db=Depends(get_db)):\n"
            "    db.execute('SELECT 1')\n"
        )
        self.assertEqual(_issues(src), [])

    def test_self_param_is_excluded(self):
        src = (
            "class Api:\n"
            "    @app.get('/items/{item_id}')\n"
            "    def get_item(self, item_id, cursor):\n"
            "        query = f'SELECT * FROM items WHERE id = {item_id}'\n"
            "        cursor.execute(query)\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertNotIn("'self'", issues[0].message)


class TestInterproceduralOneHop(unittest.TestCase):
    def test_tainted_arg_into_helper_that_executes_flags_caller(self):
        src = (
            "def run_query(cursor, q):\n"
            "    cursor.execute(q)\n"
            "\n"
            "def handler(request, cursor):\n"
            "    user_id = request.args.get('id')\n"
            "    query = f'SELECT * FROM users WHERE id = {user_id}'\n"
            "    run_query(cursor, query)\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 7)
        self.assertIn("run_query", issues[0].message)

    def test_helper_returning_tainted_value_taints_caller_assignment(self):
        src = (
            "def build_query(user_id):\n"
            "    return f'SELECT * FROM users WHERE id = {user_id}'\n"
            "\n"
            "def handler(request, cursor):\n"
            "    user_id = request.args.get('id')\n"
            "    q = build_query(user_id)\n"
            "    cursor.execute(q)\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 7)

    def test_untainted_arg_into_helper_does_not_flag(self):
        src = (
            "def run_query(cursor, q):\n"
            "    cursor.execute(q)\n"
            "\n"
            "def handler(cursor):\n"
            "    run_query(cursor, 'SELECT 1')\n"
        )
        self.assertEqual(_issues(src), [])

    def test_two_hops_is_not_followed(self):
        # inner() -> middle() -> execute(); handler calls middle() directly
        # (one hop, should flag), but calling inner()'s *caller* two levels
        # out is where the bound stops -- verified via the mutual-recursion
        # case below not blowing up, and via handler->outer->inner (2 hops)
        # not being resolved past the first hop.
        src = (
            "def inner(cursor, q):\n"
            "    cursor.execute(q)\n"
            "\n"
            "def outer(cursor, q):\n"
            "    inner(cursor, q)\n"
            "\n"
            "def handler(request, cursor):\n"
            "    user_id = request.args.get('id')\n"
            "    query = f'SELECT * FROM users WHERE id = {user_id}'\n"
            "    outer(cursor, query)\n"
        )
        # outer's own summary is computed with resolve_helpers=False, so it
        # doesn't see that inner() is itself a sink -- outer looks "safe"
        # from summary computation's point of view, so the two-hop path
        # through handler -> outer -> inner is not flagged.
        self.assertEqual(_issues(src), [])

    def test_mutually_recursive_helpers_do_not_infinite_loop(self):
        src = (
            "def a(cursor, q):\n"
            "    return b(cursor, q)\n"
            "\n"
            "def b(cursor, q):\n"
            "    return a(cursor, q)\n"
            "\n"
            "def handler(request, cursor):\n"
            "    user_id = request.args.get('id')\n"
            "    a(cursor, user_id)\n"
        )
        # Just needs to terminate without raising; summaries are computed
        # with resolve_helpers=False so a<->b's mutual calls are opaque to
        # each other's summary, not a source of unbounded recursion.
        self.assertEqual(_issues(src), [])

    def test_duplicate_function_name_is_not_resolved(self):
        src = (
            "def helper(cursor, q):\n"
            "    cursor.execute(q)\n"
            "\n"
            "def helper(x, y):\n"
            "    return x + y\n"
            "\n"
            "def handler(request, cursor):\n"
            "    user_id = request.args.get('id')\n"
            "    helper(cursor, user_id)\n"
        )
        self.assertEqual(_issues(src), [])


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

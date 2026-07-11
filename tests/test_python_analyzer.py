import os
import tempfile
import unittest

from codequality.analyzers import python_analyzer
from codequality.config import Config, DEFAULT_CONFIG, Limits
from codequality.scanner import analyze_file

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _limits():
    return Limits(dict(DEFAULT_CONFIG["limits"]))


def _read(name):
    with open(os.path.join(FIXTURES, name), "r", encoding="utf-8") as f:
        return f.read()


class TestPythonAnalyzer(unittest.TestCase):
    def test_good_file_has_no_serious_issues(self):
        """A tidy, docstringed module should score clean."""
        fm = python_analyzer.analyze("good.py", _read("good.py"), _limits())
        self.assertIsNone(fm.parse_error)
        severities = {i.severity for i in fm.issues}
        self.assertNotIn("error", severities)
        self.assertTrue(fm.has_module_docstring)
        self.assertEqual(len(fm.functions), 2)
        for fn in fm.functions:
            self.assertTrue(fn.has_docstring)

    def test_bad_file_flags_expected_issues(self):
        """Every deliberately planted defect in the bad.py fixture should be caught."""
        fm = python_analyzer.analyze("bad.py", _read("bad.py"), _limits())
        symbols = {i.symbol for i in fm.issues}
        self.assertIn("high-complexity", symbols)
        self.assertIn("deep-nesting", symbols)
        self.assertIn("bare-except", symbols)
        self.assertIn("star-import", symbols)
        self.assertIn("mutable-default-arg", symbols)
        self.assertIn("long-line", symbols)
        self.assertIn("trailing-whitespace", symbols)
        self.assertIn("todo-marker", symbols)
        self.assertIn("missing-docstring", symbols)

        fn = fm.functions[0]
        self.assertEqual(fn.name, "messy")
        self.assertGreaterEqual(fn.complexity, 10)
        self.assertGreaterEqual(fn.nesting, 5)

    def test_syntax_error_is_reported_not_raised(self):
        fm = python_analyzer.analyze("broken.py", "def f(:\n    pass\n", _limits())
        self.assertIsNotNone(fm.parse_error)
        self.assertEqual(fm.issues[0].symbol, "syntax-error")

    def test_only_lines_restricts_function_selection(self):
        source = _read("bad.py")
        # 'messy' spans roughly lines 4-22; restrict to a line far outside it.
        fm_full = python_analyzer.analyze("bad.py", source, _limits())
        fm_scoped = python_analyzer.analyze("bad.py", source, _limits(), only_lines={1})
        self.assertGreater(len(fm_full.functions), 0)
        self.assertEqual(len(fm_scoped.functions), 0)

    def test_complexity_is_deterministic(self):
        source = _read("bad.py")
        results = [python_analyzer.analyze("bad.py", source, _limits()).functions[0].complexity for _ in range(5)]
        self.assertEqual(len(set(results)), 1)

    def test_unused_import_is_flagged_but_used_one_is_not(self):
        source = "import os\nimport sys\n\n\ndef f():\n    return sys.argv\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        unused = [i for i in fm.issues if i.symbol == "unused-import"]
        self.assertEqual(len(unused), 1)
        self.assertIn("os", unused[0].message)

    def test_dunder_all_exempts_reexported_import(self):
        source = "import os\n\n__all__ = ['os']\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unused-import", {i.symbol for i in fm.issues})

    def test_unused_local_variable_is_flagged(self):
        source = "def f():\n    x = 1\n    y = 2\n    return y\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        unused = [i for i in fm.issues if i.symbol == "unused-variable"]
        self.assertEqual(len(unused), 1)
        self.assertIn("x", unused[0].message)

    def test_underscore_prefixed_variable_is_not_flagged_as_unused(self):
        source = "def f():\n    _ignored = compute()\n    return 1\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unused-variable", {i.symbol for i in fm.issues})

    def test_eval_and_shell_true_are_flagged_as_security_issues(self):
        """Both eval() and shell=True should surface as security-category issues."""
        source = (
            "import subprocess\n\n"
            "def f(cmd):\n"
            "    eval(cmd)\n"
            "    subprocess.run(cmd, shell=True)\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        symbols = {i.symbol for i in fm.issues if i.category == "security"}
        self.assertIn("dangerous-eval", symbols)
        self.assertIn("shell-true", symbols)

    def test_hardcoded_secret_is_flagged(self):
        source = "password = 'hunter2'\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        secrets = [i for i in fm.issues if i.symbol == "hardcoded-secret"]
        self.assertEqual(len(secrets), 1)

    def test_placeholder_secret_value_is_not_flagged(self):
        source = "password = 'changeme'\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("hardcoded-secret", {i.symbol for i in fm.issues})

    def test_fstring_sql_query_is_flagged_as_injection_risk(self):
        source = "def f(cursor, name):\n    cursor.execute(f\"SELECT * FROM users WHERE name = '{name}'\")\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        found = [i for i in fm.issues if i.symbol == "sql-injection-risk"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].category, "security")

    def test_percent_formatted_sql_query_is_flagged_as_injection_risk(self):
        source = "def f(cursor, name):\n    cursor.execute('SELECT * FROM users WHERE name = %s' % name)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("sql-injection-risk", {i.symbol for i in fm.issues})

    def test_concatenated_sql_query_is_flagged_as_injection_risk(self):
        source = "def f(cursor, name):\n    cursor.execute('SELECT * FROM users WHERE name = ' + name)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("sql-injection-risk", {i.symbol for i in fm.issues})

    def test_dot_format_sql_query_is_flagged_as_injection_risk(self):
        source = "def f(cursor, name):\n    cursor.execute('SELECT * FROM users WHERE name = {}'.format(name))\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("sql-injection-risk", {i.symbol for i in fm.issues})

    def test_parameterized_sql_query_is_not_flagged(self):
        source = "def f(cursor, name):\n    cursor.execute('SELECT * FROM users WHERE name = %s', (name,))\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("sql-injection-risk", {i.symbol for i in fm.issues})

    def test_plain_string_literal_sql_query_is_not_flagged(self):
        source = "def f(cursor):\n    cursor.execute('SELECT * FROM users')\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("sql-injection-risk", {i.symbol for i in fm.issues})

    def test_logging_a_secret_looking_variable_is_flagged(self):
        source = "def f(password):\n    logger.info('login with %s', password)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        found = [i for i in fm.issues if i.symbol == "sensitive-data-logging"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].category, "security")

    def test_print_of_a_secret_looking_variable_is_flagged(self):
        source = "def f(api_key):\n    print(api_key)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("sensitive-data-logging", {i.symbol for i in fm.issues})

    def test_logging_an_unrelated_variable_is_not_flagged(self):
        source = "def f(username):\n    logger.info('login: %s', username)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("sensitive-data-logging", {i.symbol for i in fm.issues})

    def test_bad_function_and_class_names_are_flagged(self):
        source = "def BadName():\n    pass\n\n\nclass lowercase_class:\n    pass\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        symbols = {i.symbol for i in fm.issues}
        self.assertIn("bad-function-name", symbols)
        self.assertIn("bad-class-name", symbols)

    def test_visitor_and_unittest_method_names_are_exempt_from_naming_check(self):
        source = "class T:\n    def setUp(self):\n        pass\n\n    def visit_If(self, node):\n        pass\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("bad-function-name", {i.symbol for i in fm.issues})

    def test_unresolved_import_is_flagged_only_when_opted_in(self):
        """--check-imports depends on this environment, so it must never run implicitly."""
        source = "import totally_fake_package_xyz_123\n"
        fm_off = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unresolved-import", {i.symbol for i in fm_off.issues})

        fm_on = python_analyzer.analyze("f.py", source, _limits(), check_imports=True)
        unresolved = [i for i in fm_on.issues if i.symbol == "unresolved-import"]
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].category, "correctness")

    def test_resolvable_import_is_not_flagged(self):
        fm = python_analyzer.analyze("f.py", "import os\n", _limits(), check_imports=True)
        self.assertNotIn("unresolved-import", {i.symbol for i in fm.issues})

    def test_relative_import_is_not_checked(self):
        fm = python_analyzer.analyze("f.py", "from . import sibling\n", _limits(), check_imports=True)
        self.assertNotIn("unresolved-import", {i.symbol for i in fm.issues})

    def test_assertion_free_test_is_flagged(self):
        source = "def test_no_assert():\n    add(1, 2)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        issues = [i for i in fm.issues if i.symbol == "assertion-free-test"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "correctness")

    def test_assert_statement_satisfies_the_check(self):
        source = "def test_it():\n    assert add(1, 2) == 3\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("assertion-free-test", {i.symbol for i in fm.issues})

    def test_self_assert_call_satisfies_the_check(self):
        source = "def test_it(self):\n    self.assertEqual(add(1, 2), 3)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("assertion-free-test", {i.symbol for i in fm.issues})

    def test_pytest_raises_satisfies_the_check(self):
        source = "def test_it():\n    with pytest.raises(ValueError):\n        raise ValueError()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("assertion-free-test", {i.symbol for i in fm.issues})

    def test_skipped_test_is_exempt_from_assertion_check(self):
        source = "@unittest.skip('wip')\ndef test_no_assert():\n    add(1, 2)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("assertion-free-test", {i.symbol for i in fm.issues})

    def test_cognitive_complexity_weights_nesting_over_flat_branches(self):
        flat = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(4))
        nested = "def f(x):\n"
        for i in range(4):
            nested += "    " * (i + 1) + f"if x > {i}:\n"
        nested += "    " * 5 + "return x\n"
        fm_flat = python_analyzer.analyze("f.py", flat, _limits())
        fm_nested = python_analyzer.analyze("f.py", nested, _limits())
        self.assertGreater(fm_nested.functions[0].cognitive, fm_flat.functions[0].cognitive)

    def test_high_cognitive_complexity_is_flagged(self):
        source = "def f(x):\n"
        for i in range(6):
            source += "    " * (i + 1) + f"if x > {i}:\n"
        source += "    " * 7 + "return x\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertGreater(fm.functions[0].cognitive, 15)
        issues = [i for i in fm.issues if i.symbol == "high-cognitive-complexity"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "complexity")

    def test_elif_chain_costs_linear_not_quadratic(self):
        source = (
            "def f(x):\n"
            "    if x == 1:\n        return 1\n"
            "    elif x == 2:\n        return 2\n"
            "    elif x == 3:\n        return 3\n"
            "    else:\n        return 0\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        # if (+1) + two elifs (+1 each, flat) + else (+1) = 4
        self.assertEqual(fm.functions[0].cognitive, 4)

    def test_boolop_inside_elif_condition_still_counts(self):
        source = (
            "def f(x, y):\n"
            "    if x:\n        return 1\n"
            "    elif x and y:\n        return 2\n"
            "    return 0\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        # if (+1) + elif (+1) + `and` (+1) = 3
        self.assertEqual(fm.functions[0].cognitive, 3)

    def test_cognitive_complexity_counts_ternary_and_except(self):
        source = (
            "def f(x):\n"
            "    y = 1 if x else 2\n"
            "    try:\n"
            "        return y\n"
            "    except ValueError:\n"
            "        return 0\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        # IfExp (+1) and one except handler (+1); try itself is free.
        self.assertEqual(fm.functions[0].cognitive, 2)

    def test_cognitive_complexity_of_flat_function_is_zero(self):
        fm = python_analyzer.analyze("f.py", "def f(a, b):\n    return a + b\n", _limits())
        self.assertEqual(fm.functions[0].cognitive, 0)

    def test_nested_function_does_not_inflate_parent_cognitive(self):
        source = (
            "def outer(x):\n"
            "    def inner(y):\n"
            "        if y:\n"
            "            if y > 1:\n"
            "                return 2\n"
            "        return 1\n"
            "    return inner(x)\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        by_name = {fn.name: fn.cognitive for fn in fm.functions}
        self.assertEqual(by_name["outer"], 0)
        self.assertEqual(by_name["inner"], 3)  # if (+1) + nested if (+2)

    def test_too_many_params_is_flagged_info(self):
        source = "def f(a, b, c, d, e, f2, g):\n    return a\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        issues = [i for i in fm.issues if i.symbol == "too-many-params"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "info")

    def test_six_params_is_within_limit(self):
        source = "def f(a, b, c, d, e, f2):\n    return a\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("too-many-params", {i.symbol for i in fm.issues})

    def test_assert_true_only_test_is_tautological(self):
        source = "def test_it():\n    run()\n    assert True\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        issues = [i for i in fm.issues if i.symbol == "tautological-test"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "correctness")
        self.assertNotIn("assertion-free-test", {i.symbol for i in fm.issues})

    def test_self_comparison_assert_is_tautological(self):
        source = "def test_it():\n    assert value(1) == value(1)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("tautological-test", {i.symbol for i in fm.issues})

    def test_assert_equal_same_args_is_tautological(self):
        source = "def test_it(self):\n    self.assertEqual(result, result)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("tautological-test", {i.symbol for i in fm.issues})

    def test_real_assertion_alongside_assert_true_is_not_flagged(self):
        source = "def test_it():\n    assert True\n    assert add(1, 2) == 3\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("tautological-test", {i.symbol for i in fm.issues})

    def test_assert_false_is_not_tautological(self):
        source = "def test_it():\n    assert False\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("tautological-test", {i.symbol for i in fm.issues})

    def test_assertion_free_test_is_not_double_flagged_as_tautological(self):
        source = "def test_no_assert():\n    add(1, 2)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("tautological-test", {i.symbol for i in fm.issues})

    def test_mock_only_test_is_flagged_info(self):
        source = "def test_it(self):\n    do_thing(self.svc)\n    self.svc.notify.assert_called_once_with('x')\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        issues = [i for i in fm.issues if i.symbol == "mock-only-test"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "info")

    def test_mock_assertion_plus_real_assertion_is_not_mock_only(self):
        source = (
            "def test_it(self):\n"
            "    result = do_thing(self.svc)\n"
            "    self.svc.notify.assert_called_once_with('x')\n"
            "    assert result == 3\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("mock-only-test", {i.symbol for i in fm.issues})

    def test_assertion_free_test_is_not_mock_only(self):
        source = "def test_no_assert():\n    add(1, 2)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("mock-only-test", {i.symbol for i in fm.issues})

    def test_stub_and_placeholder_checks_are_wired_into_analyze(self):
        source = "def convert(x):\n    raise NotImplementedError\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("stub-implementation", {i.symbol for i in fm.issues})
        source = "def f():\n    # your logic here" + "\n    pass\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("placeholder-comment", {i.symbol for i in fm.issues})

    def test_deprecated_api_check_is_wired_into_analyze(self):
        fm = python_analyzer.analyze("f.py", "import imp\n", _limits())
        self.assertIn("deprecated-api", {i.symbol for i in fm.issues})

    def test_broad_except_swallow_is_flagged(self):
        source = "def f():\n    try:\n        risky()\n    except Exception:\n        pass\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("broad-except-swallow", {i.symbol for i in fm.issues})

    def test_except_that_reraises_is_not_flagged(self):
        source = "def f():\n    try:\n        risky()\n    except Exception:\n        raise\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("broad-except-swallow", {i.symbol for i in fm.issues})

    def test_except_that_logs_is_not_flagged(self):
        source = "def f():\n    try:\n        risky()\n    except Exception:\n        logging.exception('x')\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("broad-except-swallow", {i.symbol for i in fm.issues})

    def test_narrow_except_pass_is_not_flagged(self):
        """Catching a specific exception and ignoring it is a normal, deliberate pattern."""
        source = "def f():\n    try:\n        risky()\n    except ValueError:\n        pass\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("broad-except-swallow", {i.symbol for i in fm.issues})

    def test_open_without_with_or_close_is_flagged(self):
        source = "def f(path):\n    fh = open(path)\n    return fh.read()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_open_via_with_is_not_flagged(self):
        source = "def f(path):\n    with open(path) as fh:\n        return fh.read()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_open_with_explicit_close_is_not_flagged(self):
        source = "def f(path):\n    fh = open(path)\n    data = fh.read()\n    fh.close()\n    return data\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_open_returned_directly_is_not_flagged(self):
        source = "def f(path):\n    return open(path)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_open_assigned_and_returned_is_not_flagged(self):
        source = "def f(path):\n    fh = open(path)\n    return fh\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_open_passed_to_another_call_is_not_flagged(self):
        source = "def f(path):\n    fh = open(path)\n    return json.load(fh)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_open_wrapped_by_contextlib_closing_is_not_flagged(self):
        source = "def f(path):\n    with contextlib.closing(open(path)) as fh:\n        return fh.read()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_chained_open_without_assignment_is_flagged(self):
        source = "def f(path):\n    return open(path).read()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_unrelated_call_named_open_on_other_object_is_not_flagged(self):
        source = "def f(door):\n    door.open()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_chained_open_close_is_not_flagged(self):
        source = "def f(path):\n    open(path, 'w').close()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_socket_socket_without_close_is_flagged(self):
        source = "def f():\n    s = socket.socket()\n    s.connect(('x', 1))\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("unclosed-resource", {i.symbol for i in fm.issues})

    def test_unawaited_coroutine_call_is_flagged(self):
        source = "async def fetch():\n    pass\n\n\nasync def run():\n    fetch()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_awaited_coroutine_call_is_not_flagged(self):
        source = "async def fetch():\n    pass\n\n\nasync def run():\n    await fetch()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_coroutine_scheduled_via_create_task_is_not_flagged(self):
        source = (
            "async def fetch():\n    pass\n\n\n"
            "async def run():\n    asyncio.create_task(fetch())\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_coroutine_passed_to_gather_is_not_flagged(self):
        source = (
            "async def fetch():\n    pass\n\n\n"
            "async def run():\n    await asyncio.gather(fetch(), fetch())\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_coroutine_returned_directly_is_not_flagged(self):
        source = "async def fetch():\n    pass\n\n\ndef make():\n    return fetch()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_coroutine_assigned_and_later_awaited_is_not_flagged(self):
        source = (
            "async def fetch():\n    pass\n\n\n"
            "async def run():\n    coro = fetch()\n    await coro\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_coroutine_assigned_and_never_awaited_is_flagged(self):
        source = (
            "async def fetch():\n    pass\n\n\n"
            "async def run():\n    coro = fetch()\n    print('scheduled')\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_asyncio_run_entrypoint_is_not_flagged(self):
        source = "async def main():\n    pass\n\n\nasyncio.run(main())\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_file_with_no_async_defs_is_never_flagged(self):
        source = "def f():\n    g()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unawaited-coroutine", {i.symbol for i in fm.issues})

    def test_django_manager_get_in_for_loop_is_flagged(self):
        source = "def f(ids):\n    for i in ids:\n        Order.objects.get(pk=i)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("query-in-loop", {i.symbol for i in fm.issues})

    def test_sqlalchemy_session_query_in_while_loop_is_flagged(self):
        source = "def f():\n    while more():\n        self.session.query(User).all()\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("query-in-loop", {i.symbol for i in fm.issues})

    def test_cursor_execute_in_loop_is_flagged(self):
        source = "def f(rows):\n    for r in rows:\n        self.cursor.execute('select 1')\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("query-in-loop", {i.symbol for i in fm.issues})

    def test_raw_connection_execute_in_loop_is_flagged(self):
        source = "def f(rows):\n    for r in rows:\n        conn.execute('select 1')\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("query-in-loop", {i.symbol for i in fm.issues})

    def test_plain_dict_get_in_loop_is_not_flagged(self):
        source = "def f(items):\n    for k in items:\n        cache.get(k)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("query-in-loop", {i.symbol for i in fm.issues})

    def test_query_call_outside_any_loop_is_not_flagged(self):
        source = "def f():\n    Order.objects.get(pk=1)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("query-in-loop", {i.symbol for i in fm.issues})

    def test_query_in_nested_loop_is_reported_once(self):
        source = "def f(xs, ys):\n    for x in xs:\n        for y in ys:\n            Order.objects.get(pk=y)\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        matches = [i for i in fm.issues if i.symbol == "query-in-loop"]
        self.assertEqual(len(matches), 1)

    def test_query_call_inside_loop_local_helper_function_is_not_flagged(self):
        source = (
            "def f(ids):\n"
            "    for i in ids:\n"
            "        def helper():\n"
            "            return Order.objects.get(pk=i)\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("query-in-loop", {i.symbol for i in fm.issues})

    def test_new_exception_raised_without_chaining_is_flagged(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError as e:\n"
            "        raise RuntimeError('bad value')\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertIn("lost-exception-context", {i.symbol for i in fm.issues})

    def test_new_exception_raised_with_explicit_from_is_not_flagged(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError as e:\n"
            "        raise RuntimeError('bad value') from e\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("lost-exception-context", {i.symbol for i in fm.issues})

    def test_new_exception_raised_with_explicit_from_none_is_not_flagged(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError as e:\n"
            "        raise RuntimeError('bad value') from None\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("lost-exception-context", {i.symbol for i in fm.issues})

    def test_new_exception_referencing_original_in_message_is_not_flagged(self):
        source = (
            "def f():\n"
            "    try:\n"
            "        risky()\n"
            "    except ValueError as e:\n"
            "        raise RuntimeError(str(e))\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("lost-exception-context", {i.symbol for i in fm.issues})

    def test_bare_reraise_is_not_flagged_as_lost_context(self):
        source = "def f():\n    try:\n        risky()\n    except ValueError as e:\n        raise\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("lost-exception-context", {i.symbol for i in fm.issues})

    def test_reraise_same_name_is_not_flagged_as_lost_context(self):
        source = "def f():\n    try:\n        risky()\n    except ValueError as e:\n        raise e\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("lost-exception-context", {i.symbol for i in fm.issues})

    def test_except_without_bound_name_is_not_flagged_as_lost_context(self):
        source = "def f():\n    try:\n        risky()\n    except ValueError:\n        raise RuntimeError('x')\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("lost-exception-context", {i.symbol for i in fm.issues})

    def test_stale_docstring_param_is_flagged(self):
        """A Google-style Args: entry for a removed parameter should be caught."""
        source = (
            "def f(a):\n"
            "    \"\"\"Do a thing.\n\n"
            "    Args:\n"
            "        a: first\n"
            "        removed: no longer a parameter\n"
            "    \"\"\"\n"
            "    return a\n"
        )
        fm = python_analyzer.analyze("f.py", source, _limits())
        issues = [i for i in fm.issues if i.symbol == "stale-docstring-param"]
        self.assertEqual(len(issues), 1)
        self.assertIn("removed", issues[0].message)

    def test_docstring_matching_signature_is_not_flagged(self):
        source = "def f(a):\n    \"\"\"Do a thing.\n\n    Args:\n        a: first\n    \"\"\"\n    return a\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("stale-docstring-param", {i.symbol for i in fm.issues})

    def test_docstring_without_params_section_is_not_flagged(self):
        source = "def f(a):\n    \"\"\"Just a summary.\"\"\"\n    return a\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("stale-docstring-param", {i.symbol for i in fm.issues})

    def test_unreachable_code_after_return_is_flagged(self):
        source = "def f(a):\n    if a:\n        return 1\n        unreachable()\n    return 0\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        issues = [i for i in fm.issues if i.symbol == "unreachable-code"]
        self.assertEqual(len(issues), 1)

    def test_code_without_early_return_is_not_flagged(self):
        source = "def f(a):\n    if a:\n        return 1\n    return 0\n"
        fm = python_analyzer.analyze("f.py", source, _limits())
        self.assertNotIn("unreachable-code", {i.symbol for i in fm.issues})

    def test_print_in_plain_module_function_is_flagged(self):
        """A print() call in an ordinary library function, with no
        __name__ == "__main__" guard anywhere in the file, is a candidate
        debug leftover."""
        source = "def do_work():\n    print('working')\n    return 1\n"
        fm = python_analyzer.analyze("lib.py", source, _limits())
        issues = [i for i in fm.issues if i.symbol == "print-in-library-code"]
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].category, "style")
        self.assertEqual(issues[0].severity, "info")

    def test_print_inside_main_guard_is_exempt(self):
        """A print() literally inside `if __name__ == "__main__":` is this
        file's own CLI output, not a library leftover."""
        source = (
            "def do_work():\n"
            "    return 1\n\n\n"
            "if __name__ == '__main__':\n"
            "    print(do_work())\n"
        )
        fm = python_analyzer.analyze("tool.py", source, _limits())
        self.assertNotIn("print-in-library-code", {i.symbol for i in fm.issues})

    def test_print_in_helper_only_called_from_main_guard_is_exempt(self):
        """The exemption is file-scoped (does this file contain the guard
        at all), not call-graph-traced -- a helper only ever invoked from
        under the guard is exempt too."""
        source = (
            "def report(x):\n"
            "    print(x)\n\n\n"
            "def do_work():\n"
            "    return 1\n\n\n"
            "if __name__ == '__main__':\n"
            "    report(do_work())\n"
        )
        fm = python_analyzer.analyze("tool.py", source, _limits())
        self.assertNotIn("print-in-library-code", {i.symbol for i in fm.issues})

    def test_print_in_test_file_is_exempt(self):
        source = "def test_it():\n    print('debug')\n    assert True\n"
        fm = python_analyzer.analyze("test_foo.py", source, _limits())
        self.assertNotIn("print-in-library-code", {i.symbol for i in fm.issues})

        fm2 = python_analyzer.analyze(os.path.join("tests", "foo.py"), source, _limits())
        self.assertNotIn("print-in-library-code", {i.symbol for i in fm2.issues})

    def test_print_under_examples_directory_is_exempt(self):
        source = "def do_work():\n    print('demo output')\n"
        fm = python_analyzer.analyze(os.path.join("examples", "demo.py"), source, _limits())
        self.assertNotIn("print-in-library-code", {i.symbol for i in fm.issues})

    def test_print_in_library_code_is_suppressible(self):
        """codequality: ignore[print-in-library-code] should suppress the finding."""
        source = "def do_work():\n    print('working')  # codequality: ignore[print-in-library-code]\n"
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "lib.py")
            with open(path, "w") as f:
                f.write(source)
            fm = analyze_file(root, "lib.py", "python", Config({}))
        self.assertNotIn("print-in-library-code", {i.symbol for i in fm.issues})
        self.assertEqual(fm.suppressed_count, 1)

    def test_nested_function_does_not_inflate_parent_complexity(self):
        """A closure's branching should count toward its own complexity, not its parent's."""
        source = (
            "def outer():\n"
            "    def inner():\n"
            "        if 1:\n"
            "            if 2:\n"
            "                if 3:\n"
            "                    pass\n"
            "    return inner\n"
        )
        fm = python_analyzer.analyze("nested.py", source, _limits())
        by_name = {f.name: f for f in fm.functions}
        self.assertEqual(by_name["outer"].complexity, 1)
        self.assertEqual(by_name["inner"].complexity, 4)


if __name__ == "__main__":
    unittest.main()

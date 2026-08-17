import unittest

from codequality.analyzers import treesitter_analyzer

if treesitter_analyzer.AVAILABLE:
    from tree_sitter_language_pack import get_parser

    from codequality.analyzers import js_security


def _issues(src, language="javascript", only_lines=None):
    parser = get_parser(language)
    tree = parser.parse(src)
    root = tree.root_node()
    return js_security.security_issues(root, "a.js", src, only_lines)


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestDangerousEval(unittest.TestCase):
    def test_new_function_flags(self):
        issues = _issues("function f(x) { new Function(x); }\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "dangerous-eval")

    def test_new_other_constructor_does_not_flag(self):
        self.assertEqual(_issues("function f(x) { new Map(x); }\n"), [])

    def test_bare_eval_is_not_duplicated_here(self):
        # already caught by generic_analyzer's line-level regex path --
        # js_security.py deliberately doesn't also flag it (see module docstring)
        self.assertEqual(_issues("function f(x) { eval(x); }\n"), [])


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestWeakHash(unittest.TestCase):
    def test_md5_flags(self):
        issues = _issues("const h = crypto.createHash('md5');\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "weak-hash")

    def test_sha1_flags(self):
        issues = _issues("const h = crypto.createHash('sha1');\n")
        self.assertEqual(len(issues), 1)

    def test_sha256_does_not_flag(self):
        self.assertEqual(_issues("const h = crypto.createHash('sha256');\n"), [])

    def test_unrelated_call_does_not_flag(self):
        self.assertEqual(_issues("const h = otherObj.createHash('md5');\n"), [])


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestShellTrue(unittest.TestCase):
    def test_child_process_exec_flags(self):
        issues = _issues("child_process.exec(cmd);\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "shell-true")

    def test_exec_sync_flags(self):
        issues = _issues("child_process.execSync(cmd);\n")
        self.assertEqual(len(issues), 1)

    def test_spawn_without_shell_option_does_not_flag(self):
        self.assertEqual(_issues("spawn(cmd, [], {cwd: '/tmp'});\n"), [])

    def test_spawn_with_shell_true_flags(self):
        issues = _issues("spawn(cmd, [], {shell: true});\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "shell-true")

    def test_spawn_with_shell_false_does_not_flag(self):
        self.assertEqual(_issues("spawn(cmd, [], {shell: false});\n"), [])

    def test_exec_file_with_shell_true_flags(self):
        issues = _issues("execFile(cmd, [], {shell: true});\n")
        self.assertEqual(len(issues), 1)


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestSqlInjectionRisk(unittest.TestCase):
    def test_template_literal_argument_flags(self):
        issues = _issues("db.query(`SELECT * FROM t WHERE id = ${id}`);\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "sql-injection-risk")

    def test_plain_string_literal_does_not_flag(self):
        self.assertEqual(_issues("db.query('SELECT 1');\n"), [])

    def test_template_literal_without_substitution_does_not_flag(self):
        self.assertEqual(_issues("db.query(`SELECT 1`);\n"), [])

    def test_concatenation_argument_flags(self):
        issues = _issues("db.query('SELECT * FROM t WHERE id = ' + id);\n")
        self.assertEqual(len(issues), 1)

    def test_parameterized_query_with_separate_params_not_flagged(self):
        self.assertEqual(_issues("db.query(`SELECT * FROM t WHERE id = ${id}`, [id]);\n"), [])

    def test_bare_variable_argument_not_flagged_here(self):
        # This is what js_taint_flow.py is for -- js_security.py only sees
        # the call site's own expression shape, not where `q` came from.
        self.assertEqual(_issues("db.query(q);\n"), [])

    def test_non_query_method_does_not_flag(self):
        self.assertEqual(_issues("logger.info(`request ${id}`);\n"), [])

    def test_typescript_source_parses_and_flags(self):
        issues = _issues(
            "function f(id: string, db: any) {\n"
            "  db.query(`SELECT * FROM t WHERE id = ${id}`);\n"
            "}\n",
            language="typescript",
        )
        self.assertEqual(len(issues), 1)


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestOnlyLines(unittest.TestCase):
    def test_only_lines_filters_findings(self):
        src = "const h1 = crypto.createHash('md5');\nconst h2 = crypto.createHash('md5');\n"
        self.assertEqual(len(_issues(src, only_lines={1})), 1)
        self.assertEqual(len(_issues(src, only_lines={1, 2})), 2)
        self.assertEqual(_issues(src, only_lines={99}), [])


if __name__ == "__main__":
    unittest.main()

import unittest

from codequality.analyzers import treesitter_analyzer

if treesitter_analyzer.AVAILABLE:
    from tree_sitter_language_pack import get_parser

    from codequality.analyzers import go_security


def _issues(src, only_lines=None):
    parser = get_parser("go")
    tree = parser.parse(src.encode("utf-8"))
    root = tree.root_node
    return go_security.security_issues(root, "a.go", src, only_lines)


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestWeakHash(unittest.TestCase):
    def test_md5_new_flags(self):
        src = 'package main\nimport "crypto/md5"\nfunc f() {\n\th := md5.New()\n\t_ = h\n}\n'
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "weak-hash")

    def test_sha1_sum_flags(self):
        src = 'package main\nimport "crypto/sha1"\nfunc f() {\n\th := sha1.Sum(nil)\n\t_ = h\n}\n'
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "weak-hash")

    def test_sha256_does_not_flag(self):
        src = 'package main\nimport "crypto/sha256"\nfunc f() {\n\th := sha256.New()\n\t_ = h\n}\n'
        self.assertEqual(_issues(src), [])

    def test_unrelated_package_new_does_not_flag(self):
        src = "package main\nfunc f() {\n\tb := bytes.New()\n\t_ = b\n}\n"
        self.assertEqual(_issues(src), [])

    def test_local_md5_type_does_not_flag(self):
        # A locally-defined type named 'md5' must not trigger weak-hash when
        # crypto/md5 is not imported.
        src = "package main\ntype md5 struct{}\nfunc (m *md5) New() {}\nfunc f() { m := &md5{}; m.New() }\n"
        self.assertEqual(_issues(src), [])


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestShellTrue(unittest.TestCase):
    def test_exec_command_sh_flags(self):
        src = 'package main\nfunc f() {\n\tcmd := exec.Command("sh", "-c", x)\n\t_ = cmd\n}\n'
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "shell-true")

    def test_exec_command_bash_flags(self):
        src = 'package main\nfunc f() {\n\tcmd := exec.Command("bash", "-c", x)\n\t_ = cmd\n}\n'
        self.assertEqual(len(_issues(src)), 1)

    def test_exec_command_context_flags(self):
        src = 'package main\nfunc f(ctx context.Context) {\n\tcmd := exec.CommandContext(ctx, "sh", "-c", x)\n\t_ = cmd\n}\n'
        self.assertEqual(len(_issues(src)), 1)

    def test_exec_command_plain_binary_does_not_flag(self):
        src = 'package main\nfunc f() {\n\tcmd := exec.Command("ls", "-la")\n\t_ = cmd\n}\n'
        self.assertEqual(_issues(src), [])

    def test_unrelated_command_call_does_not_flag(self):
        src = 'package main\nfunc f() {\n\tcmd := builder.Command("sh")\n\t_ = cmd\n}\n'
        self.assertEqual(_issues(src), [])


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestSqlInjectionRisk(unittest.TestCase):
    _SQL_IMPORT = 'import "database/sql"\n'

    def test_sprintf_argument_flags(self):
        src = f'package main\n{self._SQL_IMPORT}func f() {{\n\tdb.Query(fmt.Sprintf("SELECT * FROM t WHERE id = %s", id))\n}}\n'
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "sql-injection-risk")

    def test_concatenation_argument_flags(self):
        src = f'package main\n{self._SQL_IMPORT}func f() {{\n\tdb.Query("SELECT * FROM t WHERE id = " + id)\n}}\n'
        self.assertEqual(len(_issues(src)), 1)

    def test_plain_string_literal_does_not_flag(self):
        src = f'package main\n{self._SQL_IMPORT}func f() {{\n\tdb.Query("SELECT 1")\n}}\n'
        self.assertEqual(_issues(src), [])

    def test_parameterized_query_with_separate_params_not_flagged(self):
        src = f'package main\n{self._SQL_IMPORT}func f() {{\n\tdb.Query("SELECT * FROM t WHERE id = ?", id)\n}}\n'
        self.assertEqual(_issues(src), [])

    def test_bare_variable_argument_not_flagged_here(self):
        # This is what go_taint_flow.py is for -- go_security.py only sees
        # the call site's own expression shape, not where `q` came from.
        src = f'package main\n{self._SQL_IMPORT}func f() {{\n\tdb.Query(q)\n}}\n'
        self.assertEqual(_issues(src), [])

    def test_non_sql_method_does_not_flag(self):
        src = 'package main\nfunc f() {\n\tlogger.Printf("request %s", id)\n}\n'
        self.assertEqual(_issues(src), [])

    def test_no_sql_import_does_not_flag(self):
        # Without a SQL-related import the check must stay silent, preventing
        # false positives from unrelated types that happen to have a .Query() method.
        src = 'package main\nfunc f() {\n\tdb.Query(fmt.Sprintf("SELECT %s", id))\n}\n'
        self.assertEqual(_issues(src), [])

    def test_exec_context_sprintf_flags(self):
        src = (
            "package main\n"
            + self._SQL_IMPORT
            + "func f(ctx context.Context) {\n"
            + '\tdb.ExecContext(ctx, fmt.Sprintf("DELETE FROM t WHERE id = %s", id))\n'
            + "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestOnlyLines(unittest.TestCase):
    def test_only_lines_filters_findings(self):
        src = (
            "package main\n"
            'import "crypto/md5"\n'
            "func f() {\n"
            "\th1 := md5.New()\n"
            "\th2 := md5.New()\n"
            "\t_ = h1\n"
            "\t_ = h2\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src, only_lines={4})), 1)
        self.assertEqual(len(_issues(src, only_lines={4, 5})), 2)
        self.assertEqual(_issues(src, only_lines={99}), [])


if __name__ == "__main__":
    unittest.main()

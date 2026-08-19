import unittest

from codequality.analyzers import treesitter_analyzer

if treesitter_analyzer.AVAILABLE:
    from tree_sitter_language_pack import get_parser

    from codequality.analyzers import go_taint_flow


def _issues(src, only_lines=None):
    parser = get_parser("go")
    tree = parser.parse(src.encode("utf-8"))
    root = tree.root_node
    return go_taint_flow.taint_issues(root, "a.go", src, only_lines)


_PREAMBLE = "package main\n"


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestSources(unittest.TestCase):
    def test_url_query_get_flags(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\tid := r.URL.Query().Get("id")\n'
            "\tdb.Query(id)\n"
            "}\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "tainted-data-flow")
        self.assertEqual(issues[0].line, 4)

    def test_form_value_flags(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\tid := r.FormValue("id")\n'
            "\tdb.Query(id)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_post_form_value_flags(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\tid := r.PostFormValue("id")\n'
            "\tdb.Query(id)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_header_get_flags(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\ttoken := r.Header.Get("X-Token")\n'
            "\tdb.Query(token)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_os_getenv_flags(self):
        src = _PREAMBLE + (
            "func h(db *sql.DB) {\n"
            '\tsecret := os.Getenv("X")\n'
            "\tdb.Query(secret)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_os_args_flags(self):
        src = _PREAMBLE + (
            "func h(db *sql.DB) {\n"
            "\targ := os.Args[1]\n"
            "\tdb.Query(arg)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_formvalue_suffix_match_is_receiver_agnostic(self):
        # Any receiver's .FormValue(...) counts as a source, not just an
        # *http.Request's -- same best-effort, receiver-agnostic posture
        # as JS's dotted-name matching. Documents the tradeoff (a false
        # positive is possible here) rather than asserting a false negative.
        src = _PREAMBLE + (
            "func h(cfg *Config, db *sql.DB) {\n"
            '\tid := cfg.FormValue("id")\n'
            "\tdb.Query(id)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_plain_local_variable_does_not_flag(self):
        src = _PREAMBLE + (
            "func h(db *sql.DB) {\n"
            '\tid := "safe"\n'
            "\tdb.Query(id)\n"
            "}\n"
        )
        self.assertEqual(_issues(src), [])


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestPropagation(unittest.TestCase):
    def test_multi_hop_reassignment_flags(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\ta := r.FormValue("id")\n'
            "\tb := a\n"
            "\tc := b\n"
            "\tdb.Query(c)\n"
            "}\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 6)

    def test_plain_assignment_reflagged(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            "\tvar id string\n"
            '\tid = r.FormValue("id")\n'
            "\tdb.Query(id)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_sprintf_built_from_tainted_var_flags(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\tid := r.FormValue("id")\n'
            '\tq := fmt.Sprintf("SELECT * FROM t WHERE id = %s", id)\n'
            "\tdb.Query(q)\n"
            "}\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 5)

    def test_reassignment_to_literal_clears_taint(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\tq := r.FormValue("id")\n'
            '\tq = "SELECT 1"\n'
            "\tdb.Query(q)\n"
            "}\n"
        )
        self.assertEqual(_issues(src), [])


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestBranching(unittest.TestCase):
    def test_if_branch_taint_reaches_sink_after(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB, flag bool) {\n"
            '\tq := "safe"\n'
            "\tif flag {\n"
            '\t\tq = r.FormValue("id")\n'
            "\t}\n"
            "\tdb.Query(q)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_if_else_branch_taint_reaches_sink_after(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB, flag bool) {\n"
            "\tvar q string\n"
            "\tif flag {\n"
            '\t\tq = "SELECT 1"\n'
            "\t} else {\n"
            '\t\tq = r.FormValue("id")\n'
            "\t}\n"
            "\tdb.Query(q)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_else_if_branch_taint_reaches_sink_after(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB, a bool, b bool) {\n"
            "\tvar q string\n"
            "\tif a {\n"
            '\t\tq = "SELECT 1"\n'
            "\t} else if b {\n"
            '\t\tq = r.FormValue("id")\n'
            "\t}\n"
            "\tdb.Query(q)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_for_loop_body_taint_reaches_sink_after(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB, items []string) {\n"
            '\tq := "safe"\n'
            "\tfor range items {\n"
            '\t\tq = r.FormValue("id")\n'
            "\t}\n"
            "\tdb.Query(q)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_classic_for_loop_body_taint_reaches_sink_after(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\tq := "safe"\n'
            "\tfor i := 0; i < 10; i++ {\n"
            '\t\tq = r.FormValue("id")\n'
            "\t}\n"
            "\tdb.Query(q)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestNegatives(unittest.TestCase):
    def test_literal_argument_does_not_flag(self):
        src = _PREAMBLE + 'func h(db *sql.DB) {\n\tdb.Query("SELECT 1")\n}\n'
        self.assertEqual(_issues(src), [])

    def test_parameterized_query_not_flagged(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\tid := r.FormValue("id")\n'
            '\tdb.Query("SELECT 1", id)\n'
            "}\n"
        )
        self.assertEqual(_issues(src), [])

    def test_nested_func_literal_scope_is_independent(self):
        src = _PREAMBLE + (
            "func outer(r *http.Request, db *sql.DB) string {\n"
            '\tid := r.FormValue("id")\n'
            "\tinner := func(db2 *sql.DB) {\n"
            '\t\tdb2.Query("SELECT 1")\n'
            "\t}\n"
            "\tinner(db)\n"
            "\treturn id\n"
            "}\n"
        )
        self.assertEqual(_issues(src), [])

    def test_func_literal_route_handler_also_analyzed(self):
        src = _PREAMBLE + (
            "func main() {\n"
            "\thttp.HandleFunc(\"/\", func(w http.ResponseWriter, r *http.Request) {\n"
            '\t\tid := r.FormValue("id")\n'
            "\t\tdb.Query(id)\n"
            "\t})\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_method_declaration_also_analyzed(self):
        src = _PREAMBLE + (
            "func (repo *Repo) Find(r *http.Request, db *sql.DB) {\n"
            '\tid := r.FormValue("id")\n'
            "\tdb.Query(id)\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_only_lines_filters_to_diff_scope(self):
        src = _PREAMBLE + (
            "func h(r *http.Request, db *sql.DB) {\n"
            '\tid := r.FormValue("id")\n'
            "\tdb.Query(id)\n"
            "}\n"
        )
        self.assertEqual(_issues(src, only_lines={1, 2, 3}), [])
        self.assertEqual(len(_issues(src, only_lines={4})), 1)


if __name__ == "__main__":
    unittest.main()

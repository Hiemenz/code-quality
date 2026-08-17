import unittest

from codequality.analyzers import treesitter_analyzer

if treesitter_analyzer.AVAILABLE:
    from tree_sitter_language_pack import get_parser

    from codequality.analyzers import js_taint_flow


def _issues(src, language="javascript", only_lines=None):
    parser = get_parser(language)
    tree = parser.parse(src.encode("utf-8"))
    root = tree.root_node
    return js_taint_flow.taint_issues(root, "a.js", src, only_lines)


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestSources(unittest.TestCase):
    def test_req_query_flags(self):
        src = "function h(req, db) {\n  const id = req.query.id;\n  db.query(id);\n}\n"
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "tainted-data-flow")
        self.assertEqual(issues[0].line, 3)

    def test_req_params_flags(self):
        src = "function h(req, db) {\n  const id = req.params.id;\n  db.query(id);\n}\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_req_body_flags(self):
        src = "function h(req, db) {\n  const id = req.body.id;\n  db.query(id);\n}\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_req_headers_flags(self):
        src = "function h(req, db) {\n  const id = req.headers.x;\n  db.query(id);\n}\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_process_env_flags(self):
        src = "function h(db) {\n  const id = process.env.X;\n  db.query(id);\n}\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_process_argv_flags(self):
        src = "function h(db) {\n  const id = process.argv[2];\n  db.query(id);\n}\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_unrelated_member_access_does_not_flag(self):
        src = "function h(config, db) {\n  const id = config.query.id;\n  db.query(id);\n}\n"
        self.assertEqual(_issues(src), [])


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestPropagation(unittest.TestCase):
    def test_multi_hop_reassignment_flags(self):
        src = (
            "function h(req, db) {\n"
            "  const a = req.query.id;\n"
            "  const b = a;\n"
            "  const c = b;\n"
            "  db.query(c);\n"
            "}\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 5)

    def test_var_declaration_also_tracked(self):
        src = "function h(req, db) {\n  var id = req.query.id;\n  db.query(id);\n}\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_plain_assignment_reflagged(self):
        src = "function h(req, db) {\n  let id;\n  id = req.query.id;\n  db.query(id);\n}\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_template_literal_built_from_tainted_var_flags(self):
        src = (
            "function h(req, db) {\n"
            "  const id = req.query.id;\n"
            "  const q = `SELECT * FROM t WHERE id = ${id}`;\n"
            "  db.query(q);\n"
            "}\n"
        )
        issues = _issues(src)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].line, 4)

    def test_reassignment_to_literal_clears_taint(self):
        src = (
            "function h(req, db) {\n"
            "  let q = req.query.id;\n"
            "  q = 'SELECT 1';\n"
            "  db.query(q);\n"
            "}\n"
        )
        self.assertEqual(_issues(src), [])


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestBranching(unittest.TestCase):
    def test_if_branch_taint_reaches_sink_after(self):
        src = (
            "function h(req, db, flag) {\n"
            "  let q = 'safe';\n"
            "  if (flag) {\n"
            "    q = req.query.id;\n"
            "  }\n"
            "  db.query(q);\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_if_else_branch_taint_reaches_sink_after(self):
        src = (
            "function h(req, db, flag) {\n"
            "  let q;\n"
            "  if (flag) {\n"
            "    q = 'SELECT 1';\n"
            "  } else {\n"
            "    q = req.query.id;\n"
            "  }\n"
            "  db.query(q);\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_for_loop_body_taint_reaches_sink_after(self):
        src = (
            "function h(req, db, items) {\n"
            "  let q = 'safe';\n"
            "  for (const item of items) {\n"
            "    q = req.query.id;\n"
            "  }\n"
            "  db.query(q);\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_for_of_iterable_source_flags_sink_in_body(self):
        src = "function h(req, db) {\n  for (const k of req.body) {\n    db.query(k);\n  }\n}\n"
        # `k` itself isn't seeded from the iterable (best-effort scope limit,
        # see js_taint_flow.py docstring) -- this documents current behavior.
        self.assertEqual(_issues(src), [])

    def test_try_body_taint_reaches_sink_in_finally(self):
        src = (
            "function h(req, db) {\n"
            "  let q = 'safe';\n"
            "  try {\n"
            "    q = req.query.id;\n"
            "  } finally {\n"
            "    db.query(q);\n"
            "  }\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)


@unittest.skipUnless(treesitter_analyzer.AVAILABLE, "tree-sitter-language-pack extra not installed")
class TestNegatives(unittest.TestCase):
    def test_literal_argument_does_not_flag(self):
        self.assertEqual(_issues("function h(db) {\n  db.query('SELECT 1');\n}\n"), [])

    def test_parameterized_query_not_flagged(self):
        src = "function h(req, db) {\n  const id = req.query.id;\n  db.query('SELECT 1', [id]);\n}\n"
        self.assertEqual(_issues(src), [])

    def test_nested_function_scope_is_independent(self):
        src = (
            "function outer(req, db) {\n"
            "  const id = req.query.id;\n"
            "  function inner(db2) {\n"
            "    db2.query('SELECT 1');\n"
            "  }\n"
            "  inner(db);\n"
            "  return id;\n"
            "}\n"
        )
        self.assertEqual(_issues(src), [])

    def test_arrow_function_route_handler_also_analyzed(self):
        src = "const h = (req, db) => {\n  const id = req.query.id;\n  db.query(id);\n};\n"
        self.assertEqual(len(_issues(src)), 1)

    def test_method_definition_also_analyzed(self):
        src = (
            "class Repo {\n"
            "  find(req, db) {\n"
            "    const id = req.query.id;\n"
            "    db.query(id);\n"
            "  }\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src)), 1)

    def test_only_lines_filters_to_diff_scope(self):
        src = "function h(req, db) {\n  const id = req.query.id;\n  db.query(id);\n}\n"
        self.assertEqual(_issues(src, only_lines={1, 2}), [])
        self.assertEqual(len(_issues(src, only_lines={3})), 1)

    def test_typescript_source_parses_and_flags(self):
        src = (
            "function h(req: any, db: any) {\n"
            "  const id: string = req.query.id;\n"
            "  db.query(id);\n"
            "}\n"
        )
        self.assertEqual(len(_issues(src, language="typescript")), 1)


if __name__ == "__main__":
    unittest.main()

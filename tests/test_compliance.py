import unittest

from codequality import compliance


def _issue(rule, category="security"):
    return {"file": "a.py", "line": 1, "category": category, "severity": "error", "rule": rule, "symbol": rule,
            "message": "msg"}


class TestBuildReport(unittest.TestCase):
    def test_no_issues_gives_empty_report(self):
        report = compliance.build_report([])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["cwe"], {})
        self.assertEqual(report["owasp"], {})
        self.assertEqual(report["unmapped"], [])

    def test_non_security_issues_are_ignored(self):
        report = compliance.build_report([_issue("long-function", category="complexity")])
        self.assertEqual(report["total"], 0)

    def test_mapped_rule_grouped_by_cwe_and_owasp(self):
        report = compliance.build_report([_issue("sql-injection-risk")])
        self.assertEqual(report["total"], 1)
        self.assertIn("CWE-89", report["cwe"])
        self.assertIn("A03:2021", report["owasp"])
        self.assertEqual(report["unmapped"], [])

    def test_rule_with_cwe_but_no_owasp_is_not_unmapped(self):
        report = compliance.build_report([_issue("insecure-tempfile")])
        self.assertEqual(report["total"], 1)
        self.assertIn("CWE-377", report["cwe"])
        self.assertEqual(report["owasp"], {})
        self.assertEqual(report["unmapped"], [])

    def test_rule_with_no_mapping_is_unmapped(self):
        report = compliance.build_report([_issue("fstring-log-arg")])
        self.assertEqual(report["total"], 1)
        self.assertEqual(report["cwe"], {})
        self.assertEqual(report["owasp"], {})
        self.assertEqual(len(report["unmapped"]), 1)

    def test_unknown_rule_symbol_is_unmapped_not_a_crash(self):
        report = compliance.build_report([_issue("not-a-real-rule")])
        self.assertEqual(report["total"], 1)
        self.assertEqual(len(report["unmapped"]), 1)

    def test_multiple_issues_same_cwe_grouped_together(self):
        report = compliance.build_report([_issue("unsafe-yaml-load"), _issue("unsafe-deserialization")])
        self.assertEqual(len(report["cwe"]["CWE-502"]), 2)

    def test_non_security_category_rule_with_cwe_is_still_included(self):
        report = compliance.build_report([_issue("unclosed-resource", category="correctness")])
        self.assertEqual(report["total"], 1)
        self.assertIn("CWE-404", report["cwe"])

    def test_non_security_category_rule_without_mapping_is_excluded(self):
        report = compliance.build_report([_issue("bare-except", category="correctness")])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["unmapped"], [])

    def test_shell_exec_is_registered_and_mapped(self):
        report = compliance.build_report([_issue("shell-exec")])
        self.assertEqual(report["total"], 1)
        self.assertIn("CWE-78", report["cwe"])
        self.assertIn("A03:2021", report["owasp"])


class TestRenderText(unittest.TestCase):
    def test_no_findings_message(self):
        self.assertEqual(compliance.render_text(compliance.build_report([])), "No security-relevant findings.")

    def test_render_includes_counts(self):
        text = compliance.render_text(compliance.build_report([_issue("sql-injection-risk")]))
        self.assertIn("1 security-relevant finding", text)
        self.assertIn("CWE-89", text)
        self.assertIn("A03:2021", text)


if __name__ == "__main__":
    unittest.main()

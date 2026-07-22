import ast
import unittest

from codequality.analyzers.naive_datetime import naive_datetime_issues


def _issues(source, only_lines=None):
    return naive_datetime_issues(ast.parse(source), "f.py", only_lines)


class TestNaiveDatetime(unittest.TestCase):
    def test_now_without_tz_flagged(self):
        issues = _issues("import datetime\ndatetime.now()\n")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].symbol, "naive-datetime")
        self.assertEqual(issues[0].category, "correctness")
        self.assertEqual(issues[0].severity, "warn")

    def test_now_with_positional_tz_not_flagged(self):
        self.assertEqual(_issues("datetime.now(timezone.utc)\n"), [])

    def test_now_with_keyword_tz_not_flagged(self):
        self.assertEqual(_issues("datetime.now(tz=timezone.utc)\n"), [])

    def test_now_with_explicit_none_flagged(self):
        self.assertEqual(len(_issues("datetime.now(None)\n")), 1)

    def test_today_flagged(self):
        self.assertEqual(len(_issues("datetime.today()\n")), 1)

    def test_utcnow_flagged(self):
        self.assertEqual(len(_issues("datetime.utcnow()\n")), 1)

    def test_fromtimestamp_without_tz_flagged(self):
        self.assertEqual(len(_issues("datetime.fromtimestamp(x)\n")), 1)

    def test_fromtimestamp_with_tz_not_flagged(self):
        self.assertEqual(_issues("datetime.fromtimestamp(x, tz=timezone.utc)\n"), [])

    def test_utcfromtimestamp_flagged(self):
        self.assertEqual(len(_issues("datetime.utcfromtimestamp(x)\n")), 1)

    def test_qualified_datetime_datetime_now_flagged(self):
        self.assertEqual(len(_issues("import datetime\ndatetime.datetime.now()\n")), 1)

    def test_dotted_alias_now_flagged(self):
        self.assertEqual(len(_issues("dt.datetime.now()\n")), 1)

    def test_now_on_unrelated_receiver_not_flagged(self):
        self.assertEqual(_issues("scheduler.now()\n"), [])
        self.assertEqual(_issues("clock.today()\n"), [])

    def test_bare_now_call_not_flagged(self):
        self.assertEqual(_issues("now()\n"), [])

    def test_only_lines_scoping(self):
        src = "datetime.now()\ndatetime.today()\n"
        self.assertEqual(len(_issues(src, only_lines={2})), 1)


if __name__ == "__main__":
    unittest.main()

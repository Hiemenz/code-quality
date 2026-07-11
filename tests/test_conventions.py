import os
import shutil
import tempfile
import unittest

from codequality.config import Config
from codequality import conventions


def _config():
    return Config.load(os.devnull, explicit_path=None, overrides={})


class _RepoCase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cq-conv-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, rel_path, content):
        full = os.path.join(self.root, rel_path)
        os.makedirs(os.path.dirname(full) or full, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def compute(self):
        return conventions.compute(self.root, _config())


def _typed_module(n_funcs=8):
    return "".join(
        f"def f{i}(a: int, b: int) -> int:\n    return a + b\n\n" for i in range(n_funcs)
    )


def _untyped_module(n_funcs=8):
    return "".join(
        f"def g{i}(a, b):\n    return a + b\n\n" for i in range(n_funcs)
    )


class TestTypeHintConvention(_RepoCase):
    def test_untyped_file_in_typed_repo_is_flagged(self):
        self.write("typed.py", _typed_module())
        self.write("rogue.py", _untyped_module(3))
        result = self.compute()
        self.assertTrue(result["conventions"]["type_hints"]["established"])
        files = [d["file"] for d in result["deviations"] if d["convention"] == "type_hints"]
        self.assertEqual(files, ["rogue.py"])

    def test_typed_file_in_untyped_repo_is_not_a_deviation(self):
        self.write("untyped.py", _untyped_module())
        self.write("nice.py", _typed_module(3))
        result = self.compute()
        self.assertFalse(result["conventions"]["type_hints"]["established"])
        self.assertEqual([d for d in result["deviations"] if d["convention"] == "type_hints"], [])

    def test_small_file_below_sample_floor_is_not_flagged(self):
        self.write("typed.py", _typed_module())
        self.write("tiny.py", "def g(a):\n    return a\n")  # 2 slots < the floor of 5
        result = self.compute()
        self.assertEqual([d for d in result["deviations"] if d["convention"] == "type_hints"], [])

    def test_self_and_cls_are_not_annotatable_slots(self):
        source = (
            "class C:\n"
            "    def m(self, a: int) -> int:\n"
            "        return a\n"
            "    @classmethod\n"
            "    def n(cls, b: int) -> int:\n"
            "        return b\n"
        )
        self.write("c.py", source)
        result = self.compute()
        # 2 params + 2 returns = 4 slots, all annotated; self/cls excluded.
        self.assertEqual(result["conventions"]["type_hints"]["samples"], 4)
        self.assertEqual(result["conventions"]["type_hints"]["repo_share"], 1.0)

    def test_repo_below_sample_floor_establishes_nothing(self):
        self.write("one.py", "def f(a: int) -> int:\n    return a\n")
        result = self.compute()
        self.assertFalse(result["conventions"]["type_hints"]["established"])


class TestQuoteConvention(_RepoCase):
    def test_single_quote_file_in_double_quote_repo_is_flagged(self):
        body = "".join(f'X{i} = "value{i}"\n' for i in range(60))
        self.write("main.py", body)
        rogue = "".join(f"Y{i} = 'value{i}'\n" for i in range(12))
        self.write("rogue.py", rogue)
        result = self.compute()
        self.assertEqual(result["conventions"]["quote_style"]["dominant"], '"')
        files = [d["file"] for d in result["deviations"] if d["convention"] == "quote_style"]
        self.assertEqual(files, ["rogue.py"])

    def test_docstrings_and_triple_quotes_do_not_count(self):
        body = '"""Module docstring."""\n' + "".join(f"X{i} = 'v{i}'\n" for i in range(25))
        self.write("main.py", body)
        result = self.compute()
        self.assertEqual(result["conventions"]["quote_style"]["dominant"], "'")
        self.assertEqual(result["conventions"]["quote_style"]["samples"], 25)

    def test_a_few_stray_quotes_do_not_flag_a_file(self):
        body = "".join(f'X{i} = "value{i}"\n' for i in range(30))
        self.write("main.py", body)
        self.write("small.py", "A = 'x'\nB = 'y'\n")  # 2 samples < floor of 10
        result = self.compute()
        self.assertEqual([d for d in result["deviations"] if d["convention"] == "quote_style"], [])

    def test_no_dominance_when_repo_is_split(self):
        self.write("a.py", "".join(f'X{i} = "v"\n' for i in range(15)))
        self.write("b.py", "".join(f"Y{i} = 'v'\n" for i in range(15)))
        result = self.compute()
        self.assertIsNone(result["conventions"]["quote_style"]["dominant"])
        self.assertEqual(result["deviations"], [])


class TestDocstringStyleConvention(_RepoCase):
    def test_google_style_file_in_sphinx_repo_is_flagged(self):
        sphinx_fn = 'def f{i}(x):\n    """Do.\n\n    :param x: thing\n    """\n    return x\n\n'
        self.write("main.py", "".join(sphinx_fn.format(i=i).replace("def f", f"def f{i}_") for i in range(22)))
        google_fn = 'def g{i}(x):\n    """Do.\n\n    Args:\n        x: thing\n    """\n    return x\n\n'
        self.write("rogue.py", "".join(google_fn.format(i=i).replace("def g", f"def g{i}_") for i in range(3)))
        result = self.compute()
        self.assertEqual(result["conventions"]["docstring_style"]["dominant"], "sphinx")
        files = [d["file"] for d in result["deviations"] if d["convention"] == "docstring_style"]
        self.assertEqual(files, ["rogue.py"])

    def test_plain_docstrings_carry_no_style_vote(self):
        self.write("main.py", 'def f():\n    """Just a sentence."""\n    return 1\n')
        result = self.compute()
        self.assertEqual(result["conventions"]["docstring_style"]["samples"], 0)


class TestFormattingConvention(_RepoCase):
    def test_format_call_file_in_fstring_repo_is_flagged(self):
        body = "".join(f'A{i} = f"v{{X}}{i}"\n' for i in range(25))
        self.write("main.py", "X = 1\n" + body)
        rogue = "".join(f'B{i} = "v{{}}".format({i})\n' for i in range(4))
        self.write("rogue.py", rogue)
        result = self.compute()
        self.assertEqual(result["conventions"]["string_formatting"]["dominant"], "f-string")
        files = [d["file"] for d in result["deviations"] if d["convention"] == "string_formatting"]
        self.assertEqual(files, ["rogue.py"])

    def test_percent_formatting_is_ignored(self):
        body = "X = 1\n" + "".join(f'A{i} = f"v{{X}}"\n' for i in range(25))
        body += 'import logging\nlogging.getLogger(__name__).info("x %s", X)\n'
        self.write("main.py", body)
        result = self.compute()
        self.assertEqual(result["conventions"]["string_formatting"]["samples"], 25)

    def test_format_on_a_variable_is_not_counted(self):
        # Only literal-string .format() counts -- template.format() could be
        # anything (a user object with its own format method).
        self.write("main.py", "def f(template):\n    return template.format(1)\n")
        result = self.compute()
        self.assertEqual(result["conventions"]["string_formatting"]["samples"], 0)


class TestRobustness(_RepoCase):
    def test_empty_repo_reports_cleanly(self):
        result = self.compute()
        self.assertEqual(result["files_analyzed"], 0)
        self.assertEqual(result["deviations"], [])
        for info in result["conventions"].values():
            self.assertFalse(info["established"])

    def test_syntax_error_file_is_skipped_not_fatal(self):
        self.write("broken.py", "def f(:\n")
        self.write("ok.py", _typed_module(2))
        result = self.compute()
        self.assertEqual(result["files_analyzed"], 1)

    def test_deviations_are_deterministically_sorted(self):
        self.write("typed.py", _typed_module())
        self.write("z_rogue.py", _untyped_module(3))
        self.write("a_rogue.py", _untyped_module(3))
        result = self.compute()
        files = [d["file"] for d in result["deviations"]]
        self.assertEqual(files, sorted(files))

    def test_render_text_covers_established_and_not(self):
        self.write("typed.py", _typed_module())
        self.write("rogue.py", _untyped_module(3))
        text = conventions.render_text(self.compute())
        self.assertIn("Learned conventions", text)
        self.assertIn("rogue.py", text)
        self.assertIn("not established", text)

    def test_render_text_with_no_deviations(self):
        self.write("typed.py", _typed_module())
        text = conventions.render_text(self.compute())
        self.assertIn("No files deviate", text)


if __name__ == "__main__":
    unittest.main()

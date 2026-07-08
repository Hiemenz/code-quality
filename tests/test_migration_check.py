import os
import tempfile
import unittest

from codequality import migration_check


def _write(root, rel_path, content):
    full = os.path.join(root, rel_path)
    os.makedirs(os.path.dirname(full) or root, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)


class TestNoMigrationsRepo(unittest.TestCase):
    def test_repo_with_no_migrations_returns_no_issues(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "main.py", "print('hi')\n")
            self.assertEqual(migration_check.check(root), [])


class TestDjangoMigrations(unittest.TestCase):
    def test_runpython_without_reverse_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "app/migrations/0002_populate.py",
                "from django.db import migrations\n\n"
                "def forwards(apps, schema_editor):\n    pass\n\n"
                "class Migration(migrations.Migration):\n"
                "    operations = [migrations.RunPython(forwards)]\n",
            )
            issues = migration_check.check(root)
            found = [i for i in issues if i.symbol == "irreversible-django-migration"]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].file, os.path.join("app", "migrations", "0002_populate.py"))

    def test_runpython_with_reverse_function_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "app/migrations/0002_populate.py",
                "from django.db import migrations\n\n"
                "def forwards(apps, schema_editor):\n    pass\n\n"
                "def backwards(apps, schema_editor):\n    pass\n\n"
                "class Migration(migrations.Migration):\n"
                "    operations = [migrations.RunPython(forwards, backwards)]\n",
            )
            issues = migration_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "irreversible-django-migration"], [])

    def test_runpython_noop_reverse_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "app/migrations/0002_populate.py",
                "from django.db import migrations\n\n"
                "def forwards(apps, schema_editor):\n    pass\n\n"
                "class Migration(migrations.Migration):\n"
                "    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]\n",
            )
            issues = migration_check.check(root)
            self.assertEqual([i for i in issues if i.symbol == "irreversible-django-migration"], [])

    def test_init_file_in_migrations_dir_is_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "app/migrations/__init__.py", "")
            self.assertEqual(migration_check.check(root), [])


class TestAlembicMigrations(unittest.TestCase):
    def test_missing_downgrade_function_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "alembic/versions/abc123_add_col.py",
                "revision = 'abc123'\ndown_revision = None\n\n"
                "def upgrade():\n    pass\n",
            )
            issues = migration_check.check(root)
            found = [i for i in issues if i.symbol == "alembic-downgrade-missing"]
            self.assertEqual(len(found), 1)

    def test_empty_downgrade_body_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "alembic/versions/abc123_add_col.py",
                "revision = 'abc123'\ndown_revision = None\n\n"
                "def upgrade():\n    pass\n\n"
                "def downgrade():\n    pass\n",
            )
            issues = migration_check.check(root)
            found = [i for i in issues if i.symbol == "alembic-downgrade-noop"]
            self.assertEqual(len(found), 1)

    def test_real_downgrade_body_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(
                root, "alembic/versions/abc123_add_col.py",
                "revision = 'abc123'\ndown_revision = None\n\n"
                "def upgrade():\n    op.add_column('t', 'c')\n\n"
                "def downgrade():\n    op.drop_column('t', 'c')\n",
            )
            issues = migration_check.check(root)
            self.assertEqual(
                [i for i in issues if i.symbol in ("alembic-downgrade-noop", "alembic-downgrade-missing")], []
            )

    def test_non_alembic_file_with_a_downgrade_function_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "utils.py", "def downgrade():\n    pass\n")
            self.assertEqual(migration_check.check(root), [])


class TestSqlMigrations(unittest.TestCase):
    def test_up_sql_without_matching_down_is_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "migrations/0001_init.up.sql", "CREATE TABLE t (id int);\n")
            issues = migration_check.check(root)
            found = [i for i in issues if i.symbol == "sql-migration-missing-down"]
            self.assertEqual(len(found), 1)
            self.assertIn("0001_init.down.sql", found[0].message)

    def test_up_sql_with_matching_down_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "migrations/0001_init.up.sql", "CREATE TABLE t (id int);\n")
            _write(root, "migrations/0001_init.down.sql", "DROP TABLE t;\n")
            self.assertEqual(migration_check.check(root), [])


class TestRenderText(unittest.TestCase):
    def test_no_issues_renders_clean_message(self):
        self.assertIn("No issues found", migration_check.render_text([]))

    def test_issues_are_rendered_with_file_and_symbol(self):
        with tempfile.TemporaryDirectory() as root:
            _write(root, "migrations/0001_init.up.sql", "CREATE TABLE t (id int);\n")
            issues = migration_check.check(root)
            text = migration_check.render_text(issues)
            self.assertIn("0001_init.up.sql", text)
            self.assertIn("sql-migration-missing-down", text)


if __name__ == "__main__":
    unittest.main()

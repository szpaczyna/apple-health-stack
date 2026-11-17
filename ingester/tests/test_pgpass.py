import contextlib
import io
import os
import tempfile
import unittest
import zipfile
from unittest import mock

from apple_health_ingester import cli, pgpass
from apple_health_ingester.config import DatabaseConfig

WANTED = ("localhost", 5432, "health", "postgres")


class CheckTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "pgpass")

    def check(self, text, *wanted, mode=0o600):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)
        os.chmod(self.path, mode)
        return pgpass.check(self.path, *(wanted or WANTED))

    def test_exact_match(self):
        self.assertIsNone(self.check("localhost:5432:health:postgres:pw\n"))

    def test_wildcard_match(self):
        self.assertIsNone(self.check("*:*:*:postgres:pw\n"))

    def test_escaped_colon_in_password(self):
        self.assertIsNone(self.check("localhost:5432:health:postgres:pa\\:ss\n"))

    def test_escaped_colon_in_user(self):
        self.assertIsNone(self.check("localhost:5432:health:a\\:b:pw\n", "localhost", 5432, "health", "a:b"))
        self.assertIsNotNone(self.check("localhost:5432:health:a\\:b:pw\n"))

    def test_comments_and_blank_lines_are_ignored(self):
        self.assertIsNone(self.check("# localhost:5432:health:other:x\n\nlocalhost:5432:health:postgres:pw\n"))

    def test_mismatch_names_the_wanted_entry_without_the_password(self):
        error = self.check("otherhost:5432:health:postgres:s3cret\nlocalhost:5432:health\n")
        self.assertIn(self.path, error)
        self.assertIn("localhost:5432:health:postgres", error)
        self.assertNotIn("s3cret", error)

    def test_group_readable_file_is_rejected(self):
        error = self.check("localhost:5432:health:postgres:pw\n", mode=0o644)
        self.assertIn(f"chmod 600 {self.path}", error)

    def test_missing_file(self):
        self.assertIn(self.path, pgpass.check(self.path, *WANTED))

    def test_socket_host_matches_localhost(self):
        self.assertIsNone(self.check("localhost:5432:health:postgres:pw\n", "/var/run/postgresql", 5432,
                                     "health", "postgres"))

    def test_socket_host_matches_literal_path(self):
        self.assertIsNone(self.check("/custom/sock:5432:health:postgres:pw\n", "/custom/sock", 5432,
                                     "health", "postgres"))

    def test_first_match_with_empty_password_is_rejected(self):
        error = self.check("localhost:5432:health:postgres:\n*:*:*:*:pw\n")
        self.assertIn("empty password", error)


class ResolvePassfileTest(unittest.TestCase):
    def test_precedence(self):
        configured = DatabaseConfig(passfile="/cfg/pgpass")
        env = {"PGPASSFILE": "/env/pgpass"}
        self.assertEqual(pgpass.resolve_passfile(configured, env), "/cfg/pgpass")
        self.assertEqual(pgpass.resolve_passfile(DatabaseConfig(), env), "/env/pgpass")
        self.assertEqual(pgpass.resolve_passfile(DatabaseConfig(), {}), os.path.expanduser("~/.pgpass"))

    def test_empty_pgpassfile_is_ignored(self):
        self.assertEqual(pgpass.resolve_passfile(DatabaseConfig(), {"PGPASSFILE": ""}),
                         os.path.expanduser("~/.pgpass"))

    def test_tilde_is_expanded(self):
        resolved = pgpass.resolve_passfile(DatabaseConfig(), {"PGPASSFILE": "~/pg"})
        self.assertEqual(resolved, os.path.expanduser("~/pg"))
        self.assertFalse(resolved.startswith("~"))


class CliPassfileCheckTest(unittest.TestCase):
    def run_main(self, **env):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "export.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("apple_health_export/export.xml", "")
            passfile = os.path.join(tmp, "missing")
            err = io.StringIO()
            environ = {"XDG_CONFIG_HOME": tmp, "PG_PASSFILE": passfile, "UNPACK_DIR": tmp, **env}
            with (
                mock.patch.dict(os.environ, environ, clear=True),
                mock.patch.object(cli, "wait_for_db", side_effect=SystemExit(3)),
                mock.patch.object(pgpass, "check", wraps=pgpass.check) as check,
                contextlib.redirect_stderr(err),
                self.assertRaises(SystemExit) as raised,
            ):
                cli.main(["--zip", zip_path])
            return raised.exception.code, err.getvalue(), passfile, check

    def test_missing_passfile_exits_before_connecting(self):
        code, err, passfile, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertIn(passfile, err)

    def test_password_skips_the_check(self):
        code, _, _, check = self.run_main(PG_PASS="pw")
        self.assertEqual(code, 3)
        check.assert_not_called()


if __name__ == "__main__":
    unittest.main()

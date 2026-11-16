import contextlib
import dataclasses
import io
import os
import tempfile
import unittest
from unittest import mock

from apple_health_ingester import cli, config, db, routes
from apple_health_ingester.config import Config, ConfigError, DatabaseConfig, ImportConfig

DEFAULTS = Config(DatabaseConfig(), ImportConfig())
EXAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.example.yaml")


class ConfigLoadTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.env = {"XDG_CONFIG_HOME": os.path.join(self.tmp, "xdg")}

    def write(self, text, name="config.yaml"):
        path = os.path.join(self.tmp, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def load(self, text=None, cli_args=None, **env):
        cli_args = dict(cli_args or {})
        if text is not None:
            cli_args["config"] = self.write(text)
        return config.load(cli_args, {**self.env, **env})

    def assertConfigError(self, *needles, **kwargs):
        with self.assertRaises(ConfigError) as ctx:
            self.load(**kwargs)
        for needle in needles:
            self.assertIn(needle, str(ctx.exception))

    def test_defaults_without_file_or_env(self):
        self.assertEqual(self.load(), (DEFAULTS, None))

    def test_yaml_overrides_defaults(self):
        cfg, path = self.load("database:\n  port: 6000\nimport:\n  batch_size: 10\n")
        self.assertEqual(cfg.database.port, 6000)
        self.assertEqual(cfg.import_.batch_size, 10)
        self.assertEqual(cfg.database.host, "localhost")
        self.assertTrue(path.endswith("config.yaml"))

    def test_env_overrides_yaml(self):
        cfg, _ = self.load("database:\n  port: 6000\n", PG_PORT="7000", GPX_WORKERS="3")
        self.assertEqual(cfg.database.port, 7000)
        self.assertEqual(cfg.import_.gpx_workers, 3)

    def test_cli_zip_overrides_env(self):
        cfg, _ = self.load(cli_args={"zip_path": "cli.zip"}, ZIP_PATH="env.zip")
        self.assertEqual(cfg.import_.zip_path, "cli.zip")

    def test_apple_health_config_lookup(self):
        path = self.write("database:\n  user: alice\n", "env.yaml")
        cfg, used = self.load(APPLE_HEALTH_CONFIG=path)
        self.assertEqual((cfg.database.user, used), ("alice", path))

    def test_xdg_lookup(self):
        path = self.write("database:\n  user: bob\n", "xdg/apple-health-ingester/config.yaml")
        cfg, used = self.load()
        self.assertEqual((cfg.database.user, used), ("bob", path))

    def test_missing_explicit_config(self):
        self.assertConfigError("/nonexistent.yaml", cli_args={"config": "/nonexistent.yaml"})

    def test_missing_env_config(self):
        self.assertConfigError("/nonexistent.yaml", APPLE_HEALTH_CONFIG="/nonexistent.yaml")

    def test_empty_file(self):
        self.assertEqual(self.load("")[0], DEFAULTS)

    def test_unknown_key(self):
        self.assertConfigError("database.prot", "config.yaml", text="database:\n  prot: 1\n")

    def test_non_string_password_is_not_echoed(self):
        with self.assertRaises(ConfigError) as ctx:
            self.load("database:\n  password: 918273\n")
        self.assertIn("database.password", str(ctx.exception))
        self.assertNotIn("918273", str(ctx.exception))

    def test_unknown_section(self):
        self.assertConfigError("server", text="server: {}\n")

    def test_bad_yaml_types(self):
        self.assertConfigError("database.port", text="database:\n  port: abc\n")
        self.assertConfigError("database.port", text="database:\n  port: true\n")
        self.assertConfigError("database.host", text="database:\n  host: 5\n")
        self.assertConfigError("database.host", text="database:\n  host: null\n")

    def test_bad_env_int(self):
        self.assertConfigError("PG_PORT", PG_PORT="abc")

    def test_out_of_range(self):
        self.assertConfigError("import.batch_size", text="import:\n  batch_size: 0\n")
        self.assertConfigError("PG_PORT", PG_PORT="70000")

    def test_empty_env_is_unset(self):
        cfg, _ = self.load("database:\n  port: 6000\n", PG_PORT="", PG_PASS="")
        self.assertEqual((cfg.database.port, cfg.database.password), (6000, None))

    def test_tilde_expansion(self):
        cfg, _ = self.load("database:\n  passfile: ~/pgpass\n", ZIP_PATH="~/export.zip")
        self.assertEqual(cfg.database.passfile, os.path.expanduser("~/pgpass"))
        self.assertEqual(cfg.import_.zip_path, os.path.expanduser("~/export.zip"))

    def test_to_yaml_redacts_password(self):
        cfg, _ = self.load(PG_PASS="secret")
        text = config.to_yaml(cfg)
        self.assertNotIn("secret", text)
        self.assertIn("password: '***'", text)

    def test_example_matches_defaults(self):
        self.assertEqual(config.load({"config": EXAMPLE}, self.env)[0], DEFAULTS)


class CliConfigTest(unittest.TestCase):
    def run_cli(self, argv, **env):
        out = io.StringIO()
        with (
            tempfile.TemporaryDirectory() as config_home,
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_home, **env}, clear=True),
            contextlib.redirect_stdout(out),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.main(argv)
        return ctx.exception.code, out.getvalue()

    def test_print_config_hides_password(self):
        code, out = self.run_cli(["--print-config"], PG_PASS="secret")
        self.assertEqual(code, 0)
        self.assertIn("# config file: none", out)
        self.assertNotIn("secret", out)

    def test_missing_config_exits_2(self):
        code, out = self.run_cli(["--config", "/nonexistent.yaml"])
        self.assertEqual(code, 2)
        self.assertIn("/nonexistent.yaml", out)

    def test_zip_flag_reaches_the_import(self):
        code, out = self.run_cli(["--zip", "/nonexistent.zip"])
        self.assertEqual(code, 1)
        self.assertIn("/nonexistent.zip", out)

    def test_bad_env_int_exits_2(self):
        code, out = self.run_cli([], PG_PORT="abc")
        self.assertEqual(code, 2)
        self.assertIn("PG_PORT", out)


class GetConnectionTest(unittest.TestCase):
    def test_kwargs_come_from_database_config(self):
        db_cfg = DatabaseConfig(host="db", port=5433, user="u", dbname="d")
        with mock.patch.object(db.psycopg, "connect") as connect:
            db.get_connection(db_cfg)
            connect.assert_called_once_with(host="db", port=5433, user="u", dbname="d")
            connect.reset_mock()
            db.get_connection(dataclasses.replace(db_cfg, password="pw"))
            connect.assert_called_once_with(host="db", port=5433, user="u", dbname="d", password="pw")


class ResolveGpxWorkersTest(unittest.TestCase):
    def test_default_is_the_cpu_count(self):
        with mock.patch.object(routes.os, "process_cpu_count", return_value=7):
            self.assertEqual(routes.resolve_gpx_workers(None), 7)
        self.assertEqual(routes.resolve_gpx_workers(3), 3)


if __name__ == "__main__":
    unittest.main()

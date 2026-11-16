import contextlib
import io
import os
import unittest
from unittest import mock

from apple_health_ingester.log import logger


class TtyStringIO(io.StringIO):
    def isatty(self):
        return True


class LoggerTest(unittest.TestCase):
    def capture(self, method, out_cls=io.StringIO, err_cls=io.StringIO, **env):
        out, err = out_cls(), err_cls()
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            getattr(logger, method)("hello")
        return out.getvalue(), err.getvalue()

    def test_info_and_success_go_to_stdout(self):
        for method in ("info", "success"):
            self.assertEqual(self.capture(method), ("hello\n", ""))

    def test_warn_and_error_go_to_stderr(self):
        for method in ("warn", "error"):
            self.assertEqual(self.capture(method), ("", "hello\n"))

    def test_no_color_when_not_a_tty(self):
        out, err = self.capture("info")
        self.assertNotIn("\033[", out)
        out, err = self.capture("error")
        self.assertNotIn("\033[", err)

    def test_color_on_a_tty(self):
        out, _ = self.capture("info", out_cls=TtyStringIO)
        self.assertEqual(out, "\033[94mhello\033[0m\n")
        _, err = self.capture("error", err_cls=TtyStringIO)
        self.assertEqual(err, "\033[91mhello\033[0m\n")

    def test_no_color_env_disables_color_on_a_tty(self):
        out, _ = self.capture("info", out_cls=TtyStringIO, NO_COLOR="1")
        self.assertEqual(out, "hello\n")
        _, err = self.capture("warn", err_cls=TtyStringIO, NO_COLOR="1")
        self.assertEqual(err, "hello\n")

    def test_empty_no_color_keeps_color(self):
        out, _ = self.capture("success", out_cls=TtyStringIO, NO_COLOR="")
        self.assertEqual(out, "\033[92mhello\033[0m\n")


if __name__ == "__main__":
    unittest.main()

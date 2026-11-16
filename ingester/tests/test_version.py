import contextlib
import importlib.metadata
import io
import unittest

from apple_health_ingester import cli


class VersionFlagTest(unittest.TestCase):
    def test_prints_installed_version(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as cm:
            cli.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        expected = importlib.metadata.version("apple-health-ingester")
        self.assertEqual(out.getvalue(), f"apple-health-ingester {expected}\n")


if __name__ == "__main__":
    unittest.main()

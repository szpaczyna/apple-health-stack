import contextlib
import io
import math
import unittest
from unittest import mock

import psycopg

from apple_health_ingester import db
from apple_health_ingester.config import DatabaseConfig

DB_CFG = DatabaseConfig(host="db.example", port=6543, wait_timeout=5)


class WaitForDbTest(unittest.TestCase):
    def run_wait(self, failures):
        """Run wait_for_db on a fake clock; get_connection fails `failures` times, then succeeds."""
        clock = [100.0]
        deadline = clock[0] + DB_CFG.wait_timeout
        attempts = []

        def connect(db_cfg, connect_timeout=None):
            attempts.append((connect_timeout, deadline - clock[0]))
            clock[0] += 0.5
            if len(attempts) <= failures:
                raise psycopg.OperationalError("connection refused\nIs the server running?")
            return mock.MagicMock()

        def sleep(seconds):
            clock[0] += seconds

        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(db, "get_connection", side_effect=connect),
            mock.patch.object(db.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(db.time, "sleep", side_effect=sleep),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                db.wait_for_db(DB_CFG)
                code = None
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue(), attempts, clock[0]

    def assertTimeoutsBounded(self, attempts):
        for timeout, remaining in attempts:
            self.assertGreaterEqual(timeout, 2)
            self.assertLessEqual(timeout, max(2, math.ceil(remaining)))

    def test_gives_up_after_wait_timeout(self):
        code, _, err, attempts, now = self.run_wait(failures=100)
        self.assertEqual(code, 1)
        self.assertIn("db.example:6543", err)
        self.assertIn("5s", err)
        self.assertIn("connection refused", err)
        self.assertNotIn("Is the server running?", err)
        self.assertGreaterEqual(now, 100.0 + DB_CFG.wait_timeout)
        self.assertGreater(len(attempts), 1)
        self.assertTimeoutsBounded(attempts)

    def test_returns_when_the_second_attempt_succeeds(self):
        code, out, err, attempts, _ = self.run_wait(failures=1)
        self.assertIsNone(code)
        self.assertEqual(len(attempts), 2)
        self.assertIn("Waiting for PostgreSQL to be ready...", err)
        self.assertIn("PostgreSQL is ready.", out)
        self.assertTimeoutsBounded(attempts)


if __name__ == "__main__":
    unittest.main()

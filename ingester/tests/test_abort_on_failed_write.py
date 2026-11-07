import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

RECORD = ("StepCount", None, 1.0, "count", "Watch")
SLEEP = (None, "Watch", "Core", 1, None, None)


def run_main(data_rows, records_ok=True, sleep_ok=True, routes_ok=True, sources_error=None):
    """Run app.main() with the zip and database replaced by fakes."""
    conn = mock.MagicMock(closed=False)
    if sources_error:
        conn.cursor.side_effect = sources_error
    calls = {
        "zipfile": mock.MagicMock(),
        "extract_workout_routes": mock.MagicMock(return_value=0),
        "wait_for_db": mock.MagicMock(),
        "ReconnectingConnection": mock.MagicMock(return_value=conn),
        "init_schema": mock.MagicMock(),
        "truncate_tables": mock.MagicMock(),
        "drop_secondary_indexes": mock.MagicMock(),
        "find_xml_file": mock.MagicMock(return_value="apple_health_export/export.xml"),
        "health_data_generator": mock.MagicMock(return_value=iter(data_rows)),
        "write_records_batch": mock.MagicMock(return_value=records_ok),
        "write_sleep_batch": mock.MagicMock(return_value=sleep_ok),
        "write_routes_batch": mock.MagicMock(return_value=routes_ok),
        "process_workout_routes": mock.MagicMock(),
        "build_secondary_indexes": mock.MagicMock(return_value=(0.0, 0.0)),
    }
    with mock.patch.multiple(app, **calls):
        try:
            app.main()
            calls["exit_code"] = 0
        except SystemExit as e:
            calls["exit_code"] = e.code
    return calls


class AbortOnFailedWriteTest(unittest.TestCase):
    def test_failed_records_batch_mid_run_aborts(self):
        with mock.patch.object(app, "BATCH_SIZE", 1):
            calls = run_main([("record", RECORD)] * 3, records_ok=False)
        self.assertEqual(calls["exit_code"], 1)
        calls["process_workout_routes"].assert_not_called()
        calls["build_secondary_indexes"].assert_not_called()

    def test_failed_final_records_flush_aborts(self):
        calls = run_main([("record", RECORD)], records_ok=False)
        self.assertEqual(calls["exit_code"], 1)
        calls["build_secondary_indexes"].assert_not_called()

    def test_failed_final_sleep_flush_aborts(self):
        calls = run_main([("sleep", SLEEP)], sleep_ok=False)
        self.assertEqual(calls["exit_code"], 1)
        calls["build_secondary_indexes"].assert_not_called()

    def test_failed_data_sources_insert_aborts(self):
        calls = run_main([("record", RECORD)], sources_error=RuntimeError("boom"))
        self.assertEqual(calls["exit_code"], 1)
        calls["process_workout_routes"].assert_not_called()

    def test_successful_import_builds_indexes(self):
        calls = run_main([("record", RECORD), ("sleep", SLEEP)])
        self.assertEqual(calls["exit_code"], 0)
        calls["build_secondary_indexes"].assert_called_once()


class RouteWriteFailureTest(unittest.TestCase):
    def test_failed_route_write_stops_route_processing(self):
        parsed = [("Route A", [("route-a",)])]
        write = mock.MagicMock(return_value=False)
        with mock.patch.multiple(
            app,
            ROUTES_PATH=os.path.dirname(os.path.abspath(__file__)),
            get_gpx_workers=mock.MagicMock(return_value=1),
            parse_workout_route=mock.MagicMock(return_value=parsed),
            write_routes_batch=write,
        ), mock.patch.object(app.os, "listdir", return_value=["a.gpx", "b.gpx"]):
            with self.assertRaises(Exception):
                app.process_workout_routes(mock.MagicMock())
        write.assert_called_once()

    def test_unparsable_route_file_is_skipped(self):
        parse = mock.MagicMock(side_effect=ValueError("bad gpx"))
        with mock.patch.multiple(
            app,
            ROUTES_PATH=os.path.dirname(os.path.abspath(__file__)),
            get_gpx_workers=mock.MagicMock(return_value=1),
            parse_workout_route=parse,
            write_routes_batch=mock.MagicMock(return_value=True),
        ), mock.patch.object(app.os, "listdir", return_value=["a.gpx", "b.gpx"]):
            app.process_workout_routes(mock.MagicMock())
        self.assertEqual(parse.call_count, 2)


class PushSourcesRetryTest(unittest.TestCase):
    def test_dropped_connection_is_retried(self):
        conn = mock.MagicMock()
        copy = conn.cursor.return_value.__enter__.return_value.copy
        copy.side_effect = [app.psycopg.OperationalError("connection lost"), mock.MagicMock()]
        with mock.patch.object(app, "DB_RETRY_COOLDOWN", 0):
            app.push_sources(conn, {"Watch", "iPhone"})
        self.assertEqual(copy.call_count, 2)
        self.assertEqual(conn.ensure_open.call_count, 2)
        conn.discard_transaction.assert_called_once()
        conn.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()

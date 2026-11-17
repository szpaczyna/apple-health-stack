import os
import tempfile
import unittest
from unittest import mock

from apple_health_ingester import cli, db, export_xml, routes
import psycopg

RECORD = ("StepCount", None, 1.0, "count", "Watch")
SLEEP = (None, "Watch", "Core", 1, None, None)
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def run_main(data_rows, records_ok=True, sleep_ok=True, env=None,
             on_extract=None, xml_file="apple_health_export/export.xml"):
    """Run cli.main() with the zip and database replaced by fakes; on_extract(zip, path) replaces the extraction."""
    conn = mock.MagicMock()
    calls = {
        "zipfile": mock.MagicMock(),
        "extract_workout_routes": mock.MagicMock(return_value=0, side_effect=on_extract),
        "wait_for_db": mock.MagicMock(),
        "get_connection": mock.MagicMock(return_value=conn),
        "init_schema": mock.MagicMock(),
        "truncate_tables": mock.MagicMock(),
        "drop_secondary_indexes": mock.MagicMock(),
        "find_xml_file": mock.MagicMock(return_value=xml_file),
        "health_data_generator": mock.MagicMock(return_value=iter(data_rows)),
        "write_batch": mock.MagicMock(side_effect=lambda conn, table, rows: {
            "records": records_ok, "sleep_analysis": sleep_ok}[table]),
        "process_workout_routes": mock.MagicMock(),
        "build_secondary_indexes": mock.MagicMock(return_value=(0.0, 0.0)),
    }
    # Patch each name in the module that looks it up at call time.
    export_xml_names = ("health_data_generator", "write_batch")
    cli_calls = {k: v for k, v in calls.items() if k not in export_xml_names}
    with (
        tempfile.TemporaryDirectory() as config_home,
        # Keeps a real ~/.config file or PG_* variables out of the config load.
        mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config_home, "PG_PASS": "test", **(env or {})}, clear=True),
        mock.patch.multiple(cli, **cli_calls),
        mock.patch.multiple(export_xml, **{k: calls[k] for k in export_xml_names}),
    ):
        try:
            cli.main([])
            calls["exit_code"] = 0
        except SystemExit as e:
            calls["exit_code"] = e.code
    return calls


class AbortOnFailedWriteTest(unittest.TestCase):
    def test_failed_records_batch_mid_run_aborts(self):
        calls = run_main([("record", RECORD)] * 3, records_ok=False, env={"BATCH_SIZE": "1"})
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

    def test_successful_import_builds_indexes(self):
        calls = run_main([("record", RECORD), ("sleep", SLEEP)])
        self.assertEqual(calls["exit_code"], 0)
        calls["build_secondary_indexes"].assert_called_once()

    def test_missing_export_xml_aborts_before_truncate(self):
        calls = run_main([], xml_file=None)
        self.assertEqual(calls["exit_code"], 1)
        calls["truncate_tables"].assert_not_called()


class RouteWriteFailureTest(unittest.TestCase):
    def test_failed_route_write_stops_route_processing(self):
        parsed = [("Route A", [("route-a",)])]
        write = mock.MagicMock(return_value=False)
        with mock.patch.multiple(
            routes,
            parse_workout_route=mock.MagicMock(return_value=parsed),
            write_batch=write,
        ), mock.patch.object(routes.os, "listdir", return_value=["a.gpx", "b.gpx"]):
            with self.assertRaises(Exception):
                routes.process_workout_routes(mock.MagicMock(), TESTS_DIR, 1)
        write.assert_called_once()

    def test_unparsable_route_file_is_skipped(self):
        parse = mock.MagicMock(side_effect=ValueError("bad gpx"))
        with mock.patch.multiple(
            routes,
            parse_workout_route=parse,
            write_batch=mock.MagicMock(return_value=True),
        ), mock.patch.object(routes.os, "listdir", return_value=["a.gpx", "b.gpx"]):
            routes.process_workout_routes(mock.MagicMock(), TESTS_DIR, 1)
        self.assertEqual(parse.call_count, 2)


class WriteBatchFailureTest(unittest.TestCase):
    def test_dropped_connection_fails_without_retry(self):
        conn = mock.MagicMock()
        copy = conn.cursor.return_value.__enter__.return_value.copy
        copy.side_effect = psycopg.OperationalError("connection lost")
        self.assertFalse(db.write_batch(conn, "records", [RECORD]))
        copy.assert_called_once()
        conn.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

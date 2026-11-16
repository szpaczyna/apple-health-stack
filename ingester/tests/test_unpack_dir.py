import os
import tempfile
import unittest

from test_abort_on_failed_write import RECORD, run_main


def run_and_record_unpack_path(**kwargs):
    seen = []

    def on_extract(_zip, path):
        seen.append((path, os.path.isdir(path)))
        return 0

    calls = run_main([("record", RECORD)], on_extract=on_extract, **kwargs)
    return calls, seen


class TempUnpackDirTest(unittest.TestCase):
    def assertTempDirUsedAndRemoved(self, calls, seen, exit_code):
        self.assertEqual(calls["exit_code"], exit_code)
        self.assertEqual(len(seen), 1)
        path, existed = seen[0]
        self.assertTrue(existed)
        self.assertTrue(os.path.basename(path).startswith("apple-health-ingester-"))
        self.assertFalse(os.path.exists(path))

    def test_successful_run_removes_the_temp_dir(self):
        calls, seen = run_and_record_unpack_path()
        self.assertTempDirUsedAndRemoved(calls, seen, 0)
        calls["process_workout_routes"].assert_called_once()

    def test_aborted_run_removes_the_temp_dir(self):
        calls, seen = run_and_record_unpack_path(records_ok=False)
        self.assertTempDirUsedAndRemoved(calls, seen, 1)

    def test_configured_unpack_dir_is_kept(self):
        with tempfile.TemporaryDirectory() as unpack:
            calls, seen = run_and_record_unpack_path(env={"UNPACK_DIR": unpack})
            self.assertEqual(calls["exit_code"], 0)
            self.assertEqual(seen, [(unpack, True)])
            self.assertTrue(os.path.isdir(unpack))


if __name__ == "__main__":
    unittest.main()

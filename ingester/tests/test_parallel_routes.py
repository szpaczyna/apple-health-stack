import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Route {n}</name>
    <trkseg>
      <trkpt lat="52.2297" lon="21.0122"><ele>100</ele><time>2026-01-01T10:00:00Z</time></trkpt>
      <trkpt lat="52.2300" lon="21.0130"><ele>101</ele><time>2026-01-01T10:00:10Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
"""


class ParallelRoutesTest(unittest.TestCase):
    """Runs the process-pool path, with GPX parsing in real worker processes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for n in range(6):
            with open(os.path.join(self.tmp.name, f"route_{n}.gpx"), "w") as f:
                f.write(GPX.format(n=n))
        with open(os.path.join(self.tmp.name, "broken.gpx"), "w") as f:
            f.write("<gpx><trk>")

    def run_routes(self, write_ok: bool) -> mock.MagicMock:
        write = mock.MagicMock(return_value=write_ok)
        with mock.patch.multiple(
            app,
            ROUTES_PATH=self.tmp.name,
            get_gpx_workers=mock.MagicMock(return_value=2),
            write_routes_batch=write,
        ):
            app.process_workout_routes(mock.MagicMock())
        return write

    def test_all_parsed_routes_are_written_and_a_broken_file_is_skipped(self):
        write = self.run_routes(write_ok=True)
        names = sorted(call.args[1][0][0] for call in write.call_args_list)
        self.assertEqual(names, [f"route-{n}" for n in range(6)])

    def test_failed_write_aborts_the_pool(self):
        with self.assertRaises(app.ImportAbortedError):
            self.run_routes(write_ok=False)


if __name__ == "__main__":
    unittest.main()

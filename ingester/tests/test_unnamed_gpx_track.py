import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app

GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk>{name}
    <trkseg>
      <trkpt lat="52.2297" lon="21.0122"><ele>100</ele><time>2026-01-01T10:00:00Z</time></trkpt>
      <trkpt lat="52.2300" lon="21.0130"><ele>101</ele><time>2026-01-01T10:00:10Z</time></trkpt>
    </trkseg>
  </trk>
</gpx>
"""


def parse(name_element: str, file_name: str) -> list:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, file_name)
        with open(path, "w") as f:
            f.write(GPX.format(name=name_element))
        return app.parse_workout_route(path)


class UnnamedGpxTrackTest(unittest.TestCase):
    def test_unnamed_track_uses_the_file_name(self):
        tracks = parse("", "route_2026-01-01_10.00am.gpx")
        self.assertEqual(len(tracks), 1)
        track_name, points = tracks[0]
        self.assertEqual(track_name, "route_2026-01-01_10.00am")
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0][0], "route_2026-01-01_10.00am")

    def test_named_track_keeps_its_slug(self):
        tracks = parse("<name>Route 2026-01-01 10:00am</name>", "route.gpx")
        track_name, points = tracks[0]
        self.assertEqual(track_name, "Route 2026-01-01 10:00am")
        self.assertEqual(points[0][0], "route-2026-01-01-10-00am")


if __name__ == "__main__":
    unittest.main()

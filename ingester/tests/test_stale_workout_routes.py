import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


class ExtractWorkoutRoutesTest(unittest.TestCase):
    def test_routes_from_an_earlier_export_are_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            unpack = os.path.join(tmp, "export")
            routes = os.path.join(unpack, "apple_health_export", "workout-routes")
            os.makedirs(routes)
            with open(os.path.join(routes, "route_2020-01-01_old.gpx"), "w") as f:
                f.write("<gpx/>")
            # Files next to the routes directory belong to the user and stay.
            export_xml = os.path.join(unpack, "apple_health_export", "export.xml")
            with open(export_xml, "w") as f:
                f.write("<HealthData/>")

            zip_path = os.path.join(tmp, "export.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("apple_health_export/workout-routes/route_2026-01-01_new.gpx", "<gpx/>")
                zf.writestr("apple_health_export/export.xml", "<HealthData/>")

            with mock.patch.multiple(app, UNPACK_PATH=unpack, ROUTES_PATH=routes):
                with zipfile.ZipFile(zip_path) as zf:
                    count = app.extract_workout_routes(zf)

            self.assertEqual(count, 1)
            self.assertEqual(os.listdir(routes), ["route_2026-01-01_new.gpx"])
            self.assertTrue(os.path.exists(export_xml))

    def test_missing_routes_directory_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            unpack = os.path.join(tmp, "export")
            routes = os.path.join(unpack, "apple_health_export", "workout-routes")
            zip_path = os.path.join(tmp, "export.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("apple_health_export/export.xml", "<HealthData/>")

            with mock.patch.multiple(app, UNPACK_PATH=unpack, ROUTES_PATH=routes):
                with zipfile.ZipFile(zip_path) as zf:
                    self.assertEqual(app.extract_workout_routes(zf), 0)
            self.assertFalse(os.path.exists(routes))


if __name__ == "__main__":
    unittest.main()

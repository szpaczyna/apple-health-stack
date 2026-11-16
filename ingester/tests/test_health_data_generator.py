import os
import tempfile
import unittest
import zipfile

from apple_health_ingester import export_xml

EXPORT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE HealthData [
<!ELEMENT HealthData ANY>
]>
<HealthData locale="pl_PL">
 <Record type="HKCategoryTypeIdentifierHeadphoneAudioExposureEvent" sourceName="iPhone"
   startDate="2026-01-01 20:00:00 +0100" endDate="2026-01-08 20:00:00 +0100"
   value="HKCategoryValueHeadphoneAudioExposureEventSevenDayLimit">
  <MetadataEntry key="HKMetadataKeyAudioExposureLevel" value="90.5 dBASPL"/>
  <MetadataEntry key="HKMetadataKeyAudioExposureDuration" value="7200 s"/>
 </Record>
 <Record type="HKQuantityTypeIdentifierHeartRate" sourceName="Watch" unit="count/min"
   startDate="2026-01-01 20:01:00 +0100" endDate="2026-01-01 20:01:00 +0100" value="61">
  <MetadataEntry key="HKMetadataKeyHeartRateMotionContext" value="0"/>
 </Record>
</HealthData>
"""


class HealthDataGeneratorTest(unittest.TestCase):
    def test_metadata_reaches_the_headphone_formatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = os.path.join(tmp, "export.zip")
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("apple_health_export/export.xml", EXPORT_XML)
            rows = [row for _, row in export_xml.health_data_generator(zip_path, "apple_health_export/export.xml")]

        self.assertEqual(
            [(r[0], r[2], r[3]) for r in rows],
            [
                ("HeadphoneAudioExposureEvent", 90.5, "dBASPL"),
                ("HeadphoneAudioExposureEventDuration", 7200.0, "s"),
                ("HeartRate", 61.0, "count/min"),
            ],
        )


if __name__ == "__main__":
    unittest.main()

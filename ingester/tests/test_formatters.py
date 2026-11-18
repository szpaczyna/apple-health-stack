import unittest
from datetime import datetime, timedelta, timezone

from apple_health_ingester.formatters import (
    format_record,
    format_workout,
    measurement_name,
    parse_date,
    parse_float_with_try,
    sleep_analysis_formatter,
)

CET = timezone(timedelta(hours=1))


class ParseTest(unittest.TestCase):
    def test_parse_float(self):
        self.assertEqual(parse_float_with_try("72.5"), 72.5)
        self.assertEqual(parse_float_with_try(3), 3.0)

    def test_non_numeric_value_is_null(self):
        self.assertIsNone(parse_float_with_try("HKCategoryValueNotApplicable"))
        self.assertIsNone(parse_float_with_try(None))

    def test_parse_date_keeps_the_offset(self):
        self.assertEqual(
            parse_date("2026-01-01 10:00:00 +0100"),
            datetime(2026, 1, 1, 10, 0, tzinfo=CET),
        )

    def test_parse_date_without_offset_is_utc(self):
        self.assertEqual(parse_date("2026-01-01T10:00:00").tzinfo, timezone.utc)


class MeasurementNameTest(unittest.TestCase):
    def test_prefixes_are_stripped(self):
        self.assertEqual(measurement_name("HKQuantityTypeIdentifierStepCount"), "StepCount")
        self.assertEqual(measurement_name("HKCategoryTypeIdentifierSleepAnalysis"), "SleepAnalysis")
        self.assertEqual(measurement_name("HKDataTypeSleepDurationGoal"), "SleepDurationGoal")

    def test_unprefixed_type_is_unchanged(self):
        self.assertEqual(measurement_name("Record"), "Record")


class FormatRecordTest(unittest.TestCase):
    def test_quantity_record(self):
        rows = format_record({
            "type": "HKQuantityTypeIdentifierBodyMass",
            "startDate": "2026-01-01 08:00:00 +0100",
            "value": "66.2",
            "unit": "kg",
            "sourceName": "Scale",
        })
        self.assertEqual(rows, [("BodyMass", datetime(2026, 1, 1, 8, 0, tzinfo=CET), 66.2, "kg", "Scale")])

    def test_category_record_without_a_number_stores_null(self):
        rows = format_record({
            "type": "HKCategoryTypeIdentifierAppleWalkingSteadinessEvent",
            "startDate": "2026-01-01 08:00:00 +0100",
            "value": "HKCategoryValueAppleWalkingSteadinessEventInitialLow",
            "sourceName": "iPhone",
        })
        self.assertEqual(rows[0][0], "AppleWalkingSteadinessEvent")
        self.assertIsNone(rows[0][2])

    def test_mindful_session_is_its_length_in_minutes(self):
        rows = format_record({
            "type": "HKCategoryTypeIdentifierMindfulSession",
            "startDate": "2026-01-01 21:00:00 +0100",
            "endDate": "2026-01-01 21:15:30 +0100",
            "value": "HKCategoryValueNotApplicable",
            "sourceName": "Meditation App",
        })
        start = datetime(2026, 1, 1, 21, 0, tzinfo=CET)
        self.assertEqual(rows, [("MindfulSession", start, 15.5, "min", "Meditation App")])

    def test_headphone_exposure_gives_level_and_duration_rows(self):
        rows = format_record({
            "type": "HKCategoryTypeIdentifierHeadphoneAudioExposureEvent",
            "startDate": "2026-01-01 20:00:00 +0100",
            "endDate": "2026-01-08 20:00:00 +0100",
            "value": "HKCategoryValueHeadphoneAudioExposureEventSevenDayLimit",
            "sourceName": "iPhone",
            "HKMetadataKeyAudioExposureLevel": "90.5 dBASPL",
            "HKMetadataKeyAudioExposureDuration": "7200 s",
        })
        start = datetime(2026, 1, 1, 20, 0, tzinfo=CET)
        self.assertEqual(rows, [
            ("HeadphoneAudioExposureEvent", start, 90.5, "dBASPL", "iPhone"),
            ("HeadphoneAudioExposureEventDuration", start, 7200.0, "s", "iPhone"),
        ])

    def test_headphone_exposure_without_metadata_stores_null(self):
        rows = format_record({
            "type": "HKCategoryTypeIdentifierHeadphoneAudioExposureEvent",
            "startDate": "2026-01-01 20:00:00 +0100",
            "value": "HKCategoryValueHeadphoneAudioExposureEventSevenDayLimit",
        })
        self.assertEqual([r[2] for r in rows], [None, None])

    def test_apple_stand_hour(self):
        base = {
            "type": "HKCategoryTypeIdentifierAppleStandHour",
            "startDate": "2026-01-01 08:00:00 +0100",
            "sourceName": "Watch",
        }
        stood = format_record({**base, "value": "HKCategoryValueAppleStandHourStood"})
        idle = format_record({**base, "value": "HKCategoryValueAppleStandHourIdle"})
        self.assertEqual((stood[0][2], idle[0][2]), (1.0, 0.0))


class SleepAnalysisTest(unittest.TestCase):
    def test_sleep_stage(self):
        start = "2026-01-01 23:00:00 +0100"
        end = "2026-01-01 23:30:00 +0100"
        rows = sleep_analysis_formatter({
            "startDate": start,
            "endDate": end,
            "sourceName": "Watch",
            "value": "HKCategoryValueSleepAnalysisAsleepREM",
        })
        self.assertEqual(rows, [(parse_date(start), "Watch", "REM", 2, parse_date(start), parse_date(end))])

    def sleep_state(self, value):
        rows = sleep_analysis_formatter({
            "startDate": "2026-01-01 23:00:00 +0100",
            "endDate": "2026-01-01 23:30:00 +0100",
            "value": value,
        })
        return [(row[2], row[3]) for row in rows]

    def test_in_bed_is_not_asleep(self):
        self.assertEqual(self.sleep_state("HKCategoryValueSleepAnalysisInBed"), [("InBed", 3)])

    def test_unspecified_sleep_is_kept_as_asleep(self):
        self.assertEqual(self.sleep_state("HKCategoryValueSleepAnalysisAsleepUnspecified"), [("Asleep", 5)])
        self.assertEqual(self.sleep_state("HKCategoryValueSleepAnalysisAsleep"), [("Asleep", 5)])

    def test_unknown_sleep_value_is_skipped(self):
        self.assertEqual(self.sleep_state("HKCategoryValueSleepAnalysisSomethingNew"), [])


class FormatWorkoutTest(unittest.TestCase):
    def test_workout(self):
        row = format_workout({
            "workoutActivityType": "HKWorkoutActivityTypeRunning",
            "startDate": "2026-01-01 18:00:00 +0100",
            "duration": "42.5",
            "durationUnit": "min",
            "sourceName": "Watch",
        })
        self.assertEqual(row, ("Running", datetime(2026, 1, 1, 18, 0, tzinfo=CET), 42.5, "min", "Watch"))


if __name__ == "__main__":
    unittest.main()

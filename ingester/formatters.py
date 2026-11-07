"""
Formatters for converting Apple Health XML records into tuples
suitable for PostgreSQL insertion.

Records tuple format: (measurement, time, value, unit, device)
Sleep tuple format: (time, device, state, state_value, start_time, end_time)
"""

from datetime import datetime as dt
from datetime import timezone
from typing import Any


def parse_float_with_try(v: Any) -> float | None:
    """Convert v to float, or None (stored as NULL) when it isn't a number."""
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def parse_date(v: Any) -> dt:
    """Parse an ISO date string into a timezone-aware datetime."""
    parsed = dt.fromisoformat(v)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_record(record: dict[str, Any]) -> list[tuple]:
    """
    Format an Apple Health XML Record element into a list of PostgreSQL row tuples.
    Returns list of: (measurement, time, value, unit, device)
    """
    measurement = (
        record.get("type", "Record")
        .removeprefix("HKQuantityTypeIdentifier")
        .removeprefix("HKCategoryTypeIdentifier")
        .removeprefix("HKDataType")
    )

    if measurement == "AppleStandHour":
        return apple_stand_hour_formatter(record)
    if measurement == "MindfulSession":
        return mindful_session_formatter(record)
    if measurement == "HeadphoneAudioExposureEvent":
        return headphone_audio_exposure_formatter(record)
    if measurement == "SleepAnalysis":
        # Handled separately - returns None to signal caller
        return None

    date = parse_date(record.get("startDate", "2024-01-01T01:01:01"))
    value = parse_float_with_try(record.get("value", 1))
    unit = record.get("unit", "unit")
    device = record.get("sourceName", "unknown")

    return [(measurement, date, value, unit, device)]


def apple_stand_hour_formatter(record: dict) -> list[tuple]:
    """Format AppleStandHour record."""
    date = parse_date(record.get("startDate", "2024-01-01T01:01:01"))
    unit = record.get("unit", "unit")
    device = record.get("sourceName", "unknown")
    value = 1.0 if record.get("value") == "HKCategoryValueAppleStandHourStood" else 0.0

    return [("AppleStandHour", date, value, unit, device)]


def mindful_session_formatter(record: dict) -> list[tuple]:
    """Format MindfulSession as its length in minutes; its value is always NotApplicable."""
    start = parse_date(record.get("startDate"))
    minutes = (parse_date(record.get("endDate")) - start).total_seconds() / 60
    device = record.get("sourceName", "unknown")
    return [("MindfulSession", start, minutes, "min", device)]


def headphone_audio_exposure_formatter(record: dict) -> list[tuple]:
    """
    Format HeadphoneAudioExposureEvent from its metadata (e.g. "92.5 dBASPL" and
    "8090.17 s"); the value only names the exceeded limit.
    """
    start = parse_date(record.get("startDate"))
    device = record.get("sourceName", "unknown")
    level = _metadata_number(record.get("HKMetadataKeyAudioExposureLevel"))
    duration = _metadata_number(record.get("HKMetadataKeyAudioExposureDuration"))
    return [
        ("HeadphoneAudioExposureEvent", start, level, "dBASPL", device),
        ("HeadphoneAudioExposureEventDuration", start, duration, "s", device),
    ]


def _metadata_number(v: str | None) -> float | None:
    """Number from a metadata value with a unit suffix, or None."""
    return parse_float_with_try(v.split()[0]) if v else None


# Sleep state mappings
SLEEP_STATES_LOOKUP = {
    "HKCategoryValueSleepAnalysisAsleepDeep": 0,
    "HKCategoryValueSleepAnalysisAsleepCore": 1,
    "HKCategoryValueSleepAnalysisAsleepREM": 2,
    "HKCategoryValueSleepAnalysisInBed": 3,
    "HKCategoryValueSleepAnalysisAwake": 4,
}

SLEEP_STATES_SHORT = {
    "HKCategoryValueSleepAnalysisAsleepDeep": "Deep",
    "HKCategoryValueSleepAnalysisAsleepCore": "Core",
    "HKCategoryValueSleepAnalysisAsleepREM": "REM",
    "HKCategoryValueSleepAnalysisInBed": "Asleep",
    "HKCategoryValueSleepAnalysisAwake": "Awake",
}


def sleep_analysis_formatter(record: dict) -> list[tuple]:
    """
    Format SleepAnalysis record into sleep_analysis table tuples.
    Returns list of: (time, device, state, state_value, start_time, end_time)
    """
    start_date = parse_date(record.get("startDate"))
    end_date = parse_date(record.get("endDate"))
    device = record.get("sourceName", "unknown")
    raw_value = record.get("value", "")
    state_value = SLEEP_STATES_LOOKUP.get(raw_value, 5)
    state = SLEEP_STATES_SHORT.get(raw_value, "Unspecified")

    # Skip unspecified sleep states
    if state == "Unspecified":
        return []

    return [(start_date, device, state, state_value, start_date, end_date)]


def format_workout(record: dict[str, Any]) -> tuple:
    """
    Format an Apple Health XML Workout element into a PostgreSQL row tuple.
    Returns: (measurement, time, value, unit, device)
    """
    measurement = record.get("workoutActivityType", "Workout").removeprefix(
        "HKWorkoutActivityType"
    )
    date = parse_date(record.get("startDate", "2024-01-01T01:01:01"))
    value = parse_float_with_try(record.get("duration", 0))
    unit = record.get("durationUnit", "unit")
    device = record.get("sourceName", "unknown")

    return (measurement, date, value, unit, device)

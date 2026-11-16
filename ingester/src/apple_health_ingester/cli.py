"""Command-line entry point that imports an Apple Health export zip into PostgreSQL."""
import sys
import time
import zipfile

from . import export_xml

from .db import (
    ReconnectingConnection,
    build_secondary_indexes,
    drop_secondary_indexes,
    init_schema,
    truncate_tables,
    wait_for_db,
)

from .errors import ImportAbortedError

from .export_xml import process_health_data, push_sources

from .log import logger

from .routes import extract_workout_routes, process_workout_routes


# --- Main ---

def main():
    """Main execution function."""
    import_start = time.monotonic()
    # Open the zip; export.xml is streamed from it later, only GPX routes go to disk
    logger.info("Opening the export file...")
    try:
        # zipfile locates the central directory from the end of the file,
        # so data prepended before the first local header is tolerated.
        with zipfile.ZipFile(export_xml.ZIP_PATH, 'r') as zip_ref:
            route_count = extract_workout_routes(zip_ref)
        logger.success(f"Export file opened, extracted {route_count} workout routes!")
    except FileNotFoundError:
        logger.error(f"Export zip file not found at {export_xml.ZIP_PATH}")
        sys.exit(1)
    except Exception as unzip_err:
        logger.error(f"Unable to open export zip: {unzip_err}")
        sys.exit(1)

    # Connect to PostgreSQL
    wait_for_db()
    conn = ReconnectingConnection()

    # Initialize schema and truncate for fresh import
    init_schema(conn)
    truncate_tables(conn)
    drop_secondary_indexes(conn)

    # Process data
    try:
        start = time.monotonic()
        found_sources = process_health_data(conn)
        push_sources(conn, found_sources)
        data_seconds = time.monotonic() - start

        start = time.monotonic()
        process_workout_routes(conn)
        routes_seconds = time.monotonic() - start
    except ImportAbortedError as e:
        logger.error(f"Aborting the import: failed to write {e}.")
        conn.close()
        sys.exit(1)

    index_seconds, analyze_seconds = build_secondary_indexes(conn)
    conn.close()

    print_summary({
        "Data": data_seconds,
        "Routes": routes_seconds,
        "Indexes": index_seconds,
        "Analyze": analyze_seconds,
        "Total": time.monotonic() - import_start,
    })
    logger.success("\nAll done! You can now check Grafana.")


def print_summary(durations: dict[str, float]) -> None:
    """Log how long each import step took."""
    logger.info("\nImport summary:")
    for step, seconds in durations.items():
        logger.info(f"  {step:<8} {seconds:>8.1f}s")


if __name__ == "__main__":
    main()

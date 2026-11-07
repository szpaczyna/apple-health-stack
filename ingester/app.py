"""
Ingester module that converts Apple Health export zip file
into PostgreSQL records.
"""
import os
import posixpath
import re
import shutil
import sys
import time
from lxml import etree
from typing import Generator
import zipfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

import psycopg
from psycopg import sql

import gpxpy
from gpxpy.gpx import GPXTrackPoint

from formatters import (
    format_record,
    format_workout,
    sleep_analysis_formatter,
    parse_date,
)

# --- Configuration ---
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", 5432))
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASS = os.getenv("PG_PASS") or None  # None = use ~/.pgpass
PG_DB = os.getenv("PG_DB", "health")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", 15000))
DB_RETRY_COOLDOWN = int(os.getenv("DB_RETRY_COOLDOWN", 10))
DB_MAX_RETRIES = int(os.getenv("DB_MAX_RETRIES", 5))

ZIP_PATH = os.getenv("ZIP_PATH", "./export.zip")
UNPACK_PATH = "./export"
EXPORT_PATH = os.path.join(UNPACK_PATH, "apple_health_export")
ROUTES_PATH = os.path.join(EXPORT_PATH, "workout-routes")
# Paths of the same directories inside the zip archive (zip members always use "/")
ZIP_EXPORT_DIR = "apple_health_export"
ZIP_ROUTES_DIR = posixpath.join(ZIP_EXPORT_DIR, "workout-routes")
EXPORT_XML_REGEX = re.compile(r"(export|导出)\.xml", re.IGNORECASE)
# Record types whose numbers live in <MetadataEntry> children rather than in value.
METADATA_RECORD_TYPES = {"HeadphoneAudioExposureEvent"}


# --- Logger ---
class _Colors:
    """ANSI color codes"""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'


class Logger:
    """Simple logger for colorized console output."""
    def info(self, message):
        print(f"{_Colors.BLUE}{message}{_Colors.RESET}")

    def success(self, message):
        print(f"{_Colors.GREEN}{message}{_Colors.RESET}")

    def warn(self, message):
        print(f"{_Colors.YELLOW}{message}{_Colors.RESET}")

    def error(self, message):
        print(f"{_Colors.RED}{message}{_Colors.RESET}")


logger = Logger()


class ImportAbortedError(Exception):
    """A batch could not be written, so the import must stop."""


# --- Database helpers ---

def get_connection():
    """Create a new database connection. Uses ~/.pgpass if PG_PASS is not set."""
    kwargs = {
        "host": PG_HOST,
        "port": PG_PORT,
        "user": PG_USER,
        "dbname": PG_DB,
    }
    if PG_PASS is not None:
        kwargs["password"] = PG_PASS
    return psycopg.connect(**kwargs)


class ReconnectingConnection:
    """
    Wrapper around a psycopg connection that can replace a dropped connection
    in place, so every caller holding this object keeps working after a
    reconnect. Everything else (cursor, commit, rollback, close, closed, ...)
    is delegated to the current psycopg connection.
    """

    def __init__(self):
        self._conn = get_connection()

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def ensure_open(self) -> None:
        """Reconnect if the current connection is closed or broken."""
        if self._conn.closed or self._conn.broken:
            logger.warn("Reconnecting to PostgreSQL...")
            self._conn = get_connection()
            logger.success("Reconnected to PostgreSQL.")

    def discard_transaction(self) -> None:
        """
        Roll back the failed transaction if the connection is still usable,
        otherwise close it so that ensure_open() reconnects. Never raises.
        """
        try:
            if self._conn.closed or self._conn.broken:
                self._conn.close()
            else:
                self._conn.rollback()
        except psycopg.Error as e:
            logger.warn(f"Rollback failed, dropping the connection: {e}")
            self._conn.close()


def wait_for_db():
    """Wait until PostgreSQL is reachable."""
    while True:
        try:
            conn = get_connection()
            conn.close()
            logger.success("PostgreSQL is ready.")
            return
        except psycopg.OperationalError:
            logger.warn("Waiting for PostgreSQL to be ready...")
            time.sleep(2)


SCHEMA_FILE = os.path.join(os.path.dirname(__file__), "sql", "001_schema.sql")
# Secondary indexes are the plain (non-unique) CREATE INDEX statements in the
# schema file. Primary keys / unique indexes are never dropped.
SECONDARY_INDEX_REGEX = re.compile(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE)
ANALYZE_TABLES = ("records", "sleep_analysis", "workout_routes", "data_sources")


def read_schema() -> str:
    with open(SCHEMA_FILE, "r") as f:
        return f.read()


def init_schema(conn):
    """Create schema and tables if they don't exist."""
    with conn.cursor() as cur:
        cur.execute(read_schema())
    conn.commit()
    logger.success("Schema initialized.")


def drop_secondary_indexes(conn):
    """Drop the secondary indexes from the schema file before the bulk load."""
    names = SECONDARY_INDEX_REGEX.findall(read_schema())
    with conn.cursor() as cur:
        for name in names:
            cur.execute(f'DROP INDEX IF EXISTS "{name}";')
    conn.commit()
    logger.warn(f"Dropped {len(names)} secondary indexes for the bulk load.")


def build_secondary_indexes(conn) -> tuple[float, float]:
    """Recreate the secondary indexes and refresh stats; return (index, analyze) seconds."""
    logger.info("Building indexes...")
    start = time.monotonic()
    with conn.cursor() as cur:
        cur.execute(read_schema())
    conn.commit()
    index_seconds = time.monotonic() - start
    logger.success(f"Indexes built in {index_seconds:.1f}s.")

    start = time.monotonic()
    with conn.cursor() as cur:
        for table in ANALYZE_TABLES:
            cur.execute(f"ANALYZE {table};")
    conn.commit()
    analyze_seconds = time.monotonic() - start
    logger.success(f"Tables analyzed in {analyze_seconds:.1f}s.")
    return index_seconds, analyze_seconds


def truncate_tables(conn):
    """Truncate all health tables for a fresh import."""
    with conn.cursor() as cur:
        cur.execute("TRUNCATE records RESTART IDENTITY;")
        cur.execute("TRUNCATE sleep_analysis RESTART IDENTITY;")
        cur.execute("TRUNCATE workout_routes RESTART IDENTITY;")
        cur.execute("TRUNCATE data_sources;")
    conn.commit()
    logger.warn("All health tables truncated.")


RECORDS_COLUMNS = ("measurement", "time", "value", "unit", "device")
SLEEP_COLUMNS = ("time", "device", "state", "state_value", "start_time", "end_time")
ROUTES_COLUMNS = ("workout", "time", "latitude", "longitude", "elevation", "speed", "distance")
SOURCES_COLUMNS = ("device",)


def write_batch(conn, table: str, columns: tuple[str, ...], rows: list[tuple], label: str) -> bool:
    """Batch-insert rows into table using COPY, retrying on connection errors."""
    if not rows:
        return True
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, columns)),
    )
    for attempt in range(DB_MAX_RETRIES):
        try:
            conn.ensure_open()
            with conn.cursor() as cur:
                with cur.copy(copy_sql) as copy:
                    for row in rows:
                        copy.write_row(row)
            conn.commit()
            return True
        except (psycopg.OperationalError, psycopg.InterfaceError) as e:
            logger.warn(f"DB write failed for {label} (attempt {attempt + 1}/{DB_MAX_RETRIES}): {e}")
            conn.discard_transaction()
            if attempt + 1 < DB_MAX_RETRIES:
                time.sleep(DB_RETRY_COOLDOWN)
            else:
                logger.error(f"Max retries reached for {label} batch.")
                return False
        except Exception as e:
            logger.error(f"Unexpected error writing {label}: {e}")
            conn.discard_transaction()
            return False
    return False


def write_records_batch(conn, records: list[tuple]) -> bool:
    return write_batch(conn, "records", RECORDS_COLUMNS, records, "records")


def write_sleep_batch(conn, sleep_records: list[tuple]) -> bool:
    return write_batch(conn, "sleep_analysis", SLEEP_COLUMNS, sleep_records, "sleep")


def write_routes_batch(conn, route_points: list[tuple]) -> bool:
    return write_batch(conn, "workout_routes", ROUTES_COLUMNS, route_points, "routes")


# --- Route processing ---

def format_route_point(name: str, point: GPXTrackPoint, next_point=None) -> tuple:
    """Create a route point tuple for PostgreSQL."""
    slug_name = name.replace(" ", "-").replace(":", "-").lower()
    speed = point.speed_between(next_point) if next_point else None
    distance = point.distance_3d(next_point) if next_point else None
    return (slug_name, point.time, point.latitude, point.longitude, point.elevation, speed, distance)


def parse_workout_route(route_xml_file: str) -> list[tuple[str, list[tuple]]]:
    """Parse a GPX file into [(track_name, route_point_tuples), ...].

    Pure CPU work with no DB access, so it can run in a worker process.
    """
    with open(route_xml_file, "r") as gpx_file:
        gpx = gpxpy.parse(gpx_file)
    tracks = []
    for track in gpx.tracks:
        # A track without <name> is labelled after its file, not merged with others.
        track_name = track.name or os.path.splitext(os.path.basename(route_xml_file))[0]
        track_points = []
        for segment in track.segments:
            num_points = len(segment.points)
            for i in range(num_points):
                track_points.append(
                    format_route_point(
                        track_name,
                        segment.points[i],
                        segment.points[i + 1] if i + 1 < num_points else None,
                    )
                )
        tracks.append((track_name, track_points))
    return tracks


def _parse_workout_route_worker(route_xml_file: str) -> list[tuple[str, list[tuple]]]:
    """parse_workout_route for worker processes.

    Some gpxpy exceptions (e.g. GPXXMLSyntaxException) can't be unpickled in
    the parent, which would break the whole pool, so re-raise as RuntimeError.
    """
    try:
        return parse_workout_route(route_xml_file)
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e}") from None


def write_workout_route(conn, tracks: list[tuple[str, list[tuple]]]) -> None:
    """Insert the parsed tracks of one GPX file."""
    for track_name, track_points in tracks:
        logger.info(f"  Route: {track_name}")
        if not write_routes_batch(conn, track_points):
            raise ImportAbortedError(f"route {track_name}")


def get_gpx_workers() -> int:
    """Number of GPX parser processes (GPX_WORKERS, default: usable CPUs)."""
    value = os.getenv("GPX_WORKERS")
    if value:
        try:
            workers = int(value)
            if workers >= 1:
                return workers
        except ValueError:
            pass
        logger.warn(f"Invalid GPX_WORKERS={value!r}, using the CPU count")
    return os.process_cpu_count() or 1


def process_workout_routes(conn) -> None:
    """Process all GPX workout route files.

    Files are parsed in worker processes; the main process writes the results
    as they complete, so file insert order is not deterministic. At most
    2 * workers parsed files are held in memory at a time.
    """
    if not (os.path.exists(ROUTES_PATH) and os.path.isdir(ROUTES_PATH)):
        logger.warn("No workout routes found, skipping...")
        return

    route_files = [
        os.path.join(ROUTES_PATH, file)
        for file in os.listdir(ROUTES_PATH)
        if file.endswith(".gpx")
    ]
    workers = min(get_gpx_workers(), len(route_files)) or 1
    logger.info(f"Loading workout routes ({len(route_files)} files, {workers} workers)...")

    if workers == 1:
        for route_file in route_files:
            try:
                write_workout_route(conn, parse_workout_route(route_file))
            except ImportAbortedError:
                raise
            except Exception as e:
                logger.error(f"Failed to process route {route_file}: {e}")
        return

    with ProcessPoolExecutor(max_workers=workers) as executor:
        pending_files = iter(route_files)
        in_flight = {}

        def submit_next() -> None:
            route_file = next(pending_files, None)
            if route_file is not None:
                try:
                    future = executor.submit(_parse_workout_route_worker, route_file)
                except BrokenProcessPool as e:
                    logger.error(f"Failed to process route {route_file}: {e}")
                    return
                in_flight[future] = route_file

        for _ in range(2 * workers):
            submit_next()

        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                route_file = in_flight.pop(future)
                submit_next()
                try:
                    write_workout_route(conn, future.result())
                except ImportAbortedError:
                    raise
                except Exception as e:
                    logger.error(f"Failed to process route {route_file}: {e}")


# --- XML processing ---

def find_xml_file() -> str | None:
    """Find the health export XML member inside the export zip."""
    with zipfile.ZipFile(ZIP_PATH) as zip_ref:
        for name in zip_ref.namelist():
            dirname, basename = posixpath.split(name)
            if dirname == ZIP_EXPORT_DIR and EXPORT_XML_REGEX.match(basename):
                return name
    return None


class _SkipToHealthData:
    """
    Read-only stream wrapper that drops every line before the one containing
    ``<HealthData`` (XML declaration and the inline DTD, which lxml can choke on
    in some exports). Works on non-seekable streams such as zip members.
    """

    def __init__(self, f):
        self._f = f
        self._pending = b''
        for line in f:
            self._pending = line
            if b'<HealthData' in line:
                break

    def read(self, size: int = -1) -> bytes:
        if not self._pending:
            return self._f.read(size)
        if size is None or size < 0:
            data, self._pending = self._pending + self._f.read(), b''
        else:
            data, self._pending = self._pending[:size], self._pending[size:]
        return data


def health_data_generator(xml_file: str) -> Generator[tuple, None, None]:
    """
    Generator that yields (type, data) tuples from health data XML.
    xml_file is the name of the XML member inside the export zip.
    type is 'record', 'sleep', or 'workout'.
    """
    with zipfile.ZipFile(ZIP_PATH) as zip_ref, zip_ref.open(xml_file) as f:
        # Skip any leading non-XML data
        stream = _SkipToHealthData(f)

        context = etree.iterparse(stream, events=('end',), tag=('Record', 'Workout'), recover=True)

        for _, elem in context:
            try:
                if elem.tag == "Record":
                    record_type = (
                        elem.get("type", "Record")
                        .removeprefix("HKQuantityTypeIdentifier")
                        .removeprefix("HKCategoryTypeIdentifier")
                        .removeprefix("HKDataType")
                    )
                    if record_type == "SleepAnalysis":
                        sleep_rows = sleep_analysis_formatter(dict(elem.attrib))
                        for row in sleep_rows:
                            yield ("sleep", row)
                    else:
                        record = dict(elem.attrib)
                        if record_type in METADATA_RECORD_TYPES:
                            for entry in elem.iterchildren("MetadataEntry"):
                                record[entry.get("key")] = entry.get("value")
                        rows = format_record(record)
                        if rows:
                            for row in rows:
                                yield ("record", row)
                elif elem.tag == "Workout":
                    row = format_workout(dict(elem.attrib))
                    yield ("record", row)
            except Exception as e:
                logger.error(f"Error processing element: {e}")
            finally:
                elem.clear()
                while elem.getprevious() is not None:
                    del elem.getparent()[0]


def process_health_data(conn) -> set[str]:
    """
    Process the main health data XML file and push data to PostgreSQL.
    Returns set of all data sources found.
    """
    export_file = find_xml_file()
    if not export_file:
        logger.warn("No export XML file found, skipping...")
        return set()

    logger.info(f"Processing health data from {os.path.basename(export_file)}...")

    records_batch = []
    sleep_batch = []
    sources = set()
    total_records = 0
    total_sleep = 0

    for data_type, row in health_data_generator(export_file):
        if data_type == "record":
            records_batch.append(row)
            # row[4] is device
            sources.add(row[4])
            if len(records_batch) >= BATCH_SIZE:
                if write_records_batch(conn, records_batch):
                    total_records += len(records_batch)
                    logger.success(f"  Inserted {total_records} records...")
                    records_batch = []
                else:
                    raise ImportAbortedError("records batch")

        elif data_type == "sleep":
            sleep_batch.append(row)
            # row[1] is device
            sources.add(row[1])
            if len(sleep_batch) >= BATCH_SIZE:
                if write_sleep_batch(conn, sleep_batch):
                    total_sleep += len(sleep_batch)
                    logger.success(f"  Inserted {total_sleep} sleep records...")
                    sleep_batch = []
                else:
                    raise ImportAbortedError("sleep batch")

    # Flush remaining
    if records_batch:
        if not write_records_batch(conn, records_batch):
            raise ImportAbortedError("records batch")
        total_records += len(records_batch)
    if sleep_batch:
        if not write_sleep_batch(conn, sleep_batch):
            raise ImportAbortedError("sleep batch")
        total_sleep += len(sleep_batch)

    logger.success(f"Total records inserted: {total_records}")
    logger.success(f"Total sleep records inserted: {total_sleep}")
    return sources


def push_sources(conn, sources: set[str]):
    """Insert discovered data sources."""
    if not sources:
        logger.warn("No data sources to push.")
        return

    source_rows = [(s,) for s in sources if s]
    logger.info(f"Pushing {len(source_rows)} data sources...")
    # data_sources is truncated before each import and the rows are distinct.
    if not write_batch(conn, "data_sources", SOURCES_COLUMNS, source_rows, "sources"):
        raise ImportAbortedError("data sources")


# --- Main ---

def extract_workout_routes(zip_ref: zipfile.ZipFile) -> int:
    """Extract only the GPX workout routes to ROUTES_PATH. Returns the file count."""
    # Routes left over from an earlier, larger export would be imported again.
    shutil.rmtree(ROUTES_PATH, ignore_errors=True)
    count = 0
    for name in zip_ref.namelist():
        dirname, basename = posixpath.split(name)
        if dirname == ZIP_ROUTES_DIR and basename.endswith(".gpx"):
            zip_ref.extract(name, UNPACK_PATH)
            count += 1
    return count


def main():
    """Main execution function."""
    import_start = time.monotonic()
    # Open the zip; export.xml is streamed from it later, only GPX routes go to disk
    logger.info("Opening the export file...")
    try:
        # zipfile locates the central directory from the end of the file,
        # so data prepended before the first local header is tolerated.
        with zipfile.ZipFile(ZIP_PATH, 'r') as zip_ref:
            route_count = extract_workout_routes(zip_ref)
        logger.success(f"Export file opened, extracted {route_count} workout routes!")
    except FileNotFoundError:
        logger.error(f"Export zip file not found at {ZIP_PATH}")
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

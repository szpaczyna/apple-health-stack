"""PostgreSQL connection, schema and batch-write helpers for the ingester."""
import os
import re
import time

import psycopg
from psycopg import sql

from log import logger

# --- Configuration ---
PG_HOST = os.getenv("PG_HOST", "localhost")
PG_PORT = int(os.getenv("PG_PORT", 5432))
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASS = os.getenv("PG_PASS") or None  # None = use ~/.pgpass
PG_DB = os.getenv("PG_DB", "health")

DB_RETRY_COOLDOWN = int(os.getenv("DB_RETRY_COOLDOWN", 10))
DB_MAX_RETRIES = int(os.getenv("DB_MAX_RETRIES", 5))


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

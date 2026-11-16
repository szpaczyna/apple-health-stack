"""PostgreSQL connection, schema and batch-write helpers for the ingester."""
import math
import os
import re
import sys
import time
from importlib.resources import files

import psycopg
from psycopg import sql

from .config import DatabaseConfig
from .log import logger
from .pgpass import resolve_passfile


def get_connection(db_cfg: DatabaseConfig, connect_timeout: int | None = None):
    """Create a new database connection. Without a password libpq reads the resolved passfile."""
    kwargs = {
        "host": db_cfg.host,
        "port": db_cfg.port,
        "user": db_cfg.user,
        "dbname": db_cfg.dbname,
    }
    if db_cfg.password is not None:
        kwargs["password"] = db_cfg.password
    else:
        kwargs["passfile"] = resolve_passfile(db_cfg, os.environ)
    if connect_timeout is not None:
        kwargs["connect_timeout"] = connect_timeout
    return psycopg.connect(**kwargs)


class ReconnectingConnection:
    """
    Wrapper around a psycopg connection that can replace a dropped connection
    in place, so every caller holding this object keeps working after a
    reconnect. Everything else (cursor, commit, rollback, close, closed, ...)
    is delegated to the current psycopg connection.
    """

    def __init__(self, db_cfg: DatabaseConfig):
        self.cfg = db_cfg
        self._conn = get_connection(db_cfg)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def ensure_open(self) -> None:
        """Reconnect if the current connection is closed or broken."""
        if self._conn.closed or self._conn.broken:
            logger.warn("Reconnecting to PostgreSQL...")
            self._conn = get_connection(self.cfg)
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


def wait_for_db(db_cfg: DatabaseConfig):
    """Wait until PostgreSQL is reachable; exit with status 1 after db_cfg.wait_timeout seconds."""
    deadline = time.monotonic() + db_cfg.wait_timeout
    while True:
        remaining = deadline - time.monotonic()
        try:
            # libpq treats connect_timeout values below 2 as 2.
            conn = get_connection(db_cfg, connect_timeout=max(2, math.ceil(remaining)))
            conn.close()
            logger.success("PostgreSQL is ready.")
            return
        except psycopg.OperationalError as e:
            last_error = str(e).splitlines()[0] if str(e) else type(e).__name__
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.error(
                f"PostgreSQL at {db_cfg.host}:{db_cfg.port} is not reachable after "
                f"{db_cfg.wait_timeout}s: {last_error}"
            )
            sys.exit(1)
        logger.warn("Waiting for PostgreSQL to be ready...")
        time.sleep(min(2, remaining))


# Secondary indexes are the plain (non-unique) CREATE INDEX statements in the
# schema file. Primary keys / unique indexes are never dropped.
SECONDARY_INDEX_REGEX = re.compile(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+(\w+)", re.IGNORECASE)
ANALYZE_TABLES = ("records", "sleep_analysis", "workout_routes", "data_sources")


def read_schema() -> str:
    return (files(__package__) / "sql" / "001_schema.sql").read_text()


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
    max_retries = conn.cfg.max_retries
    for attempt in range(max_retries):
        try:
            conn.ensure_open()
            with conn.cursor() as cur:
                with cur.copy(copy_sql) as copy:
                    for row in rows:
                        copy.write_row(row)
            conn.commit()
            return True
        except (psycopg.OperationalError, psycopg.InterfaceError) as e:
            logger.warn(f"DB write failed for {label} (attempt {attempt + 1}/{max_retries}): {e}")
            conn.discard_transaction()
            if attempt + 1 < max_retries:
                time.sleep(conn.cfg.retry_cooldown)
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

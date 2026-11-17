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
ANALYZE_TABLES = ("records", "sleep_analysis", "workout_routes")


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
    conn.commit()
    logger.warn("All health tables truncated.")


COLUMNS = {
    "records": ("measurement", "time", "value", "unit", "device"),
    "sleep_analysis": ("time", "device", "state", "state_value", "start_time", "end_time"),
    "workout_routes": ("workout", "time", "latitude", "longitude", "elevation", "speed", "distance"),
}


def write_batch(conn, table: str, rows: list[tuple]) -> bool:
    """Batch-insert rows into table using COPY in one transaction; return False on failure."""
    if not rows:
        return True
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, COLUMNS[table])),
    )
    try:
        with conn.cursor() as cur:
            with cur.copy(copy_sql) as copy:
                for row in rows:
                    copy.write_row(row)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"DB write failed for {table}: {e}")
        return False

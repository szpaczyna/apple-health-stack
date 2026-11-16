"""Command-line entry point that imports an Apple Health export zip into PostgreSQL."""
import argparse
import contextlib
import os
import sys
import tempfile
import time
import zipfile

from . import config, pgpass

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

from .routes import extract_workout_routes, process_workout_routes, resolve_gpx_workers, routes_dir


# --- Main ---

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="apple-health-ingester",
        description="Import an Apple Health export zip into PostgreSQL.",
    )
    parser.add_argument("--config", metavar="PATH", help="YAML config file")
    parser.add_argument("--zip", metavar="PATH", help="Apple Health export zip")
    parser.add_argument("--print-config", action="store_true", help="print the effective config and exit")
    return parser.parse_args(argv)


def main(argv=None):
    """Main execution function."""
    args = parse_args(argv)
    cli_args = {"config": args.config}
    if args.zip:
        cli_args["zip_path"] = args.zip
    try:
        cfg, config_path = config.load(cli_args, os.environ)
    except config.ConfigError as e:
        logger.error(str(e))
        sys.exit(2)
    if args.print_config:
        print(f"# config file: {config_path or 'none'}")
        print(config.to_yaml(cfg), end="")
        sys.exit(0)

    db_cfg = cfg.database
    if db_cfg.password is None:
        passfile = pgpass.resolve_passfile(db_cfg, os.environ)
        error = pgpass.check(passfile, db_cfg.host, db_cfg.port, db_cfg.dbname, db_cfg.user)
        if error:
            logger.error(error)
            sys.exit(1)

    unpack_dir = cfg.import_.unpack_dir
    if unpack_dir:
        unpack_location = contextlib.nullcontext(unpack_dir)
    else:
        unpack_location = tempfile.TemporaryDirectory(prefix="apple-health-ingester-")
    with unpack_location as unpack_path:
        run_import(cfg, unpack_path)


def run_import(cfg, unpack_path):
    """Extract the routes to unpack_path, then load the export into PostgreSQL."""
    zip_path = cfg.import_.zip_path
    import_start = time.monotonic()
    # Open the zip; export.xml is streamed from it later, only GPX routes go to disk
    logger.info("Opening the export file...")
    try:
        # zipfile locates the central directory from the end of the file,
        # so data prepended before the first local header is tolerated.
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            route_count = extract_workout_routes(zip_ref, unpack_path)
        logger.success(f"Export file opened, extracted {route_count} workout routes!")
    except FileNotFoundError:
        logger.error(f"Export zip file not found at {zip_path}")
        sys.exit(1)
    except Exception as unzip_err:
        logger.error(f"Unable to open export zip: {unzip_err}")
        sys.exit(1)

    # Connect to PostgreSQL
    wait_for_db(cfg.database)
    conn = ReconnectingConnection(cfg.database)

    # Initialize schema and truncate for fresh import
    init_schema(conn)
    truncate_tables(conn)
    drop_secondary_indexes(conn)

    # Process data
    try:
        start = time.monotonic()
        found_sources = process_health_data(conn, cfg.import_)
        push_sources(conn, found_sources)
        data_seconds = time.monotonic() - start

        start = time.monotonic()
        process_workout_routes(conn, routes_dir(unpack_path), resolve_gpx_workers(cfg.import_.gpx_workers))
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

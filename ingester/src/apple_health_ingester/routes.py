"""Extracts GPX workout routes from the export zip and loads them into PostgreSQL."""
import os
import posixpath
import shutil
import zipfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool

import gpxpy
from gpxpy.gpx import GPXTrackPoint

from .db import write_routes_batch

from .errors import ImportAbortedError

from .export_xml import ZIP_EXPORT_DIR

from .log import logger

# --- Configuration ---
# Path of the routes directory inside the zip archive (zip members always use "/")
ZIP_ROUTES_DIR = posixpath.join(ZIP_EXPORT_DIR, "workout-routes")


def routes_dir(unpack_path: str) -> str:
    """Directory that extract_workout_routes() fills under unpack_path."""
    return os.path.join(unpack_path, "apple_health_export", "workout-routes")


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


def resolve_gpx_workers(configured: int | None) -> int:
    """Number of GPX parser processes: the configured value, default usable CPUs."""
    return configured or os.process_cpu_count() or 1


def process_workout_routes(conn, routes_path: str, workers: int) -> None:
    """Process all GPX workout route files in routes_path.

    Files are parsed in worker processes; the main process writes the results
    as they complete, so file insert order is not deterministic. At most
    2 * workers parsed files are held in memory at a time.
    """
    if not os.path.isdir(routes_path):
        logger.warn("No workout routes found, skipping...")
        return

    route_files = [
        os.path.join(routes_path, file)
        for file in os.listdir(routes_path)
        if file.endswith(".gpx")
    ]
    workers = min(workers, len(route_files)) or 1
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


def extract_workout_routes(zip_ref: zipfile.ZipFile, unpack_path: str) -> int:
    """Extract only the GPX workout routes under unpack_path. Returns the file count."""
    # Routes left over from an earlier, larger export would be imported again.
    shutil.rmtree(routes_dir(unpack_path), ignore_errors=True)
    count = 0
    for name in zip_ref.namelist():
        dirname, basename = posixpath.split(name)
        if dirname == ZIP_ROUTES_DIR and basename.endswith(".gpx"):
            zip_ref.extract(name, unpack_path)
            count += 1
    return count

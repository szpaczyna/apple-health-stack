"""Streams export.xml from the export zip into PostgreSQL."""
import os
import posixpath
import re
from lxml import etree
from typing import Generator
import zipfile

from .config import ImportConfig

from .db import (
    write_records_batch,
    write_sleep_batch,
)

from .errors import ImportAbortedError

from .formatters import (
    format_record,
    format_workout,
    measurement_name,
    sleep_analysis_formatter,
)

from .log import logger

# --- Configuration ---
# Path of the export directory inside the zip archive (zip members always use "/")
ZIP_EXPORT_DIR = "apple_health_export"
EXPORT_XML_REGEX = re.compile(r"(export|导出)\.xml", re.IGNORECASE)
# Record types whose numbers live in <MetadataEntry> children rather than in value.
METADATA_RECORD_TYPES = {"HeadphoneAudioExposureEvent"}


# --- XML processing ---

def find_xml_file(zip_path: str) -> str | None:
    """Find the health export XML member inside the export zip at zip_path."""
    with zipfile.ZipFile(zip_path) as zip_ref:
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


def health_data_generator(zip_path: str, xml_file: str) -> Generator[tuple, None, None]:
    """
    Generator that yields (type, data) tuples from health data XML.
    xml_file is the name of the XML member inside the export zip at zip_path.
    type is 'record', 'sleep', or 'workout'.
    """
    with zipfile.ZipFile(zip_path) as zip_ref, zip_ref.open(xml_file) as f:
        # Skip any leading non-XML data
        stream = _SkipToHealthData(f)

        context = etree.iterparse(stream, events=('end',), tag=('Record', 'Workout'), recover=True)

        for _, elem in context:
            try:
                if elem.tag == "Record":
                    record_type = measurement_name(elem.get("type", "Record"))
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


def process_health_data(conn, import_cfg: ImportConfig, export_file: str) -> None:
    """Process the main health data XML file and push data to PostgreSQL."""
    batch_size = import_cfg.batch_size

    logger.info(f"Processing health data from {os.path.basename(export_file)}...")

    records_batch = []
    sleep_batch = []
    total_records = 0
    total_sleep = 0

    for data_type, row in health_data_generator(import_cfg.zip_path, export_file):
        if data_type == "record":
            records_batch.append(row)
            if len(records_batch) >= batch_size:
                if write_records_batch(conn, records_batch):
                    total_records += len(records_batch)
                    logger.success(f"  Inserted {total_records} records...")
                    records_batch = []
                else:
                    raise ImportAbortedError("records batch")

        elif data_type == "sleep":
            sleep_batch.append(row)
            if len(sleep_batch) >= batch_size:
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

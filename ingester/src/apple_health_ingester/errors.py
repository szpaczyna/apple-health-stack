"""Exceptions shared by the ingester modules."""


class ImportAbortedError(Exception):
    """A batch could not be written, so the import must stop."""

"""Console logger shared by the ingester modules: info to stdout, warnings and errors to stderr."""

import os
import sys


class _Colors:
    """ANSI color codes"""
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'


def _emit(stream, color, message):
    # Flush so stdout progress and stderr warnings keep their order in pipes and Docker logs.
    if os.environ.get("NO_COLOR") or not getattr(stream, "isatty", lambda: False)():
        print(message, file=stream, flush=True)
    else:
        print(f"{color}{message}{_Colors.RESET}", file=stream, flush=True)


class Logger:
    """Simple logger that colors output only on a terminal (see https://no-color.org)."""
    def info(self, message):
        _emit(sys.stdout, _Colors.BLUE, message)

    def success(self, message):
        _emit(sys.stdout, _Colors.GREEN, message)

    def warn(self, message):
        _emit(sys.stderr, _Colors.YELLOW, message)

    def error(self, message):
        _emit(sys.stderr, _Colors.RED, message)


logger = Logger()

"""Colorized console logger shared by the ingester modules."""


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

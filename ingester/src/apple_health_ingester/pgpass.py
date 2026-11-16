"""Locate the libpq password file and check that it covers a connection before connecting."""
import os
import stat
from collections.abc import Mapping

from .config import DatabaseConfig


def resolve_passfile(db_cfg: DatabaseConfig, environ: Mapping[str, str]) -> str:
    """Return the configured passfile, else PGPASSFILE, else ~/.pgpass."""
    return os.path.expanduser(db_cfg.passfile or environ.get("PGPASSFILE") or "~/.pgpass")


def _split(line: str) -> list[tuple[str, str]]:
    """Split a pgpass line into at most 5 (raw, unescaped) fields."""
    fields, raw, value = [], "", ""
    chars = iter(line)
    for c in chars:
        if c == "\\":
            escaped = next(chars, "")
            raw, value = raw + c + escaped, value + escaped
        elif c == ":" and len(fields) < 4:
            fields.append((raw, value))
            raw, value = "", ""
        else:
            raw, value = raw + c, value + c
    fields.append((raw, value))
    return fields


def _match(line: str, hosts: set[str], rest: tuple[str, ...]) -> list[tuple[str, str]] | None:
    fields = _split(line)
    if len(fields) < 5:
        return None
    (host_raw, host), *others = fields[:4]
    if host_raw != "*" and host not in hosts:
        return None
    if all(raw == "*" or value == want for (raw, value), want in zip(others, rest)):
        return fields
    return None


def check(path: str, host: str, port: int | str, dbname: str, user: str) -> str | None:
    """Return None if libpq would find a password in path for this connection, else an error."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return f"Password file {path} does not exist. Create it or set database.password."
    except OSError as e:
        return f"Unable to read password file {path}: {e.strerror}."
    if not stat.S_ISREG(st.st_mode):
        return f"Password file {path} is not a regular file."
    if os.name == "posix" and st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        return (f"Password file {path} has group or world access, so libpq ignores it. "
                f"Run chmod 600 {path}.")
    # libpq maps only its build-specific default socket directory to localhost, so accept both.
    hosts = {"localhost"} if not host else {host, "localhost"} if host.startswith("/") else {host}
    rest = (str(port), dbname, user)
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            lines = f.read().split("\n")
    except PermissionError:
        return f"Password file {path} is not readable by the current user."
    except OSError as e:
        return f"Unable to read password file {path}: {e.strerror}."
    wanted = ":".join((host or "localhost", *rest))
    for line in lines:
        line = line.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        fields = _match(line, hosts, rest)
        if fields is None:
            continue
        # libpq stops at the first matching line, so an empty password there is final.
        if not fields[4][1]:
            return f"Password file {path} has an empty password for {wanted}."
        return None
    return f"Password file {path} has no entry matching {wanted}."

"""Layered configuration: dataclass defaults, YAML file, environment variables, CLI flags."""
import dataclasses
import os
from collections.abc import Mapping
from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class DatabaseConfig:
    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    dbname: str = "health"
    password: str | None = None
    passfile: str | None = None
    max_retries: int = 5
    retry_cooldown: int = 10
    wait_timeout: int = 120


@dataclass(frozen=True)
class ImportConfig:
    zip_path: str = "./export.zip"
    batch_size: int = 15000
    gpx_workers: int | None = None
    unpack_dir: str | None = None


@dataclass(frozen=True)
class Config:
    database: DatabaseConfig
    import_: ImportConfig


class ConfigError(Exception):
    pass


_SECTIONS = {"database": DatabaseConfig, "import": ImportConfig}
_ENV = {
    ("database", "host"): "PG_HOST",
    ("database", "port"): "PG_PORT",
    ("database", "user"): "PG_USER",
    ("database", "dbname"): "PG_DB",
    ("database", "password"): "PG_PASS",
    ("database", "passfile"): "PG_PASSFILE",
    ("database", "max_retries"): "DB_MAX_RETRIES",
    ("database", "retry_cooldown"): "DB_RETRY_COOLDOWN",
    ("database", "wait_timeout"): "DB_WAIT_TIMEOUT",
    ("import", "zip_path"): "ZIP_PATH",
    ("import", "batch_size"): "BATCH_SIZE",
    ("import", "gpx_workers"): "GPX_WORKERS",
    ("import", "unpack_dir"): "UNPACK_DIR",
}
_CLI = {"zip_path": ("import", "zip_path", "--zip")}
_INT_RANGES = {
    "port": (1, 65535),
    "max_retries": (1, None),
    "retry_cooldown": (0, None),
    "wait_timeout": (1, None),
    "batch_size": (1, None),
    "gpx_workers": (1, None),
}
_NULLABLE = {"password", "passfile", "unpack_dir", "gpx_workers"}
_PATHS = {"zip_path", "passfile", "unpack_dir"}


def find_config_file(cli_path, environ):
    """Return the config file path; explicit paths must exist, the XDG default may be missing."""
    explicit = cli_path or environ.get("APPLE_HEALTH_CONFIG")
    if explicit:
        path = os.path.expanduser(explicit)
        if not os.path.isfile(path):
            raise ConfigError(f"Config file {path} does not exist.")
        return path
    base = environ.get("XDG_CONFIG_HOME") or "~/.config"
    path = os.path.expanduser(os.path.join(base, "apple-health-ingester", "config.yaml"))
    return path if os.path.isfile(path) else None


def _check(key, value, source):
    if value is None:
        if key in _NULLABLE:
            return None
        raise ConfigError(f"{source} must not be null.")
    if key in _INT_RANGES:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"{source} must be an integer, got {value!r}.")
        low, high = _INT_RANGES[key]
        if value < low or (high is not None and value > high):
            bounds = f"between {low} and {high}" if high is not None else f"at least {low}"
            raise ConfigError(f"{source} must be {bounds}, got {value}.")
        return value
    if not isinstance(value, str):
        shown = "" if key == "password" else f", got {value!r}"
        raise ConfigError(f"{source} must be a string{shown}.")
    return os.path.expanduser(value) if key in _PATHS else value


def _read_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"Unable to read config file {path}: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: the top level must be a mapping.")
    return data


def load(cli_args: dict, environ: Mapping[str, str]) -> tuple[Config, str | None]:
    """Build the config from defaults, the YAML file, environ and cli_args (highest wins)."""
    path = find_config_file(cli_args.get("config"), environ)
    values = {section: {} for section in _SECTIONS}

    for section, body in (_read_file(path) if path else {}).items():
        if section not in _SECTIONS:
            raise ConfigError(f"{path}: unknown section {section!r}.")
        if body is None:
            continue
        if not isinstance(body, dict):
            raise ConfigError(f"{path}: {section} must be a mapping.")
        known = {f.name for f in dataclasses.fields(_SECTIONS[section])}
        for key, value in body.items():
            if key not in known:
                raise ConfigError(f"{path}: unknown key {section}.{key}.")
            values[section][key] = _check(key, value, f"{path}: {section}.{key}")

    for (section, key), name in _ENV.items():
        raw = environ.get(name)
        if not raw:
            continue
        if key in _INT_RANGES:
            try:
                raw = int(raw)
            except ValueError:
                raise ConfigError(f"{name} must be an integer, got {raw!r}.") from None
        values[section][key] = _check(key, raw, name)

    for arg, value in cli_args.items():
        if arg == "config" or value is None:
            continue
        if arg not in _CLI:
            raise ConfigError(f"Unknown command-line option {arg}.")
        section, key, flag = _CLI[arg]
        values[section][key] = _check(key, value, flag)

    cfg = Config(DatabaseConfig(**values["database"]), ImportConfig(**values["import"]))
    return cfg, path


def to_yaml(cfg: Config) -> str:
    """Render cfg as YAML with the password masked."""
    database = dataclasses.asdict(cfg.database)
    if database["password"] is not None:
        database["password"] = "***"
    data = {"database": database, "import": dataclasses.asdict(cfg.import_)}
    return yaml.safe_dump(data, sort_keys=False)

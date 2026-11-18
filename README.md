# Apple Health in PostgreSQL and Grafana

Import an Apple Health `export.zip` into PostgreSQL and browse it on Grafana dashboards. Docker Compose runs the whole stack on your machine.

```
export.zip → ingester → PostgreSQL → Grafana
```

The project is inspired by [k0rventen/apple-health-grafana](https://github.com/k0rventen/apple-health-grafana), which loads the same export into InfluxDB. The ingester started from its import script and was rewritten for PostgreSQL.

## Export your Apple Health data

On the iPhone, open the Health app, tap your profile picture, then **Export All Health Data**. Copy the resulting `export.zip` to your computer.

If the export fails with "Could not export data", increase the Auto-Lock time (Settings → Display & Brightness) and try again.

## Run the stack

You need Docker with the Compose plugin.

```sh
cp /path/to/export.zip .
docker compose up -d postgres grafana
docker compose run --rm ingester
```

Open <http://localhost:3000> and log in as `admin` / `health`. The dashboards are in the **Health** folder.

The ingester replaces the data on each run, so import a newer export the same way. To keep the zip somewhere else, set `EXPORT_ZIP`:

```sh
EXPORT_ZIP=~/Downloads/export.zip docker compose run --rm ingester
```

PostgreSQL listens on `127.0.0.1:5432` for your own SQL clients. If that port is taken, for example by a local PostgreSQL, set another one with `POSTGRES_PORT=5433 docker compose up -d`. The ingester and Grafana reach the database over the Compose network and keep working.

To remove the stack together with its data:

```sh
docker compose down -v
```

## Passwords

The defaults suit a stack that listens on `127.0.0.1` only. To change them, copy `.env.example` to `.env` and edit it before the first start:

| Variable | Default | Used by |
|---|---|---|
| `POSTGRES_PASSWORD` | `health` | PostgreSQL superuser, used by the ingester |
| `GRAFANA_DB_PASSWORD` | `grafana` | Read-only `grafana` database user for the datasource |
| `GRAFANA_ADMIN_PASSWORD` | `health` | Grafana `admin` login |
| `GRAFANA_DB_TIMEZONE` | `Etc/UTC` | Time zone of the `grafana` database user, see [Time zone](#time-zone) |

PostgreSQL creates the `grafana` user on the first start of an empty volume. After you change `POSTGRES_PASSWORD` or `GRAFANA_DB_PASSWORD`, recreate the volume with `docker compose down -v`.

Grafana expands `$NAME` in provisioning files, so a `$` in `GRAFANA_DB_PASSWORD` breaks the datasource login. Pick a password without `$`.

## Time zone

Daily panels on the summary dashboard split days at midnight in the `grafana` database user's time zone, which defaults to `Etc/UTC`. To match your local days, set `GRAFANA_DB_TIMEZONE` in `.env` to an IANA name such as `Europe/Warsaw` before the first start.

The init script that applies this setting runs only on an empty data volume. On an existing install, set the time zone by hand and restart Grafana so its new database sessions pick it up:

```sh
docker compose exec postgres psql -U postgres -d health -c "ALTER ROLE grafana SET timezone = 'Europe/Warsaw'"
docker compose restart grafana
```

## Dashboards

| Dashboard | Contents |
|---|---|
| Summary | Yearly totals, daily activity, heart metrics |
| Health Data | Body mass, steps and distance, heart rate, blood pressure |
| All Health | Any measurement from a dropdown, with mean, max, min and a trend line |

## What the ingester does

1. Reads `export.xml` as a stream from the zip and extracts only the `workout-routes/*.gpx` files, into a temporary directory that the ingester removes on exit. Set `import.unpack_dir` to keep them in a fixed directory instead.
2. Waits for PostgreSQL, then opens one transaction for the whole import, creates the tables if they are missing, truncates them, and drops the secondary indexes.
3. Loads records and sleep data with `COPY`, then parses the GPX routes in parallel.
4. Rebuilds the indexes, runs `ANALYZE`, and prints how long each step took.

The ingester waits up to `database.wait_timeout` seconds (120 by default) for PostgreSQL to accept connections and exits with status 1 after that. A failed write rolls the transaction back, so the tables keep the data and indexes from the previous run, and the ingester exits with status 1. Grafana panels wait for a running import to finish before they show data. Elements that the ingester cannot parse are logged and counted; the ingester commits the rest of the import, prints the count and exits with status 1. Run the import again to retry; each run truncates the tables and reloads the whole export.

Schema: `ingester/src/apple_health_ingester/sql/001_schema.sql`.

| Table | Contents |
|---|---|
| `records` | Every record and workout: `measurement, time, value, unit, device` |
| `sleep_analysis` | Sleep stages with start and end times |
| `workout_routes` | GPX track points with speed and distance |

## Install the command

The ingester is a Python package with the command `apple-health-ingester`. To run it on your machine against your own PostgreSQL server, install it with pipx or uv:

```sh
pipx install ./ingester
# or
uv tool install ./ingester
```

The package version comes from the git tag: a build on the `v1.1.1` tag gets `1.1.1`, and a build between tags gets a `.devN` version. `apple-health-ingester --version` prints it. After you pull or tag, `pipx install --force ./ingester` or `uv tool install --reinstall ./ingester` installs the new version.

Then point it at the export:

```sh
apple-health-ingester --zip ~/Downloads/export.zip
```

The ingester writes progress to stdout and warnings and errors to stderr. It colors the output only on a terminal, and setting `NO_COLOR` turns the colors off.

## Ingester settings

A command-line flag beats an environment variable, an environment variable beats the file, and the file beats the built-in default. An empty environment variable counts as unset.

`ingester/config.example.yaml` lists every key with the name of its environment variable. Copy it to `~/.config/apple-health-ingester/config.yaml` and edit it. The ingester looks for the file in this order:

1. `--config PATH`
2. `$APPLE_HEALTH_CONFIG`
3. `$XDG_CONFIG_HOME/apple-health-ingester/config.yaml`, with `~/.config` as the default for `XDG_CONFIG_HOME`

A path from `--config` or `APPLE_HEALTH_CONFIG` must exist, otherwise the ingester exits with status 2. Without any config file the ingester runs on environment variables and defaults. Unknown keys and values of the wrong type also stop it with status 2.

To check the result of all sources, print the effective config. The ingester shows the password as `***`:

```sh
apple-health-ingester --print-config
```

| YAML key | Variable | Default | Description |
|---|---|---|---|
| `database.host` | `PG_HOST` | `localhost` (`postgres` in Compose) | PostgreSQL host |
| `database.port` | `PG_PORT` | `5432` | PostgreSQL port |
| `database.user` | `PG_USER` | `postgres` | PostgreSQL user |
| `database.dbname` | `PG_DB` | `health` | Database name; it must exist |
| `database.password` | `PG_PASS` | unset, which uses a pgpass file | PostgreSQL password |
| `database.passfile` | `PG_PASSFILE` | unset, then `PGPASSFILE`, then `~/.pgpass` | Password file used without a password |
| `database.wait_timeout` | `DB_WAIT_TIMEOUT` | `120` | Seconds to wait for PostgreSQL at startup |
| `import.zip_path` | `ZIP_PATH`, `--zip` | `./export.zip` (`/app/export.zip` in the image) | Path to the export |
| `import.batch_size` | `BATCH_SIZE` | `15000` | Rows per `COPY` batch |
| `import.gpx_workers` | `GPX_WORKERS` | usable CPU count | Processes parsing GPX files; `1` parses serially |
| `import.unpack_dir` | `UNPACK_DIR` | unset, which uses a temporary directory | Directory for the extracted GPX files, kept after the run |

`GPX_WORKERS` does not see container CPU limits such as `docker run --cpus`. Set it explicitly when you use one.

The image sets `ZIP_PATH` to the mount target `/app/export.zip`, so `import.zip_path` in a config file has no effect inside the container.

### Password file

Without a password the ingester reads it from a pgpass file. Each line has the form `host:port:dbname:user:password`, and `*` matches any value in the first four fields:

```sh
echo 'localhost:5432:health:postgres:change-me' >> ~/.pgpass
chmod 600 ~/.pgpass
```

Set `database.passfile` to use a file other than `~/.pgpass`. Before it connects, the ingester checks that the file exists with mode `0600` or stricter. The file must also contain a line for the configured host, port, database and user. If a check fails, the ingester exits with status 1 and names the problem.

### Docker Compose

Compose mounts `ingester/config.example.yaml` read-only at `/etc/apple-health-ingester/config.yaml` and sets `APPLE_HEALTH_CONFIG` to that path. To use your own file, copy the example to `ingester/config.yaml`, which git ignores, and set `INGESTER_CONFIG`, either in `.env` or on the command line:

```sh
INGESTER_CONFIG=./ingester/config.yaml docker compose run --rm ingester --print-config
```

The service sets `PG_HOST` and `PG_PASS`, so the database host and password in the file have no effect under Compose.

### Docker without Compose

To run the image against another PostgreSQL server, copy `ingester/.env.example` to `ingester/.env` and edit it, or mount a config file:

```sh
docker build -t apple-health-ingester ingester
docker run --rm --env-file ingester/.env \
  -v "$PWD/export.zip:/app/export.zip:ro" apple-health-ingester
docker run --rm \
  -e APPLE_HEALTH_CONFIG=/etc/apple-health-ingester/config.yaml \
  -v "$PWD/ingester/config.yaml:/etc/apple-health-ingester/config.yaml:ro" \
  -v "$PWD/export.zip:/app/export.zip:ro" apple-health-ingester
```

Arguments after the image name go to `apple-health-ingester`, for example `--print-config`.

The container runs as uid 10001 and has no home directory. To use a pgpass file, mount it and set `PG_PASSFILE`. The mounted file must be readable by uid 10001 and have mode `0600`, for example a copy owned by that uid:

```sh
cp ~/.pgpass ~/ingester-pgpass
chmod 600 ~/ingester-pgpass
sudo chown 10001 ~/ingester-pgpass
docker run --rm --env-file ingester/.env \
  -e PG_PASSFILE=/run/secrets/pgpass \
  -v "$HOME/ingester-pgpass:/run/secrets/pgpass:ro" \
  -v "$PWD/export.zip:/app/export.zip:ro" apple-health-ingester
```

Set `PG_PASS` instead if that setup does not suit you.

## Tests

The unit tests use the standard library only. Install the package in a virtualenv first:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e ./ingester ruff
python -m unittest discover -s ingester/tests
ruff check ingester
```

## Releases

`.github/workflows/release.yml` builds the ingester image for `linux/amd64` and `linux/arm64` and pushes it to `ghcr.io/szpaczyna/apple-health-stack-ingester`. It then creates a GitHub release with generated notes.

Merging a pull request into `main` releases the next version when the pull request has a release label. `.github/workflows/auto-release.yml` takes the highest stable `vX.Y.Z` tag and bumps it according to the label:

| Label   | `v1.2.3` becomes |
|---------|------------------|
| `patch` | `v1.2.4`         |
| `minor` | `v1.3.0`         |
| `major` | `v2.0.0`         |

When a pull request has more than one release label, the largest bump wins. The workflow tags the merge commit and then runs the release workflow for the new tag. Merging a pull request without a release label creates no tag and no release, and so does a direct push to `main` or a merge into any other branch.

Pushing a `vX.Y.Z` tag by hand starts the same release workflow:

```sh
git tag v1.2.0
git push origin v1.2.0
```

The image gets the tags `1.2.0`, `1.2` and `1` plus `latest`. A tag with a hyphen, such as `v1.2.0-rc.1`, publishes only `1.2.0-rc.1` and marks the GitHub release as a prerelease.

To run the published image instead of building it, create `docker-compose.override.yml` next to `docker-compose.yml`. Compose merges the override file on its own, so `docker-compose.yml` stays unchanged:

```yaml
services:
  ingester:
    image: ghcr.io/szpaczyna/apple-health-stack-ingester:1.2.0
```

With both `build` and `image` set, Compose pulls the image and builds from source only if the pull fails. Fetch the image ahead of time with `docker compose pull ingester`, then run the ingester as before.

## Project layout

```
├── docker-compose.yml
├── .env.example
├── ingester/                       Python package and its Dockerfile
│   ├── pyproject.toml              Package metadata, dependencies and ruff settings
│   ├── config.example.yaml         Every config key with its environment variable
│   ├── src/apple_health_ingester/
│   │   ├── __main__.py             Runs the CLI for python -m apple_health_ingester
│   │   ├── cli.py                  Command-line entry point and import steps
│   │   ├── config.py               YAML file, environment and flag handling
│   │   ├── db.py                   Connection, schema, indexes and COPY writes
│   │   ├── export_xml.py           Streams export.xml from the zip into PostgreSQL
│   │   ├── routes.py               Extracts and loads the GPX workout routes
│   │   ├── pgpass.py               Password file lookup and checks
│   │   ├── errors.py               Shared exceptions
│   │   ├── formatters.py           Apple Health records to table rows
│   │   ├── log.py                  Colored console logger
│   │   └── sql/001_schema.sql      Tables and indexes
│   └── tests/                      Unit tests (python -m unittest discover -s ingester/tests)
├── postgres/                       Init script for the read-only Grafana user
└── grafana/provisioning/           Datasource and dashboards
```

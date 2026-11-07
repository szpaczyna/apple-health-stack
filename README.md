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

PostgreSQL creates the `grafana` user on the first start of an empty volume. After you change `POSTGRES_PASSWORD` or `GRAFANA_DB_PASSWORD`, recreate the volume with `docker compose down -v`.

Grafana expands `$NAME` in provisioning files, so a `$` in `GRAFANA_DB_PASSWORD` breaks the datasource login. Pick a password without `$`.

## Dashboards

| Dashboard | Contents |
|---|---|
| Summary | Yearly totals, daily activity, heart metrics |
| Health Data | Body mass, steps and distance, heart rate, blood pressure |
| All Health | Any measurement from a dropdown, with mean, max, min and a trend line |

## What the ingester does

1. Reads `export.xml` as a stream from the zip and extracts the `workout-routes/*.gpx` files only.
2. Creates the tables if they are missing, truncates them, and drops the secondary indexes.
3. Loads records and sleep data with `COPY`, then parses the GPX routes in parallel.
4. Rebuilds the indexes, runs `ANALYZE`, and prints how long each step took.

If the database connection drops, the ingester reconnects and retries the failed batch.

Schema: `ingester/sql/001_schema.sql`.

| Table | Contents |
|---|---|
| `records` | Every record and workout: `measurement, time, value, unit, device` |
| `sleep_analysis` | Sleep stages with start and end times |
| `workout_routes` | GPX track points with speed and distance |
| `data_sources` | Devices and apps that produced the data |

## Ingester settings

Set these in the `ingester` service of `docker-compose.yml`, or as environment variables when you run the ingester against another PostgreSQL server. For the second case, copy `ingester/.env.example` to `ingester/.env` and edit it. Then run the image:

```sh
docker build -t apple-health-ingester ingester
docker run --rm --env-file ingester/.env \
  -v "$PWD/export.zip:/app/export.zip:ro" apple-health-ingester
```

Or run the script directly, with the dependencies from `ingester/requirements.txt` installed:

```sh
set -a; . ingester/.env; set +a
python ingester/app.py
```

Inside the image, `ZIP_PATH` defaults to `/app/export.zip` and `PG_HOST` to `postgres`. The table lists the defaults of the script itself.

| Variable | Default | Description |
|---|---|---|
| `PG_HOST` | `localhost` (`postgres` in Compose) | PostgreSQL host |
| `PG_PORT` | `5432` | PostgreSQL port |
| `PG_USER` | `postgres` | PostgreSQL user |
| `PG_PASS` | unset, which uses `~/.pgpass` | PostgreSQL password |
| `PG_DB` | `health` | Database name; it must exist |
| `ZIP_PATH` | `./export.zip` | Path to the export |
| `BATCH_SIZE` | `15000` | Rows per `COPY` batch |
| `GPX_WORKERS` | usable CPU count | Processes parsing GPX files; `1` parses serially |
| `DB_MAX_RETRIES` | `5` | Attempts per failed batch |
| `DB_RETRY_COOLDOWN` | `10` | Seconds between attempts |

`GPX_WORKERS` does not see container CPU limits such as `docker run --cpus`. Set it explicitly when you use one.

## Tests

The unit tests use the standard library only, with the ingester's dependencies installed:

```sh
pip install -r ingester/requirements.txt
python -m unittest discover -s ingester/tests
```

## Project layout

```
├── docker-compose.yml
├── .env.example
├── ingester/                 Python ingester and its Dockerfile
│   └── sql/001_schema.sql    Tables and indexes
├── postgres/                 Init script for the read-only Grafana user
└── grafana/provisioning/     Datasource and dashboards
```

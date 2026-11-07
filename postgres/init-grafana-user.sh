#!/bin/sh
# Runs once, on the first start of an empty data volume.
set -eu

# \getenv reads the password inside psql, so it never appears in the process list.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv pw GRAFANA_DB_PASSWORD
CREATE ROLE grafana LOGIN PASSWORD :'pw';
GRANT CONNECT ON DATABASE health TO grafana;
GRANT USAGE ON SCHEMA public TO grafana;
-- The ingester creates the tables later, as the postgres user.
ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO grafana;
SQL

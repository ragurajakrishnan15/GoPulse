#!/bin/sh
# Creates the least-privilege roles used by the API. Passwords come from the container environment.
#   gopulse_api_ro : SELECT on serving.* (all dashboard queries)
#   gopulse_api_rw : INSERT only on the run-history tables (bounded demo-run endpoint)
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE SCHEMA IF NOT EXISTS serving;
CREATE ROLE gopulse_api_ro LOGIN PASSWORD '${API_RO_PASSWORD}';
CREATE ROLE gopulse_api_rw LOGIN PASSWORD '${API_RW_PASSWORD}';
GRANT USAGE ON SCHEMA serving TO gopulse_api_ro, gopulse_api_rw;
ALTER DEFAULT PRIVILEGES IN SCHEMA serving GRANT SELECT ON TABLES TO gopulse_api_ro, gopulse_api_rw;
ALTER ROLE gopulse_api_ro SET statement_timeout = '5s';
ALTER ROLE gopulse_api_rw SET statement_timeout = '5s';
SQL

#!/bin/sh
set -eu
# Only runs on a fresh PostgreSQL volume. The application gets a normal role,
# its own schema, and no superuser/database/role-management permissions.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    --set=ON_ERROR_STOP=1 --set=app_password="$DRG_APP_PASSWORD" \
    --set=app_schema="${DRG_APP_SCHEMA:-drg}" <<'SQL'
CREATE ROLE drg LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'app_password';
GRANT CONNECT ON DATABASE drg TO drg;
CREATE SCHEMA :"app_schema" AUTHORIZATION drg;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
SQL

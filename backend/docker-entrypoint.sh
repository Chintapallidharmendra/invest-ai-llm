#!/bin/sh
# Entrypoint for api, worker and migrate.
#
# DB passwords arrive as Compose secrets in /run/secrets. They are written to a 0600
# pgpass file in tmpfs (PGPASSFILE), so DB URLs carry no password and no secret is
# ever put in the environment.
set -eu

SECRETS=/run/secrets
PGPASS=/tmp/pgpass
DB_HOST="${APP_DB_HOST:-postgres}"
DB_PORT="${APP_DB_PORT:-5432}"
DB_NAME="${APP_DB_NAME:-invest_ai}"

add_pgpass() {  # role, secret file
    [ -r "$SECRETS/$2" ] || return 0
    pw=$(cat "$SECRETS/$2")
    case "$pw" in
        *:* | *\\*) echo "entrypoint: $2 must not contain ':' or '\\'" >&2; exit 1 ;;
    esac
    printf '%s:%s:%s:%s:%s\n' "$DB_HOST" "$DB_PORT" "$DB_NAME" "$1" "$pw" >> "$PGPASS"
}

umask 077
: > "$PGPASS"
add_pgpass app_rw db_app_rw_password
add_pgpass app_migrator db_migrator_password
export PGPASSFILE="$PGPASS"

role="${1:-api}"
[ "$#" -gt 0 ] && shift

case "$role" in
    api)
        exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
            --proxy-headers --forwarded-allow-ips '*' --no-server-header "$@" ;;
    worker)
        if [ -f /srv/app/worker/__main__.py ]; then
            exec python -m app.worker "$@"
        fi
        # Placeholder until Story 1.11 adds the worker entrypoint.
        echo '{"event": "worker.not_implemented", "module": "worker", "level": "warning"}'
        exec sleep infinity ;;
    migrate)
        exec alembic upgrade head ;;
    *)
        exec "$role" "$@" ;;
esac

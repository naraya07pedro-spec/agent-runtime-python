#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${PGDATABASE:?Configure an isolated restore database}"
if [[ $# != 1 || ! -f "$1" || "${RUNTIME_RESTORE_ACK:-}" != isolated-empty-database || "$PGDATABASE" != *_restore_test ]]; then
  printf '%s\n' 'Restore requires an existing dump, explicit isolation acknowledgement and a *_restore_test database.' >&2
  exit 2
fi
objects=$(psql --no-psqlrc --tuples-only --no-align --command="SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','S')")
if [[ "$objects" != 0 ]]; then
  printf '%s\n' 'Restore target must be empty. No objects were changed.' >&2
  exit 2
fi
pg_restore --single-transaction --exit-on-error --no-owner --no-acl --dbname="$PGDATABASE" "$1"
printf '%s\n' 'Restore completed. Keep workers disabled and RUNTIME_RECOVERY_READ_ONLY=true until external-effect review finishes.'

#!/usr/bin/env bash
set -euo pipefail
umask 077
: "${PGDATABASE:?Configure PostgreSQL through environment or a managed passfile}"
if [[ $# != 1 || -e "$1" ]]; then
  printf '%s\n' 'Specify a new backup path; existing files are never overwritten.' >&2
  exit 2
fi
pg_dump --format=custom --no-owner --no-acl --file="$1"
printf '%s\n' 'Backup completed. Treat the dump as sensitive; do not commit it.'

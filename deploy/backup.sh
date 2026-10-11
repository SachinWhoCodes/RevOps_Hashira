#!/bin/sh
# Nightly logical backup of the Casefile database, kept for BACKUP_KEEP_DAYS (default 14).
# Restore drill: see deploy/README.md. Runs inside the postgres image (pg_dump is there).
set -eu
umask 077  # dumps hold customer data: owner-only
KEEP="${BACKUP_KEEP_DAYS:-14}"
while true; do
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  pg_dump --format=custom --file="/backups/casefile-${stamp}.dump" \
    "host=db dbname=casefile user=casefile password=${POSTGRES_PASSWORD}"
  echo "backup written: casefile-${stamp}.dump"
  find /backups -name 'casefile-*.dump' -mtime +"${KEEP}" -delete
  sleep 86400
done

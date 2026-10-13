#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 2 && $# -le 3 ]] || { echo 'Usage: backup_database.sh CLIENT_CONFIG OUTPUT.sql.gz [DATABASE]'; exit 1; }
database_name="${3:-rallypoint}"
[[ "$database_name" =~ ^[a-zA-Z0-9_]+$ ]] || { echo 'Invalid database name'; exit 1; }
# CLIENT_CONFIG is a chmod-600 MariaDB client file; password is not on argv.
client_config="$(realpath -- "$1")"
[[ -f "$client_config" ]] || exit 1
umask 077
temporary_file="$2.tmp"
trap 'rm -f -- "$temporary_file"' EXIT
mariadb-dump --defaults-extra-file="$client_config" --single-transaction --routines --events "$database_name" | gzip > "$temporary_file"
gzip -t "$temporary_file"
mv -- "$temporary_file" "$2"
echo "Database backup written: $2"

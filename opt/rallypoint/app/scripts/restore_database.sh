#!/usr/bin/env bash
set -euo pipefail
[[ $# -ge 3 && $# -le 4 && "$3" == '--confirm-replace' ]] || { echo 'Usage: restore_database.sh CLIENT_CONFIG BACKUP.sql.gz --confirm-replace [DATABASE]'; exit 1; }
database_name="${4:-rallypoint}"
[[ "$database_name" =~ ^[a-zA-Z0-9_]+$ ]] || { echo 'Invalid database name'; exit 1; }
client_config="$(realpath -- "$1")"
backup_file="$(realpath -- "$2")"
[[ -f "$client_config" && -f "$backup_file" ]] || exit 1
# Stop service writers before running this explicitly destructive restore.
if systemctl is-active --quiet rallypoint-scoreboard.service || systemctl is-active --quiet rallypoint-web.service; then
    echo 'Stop scoreboard and web services first. Back up data/ and the current DB.'; exit 1
fi
gzip -t "$backup_file"
gzip -dc "$backup_file" | mariadb --defaults-extra-file="$client_config" "$database_name"
echo 'Database restored. Review data/web_outbox.sqlite3 and recovery snapshots before restarting writers.'

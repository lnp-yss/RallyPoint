#!/usr/bin/env bash
set -euo pipefail
# Run on the Pi with an unpacked release directory: sudo bash ... /path/to/release
[[ "$EUID" -eq 0 ]] || { echo 'Run as root'; exit 1; }
[[ $# -eq 1 ]] || { echo 'Usage: update_scoreboard.sh RELEASE_DIRECTORY'; exit 1; }
release_dir="$(realpath -- "$1")"
project_dir='/opt/rallypoint/app'
[[ "$(realpath -- "$project_dir")" == "$project_dir" ]] || { echo 'Unexpected application symlink; stop and inspect'; exit 1; }
[[ "$release_dir" != "$project_dir" && -f "$release_dir/pickleball_scoreboard/main.py" && -f "$release_dir/requirements-web.txt" ]] || { echo 'Invalid release directory'; exit 1; }
backup_root='/opt/rallypoint/backups'
mkdir -p "$backup_root"
backup_file="$backup_root/app_$(date +%Y%m%d_%H%M%S).tar.gz"
web_was_active=0
systemctl is-active --quiet rallypoint-web.service && web_was_active=1
rollback() {
    echo 'Update failed. Restoring prior code and restarting prior services.' >&2
    systemctl stop rallypoint-web.service || true
    systemctl stop rallypoint-scoreboard.service || true
    # Archive contains relative code paths only; durable data/env are never removed.
    tar -xzf "$backup_file" -C "$project_dir"
    if [[ "$web_was_active" -eq 1 ]]; then systemctl restart rallypoint-web.service || true; fi
    systemctl restart rallypoint-scoreboard.service || true
}
trap 'systemctl restart rallypoint-scoreboard.service || true; if [[ "$web_was_active" -eq 1 ]]; then systemctl restart rallypoint-web.service || true; fi' ERR
systemctl stop rallypoint-scoreboard.service
if [[ "$web_was_active" -eq 1 ]]; then systemctl stop rallypoint-web.service; fi
tar -czf "$backup_file" -C "$project_dir" \
    --exclude='data' --exclude='logs' --exclude='.venv*' --exclude='.git' \
    --exclude='__pycache__' --exclude='*.pyc' --exclude='.env*' .
trap rollback ERR
for folder in pickleball_scoreboard tests deployment; do
    [[ -d "$release_dir/$folder" ]] || { echo "Release missing $folder"; false; }
    rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' \
      "$release_dir/$folder/" "$project_dir/$folder/"
done
# Copy update-related scripts only. Existing KMS/TTY launchers are never replaced.
for file in install_web.sh update_scoreboard.sh backup_database.sh restore_database.sh; do
    install -m 0755 "$release_dir/scripts/$file" "$project_dir/scripts/$file"
done
for file in requirements-web.txt README-V3.md; do cp "$release_dir/$file" "$project_dir/$file"; done
chown -R rallypoint:rallypoint "$project_dir/pickleball_scoreboard" "$project_dir/tests" "$project_dir/deployment"
cd "$project_dir"
runuser -u rallypoint -- .venv-web/bin/python -m pip install -r requirements-web.txt
runuser -u rallypoint -- .venv-web/bin/python -m compileall -q pickleball_scoreboard
runuser -u rallypoint -- env SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy RALLYPOINT_BRIDGE=0 .venv-web/bin/python -m pytest -q
# V3 schema migration is additive only. Never drop production tables during update.
runuser -u rallypoint -- bash -c 'set -a; source /etc/rallypoint/web.env; set +a; cd /opt/rallypoint/app; .venv-web/bin/python -m pickleball_scoreboard.web --init-db'
if [[ "$web_was_active" -eq 1 ]]; then systemctl restart rallypoint-web.service; fi
systemctl restart rallypoint-scoreboard.service
sleep 5
systemctl is-active --quiet rallypoint-scoreboard.service
if [[ "$web_was_active" -eq 1 ]]; then
    systemctl is-active --quiet rallypoint-web.service
    curl --fail --silent http://127.0.0.1:8000/api/session >/dev/null
fi
trap - ERR
echo "Update healthy. Previous code: $backup_file"

#!/usr/bin/env bash
set -euo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
release="$(realpath -- "${1:?Provide the extracted release directory}")"
project='/opt/rallypoint/app'
[[ "$release" != '/' && "$release" != "$project" && -f "$release/pickleball_scoreboard/web/rotation_service.py" ]] || { echo 'Invalid release'; exit 1; }
echo 'Host schema 3 must be installed first. Existing client identity/tunnel/display are retained.'
systemctl start rallypoint-backup.service
python3 - <<'PY'
import json
from pathlib import Path
path = Path('/opt/rallypoint/app/data/current_match.json')
if path.exists():
    saved = json.loads(path.read_text())
    if saved.get('state', {}).get('status') in ('playing', 'game_over'):
        raise SystemExit('Finish the current client game before upgrading')
PY
systemctl stop rallypoint-scoreboard.service
trap 'echo "Client upgrade interrupted; restore code from verified backup before restarting." >&2' ERR
rsync -a --exclude='__pycache__' --exclude='*.pyc' "$release/pickleball_scoreboard/" "$project/pickleball_scoreboard/"
rsync -a "$release/deployment/" "$project/deployment/"
rsync -a "$release/scripts/" "$project/scripts/"
cp "$release/DYNAMIC-MATCHMAKING.md" "$project/"
chown -R rallypoint:rallypoint "$project/pickleball_scoreboard"
find "$project/deployment" -type f -exec chmod 0644 {} +
cd "$project"
python3 -m compileall -q pickleball_scoreboard
# Never reinstall dependencies, overwrite data, or replace the working X11/KMS units.
systemctl restart rallypoint-scoreboard.service
systemctl is-active rallypoint-scoreboard.service rallypoint-tunnel.service
trap - ERR

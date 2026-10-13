#!/usr/bin/env bash
# Additive host upgrade; durable data and display launchers are never copied.
set -euo pipefail
[[ "$EUID" -eq 0 ]] || { echo 'Run with sudo'; exit 1; }
release="$(realpath -- "${1:?Provide the extracted release directory}")"
project='/opt/rallypoint/app'
[[ "$release" != '/' && "$release" != "$project" && -f "$release/pickleball_scoreboard/web/rotation_service.py" ]] || { echo 'Invalid release'; exit 1; }
systemctl start rallypoint-backup.service
cd "$project"
# Refuse unattended replacement while a managed court is occupied.
set -a; source /etc/rallypoint/web.env; set +a
.venv-web/bin/python - <<'PY'
from sqlalchemy import select
from pickleball_scoreboard.web.database import Database
from pickleball_scoreboard.web.config import configuration
from pickleball_scoreboard.web.models import Match
from pickleball_scoreboard.web.models import Court
from pickleball_scoreboard.web.club_service import ClubService
from pickleball_scoreboard.web.state_service import MatchStateService
d = Database(configuration()['DATABASE_URL'])
with d.transaction() as s:
    if s.scalar(select(Match.id).where(Match.status.in_(['ACTIVE','STARTING','RESERVED']))):
        raise SystemExit('Finish/cancel managed matches before host upgrade')
    court_ids = list(s.scalars(select(Court.id)))
state = MatchStateService(ClubService(d), configuration())
for court_id in court_ids:
    live = state.get_current_state(court_id)
    if live.get('match_exists') and live.get('state', {}).get('status') in ('playing', 'game_over'):
        raise SystemExit('Finish casual games before host upgrade')
PY
systemctl stop rallypoint-web.service
systemctl stop rallypoint-scoreboard.service
systemctl stop rallypoint-matchmaking.service 2>/dev/null || true
trap 'echo "Upgrade interrupted. Use the verified backup and rollback guide; services remain stopped." >&2' ERR
for directory in pickleball_scoreboard deployment tests; do
    rsync -a --exclude='__pycache__' --exclude='*.pyc' "$release/$directory/" "$project/$directory/"
done
rsync -a "$release/scripts/" "$project/scripts/"
cp "$release/DYNAMIC-MATCHMAKING.md" "$project/"
chown -R rallypoint:rallypoint "$project/pickleball_scoreboard" "$project/tests"
find "$project/deployment" -type f -exec chmod 0644 {} +
.venv-web/bin/python -m compileall -q pickleball_scoreboard
.venv-web/bin/python -m pickleball_scoreboard.web --init-db
install -m 0644 deployment/systemd/rallypoint-matchmaking.service /etc/systemd/system/
systemctl daemon-reload
systemctl restart rallypoint-web.service
systemctl restart rallypoint-scoreboard.service
systemctl enable --now rallypoint-matchmaking.service
curl --fail --silent http://127.0.0.1:8000/api/session >/dev/null
systemctl is-active rallypoint-web.service rallypoint-scoreboard.service rallypoint-matchmaking.service
trap - ERR
echo 'Schema 3 installed. Automatic assignment stays off until enabled in Matchmaking Settings.'

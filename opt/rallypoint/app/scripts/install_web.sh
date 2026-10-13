#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
[[ "$project_dir" == '/opt/rallypoint/app' ]] || { echo 'Install production copy at /opt/rallypoint/app first'; exit 1; }
[[ "$EUID" -eq 0 ]] || { echo 'Run sudo bash scripts/install_web.sh'; exit 1; }
id rallypoint >/dev/null
apt-get update
# Optional DB packages in system Python let the existing python3 HDMI service
# synchronize results without changing its working interpreter/launch command.
apt-get install -y python3-venv python3-sqlalchemy python3-pymysql python3-pytest nginx mariadb-client rsync
cd "$project_dir"
install -d -o rallypoint -g rallypoint -m 0755 .venv-web
runuser -u rallypoint -- python3 -m venv --system-site-packages .venv-web
runuser -u rallypoint -- .venv-web/bin/python -m pip install -r requirements-web.txt
install -d -m 0750 -o root -g rallypoint /etc/rallypoint
if [[ ! -f /etc/rallypoint/web.env ]]; then
    install -m 0640 -o root -g rallypoint deployment/web.env.example /etc/rallypoint/web.env
fi
if grep -q 'CHANGE_ME' /etc/rallypoint/web.env; then
    echo 'Edit /etc/rallypoint/web.env with database credentials and generated secrets, then rerun.'
    exit 1
fi
install -m 0644 deployment/systemd/rallypoint-web.service /etc/systemd/system/rallypoint-web.service
install -m 0644 deployment/systemd/rallypoint-matchmaking.service /etc/systemd/system/rallypoint-matchmaking.service
install -m 0644 deployment/nginx/rallypoint.conf /etc/nginx/sites-available/rallypoint
ln -sfn /etc/nginx/sites-available/rallypoint /etc/nginx/sites-enabled/rallypoint
# Keep all existing nginx sites, HDMI services and firmware files unchanged.
nginx -t
systemctl daemon-reload
echo 'Web files installed. Initialize schema/admin/device using README-V3.md before enabling the web service.'
echo 'The HDMI service was not replaced or restarted. Add the bridge drop-in only after configuration is ready.'

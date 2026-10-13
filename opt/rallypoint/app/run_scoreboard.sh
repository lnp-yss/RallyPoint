#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
[[ -x .venv/bin/python ]] || { echo 'Run ./install.sh first' >&2; exit 1; }
# Prevent a second process from reading the same contacts and save file.
exec 9>data/application.lock
flock -n 9 || { echo 'Scoreboard is already running' >&2; exit 1; }
exec .venv/bin/python -m pickleball_scoreboard.main "$@"

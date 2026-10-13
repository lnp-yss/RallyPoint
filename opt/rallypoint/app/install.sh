#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
if [[ "$EUID" -eq 0 ]]; then
    echo 'Run as the desktop user, not root. Use sudo only for apt prerequisites.' >&2
    exit 1
fi
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
[[ -w . ]] || { echo 'Project directory is not writable'; exit 1; }
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [[ -e /proc/device-tree/model ]] && grep -q 'Raspberry Pi' /proc/device-tree/model; then
    .venv/bin/python -m pip install 'lgpio>=0.2,<1'
fi
mkdir -p data logs
chmod +x run_scoreboard.sh desktop_start.sh
.venv/bin/python -c 'from pathlib import Path; from pickleball_scoreboard.config import Settings; from pickleball_scoreboard.services.persistence import atomic_json; p=Path("data/settings.json"); atomic_json(p, Settings().to_dict()) if not p.exists() else None'
if [[ "${1:-}" == '--autostart' ]]; then
    .venv/bin/python scripts/install_autostart.py
fi
echo 'Installed. GPIO uses BCM numbers, pull-ups and isolated COM/NO contacts ONLY.'
echo 'Check: id -nG (gpio group); /dev/gpiochip* access. Never connect receiver 12 V to GPIO.'
echo 'Launch from the desktop: ./run_scoreboard.sh'

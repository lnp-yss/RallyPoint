#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$(readlink -f -- "$0")")"
mkdir -p logs
if [[ -z "${WAYLAND_DISPLAY:-}" && -z "${DISPLAY:-}" ]]; then
    echo 'No graphical desktop session available' >>logs/desktop-start.log
    exit 1
fi
# Import the actual logged-in desktop environment, then start the user service.
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR XAUTHORITY
systemctl --user start pickleball-scoreboard.service >>logs/desktop-start.log 2>&1

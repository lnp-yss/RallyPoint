#!/bin/sh
# Central hub: run scoring without a monitor, or use HDMI when connected at startup.
set -eu
if grep -q '^connected$' /sys/class/drm/card*-HDMI-A-*/status 2>/dev/null; then
    export SDL_VIDEODRIVER=kmsdrm
else
    export SDL_VIDEODRIVER=dummy
fi
exec /usr/bin/python3 -m pickleball_scoreboard.main

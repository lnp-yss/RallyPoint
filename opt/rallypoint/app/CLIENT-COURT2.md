# Court 2 client deployment — 2026-10-03

Client Pi: **192.168.2.124**, hostname `rallypoint-court2`, SSH user `rallypoint`.
Central server remains **192.168.2.204**. Web login credentials are unchanged.

- Court database ID: **2**.
- Scoreboard device ID: **2** (`Court 2 HDMI`).
- Court 2 live page: <http://192.168.2.204/live?court=2>.
- Court 2 controls: <http://192.168.2.204/control?court=2>.
- Match setup: <http://192.168.2.204/setup?court=2>.

## Using Court 2

1. Open the central website and sign in.
2. Create/check in players as needed.
3. Open Match setup and select **Court 2**.
4. Choose players/rules and press **START MATCH**.
5. Court 2's HDMI screen loads that match; its own RF remotes control scoring.

No active match or synthetic players were created during installation. Court 2
is AVAILABLE and the client is waiting for match setup. Court 1 was not restarted
or reassigned.

## Installed software and configuration

The fresh Debian 13 Raspberry Pi OS Lite installation now runs system Python
3.13.5, Pygame 2.6.1 and GPIO/SQLAlchemy/PyMySQL support. Native SDL/EGL/GBM graphics
libraries were installed. The fresh image lacked EGL libraries; installing
`libegl1 libegl-mesa0 libgles2` resolved the initial `EGL not initialized` error.
No desktop environment/display server, Flask, Gunicorn, Nginx or MariaDB server
was installed on the client. No `/boot` or firmware configuration was changed.

Application: `/opt/rallypoint/app`, with independent `data/` and `logs/`. Existing
Court 1 data was not copied onto the client. Client defaults are fullscreen,
Court 2, doubles side-out, 11 points, win by 2, best of 3.

Client services:

- `rallypoint-scoreboard.service`: KMSDRM, `/dev/tty1`, GPIO and optional bridge.
- `rallypoint-tunnel.service`: persistent, key-authenticated SSH to the hub.

Templates are supplied as `deployment/systemd/rallypoint-client-scoreboard.service`
and `deployment/systemd/rallypoint-tunnel.service`. They contain this Court 2
deployment's hub/user/port values; adjust them before using on another court.

Private environment: `/etc/rallypoint/client.env`, `root:rallypoint`, mode 0640.
It contains device ID 2, the shared bridge authentication token and the central
database URL via the local encrypted tunnel. No password is embedded in a
service command or stored on the development computer.

## Connection routing

```text
Central Pi 192.168.2.204                    Court 2 Pi 192.168.2.124
Web /live?court=2
   → 127.0.0.1:8766  ← reverse SSH tunnel → 127.0.0.1:8765 scoreboard bridge

MariaDB 127.0.0.1:3306 ← local SSH tunnel ← 127.0.0.1:3307 database synchronization
```

The hub's device registration uses `http://127.0.0.1:8766`. Bridge and database
listeners remain on loopback. Only the central web server is accessed by staff.

The client has a unique Ed25519 tunnel key at `/home/rallypoint/.ssh/rallypoint-hub`.
The central host key is pinned. The authorized key on the hub permits only the
required database destination and Court 2 reverse-listener port, with no remote
shell/command access. The SSH connection is restarted automatically on failure.

The hub also has an appended include for `/etc/ssh/rallypoint-courts.conf`: a
client-alive check targeted to `rallypoint` connections from `192.168.2.124`.
Its 15-second interval and three missed replies help clear stale reverse tunnels
after unexpected client disconnection. SSH configuration was validated and
reloaded; the running web/HDMI application services were not restarted. Keep the
client address reserved in the router; update that targeted policy if it changes.

## Clock, recovery and physical controls

The fresh Pi's clock was initially stuck in September and package verification
reported future signatures. It was set from the authenticated central Pi clock,
the time-sync service was restarted and a current clock timestamp persisted.
Automatic NTP synchronization was confirmed before and after reboot.

Each court retains its own atomic match snapshot, undo history and database event
outbox. Network outages do not stop local scoring. Use **RESUME SAVED MATCH** on
that court's control page, or the original recovery controls, after a saved-match
restart.

GPIO mappings remain A: BCM 17/27/22/23 and B: BCM 5/6/13/19. Receiver relays must
use isolated COM-to-GND/NO-to-GPIO dry contacts. Never connect 12 V to GPIO.
Pair each court's remotes only with that court's receivers.

## Verification results

- Client suite: **47 passed, 1 skipped** in 5.92 seconds. The web-only suite was
  skipped because Flask is intentionally absent on this client.
- Python compile checks and both systemd unit validations passed.
- Actual display: **1920×1080 KMSDRM**, GPIO initialization **GPIO ONLINE**.
- Central-to-client live state routing through port 8766 verified.
- Main-thread command receipt and wrong-match rejection verified without changing
  the client's score or any Court 1 match.
- Central database access through the encrypted port 3307 tunnel verified.
- Court 2 SSE through central Nginx verified before/after reboot.
- Tunnel interruption: the tunnel restarted; the scoreboard retained the same PID.
- Client reboot: both services automatically started; scoreboard had zero restart
  failures in the new boot. NTP synchronization remained active.
- Court 1 and the central web service remained active throughout.

RF contact operation and audible output need physical testing. After starting a
match, verify one button press produces one action and Undo restores it.

## Backups and diagnostics

Client pre-install system configuration:
`/opt/rallypoint/backups/client-deploy-20261003/system-before.tar.gz`.

Hub database/key/SSH backups:
`/opt/rallypoint/backups/court2-client-20261003/`.

On the client:

```bash
sudo systemctl status rallypoint-scoreboard.service rallypoint-tunnel.service --no-pager
sudo journalctl -u rallypoint-scoreboard.service -b -n 50 --no-pager
sudo journalctl -u rallypoint-tunnel.service -b -n 50 --no-pager
timedatectl status
```

Do not run the central `install_web.sh` on this client. Do not copy another
court's runtime `data/` directory. For future clients, allocate a new court/device
ID and central reverse port (for example 8767), while local ports 8765 and 3307
can remain the same on each separate Pi.

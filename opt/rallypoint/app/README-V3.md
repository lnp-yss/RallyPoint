# Rally Point V3 — local club management

V3 extends the existing scoreboard. It does not replace GPIO, the scoring engine,
the KMSDRM launch command, or `rallypoint-scoreboard.service`. Production targets
Raspberry Pi OS Lite 64-bit / Debian 13 with Python 3.13, Pygame 2.6.1 and the
existing SDL/KMSDRM/TTY setup. **Do not run the legacy desktop autostart installer
on this production machine. Nothing in V3 requires Desktop, X11 or Wayland.**

For the configured Pi at `192.168.2.204`, see [DEPLOYMENT-PI.md](DEPLOYMENT-PI.md).
Its V3 database is `rallypoint_v3`; the incompatible original `rallypoint` schema
was preserved. Backup/restore scripts accept an optional database name argument.

## Architecture and ownership

```text
Phone / tablet / PC
       │ HTTP, authenticated Flask session + CSRF for writes
     Nginx :80
       │ loopback :8000
     Gunicorn + Flask ───── MariaDB (club records / durable projections)
       │ authenticated loopback HTTP :8765
       ▼
  ScoreboardBridge HTTP thread → timestamped WebCommand → shared input queue
                                                    │
  GPIO / keyboard ──────────────────────────────────┤
                                                    ▼
                                    Pygame MAIN THREAD / ScoringEngine
                                                    │
                                  HDMI + atomic JSON recovery snapshot
                                                    │
                               in-memory state publication + event queue
                                                    ▼
                              background SQLite outbox → MariaDB replay

  /live ← Server-Sent Events ← bridge snapshot (100 ms polling, no scoring JS)
```

The scoring engine is authoritative. Flask, HTTP threads and database workers
never touch Pygame objects. Web controls receive QUEUED receipts, then APPLIED
or REJECTED when the main thread handles them. Commands expire after two seconds
in the main-thread queue; old clicks do not unexpectedly change a later match.
UUID request IDs suppress retries. Receipt IDs are persisted in local SQLite and
applied IDs accompany the atomic score snapshot for restart recovery. Interrupted
unconfirmed commands are not automatically replayed: inspect live state and send
a new action if required.

The original JSON save remains the HDMI recovery source. Optional web metadata,
including court/match identity, start/end time and recent applied request IDs, is
stored in its `external_context`. Older V1/V2 snapshots are accepted. The local
outbox retains database events through a MariaDB outage and replays in order;
MariaDB has a unique event UUID to make retry idempotent. Workers never make
network calls or database writes on the drawing thread. A short gap between an
event being queued and being fsynced by the worker exists; after sudden power
loss, the recovered score snapshot is republished to reconcile final state. A UPS
remains recommended if every individual event must survive an immediate power cut.

Without `RALLYPOINT_BRIDGE=1`, the original application runs with no web
dependencies. If the bridge cannot initialize, it logs the error and scoring
continues. If MariaDB, Nginx, Gunicorn or the LAN is unavailable, RF/keyboard
scoring and local JSON recovery continue. Database-dependent administration
returns 503 instead of attempting a conflicting local scoring engine.

## Windows development

Use the repaired Python 3.12 environment in this checkout:

```powershell
cd "C:\Users\larry\Documents\ChatGPT\Pickleball Scoring System"
.\.venv-dev\Scripts\python.exe -m pip install -r requirements-dev.txt -r requirements-web.txt
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web --init-db
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web --create-admin admin
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web
```

Open [the web app](http://127.0.0.1:8000/) or the database-independent
[scoreboard preview](http://127.0.0.1:8000/preview?team_a=Dominic&team_b=Mark&a=10&b=9).
SQLite is used for development when no database URL is configured. Preview
does not query the database or require a running Pygame application. Score,
names, players, server/serving team, game, timeout counts and winner are configurable.
Game/match-point flags come from Python presentation metadata, not JS scoring.

For an explicit disposable local demo:

```powershell
$env:RALLYPOINT_DATA_ROOT = "$PWD\artifacts\v3-demo"
.\.venv-dev\Scripts\python.exe scripts\seed_demo.py
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web
```

The demo-only account is `demo` / `local-demo-password`. The seeder refuses
MariaDB URLs. It is never run by production installation or startup.

To test actual web-to-Pygame synchronization, set these in **both terminals**:

```powershell
$env:RALLYPOINT_DATA_ROOT = "$PWD\artifacts\v3-demo"
$env:RALLYPOINT_DATABASE_URL = 'sqlite:///' + "$PWD/artifacts/v3-demo/data/club.db".Replace('\','/')
$env:RALLYPOINT_BRIDGE_TOKEN = 'local-development-bridge-token-1234567890'
```

Register the development scoreboard once, then start web:

```powershell
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web --register-device demo-hdmi --court 1
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web
```

In the Pygame terminal:

```powershell
$env:RALLYPOINT_BRIDGE = '1'
$env:RALLYPOINT_DEVICE_ID = '1'
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.main --keyboard --windowed --data-root artifacts/v3-demo
```

If the device is already registered, do not register it again. Its database ID
may differ from 1; `/settings` lists IDs. Test data is isolated from production.
`run_scoreboard.bat` still launches the ordinary standalone HDMI development app.

## Raspberry Pi installation — additive only

Copy the complete V3 code release to `/opt/rallypoint/app`, or use the update
script below after the initial web environment is installed. Do not copy an
`app.py` file onto `ui/dialogs.py`: those are different modules. Keep all existing
KMS/TTY/systemd configuration. V3 does not modify anything under `/boot`.

From an SSH session on the Pi:

```bash
cd /opt/rallypoint/app
sudo bash scripts/install_web.sh
```

The first run installs optional packages and creates an example
`/etc/rallypoint/web.env`, then stops until credentials/secrets are configured.
It adds DB packages to system Python so the existing `python3` HDMI process
can synchronize results, and creates a separate `.venv-web` for Gunicorn/Flask.
It does not replace or restart the HDMI service. Existing Nginx sites are kept;
if another site owns port 80, choose the intended default site after reviewing
its configuration.

### MariaDB

For a new database, use `sudo mariadb` and run:

```sql
CREATE DATABASE IF NOT EXISTS rallypoint CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'rallypoint'@'localhost' IDENTIFIED BY 'choose-a-strong-database-password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, REFERENCES
  ON rallypoint.* TO 'rallypoint'@'localhost';
FLUSH PRIVILEGES;
```

If a club database/user already exists, use its authorized credentials and inspect
the additive schema before initialization. Do not recreate or drop existing data.
The supplied SQL is `deployment/sql/schema.sql`; the supported idempotent CLI
initialization creates missing V3 tables and records migration version 1.

Edit `/etc/rallypoint/web.env`:

```bash
sudo nano /etc/rallypoint/web.env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Generate **separate** random values for `RALLYPOINT_SECRET_KEY` and
`RALLYPOINT_BRIDGE_TOKEN`. Set the DB URL, for example:

```text
RALLYPOINT_DATABASE_URL=mysql+pymysql://rallypoint:URL_ENCODED_PASSWORD@127.0.0.1/rallypoint?charset=utf8mb4
RALLYPOINT_SECRET_KEY=YOUR_GENERATED_SESSION_SECRET
RALLYPOINT_BRIDGE_TOKEN=YOUR_SEPARATE_GENERATED_BRIDGE_SECRET
RALLYPOINT_BRIDGE=1
RALLYPOINT_BRIDGE_PORT=8765
RALLYPOINT_BRIDGE_URL=http://127.0.0.1:8765
RALLYPOINT_DEVICE_ID=1
RALLYPOINT_DATA_ROOT=/opt/rallypoint/app
RALLYPOINT_PUBLIC_LIVE=true
RALLYPOINT_HTTPS=false
```

URL-encode special characters in the database password. Use only the safe
base64url-generated tokens for secrets. Keep the environment file outside the
release tree, owned `root:rallypoint`, mode `0640`. Never commit real credentials.

Rerun installation, then initialize schema/admin/device **as rallypoint**:

```bash
sudo bash scripts/install_web.sh
set -a
source /etc/rallypoint/web.env
set +a
.venv-web/bin/python -m pickleball_scoreboard.web --init-db
.venv-web/bin/python -m pickleball_scoreboard.web --create-admin admin
.venv-web/bin/python -m pickleball_scoreboard.web --register-device hdmi-main --court 1
```

The court argument is its **database ID**, not necessarily its display number.
Initial schema initialization creates Court 1 if the courts table is empty.
Register the device once. Set its actual ID in `RALLYPOINT_DEVICE_ID` afterwards.

### Enable the new web service and the optional bridge

The new `deployment/systemd/rallypoint-web.service` runs:

```text
User=rallypoint
Group=rallypoint
WorkingDirectory=/opt/rallypoint/app
EnvironmentFile=/etc/rallypoint/web.env
ExecStart=/opt/rallypoint/app/.venv-web/bin/gunicorn --bind 127.0.0.1:8000 --worker-class gthread --workers 1 --threads 32 --timeout 60 --access-logfile - --error-logfile - pickleball_scoreboard.web.wsgi:app
Restart=on-failure
```

Install the **additive environment-only** drop-in for the existing scoreboard:

```bash
sudo mkdir -p /etc/systemd/system/rallypoint-scoreboard.service.d
sudo install -m 0644 deployment/systemd/scoreboard-web-bridge.conf \
  /etc/systemd/system/rallypoint-scoreboard.service.d/web-bridge.conf
sudo systemctl daemon-reload
sudo systemctl enable --now rallypoint-web.service
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl restart rallypoint-scoreboard.service
sudo systemctl status rallypoint-scoreboard.service rallypoint-web.service --no-pager
```

This changes neither `ExecStart` nor KMSDRM/TTY options. The original working
command remains:

```bash
SDL_VIDEODRIVER=kmsdrm PYTHONPATH=/opt/rallypoint/app python3 -m pickleball_scoreboard.main
```

No service dependency makes a web failure stop the scoreboard. These are **system
services**: use `sudo systemctl` and `sudo journalctl`, not `systemctl --user`.
The old desktop `pickleball-scoreboard.service` is not the production service.

### Nginx and LAN address

`deployment/nginx/rallypoint.conf` listens on port 80 and proxies to loopback
8000. For `/api/events`, buffering/cache are disabled, HTTP/1.1 is used and the
read timeout is 60 seconds. SSE streams renew at 25 seconds with a 200 ms
reconnect interval. Up to 16 concurrent event streams are admitted by Nginx,
leaving Gunicorn threads available for administrative controls. Raise both
capacity settings together after performance testing.

The important configuration is:

```nginx
location /api/events {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 60s;
}
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
}
```

Use `http://192.168.2.204/` immediately. `http://rallypoint.local/` requires that
hostname to resolve on your LAN (for example existing Avahi/mDNS configuration).
The deployment script does not rename the Pi. Gunicorn and IPC are not exposed
directly to the LAN. This HTTP configuration is intended for a trusted club LAN;
for untrusted networks add TLS to Nginx and set `RALLYPOINT_HTTPS=true` so session
cookies require HTTPS.

## Club workflow

1. ADMIN creates registered players in `/players`; optional ratings are manual.
2. OPERATOR/ADMIN searches names at `/checkin` and checks players in.
3. `/queue` shows wait duration, rating, games today and recent partner/opponent.
   Up/down, priority and removal are persisted. Assign through the recommendation
   or manual setup.
4. The dashboard shows available/active courts, metrics, queue and a recommendation.
   ACCEPT MATCH opens preselected setup; CHANGE TEAMS allows edits; SKIP excludes
   the candidate group for that operator's session; MANUAL SELECT opens setup.
5. `/setup` selects registered players, court, singles/doubles, names, rules,
   11/15/21 target, win-by, match format, starting team/side, timeouts and notes.
   SAVE creates a PREPARED match. START reserves the court/players and sends a
   start command. HDMI validates and loads the match; database status becomes
   ACTIVE when the background projection arrives.
6. `/live?court=1` shows the same state as HDMI, including server dots (one or two),
   service call, timeouts, game/match-point metadata, winner, court and elapsed
   time. It reconnects automatically. An offline bridge shows last known DB state
   labelled OFFLINE instead of treating it as live.
7. RF, keyboard or `/control?court=1` operate the **same engine**. A point reports
   the named rally winner; CHANGE SERVER reports the serving team's lost rally.
   One rally result must be entered once. Undo restores a complete snapshot.
8. Completion updates results/statistics, frees the court and returns players to
   the queue or removes them according to `/settings`.
9. `/matches` filters by date, player ID, court ID and type; summaries include game
   scores, roster and event timeline. `/players` profiles show match win percentage,
   games/points and recent partner/opponent records.

END GAME is a confirmed referee action awarding the current non-tied leader the
game. A tied game is rejected. END MATCH is a confirmed early stop, recorded as
ENDED with no declared winner; it does not invent wins/losses. START NEXT GAME
advances only a finished game. PAUSE/RESUME are persisted and undoable. During a
timeout, RESUME ends the timeout; GPIO/keyboard retain their existing behavior.
RESET requires confirmation and restores initial scoring for the same tracked
match, with history retained. Holding D and pressing C in the HDMI reset dialog
still opens match setup. Local HDMI starts remain possible; untracked local
matches are not retroactively assigned players or written to club statistics.

### Roles and sessions

ADMIN has configuration/player/account/court management. OPERATOR has match
setup, queue, check-in, assignment and scoring controls. VIEWER has live scores
and match history only. Passwords are hashed; there is no default production
password. All mutations, including login, require CSRF tokens. Sessions use
HttpOnly/SameSite cookies, expire after eight hours and enforce roles on API
requests. Disable public live access with `RALLYPOINT_PUBLIC_LIVE=false`.

### Matchmaking

The bounded search considers up to 10 queue candidates by default (configurable
4–16). It evaluates every four-player group and the three unique doubles team
partitions; singles uses pairs. Team strength is the average manual rating;
unrated players use the configured default rating. An overdue player's place is
mandatory, ordered by queue priority/position when more players are overdue than
seats. Waiting dominates normal skill differences by default.

The transparent utility score is:

```text
sum(waiting_weight × minutes
    − fairness_weight × games_today
    + manual_priority_weight × priority)
− balance_weight × team_rating_difference
− partner_rotation_weight × recent_partner_repeats
− opponent_rotation_weight × recent_opponent_repeats
```

Weights, default rating, candidate limit and max wait are editable in `/settings`.
Defaults and comments live in `web/matchmaking.py`. Queue position determines
candidate order and which overdue players get the next seats. Recommendations
never start automatically. Results are recreational estimates, not official
ratings or a DUPR integration.

### Multiple courts

Each match and device has a court ID; no scoring path assumes Court 1. Each
physical device owns one scoring engine. Assign additional courts/devices in
admin settings. A single Pi/engine does not score two matches simultaneously.
Current IPC endpoints must be loopback; additional devices can be reached through
separate SSH tunnels to loopback ports. Discovery, remote device provisioning,
per-device tunnel supervision and credential rotation are future deployment
work, not automatically configured by this release.

## Safe production updates and rollback

Do not overwrite individual files with unrelated modules. Upload a complete
release to a separate staging directory, **without Windows virtual environments**:

```powershell
tar --exclude=.venv --exclude=.venv-dev --exclude=.venv-web --exclude=.git --exclude=__pycache__ --exclude=artifacts --exclude=data --exclude=logs -czf rallypoint-v3.tar.gz pickleball_scoreboard tests deployment scripts requirements-web.txt README-V3.md
scp rallypoint-v3.tar.gz rallypoint@192.168.2.204:/tmp/
```

Then on the Pi:

```bash
mkdir -p /tmp/rallypoint-v3-release
tar -xzf /tmp/rallypoint-v3.tar.gz -C /tmp/rallypoint-v3-release
sudo bash /tmp/rallypoint-v3-release/scripts/update_scoreboard.sh /tmp/rallypoint-v3-release
```

Initialize the web environment once before using this update script. Updates stop
writers, back up code under `/opt/rallypoint/backups`, sync code only, exclude
`.venv*`, `.git`, cache files, data/logs/credentials, compile, run tests, apply
additive schema creation, restart services and check health. Existing launchers,
boot files, systemd units, `/etc/rallypoint/web.env`, `data/` and `logs/` are
preserved. `config.py` is application code; site-specific settings belong in
`data/settings.json` rather than edits to class defaults. Failed updates restore
the previous code and restart its services; new additive DB tables are retained.
Back up recovery snapshots before any upgrade: older releases may not understand
V3 pause/ended snapshots created after a service restart. Review the journal if
rollback cannot resume such a newer save.

## Backup and restore

Create a mode-600 MariaDB client config outside the release tree:

```ini
[client]
user=rallypoint
password=YOUR_DATABASE_PASSWORD
host=127.0.0.1
```

```bash
chmod 600 /home/rallypoint/.rallypoint-db.cnf
bash scripts/backup_database.sh /home/rallypoint/.rallypoint-db.cnf /home/rallypoint/club-backup.sql.gz
sudo systemctl stop rallypoint-scoreboard.service rallypoint-web.service
sudo tar -czf /opt/rallypoint/backups/runtime-backup.tar.gz -C /opt/rallypoint/app data logs
# Keep the DB and runtime backup from the same maintenance window.
```

For a coordinated restore, stop both writers, back up the current DB/runtime,
restore the intended DB and matching `data/` snapshot, then review pending outbox
events before restart. Restoring an old DB with a newer outbox can replay results
from a different point in time; do not blindly clear the backlog.

```bash
bash scripts/restore_database.sh /home/rallypoint/.rallypoint-db.cnf /home/rallypoint/club-backup.sql.gz --confirm-replace
sudo systemctl start rallypoint-scoreboard.service rallypoint-web.service
```

Credentials are never passed on the MariaDB command line.

## Troubleshooting and acceptance

```bash
sudo systemctl status rallypoint-scoreboard.service rallypoint-web.service --no-pager
sudo journalctl -u rallypoint-scoreboard.service -n 80 --no-pager
sudo journalctl -u rallypoint-web.service -n 80 --no-pager
sudo nginx -t
sudo ss -ltnp | grep -E ':80|:8000|:8765'
```

If `--user` journal is empty, use the system commands above. For import errors,
verify module filenames and use the exact production interpreter. Web dependencies
are optional for standalone HDMI but SQLAlchemy/PyMySQL must be available in the
HDMI interpreter for direct DB replay. Missing packages or credentials retain
events in `data/web_outbox.sqlite3` and are logged.

A STARTING reservation after a network interruption is intentional: a start may
have reached HDMI. Inspect live state and the command receipt. The setup page can
release a reservation only after the bridge is online and confirms that match
is not running. Do not free potentially occupied courts based on a timeout.

Undo of completion is supported before the court/players are reassigned. Once
another court has taken those players, final-result corrections require operator
reconciliation; avoid undoing an old completed match after reassignment. A court
with a newer active match prevents an old snapshot from replacing it in the DB.
Backlog conflicts remain logged and retained for explicit reconciliation.

GPIO assignments and dry-contact safety are unchanged: A uses BCM 17/27/22/23;
B uses 5/6/13/19. COM connects to Pi GND, NO to GPIO with internal pull-up. Receiver
12 V power stays isolated. **Never apply 12 V to GPIO.** Original keyboard mappings,
F keys, debounce, duplicate suppression and one/two server dots remain supported.

Run software checks:

```powershell
.\.venv-dev\Scripts\python.exe -m pytest -q
.\.venv-dev\Scripts\python.exe -m compileall -q pickleball_scoreboard scripts
```

Use the browser acceptance path: check-in → suggested match → setup/save/start →
web/RF scoring → live updates → completion → history/statistics/available court.
The SSE poll interval targets sub-500 ms updates on a healthy LAN; actual Pi/TV,
MariaDB, Nginx, RF and LAN performance require hardware acceptance. See
`V3-IMPLEMENTATION.md` for the delivered files and verification results.

Implementation references: [Flask security guidance](https://flask.palletsprojects.com/en/stable/web-security/),
[SQLAlchemy MariaDB dialect](https://docs.sqlalchemy.org/en/20/dialects/mysql.html),
and [Nginx proxy buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering).


## Manual scores, match winners, and player rankings

Open Controls for the appropriate court. Under **Manual score & winner**, enter both current-game scores (whole numbers, 0–999) and click **SAVE MANUAL SCORES** to correct them without automatically ending the game. To finish the entire match, choose the winning team and either keep the live scoreboard scores or select **Use the manual scores entered above**, then click **DECLARE MATCH WINNER** and confirm. The declared winner can differ from the higher score for an early finish or forfeit. This counts the current unfinished game once; it does not invent extra games to meet a best-of threshold.

Commands run on the HDMI scoring engine, save atomically, use request IDs to prevent repeat application, and reject stale revisions. Undo restores scores, service, games, match status, and player result/ranking projections. Undo a completed match before editing its result, and do so before assigning its players to another match. All controls require an administrator or operator account and CSRF validation.

The **Leaderboard** page ranks registered players with completed matches across all courts. Order: match wins, then win percentage, then point difference; equal results share a rank. Each doubles partner receives the same team result and point totals. Untracked local matches and matches ended without a winner are excluded. Ranking is derived from current results, so an Undo removes the result from rankings. No skill rating is changed.

On **Live scores**, choose **Player leaderboard** in the Display selector. Rankings refresh every five seconds; **FULLSCREEN DISPLAY** fills the browser screen. Direct ranking links: `/leaderboard` and `/live?view=leaderboard`. Visibility follows the public-live setting.


## Courtside waiting-area display

Open `/preview` and click **FULLSCREEN COURTSIDE VIEW** for the TV presentation. The upper left shows the next recommended match (court number and both player pairings); the lower left shows player rankings; the right shows every configured court, its real-time score, team names, player names, serving team/server dots, and match status. Available courts show that they are ready for the next match. Court labels use the configured court number, not the database ID.

Scores are read directly from each court Pi's authenticated loopback bridge through its configured tunnel. The browser refreshes once per second without overlapping requests. Recommendations and rankings refresh every five seconds to limit database load. Rankings display six players at a time and rotate pages every ten seconds. The display is read-only; a recommendation remains awaiting operator assignment until staff start a match.

If a Pi is offline, its most recent persisted score remains visible with **OFFLINE · LAST SCORE**. If the central server connection is lost, the page preserves the last received view and displays an offline warning until it reconnects. Public visibility follows PUBLIC_LIVE; when disabled, Preview and the courtside API require an active staff/viewer login. The layout was checked at 1920×1080 and 1280×720.

The Preview page now uses actual court and club data rather than editable sample scores. The old `/api/preview` sample endpoint remains available for development compatibility.

## Court operation improvements

See [SEVEN-IMPROVEMENTS.md](SEVEN-IMPROVEMENTS.md) for Casual/Club modes, remote-only setup, court QR phone views with approved scoring, sync indicators, Up Next/On Deck, staff audit and completed-result correction, and verified daily backups.

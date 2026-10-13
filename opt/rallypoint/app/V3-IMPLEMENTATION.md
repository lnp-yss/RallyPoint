# Rally Point V3 implementation and verification

Implemented as an extension of the existing application in this workspace. No production Raspberry Pi files were changed. The original KMSDRM/TTY service, launch command, GPIO mapping, keyboard input, scoring engine and undo architecture remain in use.

## Final directory structure

```text
Pickleball Scoring System/
├── README.md / README-V3.md / V3-IMPLEMENTATION.md
├── requirements.txt / requirements-dev.txt / requirements-web.txt
├── install.sh / run_scoreboard.sh / run_scoreboard.bat
├── desktop_start.sh / pickleball-scoreboard.service (legacy desktop files)
├── pickleball_scoreboard/
│   ├── main.py / app.py / config.py
│   ├── scoring/   (pure Python authoritative engine)
│   ├── hardware/  (existing GPIO and keyboard controllers)
│   ├── ui/        (existing responsive Pygame display/setup/dialogs)
│   ├── services/
│   │   ├── event_history.py / persistence.py / sound_service.py
│   │   └── presentation.py / scoreboard_bridge.py
│   └── web/
│       ├── __init__.py / application.py / __main__.py / wsgi.py / config.py
│       ├── models.py / database.py / club_service.py
│       ├── state_service.py / matchmaking.py
│       ├── templates/page.html / templates/login.html
│       └── static/club.css / static/club.js
├── deployment/
│   ├── nginx/rallypoint.conf
│   ├── systemd/rallypoint-web.service
│   ├── systemd/scoreboard-web-bridge.conf
│   ├── sql/schema.sql
│   └── web.env.example
├── scripts/
│   ├── install_web.sh / update_scoreboard.sh
│   ├── backup_database.sh / restore_database.sh / seed_demo.py
├── tests/        (original tests + V3 suites)
├── data/         (runtime JSON, optional club DB and durable IPC outbox)
├── logs/         (runtime match logs)
└── artifacts/    (ignored local verification/demo output)
```

## Created files

- `pickleball_scoreboard/web/application.py` (lazy Flask factory separation added during Pi deployment)
- `requirements-web.txt`
- `README-V3.md`
- `V3-IMPLEMENTATION.md`
- `pickleball_scoreboard/services/scoreboard_bridge.py`
- `pickleball_scoreboard/services/presentation.py`
- `pickleball_scoreboard/web/__init__.py`
- `pickleball_scoreboard/web/__main__.py`
- `pickleball_scoreboard/web/club_service.py`
- `pickleball_scoreboard/web/config.py`
- `pickleball_scoreboard/web/database.py`
- `pickleball_scoreboard/web/matchmaking.py`
- `pickleball_scoreboard/web/models.py`
- `pickleball_scoreboard/web/state_service.py`
- `pickleball_scoreboard/web/static/club.css`
- `pickleball_scoreboard/web/static/club.js`
- `pickleball_scoreboard/web/templates/login.html`
- `pickleball_scoreboard/web/templates/page.html`
- `pickleball_scoreboard/web/wsgi.py`
- `deployment/nginx/rallypoint.conf`
- `deployment/systemd/rallypoint-web.service`
- `deployment/systemd/scoreboard-web-bridge.conf`
- `deployment/web.env.example`
- `deployment/sql/schema.sql`
- `scripts/install_web.sh`
- `scripts/update_scoreboard.sh`
- `scripts/backup_database.sh`
- `scripts/restore_database.sh`
- `scripts/seed_demo.py`
- `tests/test_web_club.py`
- `tests/test_bridge.py`
- `tests/test_v3_recovery.py`

## Modified files

- `.gitignore`
- `README.md`
- `pickleball_scoreboard/app.py`
- `pickleball_scoreboard/config.py`
- `pickleball_scoreboard/scoring/game_state.py`
- `pickleball_scoreboard/scoring/scoring_engine.py`
- `pickleball_scoreboard/services/persistence.py`
- `pickleball_scoreboard/ui/scoreboard_screen.py`

The local `.venv-dev` was provisioned with optional web/testing packages. Generated, ignored local artifacts include `artifacts/v3-demo/` (disposable SQLite/demo match), `artifacts/v3-runtime-check/setup.png`, and `artifacts/v3-preview.jpg`. These are not production configuration or release data.

## Database

SQLAlchemy models target MariaDB using PyMySQL; SQLite supports Windows development and automated tests. The additive schema includes players, courts, scoreboard devices, matches, teams, match players, games, match events, check-ins, queue entries, settings, staff accounts and migration records. `--init-db` creates missing tables and migration version 1 without dropping records. Full MariaDB DDL is in `deployment/sql/schema.sql`.

Snapshots project complete results, games, player statistics and queue/court availability transactionally. Unique event UUIDs make retries idempotent. Undo projects the restored engine snapshot and recomputes derived statistics; it does not subtract a guessed point. Reassignment conflicts remain in the backlog for operator reconciliation. Staff passwords are hashed; ADMIN/OPERATOR/VIEWER permissions, HttpOnly sessions and CSRF protect writes. Public viewing is configurable.

## IPC and recovery

An optional authenticated loopback HTTP bridge enqueues timestamped WebCommands into the same queue as GPIO/keyboard. Only the Pygame main thread mutates the central ScoringEngine. UUID receipts distinguish QUEUED/APPLIED/REJECTED; stale commands expire. The atomic JSON snapshot contains applied IDs so restart recovery does not replay uncertain score clicks.

The main thread publishes an in-memory snapshot and enqueues audit events. A background worker persists a SQLite outbox and replays into MariaDB. Network/DB outages do not block local scoring. The core HDMI app runs without Flask/SQLAlchemy if the optional bridge is disabled. The HDMI atomic JSON snapshot remains authoritative for recovery.

## Live updates

Flask streams SSE from bridge state, polling every 100 ms. Nginx disables SSE buffering. Browsers reconnect automatically and show connection state; a database snapshot is marked offline if HDMI cannot be reached. A healthy local network is designed for approximately 500 ms updates, but this is not a measured Raspberry Pi latency guarantee. Preview is presentation-only and independent of MariaDB/Pygame. Python computes game/match-point flags; JavaScript renders them.

## Matchmaking

The configurable, transparent recommender considers waiting time, manual priority, equal court opportunities, rating imbalance, recent partners and recent opponents. It searches all team partitions in a bounded candidate pool, forces overdue players into the selection, and returns reasons and balance metadata. Missing ratings use a documented recreational default. Operators accept/change/skip/manual-select suggestions; the recommender never starts HDMI automatically. Court/device identity is stored explicitly. Additional Pi devices can use authenticated loopback SSH tunnels; there is no unrestricted LAN command listener.

## Installation and development

Follow `README-V3.md` for database creation, environment secrets, admin/device registration, enabling Nginx/web and the optional environment-only HDMI drop-in. These are the entry commands:

```bash
# On the Pi, after staging the complete V3 release:
cd /opt/rallypoint/app
sudo bash scripts/install_web.sh
# Configure /etc/rallypoint/web.env; rerun and initialize as documented.
```

```powershell
# Windows standalone HDMI:
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.main --keyboard --windowed
# Windows web (SQLite defaults):
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web --init-db
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web --create-admin admin
.\.venv-dev\Scripts\python.exe -m pickleball_scoreboard.web
# Full synchronized demo: use the two-terminal environment in README-V3.md.
```

Production HDMI stays:

```bash
SDL_VIDEODRIVER=kmsdrm PYTHONPATH=/opt/rallypoint/app python3 -m pickleball_scoreboard.main
```

## Nginx configuration

```nginx
# Install in /etc/nginx/sites-available/rallypoint; enable after nginx -t.
# Gunicorn and the scoreboard IPC listener remain on loopback only.
limit_conn_zone $server_name zone=rallypoint_events:10m;
server {
    listen 80;
    server_name rallypoint.local _;
    client_max_body_size 64k;

    location /api/events {
        # Keep WSGI threads available for controls instead of unlimited streams.
        limit_conn rallypoint_events 16;
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 60s;
        add_header X-Accel-Buffering no;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 3s;
        proxy_read_timeout 15s;
    }
}
```

## Separate web service

```ini
[Unit]
Description=Rally Point local club web application
After=network.target mariadb.service
# No Requires/PartOf relationship with the HDMI scoreboard.

[Service]
Type=simple
User=rallypoint
Group=rallypoint
WorkingDirectory=/opt/rallypoint/app
EnvironmentFile=/etc/rallypoint/web.env
Environment=PYTHONUNBUFFERED=1
ExecStart=/opt/rallypoint/app/.venv-web/bin/gunicorn --bind 127.0.0.1:8000 --worker-class gthread --workers 1 --threads 32 --timeout 60 --access-logfile - --error-logfile - pickleball_scoreboard.web.wsgi:app
Restart=on-failure
RestartSec=5
NoNewPrivileges=true
PrivateTmp=true
UMask=0077

[Install]
WantedBy=multi-user.target
```


The existing `rallypoint-scoreboard.service` is not replaced. The supplied drop-in only loads the optional web environment; it changes no KMS, TTY, display, executable or boot configuration. Install/update/backup/restore scripts keep credentials outside the release tree and preserve runtime data. Updates stop writers, back up code, validate syntax/tests, perform additive schema initialization and restart with rollback on failure. Before any production upgrade, take a coordinated DB/runtime backup following README-V3.md.

## Verification results — 2026-10-02

- Full pytest suite: **55 passed in 14.12 seconds**, including all 39 original tests.
- Python compile/syntax checks: passed for application, scripts and tests.
- Dependency validation: `pip check` reports no broken requirements.
- JavaScript `node --check`: passed.
- Bash syntax checks: passed for all four new deployment/backup scripts.
- Pygame 2.6.1 / Python 3.12.14 keyboard development smoke run: 120 frames, clean exit, setup screenshot created using SDL dummy drivers.
- Browser acceptance against a separate disposable local database: login, dashboard, check-ins, balanced recommendation, match setup/save/start, main-thread HDMI acknowledgment, web point action and live SSE update verified. Phone-sized layout and desktop preview inspected. Final browser console has no JavaScript errors.
- SQLite persistence/restoration, completion projection, queue/statistics updates, Undo, database 503 handling, durable backlog, restart receipts, CSRF/roles, SSE, and loopback restrictions covered by tests.

## Remaining hardware acceptance and operational limits

1. Verify on the actual Pi: Python 3.13, installed SDL KMSDRM/TTY service, HDMI startup/reboot, RF debounce/long-press behavior, audio, GPIO permissions and one physical press per action. No GPIO hardware was available locally.
2. Run real MariaDB migration/transactions, Gunicorn/systemd and `nginx -t`, backup/restore and staged rollback checks. Windows validation used SQLite and a local Flask development server, not production MariaDB/Nginx.
3. Measure LAN SSE latency and load on the Pi; the supplied service reserves control capacity by limiting concurrent SSE streams to 16. Tune capacity for the club after measurement.
4. Coordinate runtime JSON/outbox and database backups. A sudden power cut can lose an individual audit event in the brief interval before the background worker fsyncs it; recovered authoritative state is republished. Hardware UPS validation remains appropriate for strict audit guarantees.
5. Undo of completion is supported before court/player reassignment. Conflicting historical corrections require operator reconciliation and are retained/logged rather than overwriting newer assignments.
6. The first schema migration initializes V3 tables; future schema versions need explicit additive migration functions. Future official DUPR APIs, QR/PIN, native mobile apps, tournament features and remote-device tunnel provisioning are not implemented.

The local demo web server is available at http://127.0.0.1:8000/preview. Production has not been deployed or restarted.

Update, 2026-10-03: production deployment was subsequently authorized. See
`DEPLOYMENT-PI.md` for the actual Pi configuration, newer test results and access
instructions. The preceding verification section records the initial local build.

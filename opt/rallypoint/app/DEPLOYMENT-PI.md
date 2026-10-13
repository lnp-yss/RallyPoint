# Raspberry Pi deployment — 2026-10-03

Court 2 client `192.168.2.124` was subsequently connected to this central server.
See [CLIENT-COURT2.md](CLIENT-COURT2.md) for its verified configuration and operation.

Pi: `192.168.2.204`, SSH user `rallypoint`.

Web administration: <http://192.168.2.204/>

Live scoreboard: <http://192.168.2.204/live?court=1>

Staff login username: `rallypoint`. The generated initial password is handed over
in the deployment response and stored only on the Pi in root-readable
`/etc/rallypoint/initial-admin.json`. To retrieve it over SSH:

```bash
sudo cat /etc/rallypoint/initial-admin.json
```

## Installed configuration

- Application: `/opt/rallypoint/app`.
- Existing HDMI service: `rallypoint-scoreboard.service`, original unit unchanged.
- Additive drop-in: `/etc/systemd/system/rallypoint-scoreboard.service.d/web-bridge.conf`.
- Web service: `rallypoint-web.service`, Gunicorn on `127.0.0.1:8000`.
- Authenticated scoreboard bridge: `127.0.0.1:8765`.
- Nginx: LAN port 80, SSE buffering disabled.
- Private runtime configuration: `/etc/rallypoint/web.env`, `root:rallypoint`, mode 0640.
- MariaDB: `rallypoint_v3`, dedicated `rallypoint_v3` database account.
- Backup client configuration: `/etc/rallypoint/backup-root.cnf`, root-only unix-socket authentication.

The original `rallypoint` database had an incompatible older schema and zero
matches/games/events. It was backed up and preserved, rather than altered or
replaced. The new application uses the separate V3 schema.

The old web service failed because its working directory did not exist. It was
backed up and replaced with the supplied V3 web service. The original HDMI unit
was compared byte-for-byte with its backup and remains unchanged. Its environment
drop-in supplies the correct `PYTHONPATH`, KMSDRM selection and bridge credentials.
No firmware, boot configuration, desktop or display server was installed/changed.

## Match preservation

The existing match is registered as V3 match 1 on Court 1. Score **4–6**, serving
team **A**, server **1**, and all **13 undo snapshots** were preserved and compared
with the pre-deployment backup. Displayed names were retained. Those legacy name
strings are not automatically split into registered player identities; use roster
selection for subsequent matches so player statistics can be attributed correctly.
The imported start time is estimated from the original save-file timestamp.

The recovery dialog can be resumed from `/control?court=1` with **RESUME SAVED
MATCH**, or using the original remote/keyboard recovery controls. The deployment
resumes the preserved match after the startup check.

## Backups and operation

Pre-deployment backups: `/opt/rallypoint/backups/v3-deploy-20261003/`:

- `app-before.tar.gz`: original application, saved match, history and logs.
- `legacy-database.sql.gz`: original database.
- `scoreboard.service`, `web.service`, `nginx.conf`: original configurations.
- `v3-after.sql.gz`: V3 database backup after successful initialization/recovery.

Backup the configured database:

```bash
cd /opt/rallypoint/app
sudo bash scripts/backup_database.sh /etc/rallypoint/backup-root.cnf \
  /opt/rallypoint/backups/club-$(date +%Y%m%d_%H%M%S).sql.gz rallypoint_v3
```

For coordinated recovery, stop both application services and back up `data/`
alongside the database. Follow README-V3.md before any destructive restore. For
this deployment, pass `rallypoint_v3` as the optional final restore argument.

Diagnostics:

```bash
sudo systemctl status rallypoint-scoreboard.service rallypoint-web.service --no-pager
sudo journalctl -u rallypoint-scoreboard.service -b -n 50 --no-pager
sudo journalctl -u rallypoint-web.service -b -n 50 --no-pager
sudo nginx -t
```

Do not run the legacy desktop installer or copy `app.py` over `ui/dialogs.py`.

## Verification

- Full suite: **57 passed on the Pi**, Python 3.13.5; **57 passed on Windows**, Python 3.12.14.
- Actual HDMI driver: **KMSDRM**, resolution **1920×1080**.
- GPIO initialization: **GPIO ONLINE**.
- MariaDB projection: recovered match, 4–6 game record and audit events written.
- Authenticated recovery command acknowledged in **298 ms** in one local measurement.
- Login, CSRF rejection, private IPC authentication and SSE through Nginx verified.
- Nginx configuration and Python/shell syntax checks passed.
- Scoreboard, web, Nginx and MariaDB enabled for boot.
- Reboot performed and verified: boot at **09:35 on 2026-10-03**, all four services
  active automatically. KMSDRM/GPIO and recovery checked again after reboot;
  recovery acknowledgment measured **362 ms**, score still **4–6–1**.

RF transmitters, contact wiring and audible output still require a physical
button/audio check; successful GPIO initialization cannot verify actual relay
behavior. The original HDMI service has an inherited `Envirnoment` spelling
warning; the additive environment file supplies the correct `PYTHONPATH`, so
this warning does not prevent startup. The base unit was deliberately preserved.

## Deployment fixes included in the source

The optional database worker now imports the web package without importing Flask;
the Flask factory lives in `web/application.py` and loads lazily. This keeps the
system-Python HDMI process independent of the Flask virtual environment. Resuming
a saved match now saves its receipt and immediately publishes the new view.
Hardware diagnostics are included in the bridge state. Backup/restore scripts
accept the configured database name instead of assuming `rallypoint`.

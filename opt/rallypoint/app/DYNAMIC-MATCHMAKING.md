# Rally Point: dynamic courts and session matchmaking

## Operator quick start

1. Sign in as ADMIN or OPERATOR. Open **Court Settings** in the navigation (`/settings/courts`). Apply the enabled court count, or enable/disable individual records. Increasing the count creates missing courts; reducing it disables records without deleting history. Finish/cancel an occupied court before disabling it. Existing court database IDs and numbers are preserved, including gaps in court numbers.
2. Register each new Pi under **Admin Settings → Scoreboard devices** (admin only). Use the court's **database ID**, a unique device name, hostname and a distinct loopback bridge/tunnel URL. Courts without a recent heartbeat remain OFFLINE and receive no assignments.
3. Open **Check-in**. Choose **Join solo** or **Join as fixed team** and select registered players. Already waiting/assigned/playing players cannot join again. A TEAM always contains exactly two players.
4. Open **Queue** to move, prioritize or remove whole entries, pair two waiting solos into a temporary team, or explicitly confirm breaking a temporary team. Registered fixed teams cannot be split; remove the whole entry and check the players in separately if their partnership ends.
5. Open **Matchmaking Settings** (`/settings/matchmaking`). Name the session, select its enabled courts, matching mode and scoring rules, then save. Changing the session name creates a new session. Existing playing/reserved matches keep their original mode/settings.
6. **Force matchmaking run** reserves as many valid matches as available online courts and players permit. With automatic assignment disabled, review the explanation and press START MATCH on each reservation. Cancel or move a reservation before starting; STARTING/uncertain deliveries require live-state reconciliation from Match setup.
7. To run continuously, enable **Automatically assign and send matches**. The host-only service reevaluates every five seconds, including after completion. It reserves transactionally, then sends through the existing authenticated Pi bridge. MANUAL and future KING_OF_COURT never automatically generate matches, even on a forced run.

Automatic assignment defaults to **off on upgrade**. No existing game is automatically started by the migration. Scoring, GPIO, Undo, Casual Play, the public Preview and recovery remain independent.

## Matching modes

| Mode | Behavior |
| --- | --- |
| Fair / Balanced | Default. Weighted waiting fairness, skill balance, partner/opponent variety and games played. |
| Queue | Earliest eligible queue entries first; partnerships optimize balance among that selection. Fixed teams remain whole. |
| Random | Server-side cryptographic system randomization of eligible complete units and partnerships, retaining maximum-wait safety. |
| Skill-based | Minimize difference between mean team ratings, then use weighted variety/fairness as a tie breaker. Preferred difference is exposed separately from the initial allowed difference. |
| Winners stay | Winning pair remains associated with its court; select a challenger. At the consecutive-win cap, winners return to rotation. |
| Manual | No automatic formation. Use Match setup to select players/court and scoring rules. Shared availability and fixed-team checks still apply. |
| King of Court | Session mode/court levels stored. **Advanced promotion/demotion is future work; automatic formation is unavailable.** |

Scoring mode (Side-Out/Rally) determines points and serve transitions. Matchmaking mode determines who plays and where. They use separate engines.

## Candidate selection and fairness

The engine evaluates four SOLO players, TEAM plus two SOLO players, or two TEAM entries. It never creates a partnership containing half a fixed team. It considers a bounded pool of 12 players by default (operator-configurable 4–20), retaining entire teams at the boundary. This bounds combination search, not the number of courts. It recomputes the pool after each reservation, so a seven-court configuration can reserve seven disjoint matches.

The default weights are waiting 35, skill 30, partner 15, opponent 10 and games 10. Weights are normalized to 100 if changed. Waiting contribution grows with waiting time; maximum-wait players are pulled into the candidate pool even if beyond the normal search boundary. The oldest overdue eligible player takes precedence over quality. No algorithm can form a match without enough eligible whole units.

Team rating is the arithmetic mean of its player ratings; unrated players use configurable 3.0. Difference ≤0.25 is excellent, ≤0.50 good, ≤0.75 acceptable. Recent completed-match history penalizes repeated SOLO partnerships and opponents; fixed teams are exempt from partnership penalties. Fewer games today increases the games component. Waiting preferences gradually relax the skill limit after 15 minutes. At 20 minutes, maximum-wait safety overrides skill preferences. Poor candidates wait up to a configurable 120-second court grace period before relaxing quality constraints. Force run also respects grace; set grace to zero explicitly if immediate poor-match reservations are intended.

Per-match explanations include normalized component contributions, team ratings, imbalance, longest wait and reasons. They appear on operator reservation cards and are stored in `matchmaking_json`, never on the public TV. Search is bounded and heuristic, rather than a globally optimal schedule for the entire queue.

## Architecture and files

Existing Pygame/scoring engines and IPC remain authoritative. Flask/SQLAlchemy manages the central queue and session. `RotationService` owns validation and transactional reservation; pure strategy classes choose candidates. Network dispatch occurs after commit and never holds database locks while waiting on a Pi.

Created:

- `web/matching/settings.py`, `candidates.py`, `strategies.py`, `__init__.py`: settings, whole-unit candidate generation and separate strategy classes.
- `web/rotation_service.py`: dynamic courts, grouped check-in, session configuration, reservations and winner holds.
- `web/rotation_routes.py`, `static/rotation.js`: operator APIs/pages.
- `web/scheduler.py`, `deployment/systemd/rallypoint-matchmaking.service`: host worker.
- `scripts/upgrade_dynamic_host.sh`, `upgrade_dynamic_client.sh`: deployment without replacing working display units or data.
- `deployment/sql/migration_003.sql`: additive MariaDB structure; Python CLI performs data backfill.
- `tests/test_dynamic_matchmaking.py`, `test_rotation_routes.py`: formation, concurrency, lifecycle, migration and permissions.
- `DYNAMIC-MATCHMAKING-PLAN.md`: actual pre-implementation inspection/risks.

Modified: `web/models.py`, `database.py`, `club_service.py`, `application.py`, `state_service.py`, `__main__.py`, `static/club.js`, `templates/page.html`, `app.py`, `deployment/sql/schema.sql`, `deployment/web.env.example`, `scripts/install_web.sh`, and existing `test_web_club.py` fixtures for heartbeat-aware availability.

## Migration 3

Existing tables receive only additive columns:

- courts: `enabled`, `available_since`, `level`.
- player_queue: `group_id` (existing per-player rows become members of SOLO groups).
- matches: `matchmaking_mode` (old rows `LEGACY_WEIGHTED`), `session_id`, `matchmaking_json`, `queue_groups_json`.

New tables: `queue_groups`, `play_sessions`, `scheduler_lock`, `winner_holds`. `SchemaMigration` version 3 marks successful data backfill. Existing IDs, player statistics, match results, settings, saved JSON and device registrations are retained. Fresh schemas have foreign keys for new columns; legacy added columns are application-validated because introducing constraints against old data is a separate migration concern.

`Database.migrate()` inspects each actual column before altering it. MariaDB implicitly commits DDL, so interrupted upgrades can safely be rerun. The CLI wraps the SOLO-group backfill/version marker transactionally. Run one migration process at a time while web/scheduler services are stopped. Do not run the initial `schema.sql` as an upgrade: use `python -m pickleball_scoreboard.web --init-db`.

All assignment writers, completion projection and group controls acquire the singleton scheduler row using `SELECT ... FOR UPDATE` in MariaDB. SQLite tests use `BEGIN IMMEDIATE`. Players are also locked and revalidated before claiming, and the unique queue player key prevents duplicate membership. A failed run rolls back all reservations in that run. Legacy PREPARED records remain drafts; claiming happens on Start. Cancelling an old draft cannot release players assigned to another match.

Offline is derived from heartbeat age (default 30 seconds). It never clears a court's active match. Casual games exclude their court from club assignment. Active match Undo is permitted while its players/court are still available; after reassignments the projection requires operator reconciliation instead of silently stealing players from a newer match. When automatic assignment is enabled, finish-result Undo should therefore be done promptly, before the next scheduling cycle, or pause automatic matching first.

## New APIs

All endpoints require ADMIN/OPERATOR; POST also requires the existing CSRF header. Existing scoring APIs are retained.

| Endpoint | Purpose |
| --- | --- |
| GET `/api/rotation` | Active session, settings, dynamic courts and dashboard |
| POST `/api/rotation/settings` | `{name, settings, court_ids}`; optional `new_session:true` |
| POST `/api/rotation/courts` | `{count:6}` or `{id,name,enabled,level,mark_available}` |
| POST `/api/rotation/checkin` | `{player_ids:[id]}` SOLO or `[id,id]` TEAM |
| POST `/api/rotation/groups/<id>` | `{action:up/down/priority/remove/pair/break,partner_id?,confirmed?}` |
| POST `/api/rotation/run` | Reserve valid matches across available courts |
| POST `/api/rotation/manual` | `{court_id,players:{A:[id,id],B:[id,id]}}`; uses session scoring, reserves then starts |
| POST `/api/rotation/matches/<id>` | `{action:start/cancel/reassign,court_id?}` |

`/api/courts` now exposes derived connectivity/status. Display numbers remain separate from IDs. Match payloads extend the current `START_MATCH` protocol with registered player IDs/names and matching mode; the existing scorer still receives its original `settings` object.

## Upgrade the host Pi (192.168.2.204)

Use a tested release archive containing source/tests/scripts/deployment/docs, excluding `data`, `logs`, environments and credentials. Finish current managed and casual games before restarting HDMI services. The host script refuses active managed reservations and runs the installed verified backup service first.

From Windows PowerShell:

```powershell
scp artifacts/deployment/dynamic-matchmaking-release.tar.gz rallypoint@192.168.2.204:/home/rallypoint/
ssh rallypoint@192.168.2.204
```

On the host:

```bash
mkdir -p /home/rallypoint/dynamic-release
tar -xzf /home/rallypoint/dynamic-matchmaking-release.tar.gz -C /home/rallypoint/dynamic-release
sudo bash /home/rallypoint/dynamic-release/scripts/upgrade_dynamic_host.sh /home/rallypoint/dynamic-release
sudo systemctl status rallypoint-web rallypoint-scoreboard rallypoint-matchmaking --no-pager
sudo journalctl -u rallypoint-matchmaking -n 30 --no-pager
```

Set `RALLYPOINT_COURT_ID=1` in the host bridge environment (currently `/etc/rallypoint/web.env`). This is the host's database court ID; read it from Court Settings, rather than assuming it equals the display number. Existing device ID remains 1. Do not replace bridge secrets.

If installing on another host, first perform the existing README-V3 installation and backup-service setup. Use the existing venv and requirements; this upgrade adds no runtime dependencies. Install/enable the matchmaking unit only on the central host.

## Upgrade existing Court 2 (192.168.2.124)

Upgrade host/schema first. Preserve the working Court 2 X11 service, SSH tunnels, bridge secrets and recovery files.

```powershell
scp artifacts/deployment/dynamic-matchmaking-release.tar.gz rallypoint@192.168.2.124:/home/rallypoint/
ssh rallypoint@192.168.2.124
```

```bash
mkdir -p /home/rallypoint/dynamic-release
tar -xzf /home/rallypoint/dynamic-matchmaking-release.tar.gz -C /home/rallypoint/dynamic-release
sudo bash /home/rallypoint/dynamic-release/scripts/upgrade_dynamic_client.sh /home/rallypoint/dynamic-release
sudo systemctl status rallypoint-scoreboard rallypoint-tunnel --no-pager
```

In the environment file referenced by `systemctl cat rallypoint-scoreboard`, retain its existing `RALLYPOINT_DEVICE_ID=2`, and add `RALLYPOINT_COURT_ID=2` (verify database ID). Restart the scoreboard. Court Settings should show a fresh heartbeat. Scoring works offline; the durable outbox retries synchronization when the tunnel returns.

## Clone Court 2 to Court N without reinstalling dependencies

1. Shut Court 2 down cleanly and clone its SD using Raspberry Pi Imager or an image/restore tool. Do not boot a clone onto the club network with the original device identity/tunnel running.
2. On the clone, stop/disable `rallypoint-scoreboard` and `rallypoint-tunnel` while configuring. Set a unique hostname, e.g. `sudo hostnamectl set-hostname rallypoint-court3`; update the old hostname entry in `/etc/hosts` and reboot. Use a unique DHCP reservation/IP for management; IP is never the authoritative court identity.
3. In host Court Settings enable/create the desired court. Note its database ID. Admin Settings registers one new device against that ID, with unique name/hostname and a **new** loopback tunnel port, e.g. `http://127.0.0.1:8767`. Note the new database device ID; it may differ from the court number.
4. Edit the clone's existing environment files. Change `RALLYPOINT_DEVICE_ID`, `RALLYPOINT_COURT_ID`, and court name. Keep the local bridge port 8765 and local DB forward port 3307 on each separate Pi; choose a unique host reverse port for this clone. Change the SSH destination/server address if the hub differs. Existing communication uses the SSH host setting and database URL, rather than a second incompatible HTTP server URL.
5. Generate a new SSH key for the clone and authorize it on the hub with the same restricted forwarding pattern as the existing client, allowing its chosen reverse port and the database destination. Do not reuse Court 2's reverse-port restriction. Preserve encryption/host-key verification. Review the existing `rallypoint-tunnel.service` and its environment/key file paths to apply this to the installed configuration.
6. The clone must not recover Court 2's match/outbox. After making a local backup, archive (move into a timestamped directory) its copied `current_match.json`, `bridge_match.json` and bridge SQLite outbox, including any `-wal`/`-shm` companions, **while services are stopped**. Keep scoring settings but change court name. Locate the actual SQLite filename from `scoreboard_bridge.py`/the installed data directory before moving it. Never purge the original Court 2 data or restore old device events under the new ID.
7. Enable/start the clone's existing display, tunnel and scoreboard services. Do not install the host matchmaking/web service on the client. Check the unique device/court/hostname heartbeat in Court Settings before assigning a real match.

Unique values per clone: hostname, device database ID, court database ID, display court name, management IP/DHCP lease, SSH key and host reverse bridge port. Shared/host configuration: central database destination, host SSH address, compatible authenticated bridge token. Keep credentials out of release archives. No dependency reinstallation is needed for an up-to-date cloned image.

## Concrete Court 3 clone configuration example

These example IDs assume Court Settings reports database court ID 3 and Admin Settings reports device ID 3. Substitute the actual IDs and allocated port.

On the clone, with services stopped:

```bash
sudo systemctl disable --now rallypoint-scoreboard rallypoint-tunnel
sudo hostnamectl set-hostname rallypoint-court3
# Edit /etc/hosts and /etc/rallypoint/client.env:
# RALLYPOINT_DEVICE_ID=3
# RALLYPOINT_COURT_ID=3
# Keep the existing bridge/database secrets and local forward URL.
sudo install -m 0640 -o root -g rallypoint /opt/rallypoint/app/deployment/tunnel.env.example /etc/rallypoint/tunnel.env
sudoedit /etc/rallypoint/tunnel.env
# Set host address/user, unique SSH key path and RALLYPOINT_REVERSE_PORT=8767.
sudo install -m 0644 /opt/rallypoint/app/deployment/systemd/rallypoint-tunnel.service /etc/systemd/system/rallypoint-tunnel.service
# Archive the copied key before creating a distinct clone key.
mv ~/.ssh/rallypoint-hub ~/.ssh/rallypoint-hub.court2-copy
mv ~/.ssh/rallypoint-hub.pub ~/.ssh/rallypoint-hub.court2-copy.pub
ssh-keygen -t ed25519 -N '' -f ~/.ssh/rallypoint-hub -C rallypoint-court3
cat ~/.ssh/rallypoint-hub.pub
```

Add the new public key on the hub as a **new** authorized_keys line, keeping Court 2's key intact:

```text
restrict,port-forwarding,permitopen="127.0.0.1:3306",permitlisten="127.0.0.1:8767",command="/bin/false" ssh-ed25519 NEW_CLONE_PUBLIC_KEY rallypoint-court3
```

The clone inherits the hub host key pin; verify it against the known hub fingerprint if the hub address changed. Do not disable StrictHostKeyChecking. Optional address-specific keepalive rules in `/etc/ssh/rallypoint-courts.conf` can be extended to the new client's DHCP reservation and verified with `sudo sshd -t` before reloading SSH.

Archive copied court runtime state while services remain stopped:

```bash
archive=/opt/rallypoint/backups/cloned-court2-state-$(date +%Y%m%d-%H%M%S)
sudo mkdir -p "$archive"
for name in current_match.json bridge_match.json web_outbox.sqlite3 web_outbox.sqlite3-wal web_outbox.sqlite3-shm; do
    if sudo test -f "/opt/rallypoint/app/data/$name"; then
        sudo mv "/opt/rallypoint/app/data/$name" "$archive/"
    fi
done
# Set court_name in data/settings.json to COURT 3; keep scoring/GPIO settings.
sudo systemctl daemon-reload
sudo systemctl enable --now rallypoint-tunnel rallypoint-scoreboard
sudo systemctl status rallypoint-tunnel rallypoint-display rallypoint-scoreboard --no-pager
```

Current Pi display services are preserved. The new generic tunnel template is provided for clones; upgrading existing Court 2 does not replace its installed working tunnel unit.

## Verification and rollback

Local:

```powershell
.\.venv-dev\Scripts\python.exe -m pytest -q
.\.venv-dev\Scripts\python.exe -m compileall -q pickleball_scoreboard
```

Test on isolated data first: 1/2/3/5/7 courts, mixed/fixed teams, too few players, non-contiguous numbers, offline/casual/disabled courts, concurrent requests, long waits, history variety, cancel/reassign, completion/Undo, winners-stay cap, session changes and migration reentry. Do not seed fake players/results into production. Remote RF noise, actual new Court 3 hardware, power interruption and large-display visual readability still require physical checks.

Before upgrading, retain the verified backup directory and local source archive. Stop web, scheduler and affected scoreboard services for rollback. Restore the previous application tree and its matching database/configuration using the verified backup's manifest/restore guide in `SEVEN-IMPROVEMENTS.md`; restore saved match JSON/outbox from the same checkpoint if needed. If no new matches were recorded after migration, old source can run against the additive schema, but new grouped queue/winner/session semantics require restoring the old database checkpoint as well. Never DROP new columns against a live database or restore an old dump over newer results without preserving/exporting those results first. Restore original display units/environment and restart the original services; disable the new matchmaking unit when running the previous version.

Remaining scope: advanced King of Court promotion/demotion, globally optimized whole-session schedules, performance tuning for much larger player databases, and a guided clone/provisioning wizard. Existing secure SSH forwarding remains the client transport.

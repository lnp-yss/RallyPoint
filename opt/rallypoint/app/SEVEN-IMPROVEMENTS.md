# Seven improvements: operation and recovery

The current application was backed up before changes:

- Local: `artifacts/backups/application-before-seven-20261003-152619.tar.gz`
- Central Pi: `/opt/rallypoint/backups/before-seven-20261003-152623/` (application, configuration, systemd units, MariaDB SQL)
- Court 2 Pi: `/opt/rallypoint/backups/before-seven-20261003-152721/` (application, configuration, systemd units)

## Court modes and remote setup

TV setup has **Casual Play** and **Club Managed**. Casual matches run locally without registration and still appear in Preview. They do not contribute to registered-player rankings. Club Managed waits for an operator assignment; assigned matches select this mode automatically. A live match cannot be overwritten by a server start command, even when replacement is requested. Finish the current match first. The Courts page marks an ongoing casual court as busy once its heartbeat reaches the database.

On either remote, setup buttons are A = next field, C = previous field, B = change option / enter text editor, D = request Start Match. Confirm Start with A; cancel with B or C. In the text editor, B cycles a character, A appends it, C erases the last character, and D finishes editing. Names are optional; defaults allow fast setup. Hold D for the existing reset dialog, then C opens setup. Match settings and names can be entered fully with the RF remotes. Keyboard arrows/Tab/text entry remain supported.

The TV shows its court mode, club connection state, and queued event count. Scoring, Undo, and atomic local saves continue during a network outage. Five-second heartbeats update the central last-known snapshot, including casual matches. Preview retains the last score and labels offline courts with the time since the last update. Event projection resumes in order when connectivity returns.

## Court QR codes and optional phone scoring

Open **Courts** for a printable/saveable QR SVG and **COURT PHONE VIEW** link for each court. Scanning opens `/court/<court database id>` on the central server. Phones must be on the club network. The default is view-only; no staff login is required when public live viewing is enabled.

Staff uses **Controls?court=<id> → Court phone scoring** to enable a six-digit PIN. A player enters it on the phone and receives a six-character request code. Staff checks that code, refreshes requests, and approves the phone. Requests expire after 10 minutes; approved access expires after one hour. Staff can revoke a phone or disable all phone scoring. Changing the PIN revokes existing grants. PINs are hashed, attempts are limited, and commands require CSRF, an approved court-scoped session, and the latest score revision. Phone scoring permits point/lost-rally/Undo/timeout/resume only, not reset, winner declaration, assignments, or staff administration. No phone grants are enabled by deployment.

## Waiting-player display

Preview shows Up Next and On Deck, with recommended and operator-confirmed matches explicitly distinguished. A confirmed prepared/starting match takes precedence over recommendations. On Deck recommendations exclude Up Next players. Recommendations remain advisory until an operator creates an assignment.

## Audit and official result correction

Settings has an **Operator audit** listing successful staff actions with account identity and safe details. Passwords, PINs, hashes, and connection secrets are excluded. Accepted asynchronous commands are marked QUEUED; the match timeline records the applied state and staff identity when the court confirms the command. RF events are identified as court remote/system.

In **History → completed match**, staff can correct the final game's scores and official match winner, with a mandatory reason and confirmation. Earlier games are retained. If the match is still on HDMI, correction goes through the scoring engine and Undo history. An archived result is corrected transactionally without replacing the newer court match. Reload checks reject stale history changes; offline or pending-sync courts must reconnect/sync first. Rankings are recomputed from one record per completed match, so corrections do not add another win or match. Player totals and the timeline update too.

## Automatic backups and tested restore

Both Pis run `rallypoint-backup.timer` daily at 04:00 (with up to five minutes of jitter and catch-up after shutdown). Files are stored under `/opt/rallypoint/backups/automatic/rallypoint-<UTC timestamp>/`, restricted to root. Fourteen verified automatic backups are retained. The initial before-seven backups are preserved separately.

Each automatic backup contains application source, settings/current-match data, consistent SQLite snapshots, `/etc/rallypoint` configuration, Rally Point launcher scripts, and systemd units. The central Pi also contains a transaction-consistent MariaDB dump. Installed virtual environments are excluded; requirements files are included. SHA-256 manifests are checked. Every run extracts into temporary storage, checks restored SQLite databases, and, on the central Pi, imports SQL into a uniquely named scratch database and checks required tables before dropping only that scratch database. Production data is not replaced during verification.

Manual backup: `sudo systemctl start rallypoint-backup.service`

Status: `sudo systemctl status rallypoint-backup.timer rallypoint-backup.service`

Logs: `sudo journalctl -u rallypoint-backup.service --no-pager`

Verify an existing central backup: `sudo python3 /opt/rallypoint/app/scripts/backup_scoreboard.py --verify /opt/rallypoint/backups/automatic/rallypoint-<timestamp> --database rallypoint_v3`

For a real recovery, stop the affected scoreboard/web services first, verify the selected backup, extract it into a temporary directory, restore `app/` to `/opt/rallypoint/app`, restore `config/` to `/etc/rallypoint` with original ownership and private permissions, restore `bin/` launchers to `/usr/local/bin`, restore required systemd units, and recreate dependencies if needed. On the central server, import `club.sql` into `rallypoint_v3` only when intentionally rolling back club data. Run `systemctl daemon-reload` and restart the original display/tunnel/scoreboard/web services. Restoring older data discards newer changes; preserve a fresh backup before doing so. Copy backups to separate storage periodically to protect against SD-card failure.

Daily backups are local and require no internet. QR generation adds one small dependency, `qrcode`, to web requirements only; HDMI scoring still needs no web dependency.

## Deployment verification (2026-10-03)

- Central web + HDMI/GPIO service active; Court 2 X11 display + HDMI/GPIO service active.
- Both daily backup timers enabled, next run around 04:00 club time.
- Existing Court 1 match #7 retained its 1–0 score; Court 2 match #8 retained the completed 11–0 result. Both bridges report online with zero pending events after recovery.
- Local final suite: 91 passed, 1 skipped (Unix FIFO test unavailable on Windows); Python compilation passed.
- Central full suite: 91 passed before the final recommendation regression was added. Final web/backup subset: 29 passed, including that regression and the Unix FIFO check.
- Browser verified three-court Up Next/On Deck/rankings at 1280×720 and 1920×1080, public phone view, and deployed live Preview. No browser errors recorded.
- Central SQL backup restored successfully to an isolated temporary MariaDB database; client SQLite/application restore checks passed. The original before-seven SQL restored successfully with 8 matches, 2 devices, and 2 staff accounts.
- Physical RF setup navigation, scanning a printed QR with a phone camera, and recovery from an actual power cut remain hardware checks for the club. No synthetic match results or player assignments were written to production during testing.

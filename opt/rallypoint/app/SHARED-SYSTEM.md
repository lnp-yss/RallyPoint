# Shared club system — local network edition

The main Rally Point workspace stays at http://192.168.2.204/. Its existing MariaDB, staff, players and two court devices remain in place. The club directory is http://192.168.2.204/clubs. Each new club has a separate database, login cookie, session secret and device token at /clubs/<slug>/. Numeric player/court/match IDs are local to that workspace.

## Add another club

1. Sign into the main workspace with an existing ADMIN account.
2. Select **Switch club**. Complete **Add a club**: club name, URL slug, initial administrator username and a password of at least 12 characters.
3. Open the new workspace and sign in using that club's credentials. Give that URL to its staff. Staff accounts, device registration, player records, queue, matchmaking, results and preview are scoped to this club.
4. Use **Players → Import club roster** to upload or paste CSV. Required header: `display_name`. Optional headers: `first_name,last_name,nickname,gender,skill_rating,rating_type`. Rating types: Manual, DUPR, Self-rated. Ratings: 1–8, or blank. Duplicate display names (case insensitive) are skipped. An invalid row prevents the entire import. Maximum 500 rows and 64 KB per request. Players remain ACTIVE until checked in.
5. Use **Check-in**, **Court Settings**, and **Matchmaking Settings** for open play. Existing rotation modes work independently within each club. Enable automatic assignment only after the club's devices are configured and online.

Public preview can be disabled during creation; private clubs require sign-in for scores and are hidden from the public directory. Host administrators can see all clubs; club administrators manage their own workspace. Club creation is reserved for administrators of the original host workspace.

## Configure additional Raspberry Pi devices

A fresh club deliberately inherits no physical devices. Never register an existing Rally Point device in a second workspace while it is still in use by Rally Point.

Register its device in that club's **Admin Settings**. Set its Local IPC URL to a unique host-loopback SSH reverse-tunnel port, for example `http://127.0.0.1:8767`. Use the existing client installation and reverse tunnel pattern, with a distinct port for every device. The bridge still listens only on the client Pi's 127.0.0.1:8765. Keep RF GPIO configuration local to that Pi.

Click **DOWNLOAD PRIVATE DEVICE CONFIG** for the registered device. Install the downloaded file as `/etc/rallypoint/client.env`, owned root:rallypoint, mode 0640. It contains the workspace's `RALLYPOINT_HUB_URL`, bridge token, court ID and device ID. Remove the old `RALLYPOINT_DATABASE_URL`; HTTP synchronization takes precedence when HUB_URL is set. Preserve any required display settings in the existing service wrapper. Update the client's scoreboard source to this release before enabling HTTP synchronization, then restart its scoreboard when idle.

The device posts authenticated updates to `<workspace>/api/device-events` on a background worker. Outbox events survive outages and retries; its scoring engine and RF inputs continue locally. Server-to-device commands use the authenticated reverse tunnel. Do not open the bridge port or MariaDB to the network. Tokens are shared within one club; treat downloaded configuration as a password. A stolen club token must be rotated on every device in that club by an administrator.

The main club's two existing devices continue using their existing MariaDB synchronization and tokens. Their services are not restarted by this web-only deployment.

## Tournaments

Open **Tournaments**, create an event, and register singles or fixed doubles entries from the club roster. Choose Round robin or Single elimination. Maximum 32 entries. Single elimination requires 2, 4, 8, 16 or 32 entries; byes and arbitrary-sized elimination brackets are not supported in this release. Entry order is seeding order. Round robin accepts 2–32 entries and schedules each pairing once.

Set tournament courts to manual matchmaking / turn automatic assignment off for that session, so open-play rotation cannot claim them. Generate fixtures, then prepare a ready fixture on an online available court. **OPEN MATCH CONTROLS** opens Match Setup, whose prepared-match list lets staff start that exact match. The authoritative HDMI result supplies fixture winners. Select **REFRESH RESULTS** to update standings and advance the bracket. Wins are displayed; tied round-robin entries remain tied without an automatic tiebreak or champion declaration.

Undo/result correction refreshes the event result. If a previous winner changes after a downstream fixture was assigned, the event shows NEEDS REVIEW and blocks further assignment. Staff must resolve that bracket with a host administrator; it never silently changes players in an assigned match. Cancellation/replay of fixtures, automatic seeding, pools/playoffs, payments and entry fees are not implemented.

## Storage and backups

Legacy database: MariaDB `rallypoint_v3`. Workspace registry: `<DATA_ROOT>/data/shared_registry.sqlite3`. New databases: `<DATA_ROOT>/data/clubs/<slug>/data/club.db`. The existing backup service uses SQLite online backups for both registry and workspace databases, verifies restored integrity, and backs up MariaDB separately. Restore the registry and all workspace databases together with application/configuration files. Registry and backups contain private tokens/session keys.

This first shared deployment is for trusted LAN use. It does not expose the server to the internet. HTTPS, domain setup, internet hosting, device token rotation UI, club rename/archive UI and cross-club player transfers need a later deployment. Rosters are intentionally independent; CSV is the onboarding/transfer method. A Pi 4 with 4 GB RAM can serve a small club installation, but capacity for many simultaneous clubs/spectators requires workload testing. Live scoring remains local to each court Pi.

Run validation: `.venv-dev/Scripts/python -m pytest -q` (Windows), or `.venv-web/bin/python -m pytest -q` (Pi). The new suite tests workspace cookies/data isolation, provisioning, CSV atomicity, device authentication, workspace reload, tournaments, undo conflicts and HTTP backlog recovery.

For local development, set `RALLYPOINT_DATA_ROOT` to a separate test directory and run `python -m pickleball_scoreboard.web --init-db`, then `--create-admin <username>`. Launch `python -m pickleball_scoreboard.web --port 8000` and open `http://127.0.0.1:8000/clubs`. The development server now mounts the shared workspaces too. Production continues using Gunicorn and Nginx.

## Club selection at login

Open http://192.168.2.204/login, choose your club, then enter that club’s staff credentials. Changing the club opens its isolated login page and obtains the correct session/CSRF token before sign-in. Existing direct club login links continue to work. The selector lists club names only, including clubs whose live scores are private; it does not expose rosters or accounts. Host Administration is a separate choice and requires a root host administrator account. Choosing a club does not move any physical Pi.

Deployed 2026-10-06 after verified backup `/opt/rallypoint/backups/automatic/rallypoint-20261006-054043-079339`. Browser navigation and nine local shared-system tests passed.

## Host administrator access across clubs

Sign in to Host Administration once, choose **Switch club → Open club**, and manage any club directly. The header identifies the host username and Host Admin. Club audit records identify actions as `host-admin:<username>`. This access is validated against the root account on every request; disabling or demoting it revokes access immediately. No local club staff session or copied account is created. Sign out in a delegated club signs out the host account. Club accounts remain isolated, and all state-changing requests still require that workspace’s CSRF token.

# Move a physical court between clubs

Sign in with the main host administrator account at http://192.168.2.204 and choose **Move courts**. Direct page: http://192.168.2.204/court-manager.

1. Find the physical TV/Pi, such as **Court 2 HDMI**.
2. Select the destination club and its court number (for example **Dink With Grace**, **2**).
3. Click **Review move**, check the source and destination, then **Confirm move**.
4. Wait for **COMPLETE**, then choose **Open destination club**.

The system creates the destination court/device when necessary, updates the Pi, reconnects it, verifies synchronization, and enables the destination court. Its TV and RF controls now belong to the selected club. Move it back through the same page. No configuration files, tokens, SSH commands, or device registration forms are needed for the two paired Pis.

## Current assignments

- Court 1 HDMI / 192.168.2.204: Dink With Grace, Court 1.
- Court 2 HDMI / 192.168.2.124: Rally Point, Court 2.

Installing this feature did not move Court 2. Choose its destination when ready.

## Before a move

Finish the current match and allow pending score updates to synchronize. Moves are blocked for occupied/reserved courts, unfinished matches, disconnected devices, or pending score uploads. Players, accounts, match history and rankings stay within their original club. Each club/court profile retains its own saved scoreboard state. A new profile starts with clean team/player names and Casual Play.

Only the main host administrator can move physical devices. A club administrator cannot move another club's Pi. The confirmation page shows what will change. Failed moves attempt to restore the previous configuration; **RECOVERY NEEDED** offers an explicit recovery retry. Do not create a second move while recovery is pending.

## Installation and recovery details

The paired devices use a root-owned, restricted JSON management agent. Court 2 accepts the host's management SSH key only through its forced command, without shell access or forwarding. The dedicated `rallypoint-court-manager` service performs moves outside the scoring/web request loop. Score processing continues normally until the selected idle court enters its short maintenance/restart period.

Device backups are stored under `/opt/rallypoint/backups/court-switches/`. Full host backups include the shared registry, club databases, configuration, management service, restricted agent and its sudo rule. A new third Pi requires initial installation and secure pairing; this page manages already paired physical devices.

Operational checks: `systemctl status rallypoint-court-manager` and `journalctl -u rallypoint-court-manager`. Keep `/etc/rallypoint/court-manager` and backup archives private because they contain credentials. Moving a court preserves the existing fixed bridge/tunnel addresses.

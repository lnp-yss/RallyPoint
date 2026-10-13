# Ready to Play — HDMI pre-match review

Added to both paired Pis on 2026-10-06. Open New Match Setup in Casual Play, configure teams and settings, then press D or the on-screen START button. The full-screen Ready to Play review shows club/court, team names, players, first serving team, game type, scoring mode, target/win-by and match format.

Press D again to start, or C to return to setup. Short D in this review confirms start without using a timeout; holding D does not start a match. Existing A=Yes and B=No confirmation controls still work. Keyboard development controls include Y/N and 4/0 for D; mouse buttons work too. Existing reset, recovery, scoring, and operator-assigned start behavior are unchanged. Club Managed assignments also open Ready to Play. The operator uses SEND TO COURT to deliver the match; players then press D, or the operator uses START READY MATCH / RESUME in Controls. The court remains reserved, scoring is blocked and the match clock has not started while the review is pending. C goes back without releasing the assignment; D reopens review. Assigned settings remain controlled by the operator. Waiting assignments recover directly to Ready to Play after a restart.

C preserves the previous match and leaves the edited settings available. Only confirming start creates the replacement match. Rendering uses responsive Pygame primitives and cached fonts, without new dependencies or image assets. Checked at 1920×1080 and 1280×720.

Local application tests: 12 passed. Compile checks passed. Installation checks both devices are idle before restart and verifies their bridges reconnect. Hardware RF button presses were not injected into production matches.

Before deployment full host backup: `/opt/rallypoint/backups/automatic/rallypoint-20261006-070436-696475`. Court 2 source/state/config backup: `/opt/rallypoint/backups/before-ready-to-play-20261006-150449`.

Managed review integration verified with RF-equivalent commands, authenticated bridge commands, restart restoration, and server projection: waiting match STARTING with no started_at/game record; confirmation ACTIVE with actual start time. No production matches were started for testing.

Managed extension deployment: full local regression suite 183 passed, 1 optional test skipped. Before-update host backup `/opt/rallypoint/backups/automatic/rallypoint-20261006-072708-641038`; idle host state/source backup `/opt/rallypoint/backups/before-ready-to-play-20261006-152716`; idle Court 2 state/source backup `/opt/rallypoint/backups/before-ready-to-play-20261006-152720`.

Final Pi verification: 48 application, bridge and web tests passed. Both real GPIO bridges are online, upload sync is online, and pending uploads are zero.


## Fair first serve

New HDMI setups and web match preparation default to **Random**. Team A and Team B remain available as explicit choices. A fair draw selects one team once, shown as **SERVES FIRST** on Ready to Play. Returning with C and reviewing again keeps the draw; a replacement match gets a fresh draw. Managed assignments, including automatic matchmaking, save the draw before sending it to the court. Confirmation, undo and match recovery retain that result. The starting team alternates in subsequent games.

Deployment backups (2026-10-06):
- Full host backup: `/opt/rallypoint/backups/automatic/rallypoint-20261006-074001-271877`
- Host scoreboard: `/opt/rallypoint/backups/before-random-serve-20261006-154124`
- Court 2 scoreboard: `/opt/rallypoint/backups/before-random-serve-20261006-154141`


## Courtside Preview

Preview shows a prominent READY TO PLAY card as soon as an operator reserves a match, with court number, assigned teams and players, first-serving team and game settings. After SEND TO COURT it shows WAITING FOR COURT CONFIRMATION and D TO START. Confirming on the remote or Controls replaces the card with live scores. Other active and finished courts keep their own displays. Offline assignments are marked as last known rather than live. Public Preview follows the same club visibility settings.

Before deployment: `/opt/rallypoint/backups/automatic/rallypoint-20261006-080811-902724`.

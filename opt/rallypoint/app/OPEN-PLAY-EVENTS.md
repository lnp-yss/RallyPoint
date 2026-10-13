# Open Play and Tournament events

Open **Event** in the main menu, immediately after Dashboard, to start or end an event and view event history.

1. End the previous event. All assigned/unfinished managed or casual HDMI matches must be finished or explicitly ended on Controls first. Ending an event checks everyone out, clears the waiting queue and winner holds, and cancels only unsent PREPARED drafts. Assigned matches are never silently cancelled.
2. Start a new event: name, Open Play or Tournament, and event date. Dates default to the operator phone's local calendar date. There is no automatic midnight reset.
3. Check in existing players again, or use quick add-and-check-in on Queue. Registration/profile records and skill ratings remain available.
4. Open Play uses the existing matchmaking queue. Tournament events suppress open-play recommendations and automatic rotation; use Tournaments to create a round-robin or single-elimination competition, register checked-in players, generate fixtures, and assign courts.
5. Preview shows the active event name/type and current event rankings. Dashboard queue match/game counts are event-scoped. Use Event history > Results/Match history for past events. Leaderboard offers active event, archived events, and all-time results.

Each club's database stores its own events. Changing one club's event does not affect another club. Matchmaking rules and court scope carry forward to the next event; devices, staff, player profiles and settings remain intact.

Existing matchmaking sessions appear in event history. Close the existing session before starting the first explicitly managed daily event. The additive migration does not erase old matches or reset an event on deployment. After explicit event management is used, check-in and assignment require an active event; background workers cannot silently reopen a closed event.

Closed event results are preserved: scoring controls reject archived events, and late device snapshots cannot reopen their attendance or change their match results. Historical player profile statistics and all-time rankings remain available.

## Verification

`python -m pytest tests/test_play_events.py -q`

Coverage includes lifecycle, fresh attendance, preserved rankings/profiles, club isolation, concurrent starts, assigned/casual-match blockers, permissions/CSRF, tournament association, archived controls, late snapshots and settings carry-forward. Test databases are isolated; no production matches are started by these tests.


## Resume a closed event

Open Event > Event history > RESUME EVENT and confirm the event name/date. End any other active event first and resolve unfinished court matches. Resuming keeps the original event ID, date, type, settings, tournaments, and results. Ending an event saves the active check-in and queue before clearing them; Resume restores that snapshot, including queue order, priorities, fixed/temporary teams and winner holds. Players removed before closure stay checked out. Waiting time is frozen while the event is closed. New events still start with fresh check-in. Events ended before queue snapshots were introduced cannot recover their deleted queue; the confirmation and completion notice explain when re-check-in is required. New matches add to that event's rankings. Previous match assignments and cancelled drafts do not restart. Old completed matches remain historical, including protection against late HDMI retries or Undo. The event may be ended and resumed more than once. Resume actions are recorded in the audit log.

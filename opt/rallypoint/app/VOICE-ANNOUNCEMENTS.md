# Operator voice announcements

Open Announcement Settings using the toolbar or Admin Settings. Connect the operator computer audio output to your PA speakers. Select an on-device voice for offline use, language, volume, rate and pitch, then Save Announcement Settings and Test Voice. Installed voices depend on the operator computer. No court Pi speaker is required.

Successful match assignments announce the actual database court name and player names after HDMI delivery is confirmed. Rejected starts do not announce. The default wording is: Now playing on {court}. {team_a} versus {team_b}. Players, please proceed to {court}.

Templates support {court}, {team_a}, {team_b}, {team_a_player_1}, {team_a_player_2}, {team_b_player_1}, {team_b_player_2}. Team placeholders handle singles naturally. Preferences are saved separately per browser and club; changing between hostname and IP creates a different browser origin and preferences.

Announcements play one at a time. Match IDs suppress repeated announcements, including page refresh. Replay Announcement on a dashboard court card intentionally repeats an announcement without changing the match or score. Quick Mute stops this browser queue. It never pauses scoring.

Automatic announcements for assignments made elsewhere are disabled by default. Enable them in settings, click Test / Enable PA Audio, and keep the Dashboard open. Existing matches at page load are not announced. Enable this on one PA computer to avoid different computers announcing together.

Browser speech may require a user gesture. If silent, click Test Voice, check the computer output device and volume, and select an installed on-device voice. Audio failures do not block a match. STARTED, COMPLETED, FAILED and CANCELLED reports record match, court, operator and time in the club database. These are browser playback callbacks, not proof that a loudspeaker was audible. Test Voice does not create a match log. No generated audio files are stored.

Verification: Python pytest includes announcement permissions, dynamic court and player metadata, successful-start receipt and log idempotency. Node tests: node --test tests/test_announcements.cjs. Physical PA wiring and speaker volume must be checked at the venue.

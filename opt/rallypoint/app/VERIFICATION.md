# Verification report

Verified on Windows with Python 3.12.14, Pygame 2.6.1, gpiozero 2.0.1 and pytest 8.3.5.

- 37 pytest tests passed. Pure rule tests cover opening service, both server transitions, singles, configurable targets, win-by rules, rally service options, best-of-three/five, timeouts, reset and bounded undo.
- Persistence tests cover restoration including undo history, corrupt/incomplete JSON, simulated failed atomic replacement preserving the old save, and undo after resuming a completed game and advancing to the next game.
- Input tests cover held-button repeat suppression, short/long D separation, timestamp duplicate suppression, stale events and independent team commands.
- Pygame integration tests use real keyboard events with dummy SDL display/audio at 1920×1080 and 1280×720. They cover scoring, repeated key-down, reset cancellation, pre-dialog queued commands, timeout/resume, long D, rendering, recovery and cleanup.
- Mock GPIO tests check all eight BCM assignments, pull-ups/debounce, queue callbacks, duplicate held activation and cleanup after partial permission failure. These are software tests, not electrical tests.
- `compileall` passed; `pip check` reported no broken requirements.
- All three shell launch/install scripts passed `bash -n` using Git Bash. Python sources were formatted to a 79-column target.
- Native Windows windowed keyboard launch completed 30 frames and exited cleanly. Headless smoke runs also passed. Setup and main scoreboard screenshots were visually inspected at both target sizes.

No Raspberry Pi or RF receivers were available. Actual GPIO signal behavior, RF receiver hold/release characteristics and pairing independence, HDMI audio, display hotplug, sustained Pi frame rate and reboot/autologin/user-service startup remain hardware acceptance checks. Linux startup files are provided with documented desktop-session environment handling; they were not executed on this Windows host.

Rally mode implements the requested club format: one point for every rally, including the winning point. It does not implement tournament-specific point freezes or a serve-to-win exception.

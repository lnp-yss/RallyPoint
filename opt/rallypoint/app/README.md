# Rally Point Pickleball Scoreboard

**V3 club/web extension:** see [README-V3.md](README-V3.md) for local web setup,
MariaDB, queue/matchmaking, live scores, and production KMSDRM deployment. The
production Raspberry Pi OS Lite service is `rallypoint-scoreboard.service`;
the desktop autostart instructions below describe the original desktop edition
and should not be applied to that working Lite installation.

An offline Python/Pygame scoreboard for a Raspberry Pi 4B, two independent four-button 433 MHz remotes, and an HDMI television. Large teal/amber team panels, a serving outline and dot, live score call, games won, timeout counts, lightweight feedback, and persistent match recovery are designed for 1920×1080 and 1280×720.

## Quick start on Windows or Linux

On this Windows checkout, double-click `run_scoreboard.bat` to use the repaired
Python 3.12 development environment (`.venv-dev`). Start the application through
`python -m pickleball_scoreboard.main`, not by running `app.py` directly.
If your editor reports a missing Pygame module, select the project's
`.venv-dev/Scripts/python.exe` interpreter. Recreating a virtual environment with
a different Python version requires reinstalling its dependencies; do not reuse
native extensions from an older Python version.

Python 3.10+ is required. From the project directory:

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pickleball_scoreboard.main --keyboard --windowed
.venv\Scripts\python -m pytest -q
```

Linux:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pickleball_scoreboard.main --keyboard --windowed
.venv/bin/python -m pytest -q
```

Click fields or use Tab to select them; type names, use arrows to change choices, then click **START MATCH** or press Enter. Player names are optional: enter `DOMINIC / CHEL` for Team A and `JOHN / MARIA` for Team B. Settings are saved when starting. A new match replaces the current recovery snapshot; logs remain.

No GPIO hardware is required for development. GPIO initialization failures automatically show **GPIO OFFLINE / KEYBOARD MODE**. No internet is needed after dependencies are installed. An HDMI television with its speakers enabled can play the included short WAV tones. Sound can be disabled in setup. Missing sounds or a missing `pickleball_scoreboard/assets/logo.png` are harmless.

## Controls and scoring semantics

| Command | Team A key | Team B key | Remote |
|---|---|---|---|
| This team won the rally | 1 | 7 | A |
| This team lost the rally | 2 | 8 | B |
| Undo the latest action, either team | 3 | 9 | C |
| Timeout, on release | 4 | 0 | D short press |
| Request reset confirmation, on release | hold 4 ≥3 seconds | hold 0 ≥3 seconds | D held ≥3 seconds |

Enter one rally result **once**, using either the winner's A button **or** the loser's B button. Do not enter both. A is an action/rally result, not an unconditional score increment: receiving-team wins cause service transitions in side-out scoring. B reports a lost rally even in rally scoring, where the opponent receives the point. The GPIO input worker only creates generic timestamped commands; scoring decisions belong to the central engine.

F1 opens a confirmation before setup. F2 toggles fullscreen. F3 toggles the debug overlay (off by default). R requests reset confirmation. Space pauses/resumes input or ends a timeout. Esc leaves fullscreen; in windowed mode it requests quit confirmation. Esc from setup returns to the existing match if one exists. On reset/quit/setup dialogs Y confirms, N or Esc cancels, or select with arrows and Enter. Destructive dialogs default to NO. Remote A confirms YES; remote B or C selects NO. The D release that opened the reset dialog never confirms it and never consumes a timeout.

In setup, either RF remote can operate without a keyboard: A moves to the next field, B cycles its value, C moves to the previous field, D short press starts. Names require a keyboard once or can be preconfigured in settings. Startup recovery can be accepted or rejected using the remotes. A completed saved match can also be reviewed; the result is never automatically erased.

Timeout counts are per team per game, default two. A timeout displays a 60-second banner. It ends automatically, with Space, or with a point/service press; that first press only resumes play and **does not score**. Undo remains available during timeout and restores the consumed timeout. Timeout counts persist; the presentation countdown is restarted only when a new timeout is requested, and is not restored after power loss. Input pause is a local display control and is not saved.

### Traditional side-out

Only the serving team scores. Doubles starts at **0–0–2** with the first-service exception: the starting team's first lost rally immediately causes side-out. Subsequently Server 1 losing changes to Server 2; Server 2 losing causes side-out and the new team starts at Server 1. Singles has one server per side-out and shows a two-number score call. The engine tracks the service sequence number, not individual players' court positions or identities. The starting team is configurable; successive games alternate the starting team.

These traditional transitions follow [USA Pickleball's side-out scoring explanation](https://usapickleball.org/pickleball-skills/level-one/pickleball-scoring-positioning-side-out-scoring/).

### Rally scoring

This application's configurable **club rally format awards one point on every rally**, including the winning point, as requested. Default service rule: the rally winner serves next. An optional alternate-every-rally rule is available in setup. There is one service number, shown in debug; the main score call uses two numbers. There are no point freezes or serve-to-win exceptions. This is a deliberately specified club format; sanctioned formats may differ—consult the [USA Pickleball rulebook](https://usapickleball.org/rules/) before using it for a sanctioned event.

Both formats support 11/15/21 points, win by 1/2, singles/doubles, single game/best of 3/best of 5. Game-winning feedback lasts 2.5 seconds before the next game starts. A match winner remains visible. Undo of the winning rally works before or after the automatic next-game transition, including after restarting the application.

## Hardware and electrical safety

Required: Raspberry Pi 4B with reliable power supply, microSD card, Raspberry Pi OS **64-bit Desktop**, HDMI cable/adapter, 16:9 monitor, two four-channel 433 MHz receivers and paired remotes, receiver-rated 12 V DC supply, and wiring to isolated relay contacts.

**Raspberry Pi GPIO is 3.3 V logic. NEVER connect 12 V, receiver V+, or any external voltage directly to a GPIO input.**

Use **isolated COM + NO dry contacts ONLY**. Receiver electronics use their own 12 V supply; the relay contacts switch Raspberry Pi GND to a pulled-up GPIO:

```text
Receiver power: 12 V supply -> receiver V+ / V- (per manufacturer)
Isolated relay contacts:
    Raspberry Pi GPIO17 ---- Relay NO
    Raspberry Pi GND ------- Relay COM
```

Relay open → internal pull-up → HIGH. Relay closed → Pi GND → LOW. Leave NC unused. Check receiver documentation and verify COM/NO isolation with power disconnected before wiring the Pi. Some products have voltage outputs instead of isolated contacts and are unsuitable without a separate interface. Do not join receiver V+ to the Pi. Receiver supply ground need not be joined to Pi ground when using genuinely isolated relay contacts. Power down equipment before wiring.

| Team | Remote button | BCM GPIO | Physical header pin |
|---|---|---:|---:|
| A | A | 17 | 11 |
| A | B | 27 | 13 |
| A | C | 22 | 15 |
| A | D | 23 | 16 |
| B | A | 5 | 29 |
| B | B | 6 | 31 |
| B | C | 13 | 33 |
| B | D | 19 | 35 |

Use an appropriate Pi ground pin, such as physical pin 6. GPIO settings always use **BCM numbers**, not physical header positions.

## Receiver pairing, mode and duplicate protection

On the **RESET MATCH?** dialog opened by holding D for at least three seconds,
press **C on either remote** to open match setup. Keyboard equivalents are
**3** or **9**. Configure the teams and rules, then select **START MATCH**.
The current match remains intact until the replacement match starts. In this
dialog, A confirms reset and B cancels; C opens setup instead of Undo.

Set both receivers to **MOMENTARY / JOG mode**. Each relay must remain energized while its corresponding button is held and release when the button is released. Latching/toggle modes are incompatible with D timing. Some low-cost receivers emit only a fixed-duration pulse even while a remote is held: these cannot convey long press and must be changed or replaced; keyboard R still requests reset.

Pairing sequences differ by receiver; follow its manufacturer's instructions. Typically use the receiver's learn button, then the intended transmitter button for each channel. Clear old pairings first if needed. Pair only Team A's remote to Team A's receiver and only Team B's remote to Team B's receiver. Verify every relay individually before connecting GPIO. Identical fixed-code transmitters may operate both receivers; use independently addressable remotes and verify isolation.

GPIO uses `gpiozero.Button(pull_up=True, bounce_time=None)` with a lightweight 5 ms sampling worker. Software requires a stable LOW press and stable HIGH release for `debounce_seconds` (default 30 ms). This avoids driver lockout losing quick release edges. Each complete cycle gets a unique activation number: duplicate activations are rejected, but separate quick presses are accepted even within the legacy 300 ms filter window. Held contacts and short bounce do not repeat. D emits one timeout or reset request on release. Keyboard/legacy commands retain `duplicate_seconds` (default 300 ms); all input older than two seconds is discarded. Test actual relay behavior; increase stable debounce if contacts are noisy. Both press and release must last longer than the debounce threshold.

Software cannot distinguish a separate real press from a receiver-generated false activation outside the suppression interval. Validate your receivers at the court under actual RF conditions; keep Undo available. Two different commands for the same rally cannot be automatically identified as duplicates.

## Raspberry Pi installation

Use Raspberry Pi Imager to install 64-bit Raspberry Pi OS Desktop, configure your own user, boot the desktop, and copy this entire project to a directory writable by that user. Do not assume the username is `pi`.

```bash
sudo apt update
sudo apt install python3 python3-venv python3-pip python3-dev build-essential \
  libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev libsdl2-ttf-dev
chmod +x install.sh run_scoreboard.sh desktop_start.sh
./install.sh
./run_scoreboard.sh
```

The script checks Python, creates the virtual environment, installs minimal runtime dependencies, creates data/log directories, writes defaults if absent, and verifies directory access. On a detected Pi it additionally installs `lgpio`, the GPIO backend required by current Raspberry Pi OS. This hardware-specific dependency is intentionally outside the cross-platform requirements. If a wheel is unavailable, SDL headers/build tools support building Pygame. Installation needs internet; running does not. `pytest` is only required for development: `.venv/bin/python -m pip install -r requirements-dev.txt`.

Run as the desktop user, not root. Check `id -nG` and `ls -l /dev/gpiochip*`. If your user lacks GPIO access:

```bash
sudo usermod -aG gpio "$USER"
```

Log out/reboot after changing groups. Use `GPIOZERO_PIN_FACTORY=lgpio ./run_scoreboard.sh` if backend selection needs to be explicit. The app still falls back safely to keyboard mode if GPIO cannot initialize.

## Reliable graphical autostart

Enable **Desktop Autologin** using `sudo raspi-config` (boot/autologin settings), or the equivalent Raspberry Pi desktop configuration. Choose HDMI resolution 1920×1080 or 1280×720 and disable screen blanking in desktop settings. Keep `fullscreen: true` in settings for deployment.

```bash
./install.sh --autostart
sudo reboot
```

The installer renders `pickleball-scoreboard.service` into **your systemd user configuration** with the actual project path. It installs a desktop startup hook: `~/.config/labwc/autostart` when labwc is installed, otherwise an XDG `.desktop` autostart entry. Existing labwc startup content is preserved and backed up before adding the hook. The hook imports `DISPLAY`, `WAYLAND_DISPLAY`, `XDG_RUNTIME_DIR`, and `XAUTHORITY` from the logged-in desktop, then starts the user service. This follows Raspberry Pi's [desktop kiosk autostart guidance](https://www.raspberrypi.com/tutorials/how-to-use-a-raspberry-pi-in-kiosk-mode/).

The service restarts unexpected failures with a five-second delay and limits repeated failed starts. Do **not** install it as a system/root boot service, enable user lingering for it, or invent a `DISPLAY=:0`: it needs the actual graphical session. `After=graphical-session.target` alone is not sufficient; the desktop hook is essential. If both labwc and another desktop are installed, inspect your active desktop and use the matching hook instead of enabling both.

```bash
systemctl --user status pickleball-scoreboard.service
journalctl --user -u pickleball-scoreboard.service -b
systemctl --user stop pickleball-scoreboard.service
```

Startup diagnostics also go to `logs/desktop-start.log`. To disable autostart remove the two scoreboard lines from labwc autostart, or remove the XDG scoreboard entry, then stop the user service. To relocate the project reinstall the rendered user service and update the existing desktop hook path. `run_scoreboard.sh` uses `flock` to prevent duplicate app instances.

## Configuration and files

`data/settings.json` is created by installation or first match start. Default values and pin assignments live in `pickleball_scoreboard/config.py`; JSON overrides them. Exit the application before editing JSON. Use valid JSON with no comments. Unknown or invalid settings fall back to defaults and are logged. Set team/player names in setup or JSON. `gpio_pins` has A/B arrays in remote button order A, B, C, D. Do not reuse a pin. Timing fields are `debounce_seconds`, `duplicate_seconds`, and `long_press_seconds`. Display fields are `width`, `height`, `fullscreen`; fullscreen uses the display's actual resolution. Club, timeout, sound, starting-team and match rules are centralized here too.

```text
pickleball_scoreboard/
  main.py, app.py, config.py  CLI, lifecycle/orchestration, settings
  scoring/                   Pure dataclasses, rules and central engine
  hardware/                  Timestamped queue, edge filtering, GPIO and keyboard
  ui/                        Responsive scoreboard, setup, dialogs, theme, feedback
  services/                  Bounded history, atomic JSON, optional audio
  assets/                    Optional logo, bundled WAV tones, font notes
tests/                       Scoring, input, persistence and application checks
scripts/                     Autostart installer and standard-library tone generator
data/                        Settings and current match (created at runtime)
logs/                        Timestamped event logs (created at runtime)
```

Pygame uses its bundled font, cached fonts/text surfaces, relative layout coordinates, lightweight score animation and a 60 FPS clock. No emoji rendering is needed. The pure scoring strategy registry allows new formats; future controllers can enqueue `Command` objects without changing rules or drawing code. No web/network/database subsystem is included.

## Recovery, undo and event logging

Every changed match state is written atomically: temporary file in the same directory, JSON write, flush/fsync, atomic replace, and directory fsync on POSIX. The versioned `data/current_match.json` includes settings, complete match state, final scores of completed games and up to 100 undo snapshots. Startup offers resume/new match; corrupt files are logged and ignored. Choosing NEW MATCH leaves the old snapshot intact until you start a replacement. A clean exit saves the current match and stops the GPIO worker and closes buttons, sound and Pygame. SIGTERM from the service also exits cleanly.

Logs use `logs/match_YYYY-MM-DD_HHMMSS_microseconds.log` and record timestamp, input command, resulting transition, score, service, server, game, start/resume and errors. A new process creates a new log even when resuming. Failed saves show a visible warning; resolve disk/permission issues before relying on recovery. Atomic writes reduce partial-file risk; reliable storage and power are still necessary. Consider a UPS and high-quality SD card for court deployment.

## Troubleshooting

**GPIO offline:** Confirm you are on a Pi, pins use BCM numbering, `gpiozero` and `lgpio` are installed in `.venv`, the user has device permission, and no other program owns the lines. Read the startup warning in the terminal/journal. A partially initialized controller closes all opened pins before falling back.

**HDMI/blank screen:** Run from the local desktop, not a headless SSH shell. Check active monitor input/cable, OS display settings, actual graphical environment and user-service journal. On Wayland do not force an X11 driver unless XWayland is available. F2 can switch out of fullscreen; the display scales automatically. Window resize is supported; unplug/reconnect and compositor behavior must be verified on your Pi/TV.

**RF missed/duplicate actions:** Check independent pairing and momentary mode; watch the physical relay. Check COM/NO isolation and common Pi GND contacts. F3 shows last command/pin, queue size, history and state. Adjust timing, improve receiver placement/power, and test one held press as well as rapid presses. Long D requires the contact to remain closed continuously for ≥3 seconds. Ordinary RF receivers provide no transmitter packet identity, so external noisy activations cannot be perfectly classified.

**No sound/logo:** Missing or malformed assets are logged/ignored. Confirm HDMI audio output, TV volume, and setup sound option. `scripts/generate_sounds.py` regenerates bundled tones using only the standard library. Place your logo at `pickleball_scoreboard/assets/logo.png`.

**Save/log permission failures:** Run as the directory owner and check disk space. Do not run another instance against the same data directory. The Pi launcher blocks duplicates; direct Python development launches must use separate `--data-root` directories when running concurrently.

## Verification

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q pickleball_scoreboard scripts
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python \
  -m pickleball_scoreboard.main --keyboard --windowed \
  --data-root artifacts/smoke --smoke-frames 10 --screenshot artifacts/setup.png
```

The scoring/input/persistence tests do not import Pygame or open a window. Separate application integration tests use SDL's dummy display/audio and exercise real keyboard events, dialogs, persistence and rendering at both target sizes. `--smoke-frames` and `--screenshot` are optional verification aids. Raspberry Pi GPIO electrical behavior, RF reliability, audible HDMI sound, actual TV readability/hotplug performance, and reboot/autologin must be verified on deployment hardware.

## Shared clubs on the local host

The host now offers isolated club workspaces at `/clubs/<slug>/`. Sign in as a host administrator and choose **Switch club** to provision a club. See [SHARED-SYSTEM.md](SHARED-SYSTEM.md) for club onboarding, CSV imports, device configuration, tournaments and backup/restore instructions. Existing Rally Point court scoring remains at `/`.

## Move courts between clubs

Main host administrators can use **Move courts** to review and confirm a physical Pi reassignment. See [Court switching](COURT-SWITCHING.md) for the paired-device workflow and recovery details.

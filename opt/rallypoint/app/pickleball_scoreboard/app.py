"""Main-thread orchestration; scoring remains display-independent."""

import logging
import signal
import os
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from queue import Queue, Empty
from time import monotonic
import pygame
from .hardware.commands import DuplicateFilter, Command
from .hardware.keyboard_controller import KeyboardController
from .hardware.gpio_controller import GPIOController
from .scoring.scoring_engine import ScoringEngine
from .scoring.game_state import MatchStatus, Team
from .services.persistence import Persistence
from .services.sound_service import SoundService
from .ui.scoreboard_screen import ScoreboardScreen
from .ui.setup_screen import SetupScreen
from .ui.dialogs import Dialog
from .ui.ready_screen import ReadyScreen
from .ui.animations import Feedback
from .ui.theme import Canvas, BG, WHITE, font, rendered
from .services.scoreboard_bridge import optional_bridge, WebCommand


class Application:
    def __init__(self, root, keyboard=False, windowed=False):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.configure_logging()
        self.store = Persistence(self.root / "data")
        self.settings = self.store.settings()
        if windowed:
            self.settings.fullscreen = False
        self.queue = Queue()
        self.keyboard = KeyboardController(self.queue, self.settings)
        self.gpio = GPIOController(self.queue, self.settings, keyboard)
        self.filter = DuplicateFilter(self.settings.duplicate_seconds)
        self.engine = self.store.restore()
        self.match_exists = self.engine is not None
        self.saved_engine = self.engine
        self.setup = SetupScreen(self.settings)
        self.setup.settings.starting_team = "Random"
        self.setup.settings.first_serving_team = None
        if self.engine and self.engine.state.status in (
            MatchStatus.COMPLETE,
            MatchStatus.ENDED,
        ):
            self.setup.settings.play_mode = "Casual Play"
        if self.setup.settings.play_mode == "Casual Play":
            self.default_casual_names()
        self.view = "setup"
        self.dialog = None
        if self.engine:
            self.dialog = Dialog(
                "resume",
                (
                    "UNFINISHED MATCH FOUND"
                    if self.engine.state.status != MatchStatus.COMPLETE
                    else "SAVED MATCH FOUND"
                ),
                "RESUME MATCH",
                "NEW MATCH",
            )
        else:
            self.engine = ScoringEngine(self.settings)
        self.feedback = Feedback()
        self.paused = False
        self.timeout_team = None
        self.timeout_until = 0.0
        self.game_until = 0.0
        self.debug = False
        self.last_command = "-"
        self.last_pin = "-"
        self.running = True
        pygame.display.init()
        pygame.font.init()
        self.set_display()
        pygame.display.set_caption("Rally Point Pickleball Scoreboard")
        assets = Path(__file__).resolve().parent / "assets"
        self.scoreboard = ScoreboardScreen(assets)
        self.sound = SoundService(assets / "sounds", self.settings.sound_enabled)
        self.clock = pygame.time.Clock()
        pygame.key.start_text_input()
        self.bridge = optional_bridge(self.root, self.queue)
        self.web_match_id = None
        self.web_started_at = None
        self.external_context = getattr(self.engine, "external_context", {})
        metadata = self.store.read("bridge_match.json")
        if isinstance(metadata, dict) and self.match_exists:
            self.web_match_id = metadata.get("match_id")
            self.web_started_at = metadata.get("started_at")
        if self.external_context.get("match_id"):
            self.web_match_id = self.external_context["match_id"]
            self.web_started_at = self.external_context.get("started_at")
        if self.web_match_id:
            self.engine.settings.play_mode = "Club Managed"
        self.last_heartbeat = 0.0
        self.command_actor = None
        self.paused = self.engine.state.paused
        if self.external_context.get("ready_to_play") and self.web_match_id:
            self.settings = self.engine.settings
            self.setup = SetupScreen(self.settings)
            self.dialog = ReadyScreen(self.settings, managed=True)
            self.view = "setup"
        self.publish_web("DEVICE_READY")

    def elapsed_time(self, now=None) -> str:
        """Wall-clock match duration, including breaks; finished matches freeze."""
        from time import time
        start = self.external_context.get("started_at") or self.web_started_at
        if not start:
            return "00:00" if self.external_context.get("ready_to_play") or not self.match_exists else "--:--"
        stop = self.external_context.get("ended_at") or (time() if now is None else now)
        seconds = max(0, int(stop - start))
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}" if hours else f"{minutes:02}:{seconds:02}"

    def publish_web(self, event_type: str) -> None:
        if self.bridge:
            from .services.presentation import indicators

            snapshot = {
                "identity": {
                    "device_id": os.environ.get("RALLYPOINT_DEVICE_ID"),
                    "court_id": os.environ.get("RALLYPOINT_COURT_ID"),
                    "hostname": __import__("socket").gethostname(),
                    "app_version": "3.1-dynamic-courts",
                },
                "settings": self.engine.settings.to_dict(),
                "state": self.engine.state.to_dict(),
                "started_at": self.web_started_at,
                "ready_to_play": bool(self.external_context.get("ready_to_play")),
                "cancelled_before_start": bool(self.external_context.get("cancelled_before_start")),
                "ended_at": self.external_context.get("ended_at"),
                "timeout_team": self.timeout_team,
                "view": self.view,
                "match_exists": self.match_exists,
                "actor": getattr(self, "command_actor", None),
                "correction_reason": getattr(self, "correction_reason", None),
                "hardware": {
                    "video_driver": pygame.display.get_driver(),
                    "resolution": list(self.surface.get_size()),
                    "gpio_mode": self.gpio.mode,
                },
            }
            snapshot["indicators"] = indicators(snapshot)
            self.bridge.publish(
                snapshot,
                event_type,
                self.web_match_id,
                os.environ.get("RALLYPOINT_DEVICE_ID"),
            )

    def web_command(self, command: WebCommand) -> None:
        """The only place IPC commands may touch Pygame or the scoring engine."""
        if not self.bridge:
            return
        if command.name == "UPDATE_ROSTER":
            # Trusted hub acknowledgement; never a public scoring command.
            values = command.payload.get("settings", {})
            keys = ("team_a", "team_b", "players_a", "players_b")
            if (command.payload.get("match_id") == self.web_match_id
                    and self.engine.state.status in (MatchStatus.PLAYING, MatchStatus.GAME_OVER)
                    and all(isinstance(values.get(k), str) and 0 < len(values[k]) <= 210 for k in keys)
                    and any(getattr(self.engine.settings, k) != values[k] for k in keys)):
                for settings in (self.settings, self.engine.settings, self.setup.settings,
                                 getattr(self.dialog, "settings", self.settings)):
                    for key in keys:
                        setattr(settings, key, values[key])
                self.save()
                self.publish_web("ROSTER_UPDATED")
            return
        if command.name == "UPDATE_BRANDING":
            branding = command.payload
            if all(isinstance(branding.get(key), str) and 0 < len(branding[key].strip()) <= 100
                   for key in ("scoreboard_header", "club_name")):
                for settings in (self.settings, self.engine.settings, self.setup.settings,
                                 getattr(self.dialog, "settings", self.settings)):
                    settings.scoreboard_header = branding["scoreboard_header"].strip()
                    if settings.play_mode == "Club Managed":
                        settings.club_name = branding["club_name"].strip()
                self.store.write("settings.json", self.settings.to_dict())
                self.save()
            return
        try:
            if command.request_id in self.external_context.get("applied_requests", []):
                self.bridge.complete(command)
                return
            if monotonic() - command.timestamp > 2:
                raise ValueError("Command expired; submit again")
            self.command_actor = command.payload.get("actor")
            self.correction_reason = command.payload.get("reason")
            if (
                command.payload.get("phone_control")
                and command.payload.get("expected_revision")
                != self.bridge.snapshot["revision"]
            ):
                raise ValueError("Score changed before phone command arrived")
            if command.name == "START_MATCH":
                configured_court = os.environ.get("RALLYPOINT_COURT_ID")
                if configured_court and int(configured_court) != int(
                    command.payload["court_id"]
                ):
                    raise ValueError("Assignment targets a different configured court")
                if self.match_exists and self.engine.state.status not in (
                    MatchStatus.COMPLETE,
                    MatchStatus.ENDED,
                ):
                    raise ValueError("Finish the current match before assignment")
                fields = {
                    "team_a",
                    "team_b",
                    "players_a",
                    "players_b",
                    "game_type",
                    "scoring_mode",
                    "points_to_win",
                    "win_by",
                    "best_of",
                    "timeout_limit",
                    "starting_team",
                    "first_serving_team",
                }
                settings = deepcopy(self.settings)
                settings.starting_team = "Random"
                settings.first_serving_team = None
                for key in fields:
                    if key in command.payload["settings"]:
                        setattr(settings, key, command.payload["settings"][key])
                settings.play_mode = "Club Managed"
                settings.starting_side = command.payload.get("starting_side", "Right")
                settings.court_name = (
                    command.payload.get("court_name")
                    or f"COURT {command.payload['court_id']}"
                )
                settings.resolve_first_serve()
                self.web_match_id = int(command.payload["match_id"])
                from time import time

                self.web_started_at = None
                self.external_context["ready_to_play"] = True
                self.external_context.update(
                    match_id=self.web_match_id,
                    court_id=int(command.payload["court_id"]),
                    started_at=self.web_started_at,
                    ended_at=None,
                )
                self.remember_web_request(command.request_id)
                self.store.write(
                    "bridge_match.json",
                    {"match_id": self.web_match_id, "started_at": self.web_started_at},
                )
                self.setup = SetupScreen(settings)
                self.dialog = None
                self.start(web=True, ready=True)
                self.dialog = ReadyScreen(settings, managed=True)
            else:
                if command.payload.get("match_id") != self.web_match_id:
                    raise ValueError("Command targets a different match")
                if command.name == "CHANGE_FIRST_SERVE":
                    if not self.external_context.get("ready_to_play"):
                        raise ValueError("First serve can only change on Ready to Play, before the match starts")
                    self.change_first_serve()
                    self.remember_web_request(command.request_id)
                    self.save()
                elif self.external_context.get("ready_to_play") and command.name in ("RESUME", "END_MATCH"):
                    if command.name == "RESUME":
                        self.begin_managed_match()
                    else:
                        self.dialog = None
                        self.view = "scoreboard"
                        self.external_context.pop("ready_to_play", None)
                        self.external_context["cancelled_before_start"] = True
                        self.act("END_MATCH")
                        self.save()
                    self.remember_web_request(command.request_id)
                    self.save()
                elif (
                    command.name == "RESUME_RECOVERY"
                    and self.dialog
                    and self.dialog.kind == "resume"
                ):
                    self.confirm(True)
                    self.remember_web_request(command.request_id)
                    self.save()
                    self.publish_web("RESUME_RECOVERY")
                else:
                    host_override = (
                        command.payload.get("host_override") is True
                        and command.name in ("END_GAME", "END_MATCH")
                    )
                    if command.payload.get("end_casual"):
                        if not host_override or self.engine.settings.play_mode != "Casual Play" or self.web_match_id:
                            raise ValueError("Court is no longer running Casual Play")
                        if command.payload.get("expected_revision") != self.bridge.snapshot["revision"]:
                            raise ValueError("Court changed; refresh before stopping Casual Play")
                    if host_override and command.name == "END_GAME":
                        scores = self.engine.state.game.scores
                        if self.engine.state.status != MatchStatus.PLAYING or scores["A"] == scores["B"]:
                            raise ValueError("End Match to release this court; End Game requires an untied active game")
                    if host_override:
                        self.dialog = None
                        self.view = "scoreboard"
                        self.timeout_team = None
                    if (self.dialog or self.view != "scoreboard") and not host_override:
                        raise ValueError(
                            "Close the HDMI dialog or resume the match first"
                        )
                    if command.name == "CORRECT_RESULT":
                        if (
                            command.payload.get("expected_revision")
                            != self.bridge.snapshot["revision"]
                        ):
                            raise ValueError("Scoreboard changed; reload the result")
                        if not command.payload.get("reason"):
                            raise ValueError("A correction reason is required")
                    if command.name in ("SET_SCORES", "DECLARE_WINNER"):
                        if (
                            command.payload.get("expected_revision")
                            != self.bridge.snapshot["revision"]
                        ):
                            raise ValueError(
                                "Scoreboard changed. Review the live score and submit again."
                            )
                        if self.engine.state.status not in (
                            MatchStatus.PLAYING,
                            MatchStatus.GAME_OVER,
                        ):
                            raise ValueError(
                                "The match has finished. Undo before changing its result."
                            )
                        if "scores" in command.payload:
                            ScoringEngine.validate_scores(command.payload["scores"])
                            if self.engine.state.status != MatchStatus.PLAYING:
                                raise ValueError(
                                    "Start the next game before editing its score"
                                )
                        elif command.name == "SET_SCORES":
                            raise ValueError("Both team scores are required")
                        if command.name == "DECLARE_WINNER" and command.payload.get(
                            "winner"
                        ) not in ("A", "B"):
                            raise ValueError("Select Team A or Team B as match winner")
                    if self.timeout_team and command.name not in (
                        "TEAM_A_UNDO",
                        "TEAM_B_UNDO",
                        "RESUME",
                        "END_MATCH",
                        "RESET",
                        "SET_SCORES",
                        "DECLARE_WINNER",
                    ):
                        raise ValueError("Resume play before scoring during a timeout")
                    if host_override and command.name == "END_GAME" and self.engine.state.paused:
                        self.act("RESUME")
                    if command.name == "RESUME":
                        self.timeout_team = None
                    self.remember_web_request(command.request_id)
                    result = self.act(command.name, command.payload)
                    if result and command.payload.get("end_casual"):
                        self.open_setup()
                        self.setup.settings.play_mode = "Club Managed"
                        self.setup.error = "CASUAL PLAY ENDED: waiting for operator assignment"
                    if not result and command.name != "RESUME":
                        self.external_context["applied_requests"].remove(
                            command.request_id
                        )
                        raise ValueError(
                            "Action is not valid for the current match state"
                        )
                    if not result:
                        self.save()
                        self.publish_web(command.name)
            self.bridge.complete(command)
        except (ValueError, KeyError, TypeError) as exc:
            self.bridge.complete(command, str(exc))
            logging.warning("Web command rejected: %s", exc)
        finally:
            self.command_actor = None
            self.correction_reason = None

    def remember_web_request(self, request_id: str) -> None:
        self.external_context["applied_requests"] = (
            self.external_context.get("applied_requests", []) + [request_id]
        )[-1000:]

    def configure_logging(self):
        try:
            path = self.root / "logs"
            path.mkdir(exist_ok=True)
            handler = logging.FileHandler(
                path / f"match_{datetime.now():%Y-%m-%d_%H%M%S_%f}.log",
                encoding="utf-8",
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
            )
            logging.getLogger().addHandler(handler)
            logging.getLogger().setLevel(logging.INFO)
            self.log_handler = handler
        except OSError:
            self.log_handler = None
            logging.warning("File logging unavailable")

    def set_display(self):
        flags = pygame.FULLSCREEN if self.settings.fullscreen else pygame.RESIZABLE
        size = (
            (0, 0)
            if self.settings.fullscreen
            else (self.settings.width, self.settings.height)
        )
        self.surface = pygame.display.set_mode(
            size,
            flags,
        )
        pygame.mouse.set_visible(not self.settings.fullscreen or self.view == "setup")

    def clear_input(self):
        self.keyboard.edges.clear()
        self.gpio.edges.clear()
        while True:
            try:
                pending = self.queue.get_nowait()
                if isinstance(pending, WebCommand) and getattr(self, "bridge", None):
                    self.bridge.complete(
                        pending,
                        "Input cleared by an HDMI state transition; submit again",
                    )
            except Empty:
                break

    def confirm(self, yes):
        kind = self.dialog.kind
        self.dialog = None
        self.clear_input()
        if kind == "resume":
            if yes:
                self.view = "scoreboard"
                self.match_exists = True
                fullscreen = self.settings.fullscreen
                self.settings = self.engine.settings
                self.settings.fullscreen = fullscreen
                self.sound.enabled = self.settings.sound_enabled
                if self.engine.state.status == MatchStatus.GAME_OVER:
                    self.game_until = monotonic() + 2.5
                logging.info("MATCH_RESUMED")
            else:
                self.view = "setup"
        elif kind == "managed_start":
            if yes:
                self.begin_managed_match()
            else:
                self.view = "setup"
                self.setup = SetupScreen(self.engine.settings)
                self.setup.error = "ASSIGNED MATCH: A to review. Settings changes require the operator."
                pygame.mouse.set_visible(True)
        elif kind == "start" and yes:
            self.start()
        elif kind == "end_casual" and yes:
            self.act("END_MATCH")
            self.timeout_team = None
            self.game_until = 0
            self.open_setup()
        elif kind == "reset" and yes:
            self.act("RESET")
        elif kind == "quit" and yes:
            self.running = False
        elif kind == "setup" and yes:
            self.open_setup()

    def open_setup(self) -> None:
        """Configure a replacement match without erasing the current match."""
        self.dialog = None
        self.clear_input()
        self.setup = SetupScreen(self.engine.settings)
        # This is a locally configured replacement, not an operator assignment.
        # SetupScreen owns a copy, so the old managed match stays unchanged.
        self.setup.settings.play_mode = "Casual Play"
        self.default_casual_names()
        self.setup.settings.starting_team = "Random"
        self.setup.settings.first_serving_team = None
        self.view = "setup"
        pygame.mouse.set_visible(True)

    def default_casual_names(self) -> None:
        """New local setup starts neutral; never overwrite names at match start."""
        self.setup.settings.club_name = "AmaPickle"
        self.setup.settings.team_a = "TEAM A"
        self.setup.settings.team_b = "TEAM B"
        self.setup.settings.players_a = "Team A"
        self.setup.settings.players_b = "Team B"

    def request_start(self) -> None:
        """Validate local setup before offering a confirmation that can start."""
        if self.external_context.get("ready_to_play") and self.web_match_id:
            self.dialog = ReadyScreen(self.engine.settings, managed=True)
            return
        try:
            self.setup.settings.validate()
            if self.setup.settings.play_mode == "Club Managed":
                self.setup.focus = 0
                self.setup.error = "Select CASUAL PLAY for remote start; CLUB MANAGED needs operator assignment"
                return
            self.setup.error = ""
            self.setup.settings.resolve_first_serve()
            self.dialog = ReadyScreen(self.setup.settings)
        except ValueError as exc:
            self.setup.error = str(exc)

    def change_first_serve(self):
        """Switch the pre-match choice without scoring or starting a timer."""
        managed = bool(self.external_context.get("ready_to_play") and self.web_match_id)
        if not managed and not isinstance(self.dialog, ReadyScreen):
            raise ValueError("Open Ready to Play before changing first serve")
        if (self.root / "data/court_switch.lock").exists():
            raise ValueError("Court switch in progress; wait before changing first serve")
        settings = self.engine.settings if managed else self.dialog.settings
        selected = "B" if settings.first_serving_team == "A" else "A"
        targets = [settings, self.setup.settings]
        if isinstance(self.dialog, ReadyScreen):
            targets.append(self.dialog.settings)
        if managed:
            targets.append(self.settings)
        for target in targets:
            target.starting_team = selected
            target.first_serving_team = selected
        if managed:
            self.engine.state.game.serving = Team(selected)
            self.engine.state.game.service_side = "Right"
            self.save()
            self.publish_web("FIRST_SERVE_CHANGED")
        self.feedback.show("TEAM " + selected + " SERVES FIRST", selected)
        self.sound.play("service")

    def begin_managed_match(self):
        """Confirm the reserved assignment without resetting its engine."""
        if (self.root / "data/court_switch.lock").exists():
            self.dialog = ReadyScreen(self.engine.settings, managed=True)
            self.feedback.show("COURT SWITCH IN PROGRESS", duration=2)
            return
        from time import time
        self.external_context.pop("ready_to_play", None)
        self.web_started_at = time()
        self.external_context["started_at"] = self.web_started_at
        self.engine.state.paused = self.paused = False
        self.dialog = None
        self.view = "scoreboard"
        self.clear_input()
        self.store.write("bridge_match.json", {"match_id": self.web_match_id, "started_at": self.web_started_at})
        self.save()
        logging.info("MATCH_STARTED managed match %s", self.web_match_id)
        self.publish_web("MATCH_STARTED")

    def start(self, web=False, ready=False):
        if (self.root / "data/court_switch.lock").exists():
            self.setup.error = "Court switch in progress. Please wait."
            return
        try:
            if not web:
                if self.setup.settings.play_mode == "Club Managed":
                    self.setup.error = "CLUB MANAGED: waiting for operator assignment"
                    return
                if self.web_match_id and self.engine.state.status not in (
                    MatchStatus.COMPLETE,
                    MatchStatus.ENDED,
                ):
                    self.act("END_MATCH")
                self.web_match_id, self.web_started_at = None, None
                self.external_context = {}
                self.store.write(
                    "bridge_match.json", {"match_id": None, "started_at": None}
                )
            self.settings = self.setup.settings.resolve_first_serve()
            self.engine = ScoringEngine(self.settings)
            if not ready:
                from time import time
                self.external_context["started_at"] = time()
                self.external_context["ended_at"] = None
            self.match_exists = True
            self.saved_engine = self.engine
            self.view = "scoreboard"
            self.paused = ready
            self.engine.state.paused = ready
            self.timeout_team = None
            self.game_until = 0
            self.sound.enabled = self.settings.sound_enabled
            self.store.write("settings.json", self.settings.to_dict())
            self.save()
            self.clear_input()
            pygame.mouse.set_visible(not self.settings.fullscreen)
            logging.info("%s %s", "MATCH_READY" if ready else "MATCH_STARTED", self.settings.to_dict())
            self.publish_web("MATCH_READY" if ready else "MATCH_STARTED")
        except ValueError as exc:
            self.setup.error = str(exc)

    def save(self):
        if self.match_exists:
            self.store.save(self.engine, self.external_context)
            if self.store.error:
                self.feedback.show("SAVE FAILED - CHECK DISK", duration=5)

    def act(self, name, payload=None):
        if (self.root / "data/court_switch.lock").exists():
            self.feedback.show("COURT SWITCH IN PROGRESS", duration=2)
            return
        self.last_command = name
        previous_history = len(self.engine.history)
        before = self.engine.state.to_dict()
        result = self.engine.apply(name, payload)
        if not result:
            return
        audit_result = result
        self.paused = self.engine.state.paused
        if self.match_exists:
            from time import time
            if name == "RESET":
                self.external_context["started_at"] = time()
            self.external_context["ended_at"] = (
                (self.external_context.get("ended_at") or time())
                if self.engine.state.status in (MatchStatus.COMPLETE, MatchStatus.ENDED)
                else None
            )
        team = name.split("_")[1] if name.startswith("TEAM_") else None
        after = self.engine.state.to_dict()
        if (
            before != after
            or name == "RESET"
            or len(self.engine.history) != previous_history
        ):
            self.save()
        if before["game"]["serving"] != after["game"]["serving"]:
            team = after["game"]["serving"]
            self.sound.play("service")
            if result == "POINT":
                result = "POINT / SERVICE"
        else:
            self.sound.play(
                "service" if result in ("SERVER 2", "UNDO", "TIMEOUT") else "point"
            )
        self.feedback.show(
            result, team, 2 if result == "NO TIMEOUTS REMAINING" else 1.2
        )
        if result == "TIMEOUT":
            self.timeout_team, self.timeout_until = team, monotonic() + 60
        elif result == "UNDO" or name in ("RESET", "DECLARE_WINNER"):
            self.timeout_team = None
        if self.engine.state.status == MatchStatus.GAME_OVER:
            self.game_until = monotonic() + 2.5
            self.sound.play("game")
        elif self.engine.state.status == MatchStatus.COMPLETE:
            self.sound.play("match")
        else:
            self.game_until = 0
        logging.info(
            "%s -> %s score=%s serving=%s server=%s game=%s",
            name,
            result,
            self.engine.state.game.scores,
            self.engine.state.game.serving.value,
            self.engine.state.game.server,
            self.engine.state.game_number,
        )
        self.publish_web(
            {
                "POINT": "POINT",
                "SIDE OUT": "SIDE_OUT",
                "SERVER 2": "SERVER_CHANGE",
                "GAME WON": "GAME_WON",
                "MATCH WINNER": "MATCH_WON",
            }.get(audit_result, name)
            if name != "DECLARE_WINNER"
            else name
        )
        return result

    def command(self, command: Command) -> None:
        if (self.root / "data/court_switch.lock").exists():
            if isinstance(command, WebCommand) and self.bridge:
                self.bridge.complete(
                    command, "Court is moving between clubs; please wait"
                )
            self.feedback.show("COURT SWITCH IN PROGRESS", duration=2)
            return
        if isinstance(command, WebCommand):
            self.web_command(command)
            return
        if not self.filter.accept(command):
            return
        name = command.name
        self.last_command, self.last_pin = name, str(command.pin or "-")
        if self.dialog:
            if command.timestamp <= self.dialog.opened_at:
                return
            if isinstance(self.dialog, ReadyScreen):
                if name.endswith("_POINT"):
                    self.confirm(True)
                elif name.endswith("_SERVICE"):
                    self.confirm(False)
                elif name.endswith("_UNDO"):
                    try:
                        self.change_first_serve()
                    except ValueError as exc:
                        self.feedback.show(str(exc), duration=2)
                return
            if name.endswith("_POINT"):
                self.confirm(True)
            elif self.dialog.kind == "reset" and name.endswith("_UNDO"):
                self.open_setup()
            elif name.endswith(("_SERVICE", "_UNDO")):
                self.confirm(False)
            return
        if (
            self.view == "scoreboard"
            and self.engine.settings.play_mode == "Casual Play"
            and not self.external_context.get("ready_to_play")
        ):
            remote_action = name.startswith(("TEAM_A_", "TEAM_B_")) and name.endswith(
                ("_POINT", "_SERVICE", "_UNDO", "_TIMEOUT")
            )
            if self.engine.state.status == MatchStatus.COMPLETE and (
                remote_action or name == "RESET_REQUEST"
            ):
                self.open_setup()
                return
            if name == "RESET_REQUEST":
                if self.engine.state.status == MatchStatus.ENDED:
                    self.open_setup()
                else:
                    self.dialog = Dialog("end_casual", "END MATCH?", "YES - END MATCH", "NO - CONTINUE")
                return
        if self.external_context.get("ready_to_play"):
            if name.endswith("_POINT"):
                self.request_start()
            elif name.endswith("_UNDO"):
                try:
                    self.change_first_serve()
                except ValueError as exc:
                    self.feedback.show(str(exc), duration=2)
            return
        if name == "RESET_REQUEST":
            if self.view == "scoreboard":
                self.dialog = Dialog("reset", "RESET MATCH?")
            return
        if self.view == "setup":
            if self.external_context.get("ready_to_play"):
                if name.endswith("_POINT"):
                    self.request_start()
                return
            if self.setup.command(name):
                self.request_start()
            return
        if self.paused and not name.endswith("_UNDO"):
            return
        if self.timeout_team and not name.endswith("_UNDO"):
            # First scoring/service press resumes play without scoring.
            if name.endswith(("_POINT", "_SERVICE")):
                self.timeout_team = None
                self.feedback.show("PLAY RESUMED")
                self.publish_web("TIMEOUT_RESUMED")
            return
        self.act(name)

    def event(self, event):
        if event.type == pygame.QUIT:
            self.dialog = Dialog("quit", "QUIT SCOREBOARD?")
        elif event.type == pygame.VIDEORESIZE and not self.settings.fullscreen:
            try:
                self.surface = pygame.display.set_mode(event.size, pygame.RESIZABLE)
            except pygame.error:
                logging.exception("Display resize failed")
        elif event.type == getattr(pygame, "WINDOWSIZECHANGED", -1):
            self.surface = pygame.display.get_surface() or self.surface
        elif event.type == getattr(pygame, "WINDOWDISPLAYCHANGED", -1):
            try:
                sizes = pygame.display.get_desktop_sizes()
                if (
                    self.settings.fullscreen
                    and sizes
                    and self.surface.get_size() != sizes[0]
                ):
                    self.set_display()
            except pygame.error:
                logging.exception("Display change failed; retaining current surface")
        elif event.type == getattr(pygame, "WINDOWFOCUSLOST", -1):
            self.keyboard.close()
        elif event.type == pygame.KEYDOWN:
            key = event.key
            if key == pygame.K_F2:
                self.settings.fullscreen = not self.settings.fullscreen
                self.set_display()
                return
            if key == pygame.K_F3:
                self.debug = not self.debug
                return
            if self.dialog:
                if key in (pygame.K_y, pygame.K_n, pygame.K_ESCAPE):
                    self.confirm(key == pygame.K_y)
                elif key in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_TAB):
                    self.dialog.selected = not self.dialog.selected
                elif key == pygame.K_RETURN:
                    self.confirm(self.dialog.selected)
                else:
                    self.keyboard.handle(event)
                return
            if key == pygame.K_ESCAPE:
                if self.view == "setup" and self.match_exists:
                    self.view = "scoreboard"
                elif self.settings.fullscreen:
                    self.settings.fullscreen = False
                    self.set_display()
                else:
                    self.dialog = Dialog("quit", "QUIT SCOREBOARD?")
                return
            if self.view == "scoreboard":
                if key == pygame.K_F1:
                    self.dialog = Dialog("setup", "OPEN MATCH SETUP?")
                    return
                if key == pygame.K_r:
                    self.dialog = Dialog("reset", "RESET MATCH?")
                    return
                if key == pygame.K_SPACE:
                    if self.timeout_team:
                        self.timeout_team = None
                        self.publish_web("TIMEOUT_RESUMED")
                    else:
                        self.act("RESUME" if self.paused else "PAUSE")
                    self.clear_input()
                    return
        if self.dialog:
            if event.type == pygame.MOUSEBUTTONDOWN:
                for box, value in self.dialog.buttons:
                    if box.collidepoint(event.pos):
                        if value == "change_serve":
                            try:
                                self.change_first_serve()
                            except ValueError as exc:
                                self.feedback.show(str(exc), duration=2)
                        else:
                            self.confirm(value)
                        break
            elif event.type == pygame.KEYUP:
                self.keyboard.handle(event)
        elif self.view == "setup":
            if self.external_context.get("ready_to_play"):
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_RETURN:
                        self.request_start()
                    else:
                        self.keyboard.handle(event)
                elif event.type == pygame.KEYUP:
                    self.keyboard.handle(event)
                elif event.type == pygame.MOUSEBUTTONDOWN and self.setup.start_box and self.setup.start_box.collidepoint(event.pos):
                    self.request_start()
                return
            if self.setup.event(event):
                self.request_start()
        elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
            self.keyboard.handle(event)

    def run(
        self, frame_limit: int | None = None, screenshot: Path | None = None
    ) -> None:
        frames = 0
        last_draw = 0.0
        was_animating = False
        last_sync = None
        old_term = signal.signal(
            signal.SIGTERM, lambda *_: setattr(self, "running", False)
        )
        try:
            while self.running:
                events = pygame.event.get()
                dirty = bool(events)
                for event in events:
                    self.event(event)
                self.keyboard.edges.poll()
                for _ in range(64):
                    try:
                        self.command(self.queue.get_nowait())
                        dirty = True
                    except Empty:
                        break
                now = monotonic()
                if self.bridge and now - self.last_heartbeat >= 5:
                    self.publish_web("HEARTBEAT")
                    self.last_heartbeat = now
                if (
                    self.game_until
                    and self.view == "scoreboard"
                    and now >= self.game_until
                    and not self.dialog
                    and not self.paused
                ):
                    self.act("NEXT_GAME")
                    self.game_until = 0
                    dirty = True
                if self.timeout_team and now >= self.timeout_until:
                    self.timeout_team = None
                    self.publish_web("TIMEOUT_ENDED")
                    dirty = True
                # Keep input polling at 60 Hz, but avoid repainting a static TV.
                # Animations/debug still render every frame; refresh idle screens
                # once a second for sync status and display exposure recovery.
                sync = self.bridge.sync_status if self.bridge else {}
                sync_key = (sync.get("online"), sync.get("pending"))
                animating = self.feedback.active
                draw = (
                    dirty
                    or animating
                    or was_animating
                    or self.debug
                    or sync_key != last_sync
                    or now - last_draw >= 1.0
                )
                if draw:
                    if self.view == "setup":
                        self.setup.draw(self.surface, self.debug)
                    else:
                        self.scoreboard.draw(
                            self.surface,
                            self.engine,
                            self.feedback,
                            self.gpio.mode,
                            self.paused,
                            self.timeout_team,
                            elapsed=self.elapsed_time(),
                        )
                    if self.view == "scoreboard":
                        sync = (
                            self.bridge.sync_status
                            if self.bridge
                            else {"online": False, "pending": 0}
                        )
                        label = (
                            self.engine.settings.play_mode.upper()
                            + "  /  "
                            + (
                                "CLUB ONLINE"
                                if sync.get("online")
                                else "CLUB OFFLINE - LOCAL SCORING"
                            )
                        )
                        if sync.get("pending"):
                            label += "  /  " + str(sync["pending"]) + " WAITING TO SYNC"
                        Canvas(self.surface).text(
                            label, 0.5, 0.985, 18, (150, 180, 190)
                        )
                    if self.dialog:
                        self.dialog.draw(self.surface, self.debug)
                    if self.store.error:
                        Canvas(self.surface).text(
                            "SAVE FAILED - CHECK DISK / LOG",
                            0.5,
                            0.02,
                            22,
                            (255, 100, 100),
                        )
                    if self.debug:
                        self.draw_debug()
                    pygame.display.flip()
                    last_draw = now
                was_animating = animating
                last_sync = sync_key
                self.clock.tick(60)
                frames += 1
                if frame_limit and frames >= frame_limit:
                    if screenshot:
                        pygame.image.save(self.surface, str(screenshot))
                    break
        finally:
            signal.signal(signal.SIGTERM, old_term)
            self.close()

    def draw_debug(self):
        c = Canvas(self.surface)
        c.rect(0.015, 0.015, 0.58, 0.145, BG)
        g = self.engine.state.game
        lines = [
            f"{self.clock.get_fps():.0f} FPS | {self.surface.get_size()} | {self.gpio.mode}",
            f"{self.last_command} | GPIO {self.last_pin} | serve {g.serving.value}/{g.server} | score {g.scores}",
            f"History {len(self.engine.history)} | queue {self.queue.qsize()}",
            "A: 1 point / 2 loss / 3 undo / 4 timeout | B: 7 / 8 / 9 / 0",
            "F1 setup | F2 fullscreen | F3 debug | R reset | Space pause | Esc exit",
        ]
        for i, line in enumerate(lines):
            c.text(line, 0.30, 0.034 + 0.026 * i, 21, WHITE, 0.56)

    def close(self):
        self.save()
        if self.bridge:
            self.bridge.close()
        self.gpio.close()
        self.keyboard.close()
        self.sound.close()
        rendered.cache_clear()
        font.cache_clear()
        pygame.quit()
        if self.log_handler:
            logging.getLogger().removeHandler(self.log_handler)
            self.log_handler.close()

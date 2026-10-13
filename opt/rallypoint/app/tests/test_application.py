"""Real Pygame keyboard events and recovery, using a headless SDL display."""

import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
import pytest

pygame = pytest.importorskip("pygame")
from time import monotonic
from pickleball_scoreboard.app import Application
from pickleball_scoreboard.hardware.commands import Command
from pickleball_scoreboard.ui.dialogs import Dialog


@pytest.mark.parametrize("size", [(1920, 1080), (1280, 720)])
def test_keyboard_score_undo_dialog_recovery_and_render(tmp_path, size):
    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.settings.width, app.settings.height = size
        app.set_display()
        app.setup.settings.players_a = "DOMINIC / CHEL"
        app.setup.settings.players_b = "JOHN / MARIA"
        app.start()
        app.event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_1))
        app.event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_1))
        app.event(pygame.event.Event(pygame.KEYUP, key=pygame.K_1))
        while not app.queue.empty():
            app.command(app.queue.get())
        assert app.engine.state.game.scores["A"] == 1
        app.event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r))
        assert app.dialog.kind == "reset" and not app.dialog.selected
        app.event(pygame.event.Event(pygame.KEYDOWN, key=pygame.K_n))
        assert app.engine.state.game.scores["A"] == 1
        app.dialog = Dialog("reset", "RESET MATCH?")
        app.command(Command("TEAM_A_POINT", app.dialog.opened_at - 0.01))
        assert app.dialog is not None  # Queued pre-dialog command cannot confirm.
        app.confirm(False)
        app.scoreboard.draw(app.surface, app.engine, app.feedback, app.gpio.mode)
        assert app.surface.get_size() == size
        assert app.store.restore().state.game.scores["A"] == 1
        app.act("TEAM_B_UNDO")
        assert app.engine.state.game.scores["A"] == 0
    finally:
        app.close()


def test_timeout_resume_and_long_d(tmp_path):
    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.start()
        now = monotonic()
        app.keyboard.edges.press("TEAM_A_TIMEOUT", now=now)
        app.keyboard.edges.release("TEAM_A_TIMEOUT", now=now + 0.1)
        app.command(app.queue.get())
        assert app.engine.state.game.timeouts["A"] == 1
        app.command(Command("TEAM_A_POINT", monotonic()))
        assert app.timeout_team is None and app.engine.state.game.scores["A"] == 0
        now = monotonic()
        app.keyboard.edges.press("TEAM_B_TIMEOUT", now=now - 3.1)
        app.keyboard.edges.release("TEAM_B_TIMEOUT", now=now)
        app.command(app.queue.get())
        assert app.dialog.kind == "reset"
        assert app.engine.state.game.timeouts["B"] == 0
        app.confirm(True)
        assert app.engine.state.game.timeouts["A"] == 0
    finally:
        app.close()


def test_remote_text_entry_and_managed_wait(tmp_path):
    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.setup.focus = next(
            i for i, f in enumerate(app.setup.fields) if f[0] == "players_a"
        )
        app.setup.command("TEAM_A_SERVICE")
        assert app.setup.editing
        app.setup.command("TEAM_A_SERVICE")
        app.setup.command("TEAM_A_POINT")
        assert app.setup.settings.players_a == "A"
        app.setup.command("TEAM_A_UNDO")
        assert app.setup.settings.players_a == ""
        app.setup.command("TEAM_A_TIMEOUT")
        assert not app.setup.editing
        app.setup.settings.play_mode = "Club Managed"
        app.start()
        assert app.view == "setup" and not app.match_exists
        assert "waiting" in app.setup.error
    finally:
        app.close()


def test_web_assignment_cannot_replace_casual_game(tmp_path):
    from pickleball_scoreboard.services.scoreboard_bridge import WebCommand
    from uuid import uuid4

    app = Application(tmp_path, keyboard=True, windowed=True)

    class Bridge:
        def complete(self, command, error=None):
            self.error = error

    try:
        app.start()
        app.act("TEAM_A_POINT")
        before = app.engine.state.to_dict()
        app.bridge = Bridge()
        app.web_command(
            WebCommand(
                "START_MATCH",
                {
                    "replace_confirmed": True,
                    "match_id": 99,
                    "court_id": 1,
                    "settings": {},
                },
                str(uuid4()),
            )
        )
        assert app.bridge.error and "Finish" in app.bridge.error
        assert app.engine.state.to_dict() == before
        app.bridge = None
    finally:
        app.bridge = None
        app.close()


@pytest.mark.parametrize("team", ["A", "B"])
def test_remote_new_setup_starts_after_managed_match(tmp_path, team):
    from pickleball_scoreboard.scoring.game_state import MatchStatus

    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.engine.settings.play_mode = "Club Managed"
        app.engine.state.status = MatchStatus.COMPLETE
        app.engine.state.winner = "A"
        app.match_exists = True
        app.web_match_id = 42
        app.view = "scoreboard"
        old = app.engine
        # Long D opens reset; C enters a locally configured replacement.
        app.command(Command("RESET_REQUEST", monotonic()))
        app.command(Command(f"TEAM_{team}_UNDO", app.dialog.opened_at + 0.01))
        assert app.view == "setup"
        assert app.setup.settings.play_mode == "Casual Play"
        assert old.settings.play_mode == "Club Managed"
        app.command(Command(f"TEAM_{team}_TIMEOUT", monotonic()))
        assert app.dialog.kind == "start"
        app.command(Command(f"TEAM_{team}_POINT", app.dialog.opened_at + 0.01))
        assert app.view == "scoreboard" and app.dialog is None
        assert app.web_match_id is None
        assert app.engine.state.game.scores == {"A": 0, "B": 0}
        assert app.engine.settings.play_mode == "Casual Play"
        assert app.store.restore().state.game.scores == {"A": 0, "B": 0}
    finally:
        app.close()


def test_managed_remote_start_has_actionable_message_not_false_confirmation(tmp_path):
    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.setup.settings.play_mode = "Club Managed"
        app.command(Command("TEAM_A_TIMEOUT", monotonic()))
        assert app.view == "setup" and app.dialog is None
        assert "CASUAL PLAY" in app.setup.error
        assert "operator assignment" in app.setup.error
        assert app.setup.focus == 0
    finally:
        app.close()


def test_fast_rf_cycles_update_score_and_saved_state(tmp_path):
    from pickleball_scoreboard.hardware.commands import DebouncedRelay

    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.start()
        relay = DebouncedRelay(app.gpio.edges, "TEAM_A_POINT", 17, 0.03)
        base = monotonic() - 0.5
        for start in (base, base + 0.12, base + 0.24):
            relay.sample(True, start)
            relay.sample(True, start + 0.031)
            relay.sample(False, start + 0.06)
            relay.sample(False, start + 0.091)
        while not app.queue.empty():
            app.command(app.queue.get())
        assert app.engine.state.game.scores["A"] == 3
        assert app.store.restore().state.game.scores["A"] == 3
    finally:
        app.close()


def test_maintenance_lock_blocks_rf_and_web_commands(tmp_path):
    from pickleball_scoreboard.services.scoreboard_bridge import WebCommand

    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.start()
        (tmp_path / "data/court_switch.lock").write_text("Moving court")
        app.command(Command("TEAM_A_POINT", monotonic()))
        assert app.engine.state.game.scores["A"] == 0

        class Bridge:
            def complete(self, command, error=None):
                self.error = error

        fake = Bridge()
        app.bridge = fake
        app.command(WebCommand("TEAM_A_POINT", {}, "test-request"))
        assert "moving" in fake.error
        app.bridge = None
        (tmp_path / "data/court_switch.lock").unlink()
        app.command(Command("TEAM_A_POINT", monotonic(), source="gpio", pin=17, activation=1))
        assert app.engine.state.game.scores["A"] == 1
    finally:
        app.bridge = None
        app.close()


@pytest.mark.parametrize("team", ["A", "B"])
def test_ready_review_back_preserves_match_then_a_starts_without_scoring(tmp_path, team):
    from pickleball_scoreboard.ui.ready_screen import ReadyScreen
    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        previous = app.engine
        before = previous.state.to_dict()
        app.request_start()
        assert isinstance(app.dialog, ReadyScreen)
        for size in ((1920, 1080), (1280, 720)):
            app.dialog.draw(pygame.Surface(size))
            assert len(app.dialog.buttons) == 2
        for ignored in (f"TEAM_{team}_UNDO", f"TEAM_{team}_TIMEOUT", "RESET_REQUEST"):
            app.command(Command(ignored, app.dialog.opened_at + 0.01))
            assert isinstance(app.dialog, ReadyScreen)
            assert app.engine is previous and app.engine.state.to_dict() == before
        app.command(Command(f"TEAM_{team}_SERVICE", app.dialog.opened_at + 0.01))
        assert app.dialog is None and app.view == "setup"
        assert app.engine is previous and app.engine.state.to_dict() == before
        app.request_start()
        app.command(Command(f"TEAM_{team}_POINT", app.dialog.opened_at + 0.01))
        assert app.dialog is None and app.view == "scoreboard"
        assert app.timeout_team is None
        assert app.engine.state.game.scores == {"A": 0, "B": 0}
    finally:
        app.close()


@pytest.mark.parametrize("confirmation", ["remote", "operator", "cancel"])
def test_managed_ready_assignment_waits_for_confirmation_and_survives_restart(tmp_path, confirmation):
    from pickleball_scoreboard.services.scoreboard_bridge import WebCommand
    from uuid import uuid4
    class Bridge:
        def __init__(self):
            self.error = None
            self.events = []
        def publish(self, snapshot, *args):
            self.events.append(snapshot)
        def complete(self, command, error=None):
            self.error = error
    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.bridge = Bridge()
        app.web_command(WebCommand("START_MATCH", {"match_id": 99, "court_id": 1, "court_name": "COURT 1", "settings": {"players_a": "Larry / Bryan", "players_b": "Gail / Raffy"}}, str(uuid4())))
        assert app.bridge.error is None
        assert app.dialog.kind == "managed_start"
        assert app.external_context["ready_to_play"] and app.web_started_at is None
        assert app.engine.state.paused
        app.command(Command("TEAM_A_SERVICE", app.dialog.opened_at + .01))
        assert app.dialog is None and app.web_match_id == 99
        app.command(Command("TEAM_A_POINT", monotonic()))
        assert app.engine.state.game.scores == {"A": 0, "B": 0}
        app.bridge = None
        app.close()
        app = Application(tmp_path, keyboard=True, windowed=True)
        app.bridge = Bridge()
        assert app.dialog.kind == "managed_start" and app.web_started_at is None
        if confirmation == "remote":
            lock = tmp_path / "data/court_switch.lock"
            lock.touch()
            app.confirm(True)
            assert app.external_context["ready_to_play"] and app.web_started_at is None
            lock.unlink()
            app.command(Command("TEAM_A_POINT", app.dialog.opened_at + .01))
        else:
            app.web_command(WebCommand("RESUME" if confirmation == "operator" else "END_MATCH", {"match_id": 99}, str(uuid4())))
            assert app.bridge.error is None
        assert not app.external_context.get("ready_to_play")
        assert app.engine.state.game.scores == {"A": 0, "B": 0}
        if confirmation == "cancel":
            assert app.web_started_at is None and app.engine.state.status.value == "ended"
        else:
            assert app.web_started_at is not None and not app.engine.state.paused
            assert app.web_match_id == 99 and app.dialog is None and app.view == "scoreboard"
    finally:
        app.bridge = None
        app.close()


def test_hdmi_elapsed_clock_persistence_completion_and_reset(tmp_path):
    app = Application(tmp_path,keyboard=True,windowed=True)
    try:
        app.start()
        start = app.external_context['started_at']
        assert app.elapsed_time(start + 65) == '01:05'
        assert app.elapsed_time(start + 3661) == '01:01:01'
        app.act('DECLARE_WINNER', {'scores':{'A':11,'B':0},'winner':'A'})
        app.external_context['ended_at'] = start + 125
        assert app.elapsed_time(start + 9999) == '02:05'
        app.save()
        app.close()
        app = Application(tmp_path,keyboard=True,windowed=True)
        assert app.elapsed_time(start + 9999) == '02:05'
        app.act('TEAM_A_UNDO')
        assert app.external_context['ended_at'] is None
        assert app.elapsed_time(start + 130) == '02:10'
        app.act('RESET')
        assert app.elapsed_time(app.external_context['started_at'] + 2) == '00:02'
    finally:
        app.close()


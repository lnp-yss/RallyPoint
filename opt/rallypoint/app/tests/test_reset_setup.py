import os

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pytest

pytest.importorskip("pygame")
from time import monotonic
from pickleball_scoreboard.app import Application
from pickleball_scoreboard.hardware.commands import Command


@pytest.mark.parametrize("team", ["A", "B"])
def test_long_d_then_c_opens_setup_without_undo(tmp_path, team):
    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        app.start()
        app.act("TEAM_A_POINT")
        before = app.engine.state.to_dict()
        now = monotonic()
        app.keyboard.edges.press(f"TEAM_{team}_TIMEOUT", now=now - 3.1)
        app.keyboard.edges.release(f"TEAM_{team}_TIMEOUT", now=now)
        app.command(app.queue.get())
        assert app.dialog.kind == "reset"
        app.command(Command(f"TEAM_{team}_UNDO", app.dialog.opened_at + 0.01))
        assert app.dialog is None and app.view == "setup"
        assert app.engine.state.to_dict() == before
        assert app.setup.settings.team_a == app.engine.settings.team_a
        app.setup.settings.team_a = "NEW TEAM"
        app.start()
        assert app.view == "scoreboard"
        assert app.engine.settings.team_a == "NEW TEAM"
        assert app.engine.state.game.scores == {"A": 0, "B": 0}
    finally:
        app.close()

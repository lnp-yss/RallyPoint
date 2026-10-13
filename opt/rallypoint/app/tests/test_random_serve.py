from pickleball_scoreboard.config import Settings
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.scoring.game_state import Team
from pickleball_scoreboard.services.persistence import Persistence


def test_random_draw_once_restore_undo_and_alternate(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr("pickleball_scoreboard.config.secrets.choice", lambda teams: calls.append(teams) or "B")
    settings = Settings()
    assert settings.starting_team == "Random"
    engine = ScoringEngine(settings)
    assert settings.first_serving_team is None
    assert engine.state.game.serving == Team.B
    assert engine.state.game.server == 2
    engine.apply("TEAM_B_POINT")
    engine.apply("TEAM_B_UNDO")
    assert engine.state.game.serving == Team.B
    store = Persistence(tmp_path)
    store.save(engine)
    restored = store.restore()
    assert restored.settings.first_serving_team == "B"
    for _ in range(11):
        restored.apply("TEAM_B_POINT")
    restored.apply("NEXT_GAME")
    assert restored.state.game.serving == Team.A
    for _ in range(11):
        restored.apply("TEAM_A_POINT")
    restored.apply("NEXT_GAME")
    assert restored.state.game.serving == Team.B
    assert len(calls) == 1


def test_explicit_selection_never_draws(monkeypatch):
    def fail(teams):
        raise AssertionError("Explicit selection must not draw")
    monkeypatch.setattr("pickleball_scoreboard.config.secrets.choice", fail)
    for team in ("A", "B"):
        engine = ScoringEngine(Settings(starting_team=team))
        assert engine.state.game.serving == Team(team)


def test_ready_back_does_not_redraw(monkeypatch, tmp_path):
    from pickleball_scoreboard.app import Application
    calls = []
    monkeypatch.setattr("pickleball_scoreboard.config.secrets.choice", lambda teams: calls.append(teams) or "B")
    app = Application(tmp_path, keyboard=True, windowed=True)
    try:
        assert app.setup.settings.starting_team == "Random"
        calls.clear()
        app.request_start()
        assert app.dialog.settings.first_serving_team == "B"
        app.confirm(False)
        app.request_start()
        app.confirm(True)
        assert app.engine.state.game.serving == Team.B
        assert len(calls) == 1
        app.open_setup()
        assert app.setup.settings.starting_team == "Random"
        assert app.setup.settings.first_serving_team is None
    finally:
        app.close()

from pickleball_scoreboard.config import Settings
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.services.persistence import Persistence


def test_roundtrip_history(tmp_path):
    store = Persistence(tmp_path)
    e = ScoringEngine(Settings())
    e.apply("TEAM_A_POINT")
    e.apply("TEAM_A_TIMEOUT")
    assert store.save(e)
    restored = store.restore()
    assert restored.state == e.state
    restored.apply("TEAM_A_UNDO")
    assert restored.state.game.timeouts["A"] == 0
    restored.apply("TEAM_A_UNDO")
    assert restored.state.game.scores["A"] == 0


def test_corruption(tmp_path):
    store = Persistence(tmp_path)
    assert store.restore() is None
    (tmp_path / "current_match.json").write_text("{broken")
    assert store.restore() is None
    (tmp_path / "settings.json").write_text('{"win_by": 99}')
    assert store.settings().win_by == 2

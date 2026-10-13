import json
import pytest
from pickleball_scoreboard.config import Settings
from pickleball_scoreboard.scoring.game_state import MatchState
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.services.persistence import Persistence


def test_failed_replace_preserves_save(tmp_path, monkeypatch):
    store = Persistence(tmp_path)
    e = ScoringEngine(Settings())
    store.save(e)
    before = (tmp_path / "current_match.json").read_bytes()
    e.apply("TEAM_A_POINT")

    def fail(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(
        "pickleball_scoreboard.services.persistence.os.replace", fail
    )
    assert not store.save(e)
    assert (tmp_path / "current_match.json").read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"version": 99},
        {"version": 1, "settings": {}, "state": {"game": {}}},
    ],
)
def test_malformed_snapshot(tmp_path, payload):
    (tmp_path / "current_match.json").write_text(json.dumps(payload))
    assert Persistence(tmp_path).restore() is None


def test_game_win_resume_undo(tmp_path):
    store = Persistence(tmp_path)
    e = ScoringEngine(Settings())
    for _ in range(11):
        e.apply("TEAM_A_POINT")
    store.save(e)
    restored = store.restore()
    assert restored.state.status == "game_over"
    restored.apply("NEXT_GAME")
    store.save(restored)
    restored = store.restore()
    restored.apply("TEAM_A_UNDO")
    assert restored.state.game.scores["A"] == 10
    assert restored.state.games_won["A"] == 0
    assert restored.state.game_number == 1

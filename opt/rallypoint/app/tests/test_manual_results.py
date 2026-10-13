"""Manual adjudication uses the same undo and recovery path as RF scoring."""

import pytest
from pickleball_scoreboard.config import Settings
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.scoring.game_state import MatchState
from pickleball_scoreboard.services.persistence import Persistence


def test_manual_scores_do_not_auto_complete_and_undo_restores_service():
    engine = ScoringEngine(Settings())
    engine.apply("TEAM_A_SERVICE")
    before = engine.state.to_dict()
    assert engine.apply("SET_SCORES", {"scores": {"A": 21, "B": 3}}) == "SCORES UPDATED"
    assert engine.state.status == "playing"
    assert engine.state.game.serving == before["game"]["serving"]
    engine.apply("TEAM_A_UNDO")
    assert engine.state.to_dict() == before


@pytest.mark.parametrize("scores", [None, {"A": 1}, {"A": -1, "B": 0},
    {"A": True, "B": 0}, {"A": 1.5, "B": 2}, {"A": "5", "B": 2},
    {"A": 1000, "B": 0}, {"A": 1, "B": 2, "C": 3}])
def test_invalid_manual_scores_do_not_mutate_history(scores):
    engine = ScoringEngine(Settings())
    before = engine.state.to_dict()
    with pytest.raises(ValueError):
        engine.apply("SET_SCORES", {"scores": scores})
    assert engine.state.to_dict() == before
    assert len(engine.history) == 0


def test_declare_winner_with_scores_is_one_atomic_undo(tmp_path):
    engine = ScoringEngine(Settings(best_of=5))
    engine.apply("TEAM_A_POINT")
    engine.apply("PAUSE")
    before = engine.state.to_dict()
    engine.apply("DECLARE_WINNER", {"winner": "B", "scores": {"A": 8, "B": 9}})
    assert engine.state.status == "complete" and engine.state.winner == "B"
    assert not engine.state.paused
    assert engine.state.games_won == {"A": 0, "B": 1}
    assert engine.state.completed_games == [{"A": 8, "B": 9}]
    store = Persistence(tmp_path)
    assert store.save(engine)
    restored = store.restore()
    assert restored.state.manual_game_winner == "B"
    restored.apply("TEAM_B_UNDO")
    assert restored.state.to_dict() == before


def test_forfeit_winner_can_differ_from_score_and_duplicate_is_ignored():
    engine = ScoringEngine(Settings())
    engine.apply("DECLARE_WINNER", {"winner": "B", "scores": {"A": 8, "B": 2}})
    assert engine.state.winner == "B"
    assert engine.apply("DECLARE_WINNER", {"winner": "A"}) is None
    assert len(engine.history) == 1


def test_declare_after_game_win_does_not_count_game_twice():
    engine = ScoringEngine(Settings(best_of=3))
    for _ in range(11):
        engine.apply("TEAM_A_POINT")
    before = engine.state.to_dict()
    engine.apply("DECLARE_WINNER", {"winner": "A"})
    assert engine.state.games_won == {"A": 1, "B": 0}
    assert len(engine.state.completed_games) == 1
    engine.apply("TEAM_A_UNDO")
    assert engine.state.to_dict() == before


def test_old_snapshot_loads_with_manual_result_defaults():
    data = ScoringEngine(Settings()).state.to_dict()
    del data["manual_game_winner"]
    assert MatchState.from_dict(data).manual_game_winner is None

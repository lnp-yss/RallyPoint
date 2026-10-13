import pytest
from pickleball_scoreboard.config import Settings
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.scoring.game_state import MatchStatus, Team


def test_first_service_and_servers():
    e = ScoringEngine(Settings())
    assert e.state.game.scores == {"A": 0, "B": 0}
    assert e.state.game.server == 2
    assert e.apply("TEAM_A_POINT") == "POINT"
    assert e.apply("TEAM_A_SERVICE") == "SIDE OUT"
    assert e.state.game.serving == Team.B and e.state.game.server == 1
    assert e.apply("TEAM_A_POINT") == "SERVER 2"
    assert e.state.game.scores == {"A": 1, "B": 0}
    assert e.apply("TEAM_B_SERVICE") == "SIDE OUT"
    assert e.state.game.serving == Team.A and e.state.game.server == 1


def test_singles_sideout():
    e = ScoringEngine(Settings(game_type="Singles"))
    assert e.apply("TEAM_B_POINT") == "SIDE OUT"
    assert e.state.game.server == 1


@pytest.mark.parametrize("best_of,needed", [(1, 1), (3, 2), (5, 3)])
def test_match_completion(best_of, needed):
    e = ScoringEngine(Settings(scoring_mode="Rally", best_of=best_of))
    for game in range(needed):
        for _ in range(11):
            e.apply("TEAM_A_POINT")
        assert e.state.games_won["A"] == game + 1
        if game + 1 < needed:
            assert e.state.status == MatchStatus.GAME_OVER
            e.apply("NEXT_GAME")
    assert e.state.status == MatchStatus.COMPLETE
    assert e.apply("TEAM_B_POINT") is None


@pytest.mark.parametrize("mode", ["Side-Out", "Rally"])
def test_win_by_two(mode):
    e = ScoringEngine(Settings(scoring_mode=mode))
    e.state.game.scores = {"A": 10, "B": 10}
    e.apply("TEAM_A_POINT")
    assert e.state.status == MatchStatus.PLAYING
    e.apply("TEAM_A_POINT")
    assert e.state.status == MatchStatus.GAME_OVER


@pytest.mark.parametrize(
    "command", ["TEAM_A_POINT", "TEAM_A_SERVICE", "TEAM_A_TIMEOUT"]
)
def test_undo_exact(command):
    e = ScoringEngine(Settings())
    before = e.state.to_dict()
    e.apply(command)
    e.apply("TEAM_B_UNDO")
    assert e.state.to_dict() == before


def test_undo_server_change_and_transition():
    e = ScoringEngine(Settings())
    e.apply("TEAM_A_SERVICE")
    before = e.state.to_dict()
    e.apply("TEAM_B_SERVICE")
    e.apply("TEAM_A_UNDO")
    assert e.state.to_dict() == before
    e.state.game.scores["B"] = 10
    before = e.state.to_dict()
    e.apply("TEAM_B_POINT")
    e.apply("NEXT_GAME")
    e.apply("TEAM_A_UNDO")
    assert e.state.to_dict() == before


def test_timeout_limit_reset_and_history_bound():
    e = ScoringEngine(Settings())
    e.apply("TEAM_A_TIMEOUT")
    e.apply("TEAM_A_TIMEOUT")
    assert e.apply("TEAM_A_TIMEOUT") == "NO TIMEOUTS REMAINING"
    assert len(e.history) == 2
    e.apply("RESET")
    assert e.state.game.timeouts["A"] == 0
    e.apply("TEAM_A_UNDO")
    assert e.state.game.timeouts["A"] == 2
    for _ in range(150):
        e.apply("RESET")
    assert len(e.history) == 100


@pytest.mark.parametrize("rule", ["Rally winner", "Alternate every rally"])
def test_rally_service(rule):
    e = ScoringEngine(Settings(scoring_mode="Rally", rally_service=rule))
    e.apply("TEAM_A_POINT")
    assert e.state.game.scores["A"] == 1
    assert e.state.game.serving == (
        Team.A if rule == "Rally winner" else Team.B
    )


def test_win_by_one():
    e = ScoringEngine(Settings(win_by=1))
    e.state.game.scores = {"A": 10, "B": 10}
    e.apply("TEAM_A_POINT")
    assert e.state.status == MatchStatus.GAME_OVER

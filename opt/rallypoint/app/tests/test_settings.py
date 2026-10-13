import pytest
from pickleball_scoreboard.config import Settings


@pytest.mark.parametrize(
    "values",
    [
        dict(points_to_win=12),
        dict(best_of=2),
        dict(timeout_limit=-1),
        dict(long_press_seconds=1),
    ],
)
def test_invalid_settings(values):
    with pytest.raises(ValueError):
        Settings(**values).validate()


@pytest.mark.parametrize("target", [11, 15, 21])
def test_targets(target):
    from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine

    e = ScoringEngine(Settings(points_to_win=target))
    for _ in range(target - 1):
        e.apply("TEAM_A_POINT")
    assert e.apply("TEAM_A_POINT") == "GAME WON"

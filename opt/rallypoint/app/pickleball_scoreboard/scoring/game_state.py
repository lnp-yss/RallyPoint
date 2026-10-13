from dataclasses import dataclass, field, asdict
from enum import Enum


class Team(str, Enum):
    A = "A"
    B = "B"

    @property
    def other(self):
        return Team.B if self == Team.A else Team.A


class MatchStatus(str, Enum):
    PLAYING = "playing"
    GAME_OVER = "game_over"
    COMPLETE = "complete"
    ENDED = "ended"


@dataclass
class GameState:
    scores: dict = field(default_factory=lambda: {"A": 0, "B": 0})
    serving: Team = Team.A
    server: int = 2
    first_service: bool = True
    service_side: str | None = "Right"
    timeouts: dict = field(default_factory=lambda: {"A": 0, "B": 0})


@dataclass
class MatchState:
    game: GameState = field(default_factory=GameState)
    game_number: int = 1
    games_won: dict = field(default_factory=lambda: {"A": 0, "B": 0})
    status: MatchStatus = MatchStatus.PLAYING
    winner: str | None = None
    completed_games: list = field(default_factory=list)
    paused: bool = False
    manual_game_winner: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MatchState":
        # V1/V2 snapshots predate persisted pause status.
        data = dict(data) if isinstance(data, dict) else data
        if isinstance(data, dict):
            data.setdefault("paused", False)
            data.setdefault("manual_game_winner", None)
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("Incomplete match snapshot")
        if isinstance(data.get("game"), dict):
            data["game"] = dict(data["game"])
            # Older saves did not track the service court; reconstruct from history.
            data["game"].setdefault("service_side", None)
        if not isinstance(data["game"], dict) or set(data["game"]) != set(
            GameState.__dataclass_fields__
        ):
            raise ValueError("Incomplete game snapshot")
        game = GameState(**data["game"])
        game.serving = Team(game.serving)
        result = cls(game=game, **{k: v for k, v in data.items() if k != "game"})
        result.status = MatchStatus(result.status)
        for counts in (game.scores, game.timeouts, result.games_won):
            if set(counts) != {"A", "B"} or any(
                type(v) is not int or v < 0 for v in counts.values()
            ):
                raise ValueError("Invalid counts")
        if (
            game.server not in (1, 2)
            or game.service_side not in (None, "Left", "Right")
            or type(game.first_service) is not bool
            or result.game_number < 1
        ):
            raise ValueError("Invalid game state")
        if result.winner not in (None, "A", "B"):
            raise ValueError("Invalid winner")
        if result.manual_game_winner not in (None, "A", "B"):
            raise ValueError("Invalid manual game winner")
        if (
            result.status in (MatchStatus.COMPLETE, MatchStatus.GAME_OVER)
            and result.winner is None
        ):
            raise ValueError("Finished game requires a winner")
        if type(result.paused) is not bool:
            raise ValueError("Invalid pause state")
        if not isinstance(result.completed_games, list):
            raise ValueError("Invalid completed games")
        for scores in result.completed_games:
            if (
                not isinstance(scores, dict)
                or set(scores) != {"A", "B"}
                or any(type(v) is not int or v < 0 for v in scores.values())
            ):
                raise ValueError("Invalid completed score")
        return result

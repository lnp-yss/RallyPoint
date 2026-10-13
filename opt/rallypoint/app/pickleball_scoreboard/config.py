"""Validated application and match settings."""

from dataclasses import dataclass, asdict, field
import secrets


@dataclass
class Settings:
    play_mode: str = "Casual Play"
    scoreboard_header: str = "RALLY POINT"
    club_name: str = "RALLY POINT PICKLEBALL CLUB"
    team_a: str = "TEAM A"
    team_b: str = "TEAM B"
    players_a: str = ""
    players_b: str = ""
    game_type: str = "Doubles"
    scoring_mode: str = "Side-Out"
    points_to_win: int = 11
    win_by: int = 2
    best_of: int = 3
    timeout_limit: int = 2
    rally_service: str = "Rally winner"
    starting_team: str = "Random"
    first_serving_team: str | None = None
    court_name: str = ""
    starting_side: str = "Right"
    fullscreen: bool = True
    width: int = 1920
    height: int = 1080
    sound_enabled: bool = True
    debounce_seconds: float = 0.03
    duplicate_seconds: float = 0.3
    long_press_seconds: float = 3.0
    gpio_pins: dict = field(
        default_factory=lambda: {"A": [17, 27, 22, 23], "B": [5, 6, 13, 19]}
    )

    def validate(self) -> "Settings":
        if self.play_mode not in ("Casual Play", "Club Managed"):
            raise ValueError("Invalid court play mode")
        for name in (
            "points_to_win",
            "win_by",
            "best_of",
            "timeout_limit",
            "width",
            "height",
        ):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        for name in ("fullscreen", "sound_enabled"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        for name in (
            "scoreboard_header",
            "club_name",
            "team_a",
            "team_b",
            "players_a",
            "players_b",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > 100:
                raise ValueError(f"{name} must be text, at most 100 characters")
        if not self.scoreboard_header.strip():
            raise ValueError("Scoreboard header is required")
        if not self.team_a.strip() or not self.team_b.strip():
            raise ValueError("Both teams need names")
        if self.starting_side not in ("Left", "Right") or not isinstance(
            self.court_name, str
        ):
            raise ValueError("Invalid court or starting side")
        if self.game_type not in (
            "Singles",
            "Doubles",
        ) or self.scoring_mode not in ("Side-Out", "Rally"):
            raise ValueError("Invalid scoring or game type")
        if (
            self.points_to_win not in (11, 15, 21)
            or self.win_by not in (1, 2)
            or self.best_of not in (1, 3, 5)
        ):
            raise ValueError("Invalid match format")
        if not 0 <= self.timeout_limit <= 9 or self.starting_team not in (
            "Random",
            "A",
            "B",
        ):
            raise ValueError("Invalid timeout limit or starting team")
        if self.first_serving_team not in (None, "A", "B"):
            raise ValueError("Invalid first serving team")
        if self.rally_service not in ("Rally winner", "Alternate every rally"):
            raise ValueError("Invalid rally service rule")
        if (
            min(self.width, self.height) < 320
            or not 0.01 <= self.debounce_seconds <= 1
            or not 0 <= self.duplicate_seconds <= 2
            or self.long_press_seconds < 3
        ):
            raise ValueError("Invalid display or timing settings")
        pins = [p for team in ("A", "B") for p in self.gpio_pins[team]]
        if (
            len(pins) != 8
            or len(set(pins)) != 8
            or any(type(p) is not int or not 0 <= p <= 27 for p in pins)
        ):
            raise ValueError("GPIO pins must be eight unique BCM inputs")
        return self

    def resolve_first_serve(self) -> "Settings":
        """Draw once and retain the result for confirmation and recovery."""
        self.validate()
        if self.starting_team != "Random":
            self.first_serving_team = self.starting_team
        elif self.first_serving_team is None:
            self.first_serving_team = secrets.choice(("A", "B"))
        return self

    def to_dict(self) -> dict:
        return asdict(self)

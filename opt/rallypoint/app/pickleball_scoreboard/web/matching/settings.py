"""Session settings shared by all matchmaking strategies."""

from dataclasses import dataclass, asdict
import math

MODES = (
    "FAIR_BALANCED",
    "QUEUE",
    "RANDOM",
    "SKILL_BASED",
    "WINNERS_STAY",
    "KING_OF_COURT",
    "MANUAL",
)


@dataclass
class MatchingSettings:
    mode: str = "FAIR_BALANCED"
    automatic_assignment: bool = False
    waiting_weight: float = 35
    skill_weight: float = 30
    partner_weight: float = 15
    opponent_weight: float = 10
    games_weight: float = 10
    excellent_difference: float = 0.25
    good_difference: float = 0.5
    maximum_rating_difference: float = 0.75
    preferred_rating_difference: float = 0.5
    minimum_match_quality: float = 40
    grace_period_seconds: int = 120
    long_wait_minutes: float = 15
    maximum_wait_override_minutes: float = 20
    heartbeat_timeout_seconds: int = 30
    maximum_consecutive_wins: int = 3
    candidate_limit: int = 12
    default_rating: float = 3
    scoring_mode: str = "Side-Out"
    points_to_win: int = 11
    win_by: int = 2
    best_of: int = 1

    def validate(self):
        for key, value in asdict(self).items():
            if key not in ("mode", "automatic_assignment", "scoring_mode") and type(
                value
            ) not in (int, float):
                raise ValueError(f"{key} must be a number")
        if self.mode not in MODES or type(self.automatic_assignment) is not bool:
            raise ValueError("Invalid matchmaking mode or automatic-assignment setting")
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and (
                not math.isfinite(value) or value < 0
            ):
                raise ValueError("Settings must be finite non-negative numbers")
        if not 4 <= self.candidate_limit <= 20 or type(self.candidate_limit) is not int:
            raise ValueError("Candidate search limit must be an integer from 4 to 20")
        if (
            not 1 <= self.maximum_consecutive_wins <= 20
            or type(self.maximum_consecutive_wins) is not int
        ):
            raise ValueError("Consecutive win limit must be 1–20")
        if not 10 <= self.heartbeat_timeout_seconds <= 300:
            raise ValueError("Heartbeat timeout must be 10–300 seconds")
        if not 1 <= self.default_rating <= 8 or self.grace_period_seconds > 3600:
            raise ValueError(
                "Default rating must be 1–8; grace period at most 3600 seconds"
            )
        if not 0 < self.long_wait_minutes < self.maximum_wait_override_minutes:
            raise ValueError(
                "Long-wait threshold must be below the maximum-wait override"
            )
        if (
            not self.excellent_difference
            <= self.good_difference
            <= self.maximum_rating_difference
        ):
            raise ValueError("Rating thresholds must be in increasing order")
        if (
            not self.preferred_rating_difference <= self.maximum_rating_difference
            or not 0 <= self.minimum_match_quality <= 100
        ):
            raise ValueError("Invalid preferred rating difference or quality threshold")
        if (
            not 0
            < self.waiting_weight
            + self.skill_weight
            + self.partner_weight
            + self.opponent_weight
            + self.games_weight
            <= 10000
        ):
            raise ValueError("Matchmaking weights must have a positive total")
        from ...config import Settings

        Settings(
            scoring_mode=self.scoring_mode,
            points_to_win=self.points_to_win,
            win_by=self.win_by,
            best_of=self.best_of,
        ).validate()
        return self

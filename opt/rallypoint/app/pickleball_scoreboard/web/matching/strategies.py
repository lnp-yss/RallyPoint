"""Strategy selection with shared normalized explanation and long-wait safety."""

from dataclasses import asdict
from random import SystemRandom
from .candidates import candidates


def explain(candidate, settings):
    a, b = candidate.team_a, candidate.team_b
    rating = lambda team: sum(
        (
            p.get("skill_rating")
            if p.get("skill_rating") is not None
            else settings.default_rating
        )
        for p in team
    ) / len(team)
    difference = abs(rating(a) - rating(b))
    wait = max(p["waiting_minutes"] for p in candidate.players)
    weighted_wait = sum(
        min(1, p["waiting_minutes"] / settings.maximum_wait_override_minutes) ** 1.5
        for p in candidate.players
    ) / len(candidate.players)

    def repetition(player, peer, kind):
        return sum(
            1 / (index + 1)
            for index, identifier in enumerate(player.get(kind, []))
            if identifier == peer["id"]
        )

    partner = sum(
        sum(repetition(p, q, "recent_partners") for q in team if q["id"] != p["id"])
        for team in (a, b)
        for p in team
        if p.get("entry_type") != "TEAM"
    )
    opponent = sum(repetition(p, q, "recent_opponents") for p in a for q in b) + sum(
        repetition(p, q, "recent_opponents") for p in b for q in a
    )
    values = {
        "waiting": weighted_wait,
        "skill": max(0, 1 - difference / max(settings.maximum_rating_difference, 0.01)),
        "partner": 1 / (1 + partner),
        "opponent": 1 / (1 + opponent),
        "games": sum(1 / (1 + p.get("games_today", 0)) for p in candidate.players)
        / len(candidate.players),
    }
    weights = {
        "waiting": settings.waiting_weight,
        "skill": settings.skill_weight,
        "partner": settings.partner_weight,
        "opponent": settings.opponent_weight,
        "games": settings.games_weight,
    }
    total = sum(weights.values())
    breakdown = {
        key: round(100 * weights[key] * value / total, 2)
        for key, value in values.items()
    }
    return {
        "quality": round(sum(breakdown.values()), 2),
        "breakdown": breakdown,
        "team_a_rating": round(rating(a), 3),
        "team_b_rating": round(rating(b), 3),
        "imbalance": round(difference, 3),
        "longest_wait": wait,
        "rating_quality": (
            "EXCELLENT"
            if difference <= settings.excellent_difference
            else (
                "GOOD"
                if difference <= settings.good_difference
                else (
                    "ACCEPTABLE"
                    if difference <= settings.maximum_rating_difference
                    else "WAIT_OVERRIDE"
                )
            )
        ),
        "reasons": [
            "Fixed teams preserved",
            "Waiting fairness overrides skill preferences at maximum wait",
        ],
    }


class MatchmakingStrategy:
    def rank(self, candidate, explanation, settings):
        raise NotImplementedError

    def select(
        self,
        players,
        settings,
        *,
        singles=False,
        held=(),
        grace_elapsed=0,
        excluded=None
    ):
        best, best_key = None, None
        overdue = {
            p["id"]
            for p in sorted(players, key=lambda p: (p["position"], p["id"]))
            if p["waiting_minutes"] >= settings.maximum_wait_override_minutes
        }
        # Require the oldest overdue unit if a valid match can include it.
        oldest = next(
            (
                p["id"]
                for p in sorted(players, key=lambda p: (-p["waiting_minutes"], p["id"]))
                if p["id"] in overdue
            ),
            None,
        )
        for candidate in candidates(players, settings, singles, held):
            ids = sorted(p["id"] for p in candidate.players)
            if ids in (excluded or []):
                continue
            explanation = explain(candidate, settings)
            key = (
                oldest in ids if oldest else False,
                self.rank(candidate, explanation, settings),
            )
            if self.requires_quality:
                wait = explanation["longest_wait"]
                if wait < settings.maximum_wait_override_minutes:
                    relaxation = max(0, wait - settings.long_wait_minutes) / (
                        settings.maximum_wait_override_minutes
                        - settings.long_wait_minutes
                    )
                    limit = settings.maximum_rating_difference * (1 + relaxation)
                    if (
                        explanation["imbalance"] > limit
                        or explanation["quality"] < settings.minimum_match_quality
                    ) and grace_elapsed < settings.grace_period_seconds:
                        continue
            if best_key is None or key > best_key:
                best_key = key
                best = {
                    "team_a": [p["id"] for p in candidate.team_a],
                    "team_b": [p["id"] for p in candidate.team_b],
                    **explanation,
                }
        return best

    requires_quality = False


class FairBalancedStrategy(MatchmakingStrategy):
    requires_quality = True

    def rank(self, candidate, explanation, settings):
        return explanation["quality"] + sum(
            p.get("priority", 0) * 100 for p in candidate.players
        )


class QueueStrategy(MatchmakingStrategy):
    def rank(self, candidate, explanation, settings):
        return (
            tuple(
                -p["position"]
                for p in sorted(candidate.players, key=lambda p: p["position"])
            ),
            -explanation["imbalance"],
        )


class RandomStrategy(MatchmakingStrategy):
    def rank(self, candidate, explanation, settings):
        return SystemRandom().random()

    def select(self, players, settings, **kwargs):
        shuffled = list(players)
        SystemRandom().shuffle(shuffled)
        # Randomize complete units before the bounded search, never their members.
        groups = {}
        for p in shuffled:
            groups.setdefault(p.get("group_id") or ("solo", p["id"]), []).append(p)
        flattened = []
        for index, members in enumerate(groups.values()):
            flattened.extend([{**p, "position": index} for p in members])
        return super().select(flattened, settings, **kwargs)


class SkillBasedStrategy(FairBalancedStrategy):
    def rank(self, candidate, explanation, settings):
        return (
            explanation["imbalance"] <= settings.preferred_rating_difference,
            -explanation["imbalance"],
            explanation["quality"],
        )


class WinnersStayStrategy(FairBalancedStrategy):
    pass


class ManualStrategy(MatchmakingStrategy):
    def select(self, *args, **kwargs):
        return None


class KingOfCourtStrategy(ManualStrategy):
    """Configuration exists; advanced promotion/demotion is explicitly unavailable."""


STRATEGIES = {
    "FAIR_BALANCED": FairBalancedStrategy,
    "QUEUE": QueueStrategy,
    "RANDOM": RandomStrategy,
    "SKILL_BASED": SkillBasedStrategy,
    "WINNERS_STAY": WinnersStayStrategy,
    "MANUAL": ManualStrategy,
    "KING_OF_COURT": KingOfCourtStrategy,
}


def strategy(mode):
    return STRATEGIES[mode]()

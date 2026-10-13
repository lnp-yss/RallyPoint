"""Transparent bounded search: overdue players first, then weighted quality."""

from dataclasses import dataclass, asdict
from itertools import combinations


@dataclass
class MatchmakingWeights:
    waiting: float = 4.0
    fairness: float = 3.0
    balance: float = 2.0
    partner_rotation: float = 5.0
    opponent_rotation: float = 1.0
    manual_priority: float = 20.0
    max_wait_minutes: float = 30.0
    candidate_limit: int = 10
    default_rating: float = 3.0


def recommend(players: list[dict], weights=None, singles=False, excluded=None):
    w = weights or MatchmakingWeights()
    count = 2 if singles else 4
    if len(players) < count:
        return None
    # An overdue player's seat is mandatory, regardless of skill imbalance.
    ordered = sorted(
        players,
        key=lambda p: (
            -int(p["waiting_minutes"] >= w.max_wait_minutes),
            -p.get("priority", 0),
            p["position"],
            -p["waiting_minutes"],
            p["id"],
        ),
    )
    overdue = [p["id"] for p in ordered if p["waiting_minutes"] >= w.max_wait_minutes][
        :count
    ]
    pool = ordered[: max(count, min(16, w.candidate_limit))]
    best = None
    best_quality = float("-inf")
    for group in combinations(pool, count):
        ids = {p["id"] for p in group}
        if not set(overdue).issubset(ids) or sorted(ids) in (excluded or []):
            continue
        pairs = (
            [(group[:1], group[1:])]
            if singles
            else [
                (
                    (group[0], group[i]),
                    tuple(p for j, p in enumerate(group) if j not in (0, i)),
                )
                for i in (1, 2, 3)
            ]
        )
        for a, b in pairs:
            rating = lambda team: sum(
                p.get("skill_rating") or w.default_rating for p in team
            ) / len(team)
            imbalance = abs(rating(a) - rating(b))
            partner = (
                sum(
                    q["id"] in p.get("recent_partners", [])
                    for team in (a, b)
                    for p in team
                    for q in team
                    if p is not q
                )
                / 2
            )
            opponent = sum(
                q["id"] in p.get("recent_opponents", []) for p in a for q in b
            )
            utility = sum(
                w.waiting * p["waiting_minutes"]
                - w.fairness * p["games_today"]
                + w.manual_priority * p.get("priority", 0)
                for p in group
            )
            quality = (
                utility
                - w.balance * imbalance
                - w.partner_rotation * partner
                - w.opponent_rotation * opponent
            )
            # Compare full precision; rounding display values must not reshuffle
            # an equally good recommendation as waiting time ticks forward.
            if best is None or quality > best_quality + 1e-9:
                best_quality = quality
                best = {
                    "team_a": [p["id"] for p in a],
                    "team_b": [p["id"] for p in b],
                    "quality": round(quality, 2),
                    "imbalance": round(imbalance, 2),
                    "reasons": [
                        (
                            "Overdue players guaranteed a place"
                            if overdue
                            else "Queue order and waiting time prioritized"
                        ),
                        f"Estimated team rating difference: {imbalance:.2f}",
                        (
                            "No recent repeated partners"
                            if not partner
                            else f"Recent partner repeats: {int(partner)}"
                        ),
                    ],
                }
    return best

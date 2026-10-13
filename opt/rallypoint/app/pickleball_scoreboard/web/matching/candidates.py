"""Pure candidate formation. Fixed TEAM units are never split."""

from itertools import combinations
from dataclasses import dataclass


@dataclass(frozen=True)
class Candidate:
    team_a: tuple
    team_b: tuple
    players: tuple


def units(players, singles=False):
    grouped = {}
    for player in players:
        key = player.get("group_id") or ("solo", player["id"])
        grouped.setdefault(key, []).append(player)
    result = []
    for members in grouped.values():
        kind = members[0].get("entry_type", "SOLO")
        if kind == "TEAM" and len(members) == 2 and not singles:
            result.append(tuple(members))
        elif kind == "SOLO" and len(members) == 1:
            result.append(tuple(members))
    return result


def candidates(players, settings, singles=False, held=()):
    ordered = sorted(
        players,
        key=lambda p: (
            p["waiting_minutes"] < settings.maximum_wait_override_minutes,
            (
                -p["waiting_minutes"]
                if p["waiting_minutes"] >= settings.maximum_wait_override_minutes
                else 0
            ),
            -p.get("priority", 0),
            p["position"],
            p["id"],
        ),
    )
    groups = units(ordered, singles)
    # Bound combinations, not court count. Keep complete units at the cutoff.
    pool, count = [], 0
    for unit in groups:
        if count >= settings.candidate_limit:
            break
        pool.append(unit)
        count += len(unit)
    size = 1 if singles else 2
    teams = [unit for unit in pool if len(unit) == size]
    if not singles:
        solos = [unit[0] for unit in pool if len(unit) == 1]
        teams.extend(combinations(solos, 2))
    if held:
        for challenger in teams:
            all_players = tuple(held) + tuple(challenger)
            if len({p["id"] for p in all_players}) == len(all_players):
                yield Candidate(tuple(held), tuple(challenger), all_players)
    else:
        for a, b in combinations(teams, 2):
            all_players = tuple(a) + tuple(b)
            if len({p["id"] for p in all_players}) == size * 2:
                yield Candidate(tuple(a), tuple(b), all_players)

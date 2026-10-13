"""Shared presentation metadata; browsers never implement scoring rules."""


def indicators(snapshot: dict) -> dict:
    settings, state = snapshot["settings"], snapshot["state"]
    game = state["game"]
    game_point, match_point = [], []
    if state["status"] == "playing" and not state.get("paused"):
        for team, other in [("A", "B"), ("B", "A")]:
            can_score = settings["scoring_mode"] == "Rally" or game["serving"] == team
            wins_next = (
                game["scores"][team] + 1 >= settings["points_to_win"]
                and game["scores"][team] + 1 - game["scores"][other]
                >= settings["win_by"]
            )
            if can_score and wins_next:
                game_point.append(team)
                if state["games_won"][team] + 1 >= settings["best_of"] // 2 + 1:
                    match_point.append(team)
    return {"game_point": game_point, "match_point": match_point,
            "service_side": game.get("service_side") if state["status"] == "playing" else None}

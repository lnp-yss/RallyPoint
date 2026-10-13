from .game_state import Team


def rally(game, winner: Team, settings):
    if winner == game.serving:
        game.scores[winner.value] += 1
        if game.service_side:
            game.service_side = "Left" if game.service_side == "Right" else "Right"
        return "POINT"
    if (
        settings.game_type == "Doubles"
        and not game.first_service
        and game.server == 1
    ):
        game.server = 2
        if game.service_side:
            game.service_side = "Left" if game.service_side == "Right" else "Right"
        return "SERVER 2"
    game.serving = game.serving.other
    game.server = 1
    game.first_service = False
    game.service_side = (
        "Right" if settings.game_type == "Doubles" or game.scores[game.serving.value] % 2 == 0
        else "Left"
    )
    return "SIDE OUT"

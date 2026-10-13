def rally(game, winner, settings):
    game.scores[winner.value] += 1
    game.serving = (
        winner
        if settings.rally_service == "Rally winner"
        else game.serving.other
    )
    game.server = 1
    game.first_service = False
    game.service_side = "Right" if game.scores[game.serving.value] % 2 == 0 else "Left"
    return "POINT"

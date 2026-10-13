import logging
from pathlib import Path
import pygame
from .theme import Canvas, BG, PANEL, WHITE, MUTED, COLORS
from ..scoring.game_state import MatchStatus


class ScoreboardScreen:
    def __init__(self, assets):
        self.logo = None
        self.scaled_logo = None
        self.logo_size = None
        path = Path(assets) / "logo.png"
        if path.exists():
            try:
                self.logo = pygame.image.load(str(path)).convert_alpha()
            except Exception:
                logging.warning("Logo could not be loaded")

    def draw(
        self,
        surface,
        engine,
        feedback,
        gpio_mode,
        paused=False,
        timeout_team=None,
        elapsed="00:00",
    ):
        c = Canvas(surface)
        surface.fill(BG)
        s, state = engine.settings, engine.state
        game = state.game
        c.text(s.scoreboard_header, 0.5, 0.055, 54, WHITE, .72)
        c.text(s.club_name, 0.5, 0.112, 38, MUTED, .84)
        if s.court_name:
            c.text(s.court_name, 0.90, 0.065, 25, MUTED, 0.17)
        c.text("ELAPSED", .90, .105, 21, MUTED, .17)
        c.text(elapsed, .90, .14, 38, WHITE, .17)
        if self.logo:
            size = (round(110 * c.scale), round(85 * c.scale))
            if size != self.logo_size:
                self.scaled_logo = pygame.transform.smoothscale(self.logo, size)
                self.logo_size = size
            surface.blit(self.scaled_logo, (round(c.w * 0.035), round(c.h * 0.035)))
        for team, x, center in [("A", 0.035, 0.265), ("B", 0.515, 0.745)]:
            color = COLORS[team]
            c.rect(x, 0.17, 0.45, 0.58, PANEL)
            c.rect(x, 0.17, 0.45, 0.008, color)
            if game.serving.value == team:
                c.rect(x, 0.17, 0.45, 0.58, color, max(2, round(4 * c.scale)))
            name = s.team_a if team == "A" else s.team_b
            players = s.players_a if team == "A" else s.players_b
            if game.serving.value == team and state.status == MatchStatus.PLAYING and game.service_side:
                badge_x = x + 0.012 if game.service_side == "Left" else x + 0.283
                c.rect(badge_x, 0.184, 0.155, 0.045, color, radius=9)
                c.text(game.service_side.upper() + " SERVICE", badge_x + 0.0775,
                       0.2065, 29, BG, 0.145)
            c.text(name, center, 0.263, 63, color, 0.40)
            c.text(
                game.scores[team],
                center,
                0.455,
                round(500 * feedback.pulse(team)),
                WHITE,
                0.42,
            )
            c.text(players, center, 0.665, 37, MUTED, 0.40)
            c.text(
                f"GAMES  {state.games_won[team]}",
                center - 0.09,
                0.720,
                32,
                color,
                0.2,
            )
            remaining = s.timeout_limit - game.timeouts[team]
            c.text(f"TIMEOUTS  {remaining}", center + 0.10, 0.720, 29, MUTED, 0.2)
        serving_name = s.team_a if game.serving.value == "A" else s.team_b
        dot_positions = (0.265,) if game.server == 1 else (0.245, 0.285)
        for x in dot_positions:
            # A contrasting rim keeps the large ball visible across the court.
            c.dot(x, 0.803, 32, WHITE)
            c.dot(x, 0.803, 28, COLORS[game.serving.value])
        c.text(
            f"SERVING  {serving_name}",
            0.59,
            0.802,
            46,
            COLORS[game.serving.value],
            0.53,
        )
        call = f"{game.scores[game.serving.value]} - {game.scores[game.serving.other.value]}"
        if s.scoring_mode == "Side-Out" and s.game_type == "Doubles":
            call += f" - {game.server}"
        c.text(call, 0.5, 0.861, 60)
        fmt = "SINGLE GAME" if s.best_of == 1 else f"BEST OF {s.best_of}"
        c.text(
            f"GAME {state.game_number}   /   {fmt}   /   {s.scoring_mode.upper()}   /   {s.game_type.upper()}",
            0.5,
            0.931,
            28,
            MUTED,
        )
        if "OFFLINE" in gpio_mode:
            c.text("GPIO OFFLINE  /  KEYBOARD MODE", 0.5, 0.98, 20, MUTED)
        if feedback.active:
            c.rect(
                0.31,
                0.125,
                0.38,
                0.044,
                COLORS.get(feedback.team, (75, 100, 121)),
            )
            c.text(feedback.message, 0.5, 0.147, 25, BG, 0.36)
        c.text("\u00a9 Larry Perico 2026 Version 0.0.1", 0.84, 0.988, 17, MUTED, 0.30)
        if state.status != MatchStatus.PLAYING:
            if state.status == MatchStatus.ENDED:
                self.banner(c, "MATCH ENDED", "")
                return
            winner = s.team_a if state.winner == "A" else s.team_b
            self.banner(
                c,
                (
                    "MATCH WINNER"
                    if state.status == MatchStatus.COMPLETE
                    else "GAME WON"
                ),
                winner,
            )
        elif paused or timeout_team:
            self.banner(
                c,
                "INPUT PAUSED" if paused else "TIMEOUT",
                ("" if paused else (s.team_a if timeout_team == "A" else s.team_b)),
            )

    @staticmethod
    def banner(c, title, detail):
        c.rect(0.14, 0.34, 0.72, 0.28, BG)
        c.rect(0.14, 0.34, 0.72, 0.28, MUTED, 2)
        c.text(title, 0.5, 0.42, 66)
        c.text(detail, 0.5, 0.53, 65, COLORS["A"], 0.66)

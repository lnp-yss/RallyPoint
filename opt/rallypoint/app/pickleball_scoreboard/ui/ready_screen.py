"""TV-sized pre-match review using the existing confirmation lifecycle."""
from copy import deepcopy
from .dialogs import Dialog
from .theme import Canvas, BG, PANEL, WHITE, MUTED, COLORS


class ReadyScreen(Dialog):
    """Keep setup and the previous match intact until start is confirmed."""

    def __init__(self, settings, managed=False):
        super().__init__("start", "READY TO PLAY", "START MATCH", "BACK")
        self.settings = deepcopy(settings)
        if managed:
            self.kind = "managed_start"

    def draw(self, surface, debug=False):
        surface.fill(BG)
        c = Canvas(surface)
        settings = self.settings
        c.text(settings.club_name, .5, .065, 54, COLORS["A"], .62)
        c.rect(.84, .025, .14, .075, PANEL)
        c.text(settings.court_name or "YOUR COURT", .91, .063, 40, WHITE, .12)
        c.text("READY TO PLAY", .5, .175, 140, WHITE, .93)
        for team, x in (("A", .025), ("B", .515)):
            color = COLORS[team]
            center = x + .23
            c.rect(x, .27, .46, .36, PANEL)
            c.rect(x, .27, .46, .36, color, 3)
            name = settings.team_a if team == "A" else settings.team_b
            players = settings.players_a if team == "A" else settings.players_b
            c.text(name, center, .33, 65, color, .42)
            c.text(players or "PLAYERS READY", center, .445, 88, WHITE, .42)
            if settings.first_serving_team == team:
                ball_x = center - .15
                c.dot(ball_x, .56, 36, (222, 239, 45))
                for dx, dy in ((0, 0), (-.011, -.016), (.011, -.016), (-.011, .016), (.011, .016)):
                    c.dot(ball_x + dx, .56 + dy, 5, BG)
                c.text("SERVES FIRST", center + .04, .56, 43, color, .29)
        if settings.starting_team == "Random":
            c.text("FIRST SERVE: RANDOM DRAW", .5, .235, 31, MUTED, .8)
        c.rect(.025, .66, .95, .085, PANEL)
        values = [settings.game_type.upper(), settings.scoring_mode.upper() + " SCORING",
                  str(settings.points_to_win) + " POINTS / WIN BY " + str(settings.win_by),
                  "SINGLE GAME" if settings.best_of == 1 else "BEST OF " + str(settings.best_of)]
        for index, label in enumerate(values):
            c.text(label, .145 + index * .237, .703, 37, WHITE, .22)
        start = c.rect(.18, .80, .42, .11, COLORS["A"])
        back = c.rect(.62, .80, .20, .11, PANEL)
        c.rect(.18 if self.selected else .62, .80, .42 if self.selected else .20, .11, WHITE, 2)
        self.buttons = [(start, True), (back, False)]
        c.text("A  START MATCH", .39, .855, 68, BG, .38)
        c.text("B  BACK", .72, .855, 48, WHITE, .17)
        # Secondary remote action shares the quiet footer baseline.
        c.text("C  CHANGE FIRST SERVE", .20, .95, 29, COLORS["B"], .34)
        c.text(("CLUB MANAGED / " if settings.play_mode == "Club Managed" else "") +
               "Check teams and settings before starting", .65, .95, 29, MUTED, .59)
        if debug:
            c.text("Keyboard: Y / N  ·  Remote simulation: 1 / 7 START, 2 / 8 BACK, 3 / 9 CHANGE SERVE", .5, .985, 23, MUTED)

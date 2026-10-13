from copy import deepcopy
import pygame
from .theme import Canvas, BG, PANEL, WHITE, MUTED, COLORS


class SetupScreen:
    fields = [
        ("play_mode", "COURT MODE", ["Casual Play", "Club Managed"]),
        ("team_a", "TEAM A NAME", None),
        ("team_b", "TEAM B NAME", None),
        ("players_a", "TEAM A PLAYERS ( / separated)", None),
        ("players_b", "TEAM B PLAYERS ( / separated)", None),
        ("game_type", "GAME TYPE", ["Singles", "Doubles"]),
        ("scoring_mode", "SCORING", ["Side-Out", "Rally"]),
        ("points_to_win", "POINTS TO WIN", [11, 15, 21]),
        ("win_by", "WIN BY", [1, 2]),
        ("best_of", "MATCH: BEST OF", [1, 3, 5]),
        ("timeout_limit", "TIMEOUTS PER GAME", list(range(10))),
        (
            "rally_service",
            "RALLY SERVICE RULE",
            ["Rally winner", "Alternate every rally"],
        ),
        ("starting_team", "FIRST SERVING TEAM", ["Random", "A", "B"]),
        ("sound_enabled", "SOUND ENABLED", [True, False]),
        ("club_name", "CLUB NAME", None),
    ]

    def __init__(self, settings):
        self.settings = deepcopy(settings)
        self.focus = 0
        self.boxes = []
        self.start_box = None
        self.error = ""
        self.editing = False
        self.character = 0
        self.alphabet = " ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-"

    def change(self, direction=1):
        key, _, choices = self.fields[self.focus]
        if choices:
            value = getattr(self.settings, key)
            setattr(
                self.settings,
                key,
                choices[(choices.index(value) + direction) % len(choices)],
            )

            if key == "starting_team":
                self.settings.first_serving_team = None

    def event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            if self.start_box and self.start_box.collidepoint(event.pos):
                return True
            for index, box in enumerate(self.boxes):
                if box.collidepoint(event.pos):
                    self.focus = index
                    self.change()
        if event.type == pygame.KEYDOWN:
            if event.key == pygame.K_TAB:
                self.focus = (
                    self.focus + (-1 if event.mod & pygame.KMOD_SHIFT else 1)
                ) % len(self.fields)
            elif event.key in (pygame.K_UP, pygame.K_DOWN):
                self.focus = (
                    self.focus + (1 if event.key == pygame.K_DOWN else -1)
                ) % len(self.fields)
            elif event.key in (pygame.K_LEFT, pygame.K_RIGHT):
                self.change(1 if event.key == pygame.K_RIGHT else -1)
            elif event.key == pygame.K_RETURN:
                return True
            elif event.key == pygame.K_BACKSPACE:
                key, _, choices = self.fields[self.focus]
                if choices is None:
                    setattr(
                        self.settings, key, getattr(self.settings, key)[:-1]
                    )
        if event.type == pygame.TEXTINPUT:
            key, _, choices = self.fields[self.focus]
            if choices is None:
                setattr(
                    self.settings,
                    key,
                    (getattr(self.settings, key) + event.text)[:60],
                )
        return False

    def command(self, name):
        """Four-button text entry: B cycles, A appends, C erases, D finishes."""
        key, _, choices = self.fields[self.focus]
        if self.editing:
            if name.endswith("_SERVICE"):
                self.character = (self.character + 1) % len(self.alphabet)
            elif name.endswith("_POINT"):
                setattr(self.settings, key, (getattr(self.settings, key) + self.alphabet[self.character])[:60])
            elif name.endswith("_UNDO"):
                setattr(self.settings, key, getattr(self.settings, key)[:-1])
            elif name.endswith("_TIMEOUT"):
                self.editing = False
            return False
        if name.endswith("_POINT"):
            self.focus = (self.focus + 1) % len(self.fields)
        elif name.endswith("_UNDO"):
            self.focus = (self.focus - 1) % len(self.fields)
        elif name.endswith("_SERVICE"):
            if choices is None:
                self.editing = True
            else:
                self.change()
        elif name.endswith("_TIMEOUT"):
            return True
        return False

    def draw(self, surface, debug=False):
        surface.fill(BG)
        c = Canvas(surface)
        c.text("MATCH SETUP", 0.5, 0.065, 66)
        c.text(
            "Configure your court. Start when both teams are ready.",
            0.5,
            0.12,
            29,
            MUTED,
        )
        self.boxes = []
        for index, (key, label, choices) in enumerate(self.fields):
            col, row = index % 2, index // 2
            x, y = 0.07 + col * 0.47, 0.165 + row * 0.078
            c.text(label, x + 0.195, y, 22, MUTED, 0.38)
            box = c.rect(x, y + 0.017, 0.39, 0.053, PANEL)
            self.boxes.append(box)
            if self.focus == index:
                c.rect(x, y + 0.017, 0.39, 0.053, COLORS["A"], 2)
            value = getattr(self.settings, key)
            if key == "starting_team":
                value = {"Random": "Random", "A": "Team A", "B": "Team B"}.get(value, "Random")
            c.text(
                str(value),
                x + 0.195,
                y + 0.043,
                30,
                WHITE,
                0.37,
            )
        self.start_box = c.rect(0.30, 0.825, 0.40, 0.073, COLORS["A"])
        c.text("START MATCH", 0.5, 0.862, 42, BG)
        if debug:
            c.text(
                "Tab: next field   /   arrows: change   /   Enter: start",
                0.5,
                0.932,
                24,
                MUTED,
            )
        c.text(
            ("EDIT: B = next letter [" + self.alphabet[self.character] + "]   A = append   C = erase   D = done"
             if self.editing else "Remote A: next   B: change / edit text   C: previous   D: start"),
            0.5,
            0.966,
            24,
            MUTED,
        )
        c.text("\u00a9 Larry Perico 2026 Version 0.0.1", 0.84, 0.988, 17, MUTED, 0.30)
        if self.error:
            c.text(self.error, 0.5, 0.79, 24, COLORS["B"])

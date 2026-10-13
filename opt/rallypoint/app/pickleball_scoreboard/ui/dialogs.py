from .theme import Canvas, BG, PANEL, WHITE, MUTED, COLORS
from time import monotonic


class Dialog:
    def __init__(self, kind, title, yes="YES", no="NO"):
        self.kind, self.title, self.yes, self.no = kind, title, yes, no
        self.selected = False  # Destructive action always defaults to NO.
        self.opened_at = monotonic()
        self.buttons = []

    def draw(self, surface, debug=False):
        c = Canvas(surface)
        c.rect(0.12, 0.24, 0.76, 0.50, BG)
        c.rect(0.12, 0.24, 0.76, 0.50, MUTED, 2)
        c.text(self.title, 0.5, 0.345, 66, WHITE, 0.7)
        self.buttons = []
        for value, x, label in [
            (True, 0.20, self.yes),
            (False, 0.52, self.no),
        ]:
            box = c.rect(
                x,
                0.47,
                0.28,
                0.105,
                COLORS["A"] if self.selected == value else PANEL,
            )
            self.buttons.append((box, value))
            c.text(
                label,
                x + 0.14,
                0.522,
                37,
                BG if self.selected == value else WHITE,
                0.25,
            )
        controls = (
            "Remote A: YES   /   B: NO   /   C: NEW MATCH SETUP"
            if self.kind == "reset"
            else "Remote A: YES   /   Remote B or C: NO"
        )
        c.text(controls, 0.5, 0.65, 27, MUTED, 0.70)
        if debug:
            c.text("Keyboard: Y / N   or arrows + Enter", 0.5, 0.69, 25, MUTED)

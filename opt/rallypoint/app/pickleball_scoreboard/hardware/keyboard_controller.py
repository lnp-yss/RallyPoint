from .commands import InputEdges


class KeyboardController:
    mapping = dict(
        zip(
            "12347890",
            [
                f"TEAM_{team}_{action}"
                for team in ("A", "B")
                for action in ("POINT", "SERVICE", "UNDO", "TIMEOUT")
            ],
        )
    )

    def __init__(self, queue, settings):
        self.edges = InputEdges(queue, settings.long_press_seconds)

    def handle(self, event):
        import pygame

        name = self.mapping.get(pygame.key.name(event.key))
        if name:
            if event.type == pygame.KEYDOWN:
                self.edges.press(name)
            elif event.type == pygame.KEYUP:
                self.edges.release(name)

    def close(self):
        self.edges.clear()

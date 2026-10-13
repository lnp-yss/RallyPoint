"""Resolution-relative drawing and bounded font/text caches."""

from functools import lru_cache
import pygame

BG = (9, 17, 29)
PANEL = (17, 30, 47)
WHITE = (240, 246, 250)
MUTED = (149, 171, 193)
COLORS = {"A": (41, 205, 210), "B": (255, 176, 67)}


@lru_cache(maxsize=96)
def font(size, bold=False):
    return pygame.font.Font(None, max(12, size))


@lru_cache(maxsize=512)
def rendered(text, size, color):
    return font(size).render(text, True, color)


class Canvas:
    def __init__(self, surface):
        self.surface = surface
        self.w, self.h = surface.get_size()
        self.scale = min(self.w / 1920, self.h / 1080)

    def rect(self, x, y, w, h, color, border=0, radius=18):
        box = pygame.Rect(
            round(x * self.w),
            round(y * self.h),
            round(w * self.w),
            round(h * self.h),
        )
        pygame.draw.rect(
            self.surface,
            color,
            box,
            border,
            border_radius=round(radius * self.scale),
        )
        return box

    def text(self, text, x, y, size, color=WHITE, max_width=0.9):
        size = max(12, round(size * self.scale))
        text = str(text)
        image = rendered(text, size, color)
        if image.get_width() > max_width * self.w:
            size = max(12, int(size * max_width * self.w / image.get_width()))
            image = rendered(text, size, color)
        self.surface.blit(
            image,
            image.get_rect(center=(round(x * self.w), round(y * self.h))),
        )

    def dot(self, x, y, radius, color):
        pygame.draw.circle(
            self.surface,
            color,
            (round(x * self.w), round(y * self.h)),
            max(2, round(radius * self.scale)),
        )

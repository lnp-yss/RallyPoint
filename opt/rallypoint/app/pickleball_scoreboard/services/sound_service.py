import logging
from pathlib import Path


class SoundService:
    def __init__(self, directory, enabled=True):
        self.sounds = {}
        self.enabled = enabled
        try:
            import pygame

            pygame.mixer.init(
                frequency=22050, size=-16, channels=1, buffer=512
            )
            for name in ("point", "service", "game", "match"):
                path = Path(directory) / f"{name}.wav"
                if path.exists():
                    self.sounds[name] = pygame.mixer.Sound(str(path))
        except Exception:
            logging.warning("Audio unavailable", exc_info=True)

    def play(self, name):
        if self.enabled and name in self.sounds:
            try:
                self.sounds[name].play()
            except Exception:
                logging.warning("Audio playback unavailable")

    def close(self):
        import pygame

        pygame.mixer.quit()

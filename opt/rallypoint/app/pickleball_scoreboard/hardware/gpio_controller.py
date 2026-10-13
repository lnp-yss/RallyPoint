"""Active-low dry contacts. GPIO callbacks never touch application state."""

import logging
from pathlib import Path
from threading import Event, Thread
from time import monotonic
from .commands import InputEdges, DebouncedRelay


class GPIOController:
    def __init__(self, queue, settings, force_keyboard=False):
        self.buttons = []
        self.relays = []
        self.stop = Event()
        self.worker = None
        self.edges = InputEdges(queue, settings.long_press_seconds)
        self.mode = "GPIO OFFLINE / KEYBOARD MODE"
        self.error = ""
        if force_keyboard:
            return
        try:
            model = Path("/proc/device-tree/model")
            if not model.exists() or "Raspberry Pi" not in model.read_text():
                raise RuntimeError("Raspberry Pi hardware not detected")
            from gpiozero import Button

            for team, pins in settings.gpio_pins.items():
                for action, pin in zip(("POINT", "SERVICE", "UNDO", "TIMEOUT"), pins):
                    name = f"TEAM_{team}_{action}"
                    button = Button(
                        pin,
                        pull_up=True,
                        # Driver lockout can discard the release of a quick tap.
                        # Stable-state software debounce handles both edges.
                        bounce_time=None,
                    )
                    self.buttons.append(button)
                    self.relays.append(
                        DebouncedRelay(self.edges, name, pin, settings.debounce_seconds)
                    )
            self.worker = Thread(target=self.poll, daemon=True, name="rf-input")
            self.worker.start()
            self.mode = "GPIO ONLINE"
        except Exception as exc:
            self.error = str(exc)
            logging.warning("GPIO unavailable: %s", exc)
            self.close()

    def poll(self):
        """Read all relays without networking, disk I/O or scoring logic."""
        try:
            while not self.stop.is_set():
                now = monotonic()
                for button, relay in zip(self.buttons, self.relays):
                    relay.sample(button.is_pressed, now)
                self.edges.poll(now)
                self.stop.wait(0.005)
        except Exception as exc:
            self.error = str(exc)
            self.mode = "GPIO OFFLINE / KEYBOARD MODE"
            logging.exception("GPIO polling failed; keyboard remains available")

    def close(self):
        self.stop.set()
        if self.worker:
            self.worker.join(timeout=1)
        for button in self.buttons:
            try:
                button.when_pressed = None
                button.when_released = None
                button.close()
            except Exception:
                logging.exception("GPIO cleanup failed")
        self.buttons.clear()
        self.edges.clear()

"""Timestamped input and shared edge/duplicate protection."""

from dataclasses import dataclass
from queue import Queue
from threading import RLock
from time import monotonic


@dataclass(frozen=True)
class Command:
    name: str
    timestamp: float
    source: str = "keyboard"
    pin: int | None = None
    activation: int | None = None


class InputEdges:
    """One command per activation; held D fires at its long-press threshold."""

    def __init__(self, queue: Queue, long_press: float):
        self.queue = queue
        self.long_press = max(3.0, long_press)
        self.pressed = {}
        self.long_fired = set()
        self.lock = RLock()
        self.activation = 0

    def press(
        self,
        name: str,
        source: str = "keyboard",
        pin: int | None = None,
        now: float | None = None,
    ) -> None:
        now = monotonic() if now is None else now
        with self.lock:
            if name in self.pressed:
                return
            self.activation += 1
            activation = self.activation if source == "gpio" else None
            self.pressed[name] = (now, source, pin, activation)
            if not name.endswith("_TIMEOUT"):
                self.queue.put(Command(name, now, source, pin, activation))

    def release(self, name: str, now: float | None = None) -> None:
        now = monotonic() if now is None else now
        with self.lock:
            previous = self.pressed.pop(name, None)
            fired = name in self.long_fired
            self.long_fired.discard(name)
            if previous and name.endswith("_TIMEOUT") and not fired:
                start, source, pin, activation = previous
                self.queue.put(
                    Command(
                        ("RESET_REQUEST" if now - start >= self.long_press else name),
                        now,
                        source,
                        pin,
                        activation,
                    )
                )

    def poll(self, now: float | None = None) -> None:
        """Queue a long press while held, exactly once until stable release."""
        now = monotonic() if now is None else now
        with self.lock:
            for name, (start, source, pin, activation) in self.pressed.items():
                if name.endswith("_TIMEOUT") and name not in self.long_fired and now - start >= self.long_press:
                    self.long_fired.add(name)
                    self.queue.put(Command("RESET_REQUEST", now, source, pin, activation))

    def clear(self):
        with self.lock:
            self.pressed.clear()
            self.long_fired.clear()


class DuplicateFilter:
    def __init__(self, interval):
        self.interval = interval
        self.last = {}
        self.activations = {}

    def accept(self, command: Command, now: float | None = None) -> bool:
        now = monotonic() if now is None else now
        # Old queued input must not unexpectedly change the score after a dialog.
        if now - command.timestamp > 2 or command.timestamp > now + 0.1:
            return False
        if command.source == "gpio" and command.activation is not None:
            # Stable release/re-press is a new activation even within 300 ms.
            # Replayed commands from the same activation remain suppressed.
            key = (command.pin, command.name)
            previous = self.activations.get(key, 0)
            if command.activation <= previous:
                return False
            self.activations[key] = command.activation
            return True
        previous = self.last.get(command.name)
        if previous is not None and command.timestamp - previous < self.interval:
            return False
        self.last[command.name] = command.timestamp
        return True


class DebouncedRelay:
    """Require stable HIGH and LOW, rather than discarding release edges.

    Each independently debounced relay rearms only after a stable release.
    A held relay and contact bounce cannot generate additional activations.
    """

    def __init__(self, edges, name, pin, seconds):
        self.edges, self.name, self.pin = edges, name, pin
        self.seconds = seconds
        self.active = self.candidate = False
        self.since = 0.0

    def sample(self, pressed, now):
        if pressed != self.candidate:
            self.candidate = pressed
            self.since = now
        if self.candidate != self.active and now - self.since >= self.seconds:
            self.active = self.candidate
            if self.active:
                self.edges.press(self.name, "gpio", self.pin, now=self.since)
            else:
                self.edges.release(self.name, now=self.since)

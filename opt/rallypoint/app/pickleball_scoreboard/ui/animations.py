from time import monotonic


class Feedback:
    def __init__(self):
        self.message = ""
        self.until = 0.0
        self.team = None
        self.started = 0.0

    def show(self, message, team=None, duration=1.2):
        self.message, self.team = message, team
        self.started = monotonic()
        self.until = self.started + duration

    @property
    def active(self):
        return monotonic() < self.until

    def pulse(self, team):
        elapsed = monotonic() - self.started
        return (
            1 + 0.065 * max(0, 1 - elapsed / 0.35) if self.team == team else 1
        )

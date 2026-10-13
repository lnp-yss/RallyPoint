from queue import Queue
from pickleball_scoreboard.config import Settings
from pickleball_scoreboard.hardware.gpio_controller import GPIOController


def test_non_pi_falls_back_and_no_callbacks():
    controller = GPIOController(Queue(), Settings(), force_keyboard=True)
    assert "KEYBOARD MODE" in controller.mode
    assert not controller.buttons
    controller.close()


def test_gpio_config_callbacks_and_cleanup(monkeypatch):
    import gpiozero
    from pathlib import Path

    created = []

    class FakeButton:
        def __init__(self, pin, pull_up, bounce_time):
            self.pin, self.pull_up, self.bounce_time = (
                pin,
                pull_up,
                bounce_time,
            )
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(Path, "exists", lambda _: True)
    monkeypatch.setattr(Path, "read_text", lambda _: "Raspberry Pi 4 Model B")
    monkeypatch.setattr(gpiozero, "Button", FakeButton)
    queue = Queue()
    controller = GPIOController(queue, Settings())
    assert controller.mode == "GPIO ONLINE"
    assert [b.pin for b in created] == [17, 27, 22, 23, 5, 6, 13, 19]
    assert all(b.pull_up and b.bounce_time == 0.2 for b in created)
    created[0].when_pressed()
    created[0].when_pressed()
    assert queue.qsize() == 1
    command = queue.get()
    assert command.name == "TEAM_A_POINT" and command.pin == 17
    controller.close()
    assert all(
        b.closed and b.when_pressed is None and b.when_released is None
        for b in created
    )


def test_partial_gpio_failure_closes_pins(monkeypatch):
    import gpiozero
    from pathlib import Path

    created = []

    class FakeButton:
        def __init__(self, pin, **kwargs):
            if created:
                raise PermissionError("device denied")
            self.closed = False
            created.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr(Path, "exists", lambda _: True)
    monkeypatch.setattr(Path, "read_text", lambda _: "Raspberry Pi")
    monkeypatch.setattr(gpiozero, "Button", FakeButton)
    controller = GPIOController(Queue(), Settings())
    assert "KEYBOARD MODE" in controller.mode
    assert created[0].closed

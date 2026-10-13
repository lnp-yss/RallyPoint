from queue import Queue
from pickleball_scoreboard.hardware.commands import (
    InputEdges,
    DuplicateFilter,
    Command,
)


def test_repeat_and_long_press():
    q = Queue()
    e = InputEdges(q, 3)
    e.press("TEAM_A_POINT", now=10)
    e.press("TEAM_A_POINT", now=11)
    assert q.qsize() == 1
    e.release("TEAM_A_POINT", now=12)
    q.get()
    e.press("TEAM_A_TIMEOUT", now=20)
    assert q.empty()
    e.release("TEAM_A_TIMEOUT", now=23)
    assert q.get().name == "RESET_REQUEST"
    assert q.empty()
    e.press("TEAM_B_TIMEOUT", now=30)
    e.release("TEAM_B_TIMEOUT", now=32.99)
    assert q.get().name == "TEAM_B_TIMEOUT"


def test_duplicate_and_stale():
    f = DuplicateFilter(0.3)
    assert f.accept(Command("TEAM_A_POINT", 10), now=10)
    assert not f.accept(Command("TEAM_A_POINT", 10.2), now=10.2)
    assert f.accept(Command("TEAM_A_POINT", 10.31), now=10.31)
    assert f.accept(Command("TEAM_B_POINT", 10.32), now=10.32)
    assert not f.accept(Command("TEAM_A_POINT", 11), now=14)

import os
import json
from queue import Queue
from time import monotonic, sleep
from uuid import uuid4
from urllib.request import Request, urlopen
import pytest
from pickleball_scoreboard.services.scoreboard_bridge import (
    ScoreboardBridge,
    DurableOutbox,
    WebCommand,
)


def test_authenticated_ipc_and_command_idempotency(tmp_path):
    (tmp_path / "data").mkdir()
    queue = Queue()
    bridge = ScoreboardBridge(tmp_path, queue, "s" * 40, port=0)
    url = f"http://127.0.0.1:{bridge.port}"
    try:
        from urllib.error import HTTPError

        with pytest.raises(HTTPError):
            urlopen(url + "/state")
        data = json.dumps(
            {
                "request_id": str(uuid4()),
                "name": "TEAM_A_POINT",
                "payload": {"match_id": 1},
            }
        ).encode()
        req = Request(
            url + "/commands",
            data=data,
            headers={"Authorization": "Bearer " + "s" * 40},
        )
        for _ in range(2):
            assert urlopen(req).status == 202
        assert queue.qsize() == 1
        command = queue.get()
        assert isinstance(command, WebCommand)
        bridge.complete(command)
        receipt = json.load(
            urlopen(
                Request(
                    url + "/receipts/" + command.request_id,
                    headers={"Authorization": "Bearer " + "s" * 40},
                )
            )
        )
        assert receipt["status"] == "APPLIED"
        bridge.publish({"state": {"score": 2}}, "POINT", match_id=1)
        deadline = monotonic() + 1
        while not bridge.outbox.next() and monotonic() < deadline:
            sleep(0.01)
        assert bridge.outbox.next()[1]["event_type"] == "POINT"
    finally:
        bridge.close()


def test_durable_outbox_reopens(tmp_path):
    path = tmp_path / "backlog.db"
    event = {"event_id": str(uuid4()), "value": 1}
    store = DurableOutbox(path)
    store.append(event)
    store.append(event)
    reopened = DurableOutbox(path)
    row = reopened.next()
    assert row[1] == event
    reopened.remove(row[0])
    assert reopened.next() is None


def test_scoreboard_start_from_web_and_offline_database(tmp_path, monkeypatch):
    pytest.importorskip("pygame")
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    os.environ["SDL_AUDIODRIVER"] = "dummy"
    monkeypatch.setenv("RALLYPOINT_BRIDGE", "1")
    monkeypatch.setenv("RALLYPOINT_BRIDGE_TOKEN", "s" * 40)
    monkeypatch.setenv("RALLYPOINT_BRIDGE_PORT", "0")
    monkeypatch.delenv("RALLYPOINT_DATABASE_URL", raising=False)
    from pickleball_scoreboard.app import Application

    app = Application(tmp_path, True, True)
    try:
        command = WebCommand(
            "START_MATCH",
            {
                "settings": {
                    "team_a": "Dominic / Chel",
                    "team_b": "Mark / Anna",
                    "players_a": "Dominic / Chel",
                    "players_b": "Mark / Anna",
                },
                "match_id": 15,
                "court_id": 2,
            },
            str(uuid4()),
        )
        app.command(command)
        assert app.engine.settings.team_a == "Dominic / Chel"
        assert app.web_match_id == 15 and app.dialog.kind == "managed_start"
        assert app.web_started_at is None
        app.command(WebCommand("RESUME", {"match_id": 15}, str(uuid4())))
        assert app.view == "scoreboard" and app.dialog is None
        app.command(WebCommand("TEAM_A_POINT", {"match_id": 15}, str(uuid4())))
        assert app.engine.state.game.scores["A"] == 1
        app.command(WebCommand("TEAM_B_POINT", {"match_id": 999}, str(uuid4())))
        assert app.engine.state.game.serving.value == "A"
        app.command(WebCommand("PAUSE", {"match_id": 15}, str(uuid4())))
        assert app.paused
        app.command(WebCommand("RESUME", {"match_id": 15}, str(uuid4())))
        assert not app.paused
        app.command(WebCommand("TEAM_A_UNDO", {"match_id": 15}, str(uuid4())))
        assert app.paused
        app.act("RESUME")
        app.act("TEAM_A_POINT")
        assert app.engine.state.game.scores["A"] == 2
        assert app.bridge.snapshot["state"]["game"]["scores"]["A"] == 2
    finally:
        app.close()


def test_recovery_command_publishes_resumed_state(tmp_path, monkeypatch):
    pytest.importorskip("pygame")
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    monkeypatch.setenv("RALLYPOINT_BRIDGE", "1")
    monkeypatch.setenv("RALLYPOINT_BRIDGE_TOKEN", "s" * 40)
    monkeypatch.setenv("RALLYPOINT_BRIDGE_PORT", "0")
    monkeypatch.delenv("RALLYPOINT_DATABASE_URL", raising=False)
    from pickleball_scoreboard.app import Application
    from pickleball_scoreboard.config import Settings
    from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
    from pickleball_scoreboard.services.persistence import Persistence

    engine = ScoringEngine(Settings())
    engine.apply("TEAM_A_POINT")
    Persistence(tmp_path / "data").save(engine, {"match_id": 4})
    app = Application(tmp_path, True, True)
    try:
        assert app.dialog.kind == "resume"
        request_id = str(uuid4())
        app.command(WebCommand("RESUME_RECOVERY", {"match_id": 4}, request_id))
        assert app.view == "scoreboard" and app.dialog is None
        assert app.bridge.snapshot["view"] == "scoreboard"
        assert app.bridge.snapshot["hardware"]["video_driver"] == "dummy"
        assert app.engine.state.game.scores["A"] == 1
        assert (
            request_id
            in Persistence(tmp_path / "data")
            .restore()
            .external_context["applied_requests"]
        )
    finally:
        app.close()


def test_database_worker_import_does_not_load_flask():
    pytest.importorskip("sqlalchemy")
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from pickleball_scoreboard.web.database import Database; from pickleball_scoreboard.web.club_service import ClubService; assert 'flask' not in sys.modules",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_manual_commands_are_applied_once_and_reject_stale_revision(tmp_path, monkeypatch):
    pytest.importorskip("pygame")
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    monkeypatch.setenv("SDL_AUDIODRIVER", "dummy")
    monkeypatch.setenv("RALLYPOINT_BRIDGE", "1")
    monkeypatch.setenv("RALLYPOINT_BRIDGE_TOKEN", "s" * 40)
    monkeypatch.setenv("RALLYPOINT_BRIDGE_PORT", "0")
    monkeypatch.delenv("RALLYPOINT_DATABASE_URL", raising=False)
    from pickleball_scoreboard.app import Application
    app = Application(tmp_path, True, True)
    try:
        app.command(WebCommand("START_MATCH", {"settings": {}, "match_id": 15, "court_id": 2}, str(uuid4())))
        app.command(WebCommand("RESUME", {"match_id": 15}, str(uuid4())))
        before = app.engine.state.to_dict()
        stale = WebCommand("SET_SCORES", {"match_id": 15, "scores": {"A": 8, "B": 6}, "expected_revision": 0}, str(uuid4()))
        app.command(stale)
        assert app.engine.state.to_dict() == before
        assert app.bridge.receipts[stale.request_id]["status"] == "REJECTED"
        cmd = WebCommand("DECLARE_WINNER", {"match_id": 15, "scores": {"A": 8, "B": 6}, "winner": "A", "expected_revision": app.bridge.snapshot["revision"]}, str(uuid4()))
        app.command(cmd)
        assert app.bridge.receipts[cmd.request_id]["status"] == "APPLIED"
        assert app.store.restore().state.winner == "A"
        count = len(app.engine.history)
        app.command(cmd)
        assert len(app.engine.history) == count
        app.command(WebCommand("TEAM_A_UNDO", {"match_id": 15}, str(uuid4())))
        assert app.engine.state.to_dict() == before
    finally:
        app.close()

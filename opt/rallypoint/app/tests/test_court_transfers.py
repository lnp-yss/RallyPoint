"""Court move authorization, isolation, idempotency, rollback and recovery."""

from copy import deepcopy
from pathlib import Path
import json
from uuid import uuid4
import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response
from test_shared_system import host, login
from pickleball_scoreboard.web.court_transfers import enqueue, binding, plan
from pickleball_scoreboard.web.court_move_worker import MoveWorker
from pickleball_scoreboard.web.models import Court, ScoreboardDevice, Player
from scripts import court_device_agent as agent


@pytest.fixture
def paired(host):
    app = host.root_app
    with app.extensions["database"].transaction() as s:
        s.add(
            ScoreboardDevice(
                device_name="Physical Court 1",
                court_id=1,
                hostname="test-pi",
                bridge_url="http://127.0.0.1:8765",
            )
        )
        s.add(Player(display_name="Legacy history stays"))
    with host.connect() as db:
        db.execute(
            "INSERT INTO physical_courts VALUES (1,'Physical Court 1','http://127.0.0.1:8765','legacy',1,1,0)"
        )
    snapshot = {
        "connected": True,
        "match_exists": False,
        "state": {"status": "playing"},
        "identity": {"device_id": 1, "court_id": 1, "hostname": "test-pi"},
        "sync": {"online": True, "pending": 0},
    }
    for app in [host.root_app, *host.apps.values()]:
        app.extensions["match_state"].request = lambda *args, **kwargs: deepcopy(
            snapshot
        )
    return host, snapshot


def proposal(host):
    return {
        "physical_id": 1,
        "target_slug": "alpha",
        "court_number": 2,
        "revision": 0,
        "confirmed": True,
        "request_id": str(uuid4()),
    }


def test_permissions_and_confirmation(paired):
    host, snapshot = paired
    client = Client(host, Response)
    assert client.get("/api/court-manager").status_code == 403
    login(client, "/clubs/alpha")
    assert client.get("/api/court-manager").status_code == 403
    token = login(client, username="host")
    headers = {"X-CSRF-Token": token}
    assert client.get("/court-manager").status_code == 200
    assert client.get("/api/court-manager").json["courts"][0]["ready"]
    values = proposal(host)
    values["confirmed"] = False
    assert (
        client.post(
            "/api/court-manager/moves", json=values, headers=headers
        ).status_code
        == 400
    )
    values["confirmed"] = True
    assert client.post("/api/court-manager/moves", json=values).status_code == 403
    assert (
        client.post(
            "/api/court-manager/moves", json=values, headers=headers
        ).status_code
        == 202
    )
    assert (
        client.post(
            "/api/court-manager/moves", json=values, headers=headers
        ).status_code
        == 202
    )
    with host.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM court_moves").fetchone()[0] == 1


@pytest.mark.parametrize(
    "change", [{"match_exists": True}, {"sync": {"pending": 1}}, {"connected": False}]
)
def test_busy_pending_and_offline_courts_block_moves(paired, change):
    host, snapshot = paired
    snapshot.update(change)
    with pytest.raises(ValueError):
        enqueue(host, proposal(host), "admin")
    with host.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM court_moves").fetchone()[0] == 0


def test_success_creates_court_preserves_roster_and_rebinds(paired):
    host, snapshot = paired
    calls = []

    def transport(physical, payload):
        calls.append(payload["action"])
        if payload["action"] == "apply":
            snapshot["identity"].update(
                device_id=payload["device_id"], court_id=payload["court_id"]
            )
        return deepcopy(snapshot)

    job = enqueue(host, proposal(host), "admin")
    worker = MoveWorker(host, transport)
    assert worker.once()
    assert not worker.once()
    assert calls == ["apply", "finalize"]
    assert binding(host, 1)["slug"] == "alpha"
    with host.root_app.extensions["database"].transaction() as s:
        assert not s.get(Court, 1).enabled
        assert s.get(Player, 1).display_name == "Legacy history stays"
    with host.workspace("alpha").extensions["database"].transaction() as s:
        court = s.get(Court, binding(host, 1)["court_id"])
        assert court.number == 2 and court.enabled
        assert s.get(Player, 1) is None
    with host.connect() as db:
        assert (
            db.execute(
                "SELECT status FROM court_moves WHERE id=?", (job["id"],)
            ).fetchone()[0]
            == "DONE"
        )


def test_failed_configuration_restores_original_assignment(paired):
    host, snapshot = paired
    job = enqueue(host, proposal(host), "admin")

    def transport(physical, payload):
        if payload["action"] == "apply":
            raise OSError("Pi unreachable")
        return {"restored": True}

    MoveWorker(host, transport).once()
    assert binding(host, 1)["slug"] == "legacy"
    with host.root_app.extensions["database"].transaction() as s:
        assert s.get(Court, 1).enabled
    with host.workspace("alpha").extensions["database"].transaction() as s:
        assert not s.get(Court, 2).enabled
    with host.connect() as db:
        row = db.execute(
            "SELECT status,message FROM court_moves WHERE id=?", (job["id"],)
        ).fetchone()
        assert row["status"] == "FAILED" and "restored" in row["message"]


@pytest.mark.parametrize("partial_source", [False, True])
def test_interrupted_job_resumes_checkpoint(paired, partial_source):
    host, snapshot = paired
    job = enqueue(host, proposal(host), "admin")
    worker = MoveWorker(host, None)
    with host.connect() as db:
        payload = json.loads(
            db.execute(
                "SELECT payload FROM court_moves WHERE id=?", (job["id"],)
            ).fetchone()[0]
        )
    context = worker.freeze(job["id"], payload)
    if partial_source:
        context.pop("old_source")
        with host.root_app.extensions["database"].transaction() as session:
            source = session.get(Court, 1)
            source.enabled = 1
            source.status = "AVAILABLE"
        worker.update(job["id"], "MOVING", "Interrupted before source preparation", payload)
    # Simulate a worker restart after the Pi had applied its new profile.
    snapshot["identity"].update(
        device_id=context["target_device_id"], court_id=context["target_court_id"]
    )
    worker = MoveWorker(host, lambda *args: deepcopy(snapshot))
    worker.once()
    assert binding(host, 1)["slug"] == "alpha"
    with host.connect() as db:
        assert (
            db.execute(
                "SELECT status FROM court_moves WHERE id=?", (job["id"],)
            ).fetchone()[0]
            == "DONE"
        )


def test_failed_rollback_requires_explicit_recovery(paired):
    host, snapshot = paired
    job = enqueue(host, proposal(host), "admin")

    def unavailable(*args):
        raise OSError("Disconnected")

    MoveWorker(host, unavailable).once()
    with host.connect() as db:
        assert (
            db.execute("SELECT status FROM court_moves").fetchone()[0] == "NEEDS_REVIEW"
        )
    with pytest.raises(ValueError):
        enqueue(host, proposal(host), "admin")
    client = Client(host, Response)
    token = login(client, username="host")
    assert (
        client.post(
            "/api/court-manager/moves/" + job["id"] + "/retry",
            json={},
            headers={"X-CSRF-Token": token},
        ).status_code
        == 202
    )


def test_stale_assignment_and_conflicting_destination(paired):
    host, snapshot = paired
    values = proposal(host)
    values["revision"] = 99
    with pytest.raises(ValueError):
        enqueue(host, values, "admin")
    with host.workspace("alpha").extensions["database"].transaction() as s:
        s.add(
            ScoreboardDevice(
                device_name="Other Pi", court_id=1, bridge_url="http://127.0.0.1:8888"
            )
        )
    with pytest.raises(ValueError):
        plan(host, 1, "alpha", 1)


def test_agent_rejects_injection_and_arbitrary_servers():
    values = {
        "job_id": str(uuid4()),
        "profile": "alpha",
        "token": "x" * 40,
        "device_id": 1,
        "court_id": 1,
        "court_number": 1,
        "hub_url": "http://192.168.2.204/clubs/alpha",
        "club_name": "Alpha",
        "court_name": "Court 1",
    }
    assert agent.checked(values, {"hub_ip": "192.168.2.204"})
    for key, value in [
        ("profile", "../escape"),
        ("hub_url", "http://evil.example/clubs/alpha"),
        ("token", "x" * 40 + "\nOTHER=value"),
        ("club_name", "Bad\nName"),
    ]:
        data = {**values, key: value}
        with pytest.raises(ValueError):
            agent.checked(data, {"hub_ip": "192.168.2.204"})


def test_agent_switch_is_idempotent_and_rolls_back_configuration(tmp_path, monkeypatch):
    root = tmp_path / "app"
    (root / "data").mkdir(parents=True)
    (root / "data/settings.json").write_text(
        json.dumps({"club_name": "Original", "court_name": "Court 1"})
    )
    config = tmp_path / "etc/agent.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "hub_ip": "192.168.2.204",
                "original_court_number": 1,
                "launcher": "python",
            }
        )
    )
    for name, value in {
        "ROOT": root,
        "CONF": config,
        "ENV": config.parent / "managed.env",
        "DROP": config.parent / "drop.conf",
        "STATE": config.parent / "state.json",
        "BACKUPS": tmp_path / "backups",
    }.items():
        monkeypatch.setattr(agent, name, value)
    monkeypatch.setattr(agent.shutil, "chown", lambda *args, **kwargs: None)
    calls = []

    class Completed:
        returncode = 0

    monkeypatch.setattr(
        agent.subprocess,
        "run",
        lambda command, **kwargs: (calls.append(command) or Completed()),
    )

    def env():
        return (
            dict(line.split("=", 1) for line in agent.ENV.read_text().splitlines())
            if agent.ENV.exists()
            else {}
        )

    monkeypatch.setattr(agent, "environment", env)

    def current():
        values = env()
        newroot = Path(values.get("RALLYPOINT_SCOREBOARD_DATA_ROOT", str(root)))
        return {
            "connected": True,
            "match_exists": False,
            "state": {"status": "playing"},
            "data_root": str(newroot),
            "identity": {
                "device_id": values.get("RALLYPOINT_DEVICE_ID", "1"),
                "court_id": values.get("RALLYPOINT_COURT_ID", "1"),
                "hostname": "test-pi",
            },
            "settings": json.loads((newroot / "data/settings.json").read_text()),
            "sync": {"online": True, "pending": 0},
        }

    monkeypatch.setattr(agent, "current", current)
    payload = {
        "action": "apply",
        "job_id": str(uuid4()),
        "profile": "alpha",
        "token": "x" * 40,
        "device_id": 2,
        "court_id": 2,
        "court_number": 2,
        "hub_url": "http://192.168.2.204/clubs/alpha",
        "club_name": "Alpha",
        "court_name": "Court 2",
    }
    result = agent.run(payload)
    assert result["identity"]["court_id"] == "2"
    assert (root / "data/court_switch.lock").exists()
    assert (root / "device-workspaces/alpha-court2/data/court_switch.lock").exists()
    restarts = len(calls)
    agent.run(payload)
    assert len(calls) == restarts
    assert agent.run({"action": "rollback", "job_id": payload["job_id"]})["restored"]
    assert not agent.ENV.exists() and not agent.DROP.exists()
    assert not (root / "data/court_switch.lock").exists()
    assert (root / "data/settings.json").read_text().find("Original") >= 0
    assert agent.run({"action": "rollback", "job_id": payload["job_id"]})["restored"]


def test_agent_catches_rally_started_during_shutdown(tmp_path, monkeypatch):
    root = tmp_path / "app"
    (root / "data").mkdir(parents=True)
    (root / "data/settings.json").write_text(
        json.dumps({"club_name": "Original", "court_name": "Court 1"})
    )
    config = tmp_path / "etc/agent.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "hub_ip": "192.168.2.204",
                "original_court_number": 1,
                "launcher": "python",
            }
        )
    )
    for name, value in {
        "ROOT": root,
        "CONF": config,
        "ENV": config.parent / "managed.env",
        "DROP": config.parent / "drop.conf",
        "STATE": config.parent / "state.json",
        "BACKUPS": tmp_path / "backups",
    }.items():
        monkeypatch.setattr(agent, name, value)
    monkeypatch.setattr(
        agent,
        "current",
        lambda: {
            "connected": True,
            "match_exists": False,
            "data_root": str(root),
            "sync": {"pending": 0},
        },
    )

    class Completed:
        returncode = 1

    def run(command, **kwargs):
        if command[:2] == ["systemctl", "stop"]:
            (root / "data/current_match.json").write_text(
                json.dumps(
                    {"state": {"status": "playing"}, "history": [{"preserved": True}]}
                )
            )
        return Completed()

    monkeypatch.setattr(agent.subprocess, "run", run)
    payload = {
        "action": "apply",
        "job_id": str(uuid4()),
        "profile": "alpha",
        "token": "x" * 40,
        "device_id": 2,
        "court_id": 2,
        "court_number": 2,
        "hub_url": "http://192.168.2.204/clubs/alpha",
        "club_name": "Alpha",
        "court_name": "Court 2",
    }
    with pytest.raises(ValueError, match="unfinished"):
        agent.run(payload)
    assert not agent.ENV.exists()
    assert json.loads((root / "data/current_match.json").read_text())["history"] == [
        {"preserved": True}
    ]

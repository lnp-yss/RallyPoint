"""Isolation and lifecycle checks through the shared WSGI dispatcher."""

import pytest
from uuid import uuid4
from werkzeug.test import Client
from werkzeug.wrappers import Response
from werkzeug.security import generate_password_hash
from pickleball_scoreboard.web.shared_system import SharedSystem
from pickleball_scoreboard.web.models import (
    User,
    Player,
    ScoreboardDevice,
    DeviceSnapshot,
    Match,
)


@pytest.fixture
def host(tmp_path):
    shared = SharedSystem(
        {
            "DATABASE_URL": "sqlite:///" + (tmp_path / "legacy.db").as_posix(),
            "DATA_ROOT": str(tmp_path),
            "SECRET_KEY": "root-test-secret",
            "TESTING": True,
            "BRIDGE_TOKEN": "r" * 40,
        }
    )
    with shared.root_app.extensions["database"].transaction() as s:
        s.add(
            User(
                username="host",
                password_hash=generate_password_hash("testpassword123"),
                role="ADMIN",
            )
        )
    for slug in ("alpha", "bravo"):
        shared.create_club(
            {
                "name": slug.title(),
                "slug": slug,
                "username": "owner",
                "password": "testpassword123",
                "public_live": slug == "alpha",
            }
        )
    yield shared
    for app in [shared.root_app, *shared.apps.values()]:
        app.extensions["database"].engine.dispose()


def login(client, prefix="", username="owner"):
    token = client.get(prefix + "/api/session").json["csrf"]
    response = client.post(
        prefix + "/login",
        json={"username": username, "password": "testpassword123"},
        headers={"X-CSRF-Token": token},
    )
    assert response.status_code == 302
    assert response.headers["Location"].startswith(prefix + "/")
    return client.get(prefix + "/api/session").json["csrf"]


def test_club_isolation_sessions_and_provisioning(host):
    client = Client(host, Response)
    token = login(client, "/clubs/alpha")
    h = {"X-CSRF-Token": token}
    assert (
        client.post(
            "/clubs/alpha/api/players", json={"display_name": "Alpha only"}, headers=h
        ).status_code
        == 201
    )
    assert client.get("/clubs/bravo/api/players").status_code == 401
    assert client.post("/api/clubs", json={}, headers=h).status_code in (400, 403)
    token = login(client, "/clubs/bravo")
    assert client.get("/clubs/bravo/api/players").json == []
    assert (
        client.get("/clubs/alpha/api/players").json[0]["display_name"] == "Alpha only"
    )
    assert client.get("/clubs/alpha/preview").status_code == 200
    anonymous = Client(host, Response)
    assert anonymous.get("/clubs/bravo/preview").status_code == 302
    assert [c["slug"] for c in anonymous.get("/api/clubs").json["clubs"]] == [
        None,
        "alpha",
    ]
    token = login(client, username="host")
    assert (
        client.post(
            "/api/clubs",
            json={
                "name": "Third",
                "slug": "third",
                "username": "admin",
                "password": "testpassword123",
            },
            headers={"X-CSRF-Token": token},
        ).status_code
        == 201
    )
    assert client.get("/clubs/third").headers["Location"] == "/clubs/third/"
    assert (
        client.get("/clubs/alpha/players").data.find(b'data-base="/clubs/alpha"') >= 0
    )


def test_csv_atomic_duplicate_and_foreign_device_token(host):
    client = Client(host, Response)
    token = login(client, "/clubs/alpha")
    h = {"X-CSRF-Token": token}
    path = "/clubs/alpha/api/players/import"
    assert (
        client.post(
            path, json={"csv": "display_name,skill_rating\nGood,3\nBad,nope"}, headers=h
        ).status_code
        == 400
    )
    assert client.get("/clubs/alpha/api/players").json == []
    assert client.post(
        path,
        json={"csv": "display_name,rating_type\nLarry,DUPR\nlarry,Manual"},
        headers=h,
    ).json == {"imported": 1, "skipped": 1}
    app = host.workspace("alpha")
    with app.extensions["database"].transaction() as s:
        s.add(ScoreboardDevice(device_name="alpha-device", court_id=1))
    event = {
        "event_id": str(uuid4()),
        "device_id": 1,
        "event_type": "HEARTBEAT",
        "snapshot": {"identity": {"device_id": 1, "court_id": 1}, "updated_at": 1},
    }
    assert (
        client.post(
            "/clubs/alpha/api/device-events",
            json=event,
            headers={
                "Authorization": "Bearer "
                + host.workspace("bravo").config["BRIDGE_TOKEN"]
            },
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/clubs/alpha/api/device-events",
            json=event,
            headers={"Authorization": "Bearer " + app.config["BRIDGE_TOKEN"]},
        ).status_code
        == 200
    )
    with host.workspace("bravo").extensions["database"].transaction() as s:
        assert s.get(DeviceSnapshot, 1) is None
    config = client.get("/clubs/alpha/api/devices/1/configuration")
    assert config.status_code == 200
    assert b"http://localhost/clubs/alpha" in config.data


@pytest.mark.parametrize(
    "format,count,fixtures", [("ROUND_ROBIN", 3, 3), ("SINGLE_ELIMINATION", 4, 3)]
)
def test_tournament_registration_fixtures_and_assignment(host, format, count, fixtures):
    client = Client(host, Response)
    token = login(client, "/clubs/alpha")
    h = {"X-CSRF-Token": token}
    base = "/clubs/alpha/api/tournaments"
    app = host.workspace("alpha")
    players = [
        app.extensions["club"].save_player({"display_name": f"Player {i}"})["id"]
        for i in range(count)
    ]
    t = client.post(
        base,
        json={"name": "Club cup", "game_type": "Singles", "format": format},
        headers=h,
    ).json
    for pid in players:
        assert (
            client.post(
                base + f"/{t['id']}/entries", json={"players": [pid]}, headers=h
            ).status_code
            == 200
        )
    assert (
        client.post(base + f"/{t['id']}/generate", json={}, headers=h).status_code
        == 200
    )
    result = client.get(base).json[0]
    assert len(result["fixtures_json"]) == fixtures
    path = base + f"/{t['id']}/fixtures/1/assign"
    assigned = client.post(path, json={"court_id": 1}, headers=h)
    assert assigned.status_code == 201, assigned.json
    assert client.post(path, json={"court_id": 1}, headers=h).status_code == 400
    assert client.get("/clubs/bravo/api/tournaments").status_code == 401
    assert assigned.json["settings_json"]["club_name"] == "Alpha"


def test_bracket_advancement_and_undo_requires_review(host):
    client = Client(host, Response)
    token = login(client, "/clubs/alpha")
    h = {"X-CSRF-Token": token}
    base = "/clubs/alpha/api/tournaments"
    app = host.workspace("alpha")
    db = app.extensions["database"]
    players = [
        app.extensions["club"].save_player({"display_name": f"Entrant {i}"})["id"]
        for i in range(4)
    ]
    tid = client.post(
        base,
        json={
            "name": "Knockout",
            "game_type": "Singles",
            "format": "SINGLE_ELIMINATION",
        },
        headers=h,
    ).json["id"]
    for pid in players:
        client.post(base + f"/{tid}/entries", json={"players": [pid]}, headers=h)
    client.post(base + f"/{tid}/generate", json={}, headers=h)
    mids = []
    for fid in (1, 2):
        m = client.post(
            base + f"/{tid}/fixtures/{fid}/assign", json={"court_id": 1}, headers=h
        ).json
        mids.append(m["id"])
        with db.transaction() as s:
            match = s.get(Match, m["id"])
            match.status = "COMPLETE"
            match.winner_team = "A"
    final = client.get(base).json[0]["fixtures_json"][-1]
    assert (final["a"], final["b"], final["status"]) == (0, 2, "READY")
    assert (
        client.post(
            base + f"/{tid}/fixtures/3/assign", json={"court_id": 1}, headers=h
        ).status_code
        == 201
    )
    with db.transaction() as s:
        match = s.get(Match, mids[0])
        match.status = "PLAYING"
        match.winner_team = None
    assert client.get(base).json[0]["status"] == "NEEDS_REVIEW"


def test_workspace_reloads_and_duplicate_url(host):
    with pytest.raises(ValueError):
        host.create_club(
            {
                "slug": "alpha",
                "name": "Collision",
                "username": "admin",
                "password": "testpassword123",
            }
        )
    old = host.apps.pop("alpha")
    old.extensions["database"].engine.dispose()
    app = host.workspace("alpha")
    assert app.config["CLUB_NAME"] == "Alpha"
    client = Client(host, Response)
    login(client, "/clubs/alpha")
    assert client.get("/clubs/alpha/tournaments").status_code == 200


def test_http_sync_retains_backlog_then_delivers(tmp_path):
    import json
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from queue import Queue
    from pickleball_scoreboard.services.scoreboard_bridge import ScoreboardBridge

    accepted = []
    available = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/clubs/alpha/api/device-events"
            assert self.headers["Authorization"] == "Bearer " + "x" * 40
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200 if available.is_set() else 503)
            self.end_headers()
            if available.is_set():
                accepted.append(data)
                self.wfile.write(b'{"accepted":true}')

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    (tmp_path / "data").mkdir()
    bridge = ScoreboardBridge(
        tmp_path,
        Queue(),
        "x" * 40,
        port=0,
        hub_url=f"http://127.0.0.1:{server.server_port}/clubs/alpha",
    )
    try:
        bridge.publish({"identity": {"court_id": 1}}, "HEARTBEAT", device_id=1)
        deadline = time.monotonic() + 3
        while bridge.outbox.next() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert bridge.outbox.next() is not None
        available.set()
        deadline = time.monotonic() + 5
        while not accepted and time.monotonic() < deadline:
            time.sleep(0.02)
        assert len(accepted) == 1
        deadline = time.monotonic() + 1
        while bridge.outbox.next() is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert bridge.outbox.next() is None
    finally:
        bridge.close()
        server.shutdown()
        server.server_close()


def test_backup_contains_registry_and_all_workspace_databases(host, tmp_path):
    import tarfile
    import sqlite3
    from scripts.backup_scoreboard import backup, verify

    root = host.directory.parent.parent
    (root / "pickleball_scoreboard").mkdir()
    (root / "pickleball_scoreboard/app.py").write_text(
        "# backup application marker", encoding="utf-8"
    )
    host.workspace("alpha").extensions["club"].save_player(
        {"display_name": "Preserved Alpha"}
    )
    target = backup(root, tmp_path.parent / (tmp_path.name + "-backups"))
    verify(target)
    restored = tmp_path / "restored"
    with tarfile.open(target / "application.tar.gz") as archive:
        archive.extractall(restored, filter="data")
    with sqlite3.connect(restored / "app/data/shared_registry.sqlite3") as registry:
        assert (
            registry.execute("SELECT COUNT(*) FROM clubs WHERE ready=1").fetchone()[0]
            == 2
        )
    with sqlite3.connect(restored / "app/data/clubs/alpha/data/club.db") as db:
        assert (
            db.execute("SELECT display_name FROM players").fetchone()[0]
            == "Preserved Alpha"
        )
    with sqlite3.connect(restored / "app/data/clubs/bravo/data/club.db") as db:
        assert db.execute("SELECT COUNT(*) FROM players").fetchone()[0] == 0


def test_login_club_selection_keeps_credentials_in_selected_workspace(host):
    client = Client(host, Response)
    root = client.get("/login").text
    assert 'id="login-club"' in root
    assert '/clubs/alpha/login' in root and '/clubs/bravo/login' in root
    assert 'Host Administration' in root
    child = client.get("/clubs/bravo/login").text
    assert 'value="/clubs/bravo/login" selected' in child
    assert 'action="/clubs/bravo/login"' in child
    token = client.get("/clubs/bravo/api/session").json["csrf"]
    denied = client.post("/clubs/bravo/login", json={"username": "host", "password": "testpassword123"}, headers={"X-CSRF-Token": token})
    assert denied.status_code == 401
    login(client, "/clubs/bravo")
    assert client.get("/api/dashboard").status_code == 401
    assert 'name="host_admin" value="1"' in client.get("/login?host=1").text
    token = client.get("/clubs/bravo/api/session").json["csrf"]
    denied = client.post("/clubs/bravo/login", json={"username": "owner", "password": "testpassword123", "host_admin": "1"}, headers={"X-CSRF-Token": token})
    assert denied.status_code == 403


def test_host_admin_opens_all_clubs_without_child_login_and_revokes_immediately(host):
    client = Client(host, Response)
    login(client, username="host")
    for slug in ("alpha", "bravo"):
        page = client.get("/clubs/" + slug + "/")
        assert page.status_code == 200
        assert "host · Host Admin" in page.text
        assert 'action="/logout"' in page.text
        token = client.get("/clubs/" + slug + "/api/session").json["csrf"]
        response = client.post("/clubs/" + slug + "/api/players", json={"display_name": "Host created"}, headers={"X-CSRF-Token": token})
        assert response.status_code == 201
        assert client.post("/clubs/" + slug + "/api/players", json={"display_name": "Rejected"}).status_code == 403
        assert client.get("/clubs/" + slug + "/api/audit").json[0]["actor"] == "host-admin:host"
    with host.root_app.extensions["database"].transaction() as database_session:
        user = database_session.scalar(__import__("sqlalchemy").select(User).where(User.username == "host"))
        user.active = 0
    assert client.get("/clubs/alpha/api/settings").status_code == 401


def test_club_admin_cannot_delegate_to_another_club(host):
    client = Client(host, Response)
    login(client, "/clubs/alpha")
    assert client.get("/clubs/bravo/api/settings").status_code == 401
    assert client.get("/api/settings").status_code == 401

from uuid import uuid4
import pytest

pytest.importorskip("flask")
pytest.importorskip("sqlalchemy")
from werkzeug.security import generate_password_hash
from sqlalchemy import select, func
from pickleball_scoreboard.web import create_app
from pickleball_scoreboard.web.models import User, Court, Match, MatchEvent, Player, ScoreboardDevice, utcnow
from pickleball_scoreboard.web.matchmaking import recommend
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.config import Settings


@pytest.fixture
def web(tmp_path):
    app = create_app(
        {
            "DATABASE_URL": "sqlite:///" + (tmp_path / "club.db").as_posix(),
            "SECRET_KEY": "test-session-key",
            "DATA_ROOT": str(tmp_path),
            "TESTING": True,
            "BRIDGE_TOKEN": "x" * 40,
        }
    )
    app.extensions["database"].migrate()
    with app.extensions["database"].transaction() as s:
        s.add(ScoreboardDevice(device_name="test-hdmi", court_id=1, last_seen_at=utcnow()))
        for role in ("ADMIN", "OPERATOR", "VIEWER"):
            s.add(
                User(
                    username=role,
                    password_hash=generate_password_hash("testpassword123"),
                    role=role,
                )
            )
    yield app
    app.extensions["database"].engine.dispose()


def signed_in(web, role="ADMIN"):
    client = web.test_client()
    csrf = client.get("/api/session").json["csrf"]
    assert (
        client.post(
            "/login",
            json={"username": role, "password": "testpassword123"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 302
    )
    return client, client.get("/api/session").json["csrf"]


def roster(web):
    club = web.extensions["club"]
    players = [
        club.save_player({"display_name": name, "skill_rating": rating})
        for name, rating in [
            ("Dominic", 3.8),
            ("Chel", 3.4),
            ("Mark", 3.7),
            ("Anna", 3.5),
        ]
    ]
    for p in players:
        club.checkin(p["id"])
    return players


def payload(players):
    return {
        "court_id": 1,
        "settings": {
            "team_a": "Dominic / Chel",
            "team_b": "Mark / Anna",
            "best_of": 1,
            "scoring_mode": "Rally",
        },
        "players": {
            "A": [p["id"] for p in players[:2]],
            "B": [p["id"] for p in players[2:]],
        },
    }


def test_roles_and_csrf(web):
    guest = web.test_client()
    assert guest.get("/api/dashboard").status_code == 401
    assert guest.post("/api/players", json={}).status_code == 403
    viewer, csrf = signed_in(web, "VIEWER")
    assert viewer.get("/api/matches").status_code == 200
    assert viewer.get("/api/players").status_code == 403
    assert (
        viewer.post("/api/matches", json={}, headers={"X-CSRF-Token": csrf}).status_code
        == 403
    )
    operator, csrf = signed_in(web, "OPERATOR")
    assert operator.get("/api/dashboard").status_code == 200
    assert (
        operator.post("/api/users", json={}, headers={"X-CSRF-Token": csrf}).status_code
        == 403
    )


def test_pages_and_preview_without_database(web, monkeypatch):
    client, csrf = signed_in(web)
    for route in (
        "/",
        "/setup",
        "/players",
        "/queue",
        "/checkin",
        "/courts",
        "/control",
        "/matches",
        "/settings",
        "/live",
        "/preview",
    ):
        assert client.get(route).status_code == 200

    def outage():
        raise RuntimeError("database is down")

    monkeypatch.setattr(web.extensions["database"], "transaction", outage)
    response = client.get("/api/preview?a=10&b=9&server=2&best_of=1")
    assert response.status_code == 200
    assert response.json["indicators"]["match_point"] == ["A"]


def test_checkin_queue_and_player_profile(web):
    players = roster(web)
    club = web.extensions["club"]
    assert len(club.dashboard()["queue"]) == 4
    club.checkin(players[0]["id"])
    assert len(club.dashboard()["queue"]) == 4
    club.queue_action(players[3]["id"], "up")
    assert club.dashboard()["queue"][2]["id"] == players[3]["id"]
    club.queue_action(players[3]["id"], "priority")
    assert club.dashboard()["queue"][0]["id"] == players[3]["id"]
    club.queue_action(players[3]["id"], "remove")
    assert len(club.dashboard()["queue"]) == 3
    client, _ = signed_in(web)
    assert (
        client.get("/api/players/" + str(players[0]["id"])).json["statistics"][
            "matches"
        ]
        == 0
    )


def test_setup_reservation_and_completion_undo(web):
    players = roster(web)
    club = web.extensions["club"]
    match = club.prepare_match(payload(players))
    other = club.prepare_match(payload(players))
    club.reserve_match(match["id"])
    with pytest.raises(ValueError):
        club.reserve_match(other["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    for _ in range(11):
        engine.apply("TEAM_A_POINT")
    event = {
        "event_id": str(uuid4()),
        "event_type": "MATCH_WON",
        "match_id": match["id"],
        "snapshot": {
            "settings": engine.settings.to_dict(),
            "state": engine.state.to_dict(),
        },
    }
    club.publish_state(event)
    club.publish_state(event)
    dashboard = club.dashboard()
    assert dashboard["courts"][0]["status"] == "AVAILABLE"
    assert len(dashboard["queue"]) == 4
    with club.db.transaction() as s:
        assert s.get(Player, players[0]["id"]).wins == 1
        assert s.get(Player, players[2]["id"]).losses == 1
        assert (
            s.scalar(
                select(func.count())
                .select_from(MatchEvent)
                .where(MatchEvent.event_id == event["event_id"])
            )
            == 1
        )
    engine.apply("TEAM_A_UNDO")
    club.publish_state(
        {
            **event,
            "event_id": str(uuid4()),
            "event_type": "UNDO",
            "snapshot": {
                "settings": engine.settings.to_dict(),
                "state": engine.state.to_dict(),
            },
        }
    )
    assert club.dashboard()["courts"][0]["status"] == "PLAYING"
    assert not club.dashboard()["queue"]
    with club.db.transaction() as s:
        assert s.get(Player, players[0]["id"]).wins == 0


def test_web_setup_and_danger_confirmation(web):
    players = roster(web)
    client, csrf = signed_in(web, "OPERATOR")
    response = client.post(
        "/api/matches", json=payload(players), headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 201
    assert response.json["settings_json"]["players_a"] == "Dominic / Chel"
    assert (
        client.post(
            "/api/matches/1/commands",
            json={"name": "RESET"},
            headers={"X-CSRF-Token": csrf},
        ).status_code
        == 400
    )


def test_undo_projection_rejects_reassigned_players(web):
    players = roster(web)
    club = web.extensions["club"]
    match = club.prepare_match(payload(players))
    club.reserve_match(match["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    for _ in range(11):
        engine.apply("TEAM_A_POINT")
    event = {
        "event_id": str(uuid4()),
        "event_type": "MATCH_WON",
        "match_id": match["id"],
        "snapshot": {
            "settings": engine.settings.to_dict(),
            "state": engine.state.to_dict(),
        },
    }
    club.publish_state(event)
    with club.db.transaction() as s:
        court = Court(name="Court 2", number=2)
        s.add(court)
        s.flush()
        court_id = court.id
    next_payload = payload(players)
    next_payload["court_id"] = court_id
    next_match = club.prepare_match(next_payload)
    club.reserve_match(next_match["id"])
    engine.apply("TEAM_A_UNDO")
    with pytest.raises(ValueError, match="reassigned"):
        club.publish_state(
            {
                **event,
                "event_id": str(uuid4()),
                "event_type": "UNDO",
                "snapshot": {
                    "settings": engine.settings.to_dict(),
                    "state": engine.state.to_dict(),
                },
            }
        )
    with club.db.transaction() as s:
        assert s.get(Match, match["id"]).status == "COMPLETE"
        assert s.get(Player, players[0]["id"]).wins == 1


def test_matchmaking_balance_and_overdue():
    players = [
        {
            "id": i + 1,
            "position": i,
            "waiting_minutes": 10,
            "games_today": 0,
            "skill_rating": rating,
        }
        for i, rating in enumerate([3.8, 3.4, 3.7, 3.5])
    ]
    result = recommend(players)
    assert result["imbalance"] == 0
    players += [
        {
            "id": 5,
            "position": 99,
            "waiting_minutes": 40,
            "games_today": 20,
            "skill_rating": 7.5,
        }
    ]
    result = recommend(players)
    assert 5 in result["team_a"] + result["team_b"]
    assert recommend(players[:3]) is None


def test_rotation_avoids_recent_partner():
    players = [
        {
            "id": i,
            "position": i,
            "waiting_minutes": 5,
            "games_today": 0,
            "skill_rating": 3,
            "recent_partners": [2] if i == 1 else [1] if i == 2 else [],
        }
        for i in range(1, 5)
    ]
    result = recommend(players)
    assert not ({1, 2}.issubset(result["team_a"]) or {1, 2}.issubset(result["team_b"]))


def test_database_outage_returns_503(web, monkeypatch):
    from sqlalchemy.exc import OperationalError

    client, _ = signed_in(web)

    def outage():
        raise OperationalError("query", {}, Exception("unavailable"))

    monkeypatch.setattr(web.extensions["database"], "transaction", outage)
    assert client.get("/api/dashboard").status_code == 503


def test_sse_emits_and_loopback_validation(web, monkeypatch):
    service = web.extensions["match_state"]
    monkeypatch.setattr(
        service, "get_current_state", lambda *_: {"connected": True, "revision": 7}
    )
    response = web.test_client().get("/api/events", buffered=False)
    assert "event: state" in next(iter(response.response)).decode()
    assert response.headers["X-Accel-Buffering"] == "no"
    response.close()
    with pytest.raises(ValueError):
        service.request("/state", url="http://192.168.2.204:8765")


def test_manual_result_projection_rankings_and_undo(web):
    from pickleball_scoreboard.web.models import Game
    players = roster(web)
    club = web.extensions["club"]
    match = club.prepare_match(payload(players))
    club.reserve_match(match["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    engine.apply("DECLARE_WINNER", {"winner": "B", "scores": {"A": 8, "B": 2}})
    def publish(kind):
        club.publish_state({
            "event_id": str(uuid4()), "event_type": kind, "match_id": match["id"],
            "snapshot": {"settings": engine.settings.to_dict(), "state": engine.state.to_dict()}
        })
    publish("DECLARE_WINNER")
    result = web.test_client().get("/api/leaderboard").json
    assert [p["rank"] for p in result["players"]] == [1, 1, 3, 3]
    winners = result["players"][:2]
    assert {p["player_id"] for p in winners} == {p["id"] for p in players[2:]}
    assert all(p["wins"] == 1 and p["point_difference"] == -6 for p in winners)
    with club.db.transaction() as s:
        game = s.scalar(select(Game).where(Game.match_id == match["id"]))
        assert game.winner_team == "B"
    engine.apply("TEAM_A_UNDO")
    publish("UNDO")
    assert club.leaderboard()["players"] == []
    assert club.dashboard()["courts"][0]["status"] == "PLAYING"


def test_manual_controls_permissions_validation_and_court_routing(web, monkeypatch):
    from pickleball_scoreboard.web.models import ScoreboardDevice
    players = roster(web)
    club = web.extensions["club"]
    with club.db.transaction() as s:
        s.add(Court(id=2, number=2, name="Court 2"))
        s.add(ScoreboardDevice(device_name="Court 2", court_id=2, bridge_url="http://127.0.0.1:8766", last_seen_at=utcnow()))
    data = payload(players)
    data["court_id"] = 2
    match = club.prepare_match(data)
    club.reserve_match(match["id"])
    with club.db.transaction() as s:
        s.get(Match, match["id"]).status = "ACTIVE"
    facade = web.extensions["match_state"]
    calls = []
    def capture(path, payload=None, url=None):
        calls.append((path, payload, url))
        return {"request_id": payload["request_id"], "status": "QUEUED"}
    monkeypatch.setattr(facade, "request", capture)
    client, csrf = signed_in(web, "OPERATOR")
    route = f"/api/matches/{match['id']}/commands"
    details = {"winner": "A", "scores": {"A": 9, "B": 7}, "expected_revision": 3, "match_id": 999}
    body = {"name": "DECLARE_WINNER", "confirmed": True, "payload": details}
    assert client.post(route, json={**body, "confirmed": False}, headers={"X-CSRF-Token": csrf}).status_code == 400
    assert client.post(route, json={**body, "payload": {**details, "scores": {"A": -1, "B": 0}}}, headers={"X-CSRF-Token": csrf}).status_code == 400
    assert client.post(route, json=body).status_code == 403
    assert calls == []
    assert client.post(route, json=body, headers={"X-CSRF-Token": csrf}).status_code == 202
    assert calls[0][2] == "http://127.0.0.1:8766"
    assert calls[0][1]["payload"]["match_id"] == match["id"]
    viewer, token = signed_in(web, "VIEWER")
    assert viewer.post(route, json=body, headers={"X-CSRF-Token": token}).status_code == 403
    web.config["PUBLIC_LIVE"] = False
    assert web.test_client().get("/api/leaderboard").status_code == 401
    assert viewer.get("/api/leaderboard").status_code == 200


def test_courtside_public_feed_all_courts_recommendation_and_privacy(web, monkeypatch):
    club = web.extensions["club"]
    players = roster(web)
    with club.db.transaction() as s:
        s.add(Court(id=2, number=7, name="Garden Court"))
        s.flush()
        s.add(ScoreboardDevice(device_name="garden",court_id=2,last_seen_at=utcnow()))
    for name in ("Next One", "Next Two", "Next Three", "Next Four"):
        p = club.save_player({"display_name": name})
        club.checkin(p["id"])
    match = club.prepare_match(payload(players))
    club.reserve_match(match["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    engine.apply("TEAM_A_POINT")
    club.publish_state({"event_id": str(uuid4()), "event_type": "POINT", "match_id": match["id"],
        "snapshot": {"settings": engine.settings.to_dict(), "state": engine.state.to_dict()}})
    def live(court_id):
        return {"connected": court_id == 1, "match_id": match["id"] if court_id == 1 else None,
                "settings": engine.settings.to_dict(), "state": engine.state.to_dict()}
    monkeypatch.setattr(web.extensions["match_state"], "get_current_state", live)
    client = web.test_client()
    d = client.get("/api/courtside").json
    assert [c["number"] for c in d["courts"]] == [1, 7]
    assert d["courts"][0]["live"]["state"]["game"]["scores"] == {"A": 1, "B": 0}
    assert d["courts"][0]["live"]["settings"]["players_a"] == "Dominic / Chel"
    assert d["courts"][1]["live"]["connected"] is False
    assert d["recommended_match"]["court_number"] == 7
    assert set(d["recommended_match"]["team_a"] + d["recommended_match"]["team_b"]) == {"Next One", "Next Two", "Next Three", "Next Four"}
    assert d["waiting_players"] == 4
    assert "queue" not in d and "matches" not in d
    assert all("bridge_url" not in court for court in d["courts"])
    # Score polling expires after 100 ms, independently of the five-second
    # recommendation/ranking cache. Advance time without a flaky sleep.
    from time import monotonic
    monkeypatch.setattr("pickleball_scoreboard.web.state_service.monotonic", lambda: monotonic() + .2)
    engine.apply("TEAM_A_POINT")
    d = client.get("/api/courtside").json
    assert d["courts"][0]["live"]["state"]["game"]["scores"]["A"] == 2
    web.config["PUBLIC_LIVE"] = False
    assert client.get("/api/courtside").status_code == 401
    assert client.get("/preview").status_code == 302
    viewer, _ = signed_in(web, "VIEWER")
    assert viewer.get("/api/courtside").status_code == 200


def test_courtside_no_courts_and_no_pending_match(web):
    from sqlalchemy import delete
    club = web.extensions["club"]
    with club.db.transaction() as s:
        s.execute(delete(ScoreboardDevice))
        s.execute(delete(Court))
    d = web.test_client().get("/api/courtside").json
    assert d["courts"] == []
    assert d["recommended_match"] is None
    assert d["leaderboard"]["players"] == []


def test_equal_recommendations_do_not_shuffle_as_waiting_time_changes():
    players = [{"id":i,"position":i,"waiting_minutes":1.001,"priority":0,"games_today":0,"skill_rating":3.5} for i in range(1,5)]
    first = recommend(players)
    for p in players:
        p["waiting_minutes"] = 1.002
    later = recommend(players)
    assert first["team_a"] == later["team_a"] == [1, 2]
    assert first["team_b"] == later["team_b"] == [3, 4]

@pytest.mark.parametrize("role", ["OPERATOR", "VIEWER"])
def test_admin_management_denied(web, role):
    client, csrf = signed_in(web, role)
    headers = {"X-CSRF-Token": csrf}
    for path in ("/api/devices", "/api/devices/1/delete", "/api/users/1", "/api/users/1/delete"):
        assert client.post(path, json={}, headers=headers).status_code == 403
    assert client.get('/api/settings').status_code == 403


def test_staff_edit_delete_and_admin_protection(web):
    client, csrf = signed_in(web)
    headers = {"X-CSRF-Token": csrf}
    users = client.get('/api/settings').json['users']
    assert all('password_hash' not in u for u in users)
    admin = next(u for u in users if u['role'] == 'ADMIN')
    operator = next(u for u in users if u['role'] == 'OPERATOR')
    assert client.post('/api/users/' + str(admin['id']), json={'role': 'VIEWER'}, headers=headers).status_code == 400
    assert client.post('/api/users/' + str(admin['id']) + '/delete', json={}, headers=headers).status_code == 400
    other, _ = signed_in(web, 'OPERATOR')
    assert client.post('/api/users/' + str(operator['id']), json={'username': 'Updated', 'role': 'VIEWER'}, headers=headers).status_code == 200
    assert other.get('/api/players').status_code == 403
    assert client.post('/api/users/' + str(operator['id']) + '/delete', json={}, headers=headers).status_code == 200
    assert other.get('/api/courts').status_code == 401


def test_device_edit_delete(web):
    client, csrf = signed_in(web)
    headers = {"X-CSRF-Token": csrf}
    values = {'id': 1, 'device_name': 'Test display', 'court_id': 1, 'bridge_url': 'http://127.0.0.1:8765'}
    response = client.post('/api/devices', json=values, headers=headers)
    assert response.status_code == 200
    device_id = response.json['id']
    values.update(id=device_id, device_name='Updated display')
    assert client.post('/api/devices', json=values, headers=headers).json['device_name'] == 'Updated display'
    with web.extensions['database'].transaction() as s:
        s.get(Court, 1).active_match_id = 123
    path = '/api/devices/' + str(device_id) + '/delete'
    assert client.post(path, json={}, headers=headers).status_code == 400
    with web.extensions['database'].transaction() as s:
        s.get(Court, 1).active_match_id = None
    assert client.post(path, json={}, headers=headers).status_code == 200
    assert client.post(path, json={}, headers=headers).status_code == 404


def test_login_link_opens_public_preview(web):
    client = web.test_client()
    assert b'href="/preview">View live scoreboard' in client.get('/login').data
    assert client.get('/preview').status_code == 200


def test_admin_changes_staff_password(web):
    client, csrf = signed_in(web)
    headers = {'X-CSRF-Token': csrf}
    account = next(u for u in client.get('/api/settings').json['users'] if u['role'] == 'OPERATOR')
    path = '/api/users/' + str(account['id'])
    assert client.post(path, json={'password': 'short'}, headers=headers).status_code == 400
    assert client.post(path, json={'password': ''}, headers=headers).status_code == 200
    signed_in(web, 'OPERATOR')
    assert client.post(path, json={'password': 'replacement-password-123'}, headers=headers).status_code == 200
    other = web.test_client()
    token = other.get('/api/session').json['csrf']
    assert other.post('/login', json={'username': 'OPERATOR', 'password': 'testpassword123'}, headers={'X-CSRF-Token': token}).status_code == 401
    assert other.post('/login', json={'username': 'OPERATOR', 'password': 'replacement-password-123'}, headers={'X-CSRF-Token': token}).status_code == 302


def test_phone_pin_approval_expiry_and_court_scope(web, monkeypatch):
    from pickleball_scoreboard.web.models import PhoneAccess, utcnow
    from datetime import timedelta
    admin, token = signed_in(web)
    headers = {'X-CSRF-Token': token}
    base = '/api/courts/1/phone'
    phone = web.test_client()
    csrf = phone.get('/api/session').json['csrf']
    ph = {'X-CSRF-Token': csrf}
    assert phone.get('/court/1').status_code == 200
    assert b'<svg' in phone.get('/api/courts/1/qr.svg').data
    assert phone.post(base + '/commands', json={'name':'TEAM_A_POINT'}, headers=ph).status_code == 403
    assert admin.post(base + '/config', json={'enabled':True,'pin':'123456'}, headers=headers).status_code == 200
    assert phone.post(base + '/request', json={'pin':'bad'}, headers=ph).status_code == 403
    assert phone.post(base + '/request', json={'pin':'123456'}, headers=ph).status_code == 200
    grant = admin.get(base + '/admin').json['requests'][0]
    assert admin.post(base + '/approve', json={'id':grant['id'],'approved':True},headers=headers).status_code == 200
    assert phone.get(base).json['status'] == 'APPROVED'
    service = web.extensions['match_state']
    monkeypatch.setattr(service, 'get_current_state', lambda _: {'connected':True,'match_exists':True,'match_id':None,'revision':10})
    monkeypatch.setattr(service, 'device_url', lambda _: 'http://127.0.0.1:8765')
    calls=[]
    monkeypatch.setattr(service,'request',lambda *args,**kw: calls.append(args) or {'status':'QUEUED','request_id':str(uuid4())})
    assert phone.post(base + '/commands', json={'name':'RESET','revision':10},headers=ph).status_code == 400
    assert phone.post(base + '/commands', json={'name':'TEAM_A_POINT','revision':9},headers=ph).status_code == 400
    assert phone.post(base + '/commands', json={'name':'TEAM_A_POINT','revision':10},headers=ph).status_code == 202
    assert calls[0][1]['payload']['phone_control'] is True
    assert phone.post('/api/courts/2/phone/commands',json={'name':'TEAM_A_POINT','revision':10},headers=ph).status_code == 403
    with web.extensions['database'].transaction() as s:
        s.get(PhoneAccess,grant['id']).expires_at=utcnow()-timedelta(seconds=1)
    assert phone.post(base + '/commands',json={'name':'TEAM_A_POINT','revision':10},headers=ph).status_code == 403


def test_phone_pin_rate_limit_and_operator_security(web):
    viewer, csrf = signed_in(web,'VIEWER')
    assert viewer.post('/api/courts/1/phone/config',json={'enabled':True,'pin':'123456'},headers={'X-CSRF-Token':csrf}).status_code == 403
    for _ in range(5):
        assert viewer.post('/api/courts/1/phone/request',json={'pin':'bad'},headers={'X-CSRF-Token':csrf}).status_code == 403
    assert viewer.post('/api/courts/1/phone/request',json={'pin':'bad'},headers={'X-CSRF-Token':csrf}).status_code == 429


def test_casual_snapshot_recovery_and_on_deck(web, monkeypatch):
    from pickleball_scoreboard.web.models import ScoreboardDevice
    club=web.extensions['club']
    with web.extensions['database'].transaction() as s:
        s.get(ScoreboardDevice, 1).device_name = 'casual'
    engine=ScoringEngine(Settings())
    engine.apply('TEAM_A_POINT')
    snapshot={'match_exists':True,'match_id':None,'updated_at':123,'settings':engine.settings.to_dict(),'state':engine.state.to_dict()}
    club.publish_state({'event_id':str(uuid4()),'event_type':'POINT','device_id':1,'match_id':None,'snapshot':snapshot})
    def offline(*args,**kwargs):
        raise OSError('offline')
    monkeypatch.setattr(web.extensions['match_state'],'request',offline)
    restored=web.extensions['match_state'].get_current_state(1)
    assert restored['connected'] is False and restored['state']['game']['scores']['A']==1
    for i in range(8):
        player=club.save_player({'display_name':'Waiting '+str(i)})
        club.checkin(player['id'])
    data=club.courtside()
    assert data['on_deck']
    first=set(data['recommended_match']['team_a']+data['recommended_match']['team_b'])
    second=set(data['on_deck']['team_a']+data['on_deck']['team_b'])
    assert not first.intersection(second)


def test_correct_archived_result_without_double_counting(web,monkeypatch):
    players=roster(web)
    club=web.extensions['club']
    match=club.prepare_match(payload(players))
    club.reserve_match(match['id'])
    engine=ScoringEngine(Settings(**match['settings_json']))
    engine.apply('DECLARE_WINNER',{'scores':{'A':8,'B':6},'winner':'A'})
    club.publish_state({'event_id':str(uuid4()),'event_type':'DECLARE_WINNER','match_id':match['id'],'snapshot':{'settings':engine.settings.to_dict(),'state':engine.state.to_dict()}})
    service=web.extensions['match_state']
    monkeypatch.setattr(service,'get_current_state',lambda _: {'connected':True,'match_id':999})
    client,csrf=signed_in(web)
    values={'scores':{'A':6,'B':8},'winner':'B','reason':'Operator correction','confirmed':True,'record_revision':0}
    path='/api/matches/'+str(match['id'])+'/correct'
    assert client.post(path,json=values,headers={'X-CSRF-Token':csrf}).status_code == 200
    rankings=club.leaderboard()['players']
    assert all(p['matches']==1 for p in rankings)
    assert rankings[0]['player_id'] in [p['id'] for p in players[2:]]
    assert client.post(path,json=values,headers={'X-CSRF-Token':csrf}).status_code == 400
    values['record_revision']=1
    assert client.post(path,json=values,headers={'X-CSRF-Token':csrf}).status_code == 200
    assert all(p['matches']==1 for p in club.leaderboard()['players'])
    audit=client.get('/api/audit').json
    assert audit[0]['actor']=='ADMIN' and audit[0]['details']['reason']=='Operator correction'


def test_confirmed_courtside_match_excludes_recommended_players(web):
    club=web.extensions['club']
    players=roster(web)
    club.prepare_match(payload(players))
    for i in range(4):
        player=club.save_player({'display_name':'Next '+str(i)})
        club.checkin(player['id'])
    data=club.courtside()
    assert len(data['confirmed_matches'])==1
    assert all(name.startswith('Next ') for name in data['recommended_match']['team_a']+data['recommended_match']['team_b'])


def test_managed_ready_reserves_court_without_starting_clock_or_game(web):
    from pickleball_scoreboard.web.models import Game
    club = web.extensions["club"]
    players = roster(web)
    match = club.prepare_match(payload(players))
    club.reserve_match(match["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    snapshot = {"settings": engine.settings.to_dict(), "state": engine.state.to_dict(), "ready_to_play": True, "started_at": None}
    club.publish_state({"event_id": str(uuid4()), "event_type": "MATCH_READY", "match_id": match["id"], "snapshot": snapshot})
    with club.db.transaction() as s:
        stored = s.get(Match, match["id"])
        assert stored.status == "STARTING" and stored.started_at is None
        assert s.get(Court, stored.court_id).active_match_id == stored.id
        assert s.scalar(select(func.count()).select_from(Game).where(Game.match_id == stored.id)) == 0
    snapshot["ready_to_play"] = False
    snapshot["started_at"] = 1800000000
    club.publish_state({"event_id": str(uuid4()), "event_type": "MATCH_STARTED", "match_id": match["id"], "snapshot": snapshot})
    with club.db.transaction() as s:
        stored = s.get(Match, match["id"])
        assert stored.status == "ACTIVE" and stored.started_at is not None
        assert s.scalar(select(func.count()).select_from(Game).where(Game.match_id == stored.id)) == 1


def test_courtside_reserved_assignment_includes_ready_settings(web):
    club = web.extensions["club"]
    players = roster(web)
    match = club.prepare_match(payload(players))
    club.reserve_match(match["id"])
    assignment = club.courtside()["courts"][0]["assignment"]
    assert assignment["settings"]["first_serving_team"] == "A"
    assert assignment["settings"]["starting_team"] == "Random"
    assert assignment["settings"]["players_a"] == "Dominic / Chel"
    assert assignment["settings"]["points_to_win"] == 11

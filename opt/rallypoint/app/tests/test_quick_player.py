import pytest
from sqlalchemy import select, func
from test_web_club import web, signed_in
from pickleball_scoreboard.web.models import Player, QueueEntry, Checkin, QueueGroup


def test_operator_quick_add_and_duplicate_protection(web):
    client, csrf = signed_in(web, "OPERATOR")
    headers = {"X-CSRF-Token": csrf}
    data = {"first_name": "  Alex  ", "last_name": "Reyes", "skill_rating": "3.5", "rating_type": "Self-rated"}
    response = client.post("/api/players/quick-checkin", json=data, headers=headers)
    assert response.status_code == 201
    p = response.json["player"]
    assert (p["first_name"], p["display_name"], p["status"], p["rating_type"]) == ("Alex", "Alex", "WAITING", "Self-rated")
    assert p["skill_rating"] == 3.5
    assert response.json["queue_entry"]["player_id"] == p["id"]
    repeat = client.post("/api/players/quick-checkin", json={"first_name":"alex"}, headers=headers)
    assert repeat.status_code == 400
    assert "already exists" in repeat.json["error"]
    with web.extensions["database"].transaction() as s:
        for model in (Player, QueueEntry, Checkin, QueueGroup):
            assert s.scalar(select(func.count()).select_from(model)) == 1
    assert client.post("/api/players/quick-checkin", json={"first_name":"Alex", "display_name":"Alex R"}, headers=headers).status_code == 201


def test_quick_add_permissions_and_validation(web):
    viewer, csrf = signed_in(web, "VIEWER")
    assert viewer.post("/api/players/quick-checkin", json={"first_name":"No"},headers={"X-CSRF-Token":csrf}).status_code == 403
    client, csrf = signed_in(web, "OPERATOR")
    assert client.post("/api/players/quick-checkin",json={"first_name":"No"}).status_code == 403
    for data in ({}, {"first_name":" "}, {"first_name":"x"*81}, {"first_name":"Alex","skill_rating":"9"}, {"first_name":"Alex","rating_type":"Other"}):
        assert client.post("/api/players/quick-checkin",json=data,headers={"X-CSRF-Token":csrf}).status_code == 400
    with web.extensions["database"].transaction() as s:
        assert s.scalar(select(func.count()).select_from(Player)) == 0


def test_quick_add_rolls_back_if_queue_fails(web, monkeypatch):
    club = web.extensions["club"]
    def fail(*args):
        raise ValueError("Queue unavailable")
    monkeypatch.setattr(club, "_checkin_player", fail)
    with pytest.raises(ValueError):
        club.quick_checkin_player({"first_name":"Alex"})
    with club.db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(Player)) == 0

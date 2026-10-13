from sqlalchemy import select
from test_web_club import web, signed_in, roster, payload
from pickleball_scoreboard.web.models import Court, ScoreboardDevice, utcnow


def add_players(web, count):
    club = web.extensions["club"]
    for index in range(count):
        club.quick_checkin_player({"first_name": "Waiting " + str(index)})


def test_two_non_overlapping_recommendations_when_courts_busy(web):
    add_players(web, 8)
    with web.extensions["database"].transaction() as s:
        s.get(Court,1).status = "MAINTENANCE"
    client, _ = signed_in(web, "OPERATOR")
    data = client.get("/api/dashboard").json
    assert len(data["recommendations"]) == 2
    assert data["suggestion"] == data["recommendations"][0]
    ids = []
    for match in data["recommendations"]:
        assert match["court_id"] is None
        assert len(match["team_a"]) == len(match["team_b"]) == 2
        ids.extend(match["team_a"] + match["team_b"])
    assert len(set(ids)) == 8


def test_recommendations_use_distinct_available_courts(web):
    add_players(web, 8)
    with web.extensions["database"].transaction() as s:
        court = Court(number=8,name="Garden Court")
        s.add(court); s.flush()
        s.add(ScoreboardDevice(device_name="garden",court_id=court.id,last_seen_at=utcnow()))
    matches = web.extensions["club"].dashboard()["recommendations"]
    assert len(matches) == 2
    assert all(m["court_id"] for m in matches)
    assert matches[0]["court_id"] != matches[1]["court_id"]


def test_recommendations_require_enough_players(web):
    add_players(web, 3)
    club = web.extensions["club"]
    assert club.dashboard()["recommendations"] == []
    club.quick_checkin_player({"first_name":"Fourth"})
    assert len(club.dashboard()["recommendations"]) == 1
    assert len(club.dashboard(singles=True)["recommendations"]) == 2


def test_public_preview_has_two_named_recommendations_without_private_fields(web):
    add_players(web, 8)
    data = web.test_client().get("/api/courtside").json
    matches = data["recommendations"]
    assert len(matches) == 2
    names = [name for m in matches for name in m["team_a"] + m["team_b"]]
    assert len(set(names)) == 8
    assert data["recommended_match"] == matches[0]
    assert data["on_deck"] == matches[1]
    assert all(set(m) == {"court_number","court_name","team_a","team_b"} for m in matches)


def test_preview_recommendations_exclude_prepared_match_players(web):
    reserved = roster(web)
    add_players(web, 8)
    web.extensions["club"].prepare_match(payload(reserved))
    data = web.test_client().get("/api/courtside").json
    names = [name for m in data["recommendations"] for name in m["team_a"] + m["team_b"]]
    assert len(set(names)) == 8
    assert not set(names).intersection(p["display_name"] for p in reserved)
    assert len(data["confirmed_matches"]) == 1

from uuid import uuid4
from test_web_club import web, roster, payload
from pickleball_scoreboard.config import Settings
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine


def test_waiting_counts_completed_matches(web):
    club = web.extensions["club"]
    players = roster(web)
    assert all(p["matches_completed"] == 0 for p in club.dashboard()["queue"])
    match = club.prepare_match(payload(players))
    club.reserve_match(match["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    engine.apply("DECLARE_WINNER", {"scores":{"A":11,"B":5},"winner":"A"})
    club.publish_state({"event_id":str(uuid4()),"event_type":"DECLARE_WINNER","match_id":match["id"],
                        "snapshot":{"settings":engine.settings.to_dict(),"state":engine.state.to_dict()}})
    for p in players:
        club.checkin(p["id"])
    assert len(club.dashboard()["queue"]) == 4
    assert all(p["matches_completed"] == 1 for p in club.dashboard()["queue"])

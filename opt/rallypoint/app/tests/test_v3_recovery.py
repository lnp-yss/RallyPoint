import json
from uuid import uuid4
from pickleball_scoreboard.services.scoreboard_bridge import DurableOutbox
from pickleball_scoreboard.config import Settings
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.scoring.game_state import MatchState
from pickleball_scoreboard.services.persistence import Persistence


def test_restart_command_receipt_matches_atomic_score_snapshot(tmp_path):
    store = Persistence(tmp_path)
    engine = ScoringEngine(Settings())
    request_id = str(uuid4())
    engine.apply("TEAM_A_POINT")
    context = {"match_id": 1, "started_at": 123, "applied_requests": [request_id]}
    assert store.save(engine, context)
    restored = store.restore()
    assert restored.external_context == context
    queue = DurableOutbox(tmp_path / "commands.db")
    queue.receipt(request_id, {"request_id": request_id, "status": "QUEUED"})
    unapplied = str(uuid4())
    queue.receipt(unapplied, {"request_id": unapplied, "status": "QUEUED"})
    queue.recover_receipts(restored.external_context["applied_requests"])
    assert queue.receipt(request_id)["status"] == "APPLIED"
    assert queue.receipt(unapplied)["status"] == "REJECTED"


def test_v2_snapshot_loads_and_paused_undo():
    engine = ScoringEngine(Settings())
    data = engine.state.to_dict()
    del data["paused"]
    assert MatchState.from_dict(data).paused is False
    engine.apply("TEAM_A_POINT")
    engine.apply("PAUSE")
    assert engine.apply("TEAM_A_POINT") is None
    engine.apply("RESUME")
    engine.apply("TEAM_A_UNDO")
    assert engine.state.paused
    engine.apply("TEAM_A_UNDO")
    assert not engine.state.paused and engine.state.game.scores["A"] == 1


def test_manual_end_game_end_match_undo():
    engine = ScoringEngine(Settings())
    assert engine.apply("END_GAME") is None  # Cannot declare a tied winner.
    engine.apply("TEAM_A_POINT")
    before = engine.state.to_dict()
    assert engine.apply("END_GAME") == "GAME WON"
    engine.apply("TEAM_A_UNDO")
    assert engine.state.to_dict() == before
    engine.apply("END_MATCH")
    assert engine.state.status == "ended" and engine.state.winner is None
    engine.apply("TEAM_A_UNDO")
    assert engine.state.to_dict() == before

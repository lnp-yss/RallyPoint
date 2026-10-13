"""Real database reservations and pure strategies; no Pygame window required."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select, func
from pickleball_scoreboard.web.database import Database
from pickleball_scoreboard.web.club_service import ClubService
from pickleball_scoreboard.web.models import (
    Court,
    Player,
    QueueEntry,
    QueueGroup,
    Match,
    MatchPlayer,
    ScoreboardDevice,
    DeviceSnapshot,
    WinnerHold,
    SchemaMigration,
    utcnow,
)
from pickleball_scoreboard.web.matching import MatchingSettings, strategy
from pickleball_scoreboard.scoring.scoring_engine import ScoringEngine
from pickleball_scoreboard.config import Settings


@pytest.fixture
def club(tmp_path):
    db = Database("sqlite:///" + (tmp_path / "rotation.db").as_posix())
    db.migrate()
    service = ClubService(db)
    yield service
    db.engine.dispose()


def courts(club, count, numbers=None):
    club.rotation.configure_courts({"count": count})
    with club.db.transaction() as s:
        rows = list(s.scalars(select(Court).order_by(Court.id)))
        for index in reversed(range(len(rows))):
            court = rows[index]
            if numbers:
                court.number = numbers[index]
                court.name = f"Court {court.number}"
                s.flush()
            court.available_since = utcnow() - timedelta(minutes=3)
            s.add(
                ScoreboardDevice(
                    device_name=f"pi-{court.id}",
                    court_id=court.id,
                    last_seen_at=utcnow(),
                )
            )
    return [c.id for c in rows]


def players(club, count, ratings=None):
    return [
        club.save_player(
            {
                "display_name": f"Player {i}",
                "skill_rating": ratings[i] if ratings else 3,
            }
        )["id"]
        for i in range(count)
    ]


def solos(club, ids):
    for i in ids:
        club.rotation.checkin([i])


def configure(club, mode="FAIR_BALANCED", **kwargs):
    return club.rotation.save_configuration(
        {"name": "Test Open Play", "settings": {"mode": mode, **kwargs}}
    )


def assignments(club):
    with club.db.transaction() as s:
        return [
            (x.match_id, x.player_id, x.team) for x in s.scalars(select(MatchPlayer))
        ]


@pytest.mark.parametrize("count", [1, 2, 3, 5, 7])
def test_dynamic_court_count_no_duplicate_players(club, count):
    courts(club, count)
    ids = players(club, count * 4 + 3)
    solos(club, ids)
    result = club.rotation.generate(force=True)
    assert len(result) == count
    links = assignments(club)
    assert len({p for _, p, _ in links}) == 4 * count
    assert len({m["court_id"] for m in result}) == count
    assert len(club.dashboard()["queue"]) == 3


@pytest.mark.parametrize("groups", [(1, 1, 1, 1), (2, 1, 1), (2, 2)])
def test_all_valid_team_combinations(club, groups):
    courts(club, 1)
    ids = players(club, 4)
    offset = 0
    fixed = []
    for size in groups:
        unit = ids[offset : offset + size]
        club.rotation.checkin(unit)
        if size == 2:
            fixed.append(unit)
        offset += size
    assert len(club.rotation.generate(force=True)) == 1
    links = {p: t for _, p, t in assignments(club)}
    assert all(links[a] == links[b] for a, b in fixed)


def test_no_match_with_insufficient_players(club):
    courts(club, 5)
    solos(club, players(club, 3))
    assert club.rotation.generate(force=True) == []


def test_non_contiguous_numbers_and_database_identity(club):
    court_ids = courts(club, 3, [2, 5, 7])
    solos(club, players(club, 12))
    result = club.rotation.generate(force=True)
    assert [m["court_id"] for m in result] == court_ids
    assert [c["number"] for c in club.rotation.courts()] == [2, 5, 7]


def test_disabled_offline_and_casual_courts_excluded(club):
    ids = courts(club, 4)
    with club.db.transaction() as s:
        s.get(Court, ids[0]).enabled = 0
        device = s.scalar(
            select(ScoreboardDevice).where(ScoreboardDevice.court_id == ids[1])
        )
        device.last_seen_at = utcnow() - timedelta(minutes=2)
        device = s.scalar(
            select(ScoreboardDevice).where(ScoreboardDevice.court_id == ids[2])
        )
        s.add(
            DeviceSnapshot(
                device_id=device.id,
                snapshot_json={"match_exists": True, "state": {"status": "playing"}},
            )
        )
    solos(club, players(club, 16))
    assert [m["court_id"] for m in club.rotation.generate(force=True)] == [ids[3]]
    assert [c["status"] for c in club.rotation.courts()] == [
        "DISABLED",
        "OFFLINE",
        "CASUAL_IN_USE",
        "RESERVED",
    ]


def test_concurrent_generators_serialize(club):
    courts(club, 3)
    solos(club, players(club, 12))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: club.rotation.generate(force=True), range(2)))
    assert sum(map(len, results)) == 3
    assert len({p for _, p, _ in assignments(club)}) == 12


def test_duplicate_checkin_and_split_rejected(club):
    courts(club, 1)
    a, b, c, d = players(club, 4)
    group = club.rotation.checkin([a, b])
    solos(club, [c, d])
    with pytest.raises(ValueError):
        club.rotation.checkin([a])
    with pytest.raises(ValueError, match="split"):
        club.rotation.manual({"court_id": 1, "players": {"A": [a, c], "B": [b, d]}})
    with club.db.transaction() as s:
        assert s.scalar(select(func.count()).select_from(Match)) == 0
        assert s.get(QueueGroup, group["id"]).status == "WAITING"


def test_atomic_rollback_after_second_court_failure(club, monkeypatch):
    courts(club, 2)
    solos(club, players(club, 8))
    original = club.rotation.create_reserved
    calls = []

    def fail(*args):
        calls.append(1)
        if len(calls) == 2:
            raise ValueError("Simulated database failure")
        return original(*args)

    monkeypatch.setattr(club.rotation, "create_reserved", fail)
    with pytest.raises(ValueError):
        club.rotation.generate(force=True)
    assert not assignments(club)
    assert all(c["status"] == "AVAILABLE" for c in club.rotation.courts())
    assert len(club.dashboard()["queue"]) == 8


def test_mode_change_reserved_match_retains_original_settings(club):
    courts(club, 2)
    configure(club, "QUEUE")
    solos(club, players(club, 8))
    first = club.rotation.generate(force=True)
    configure(club, "RANDOM", points_to_win=21)
    with club.db.transaction() as s:
        assert all(s.get(Match, m["id"]).matchmaking_mode == "QUEUE" for m in first)
        assert all(s.get(Match, m["id"]).points_to_win == 11 for m in first)
    with pytest.raises(ValueError):
        club.rotation.configure_courts({"count": 1})


def test_cancel_reassign_and_reuse_players(club):
    courts(club, 2)
    solos(club, players(club, 4))
    match = club.rotation.generate(force=True)[0]
    club.rotation.cancel(match["id"], 2)
    with club.db.transaction() as s:
        assert s.get(Match, match["id"]).court_id == 2
        assert s.get(Court, 1).active_match_id is None
    club.rotation.cancel(match["id"])
    assert len(club.dashboard()["queue"]) == 4
    assert len(club.rotation.generate(force=True)) == 1


def publish(club, match, engine, event_type="RESULT"):
    club.publish_state(
        {
            "event_id": str(uuid4()),
            "event_type": event_type,
            "match_id": match["id"],
            "snapshot": {
                "settings": engine.settings.to_dict(),
                "state": engine.state.to_dict(),
            },
        }
    )


def test_completion_fixed_team_requeue_and_undo(club):
    courts(club, 1)
    ids = players(club, 4)
    group = club.rotation.checkin(ids[:2])
    solos(club, ids[2:])
    match = club.rotation.generate(force=True)[0]
    club.reserve_match(match["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    engine.apply("DECLARE_WINNER", {"winner": "A", "scores": {"A": 11, "B": 8}})
    publish(club, match, engine)
    queue = club.dashboard()["queue"]
    assert {p["id"] for p in queue if p["group_id"] == group["id"]} == set(ids[:2])
    assert len(queue) == 4
    engine.apply("TEAM_A_UNDO")
    publish(club, match, engine, "UNDO")
    assert not club.dashboard()["queue"]
    assert club.rotation.courts()[0]["stored_status"] == "IN_USE"


def test_winners_stay_and_cap(club):
    courts(club, 1)
    configure(club, "WINNERS_STAY", maximum_consecutive_wins=2)
    ids = players(club, 6)
    club.rotation.checkin(ids[:2])
    solos(club, ids[2:])
    match = club.rotation.generate(force=True)[0]
    for index in range(2):
        club.reserve_match(match["id"])
        engine = ScoringEngine(Settings(**match["settings_json"]))
        engine.apply("DECLARE_WINNER", {"winner": "A", "scores": {"A": 11, "B": 5}})
        publish(club, match, engine)
        with club.db.transaction() as s:
            hold = s.get(WinnerHold, 1)
            assert bool(hold.active) == (index == 0)
        if index == 0:
            next_match = club.rotation.generate(force=True)[0]
            with club.db.transaction() as s:
                assert set(
                    s.scalars(
                        select(MatchPlayer.player_id).where(
                            MatchPlayer.match_id == next_match["id"],
                            MatchPlayer.team == "A",
                        )
                    )
                ) == set(
                    s.scalars(
                        select(MatchPlayer.player_id).where(
                            MatchPlayer.match_id == match["id"], MatchPlayer.team == "A"
                        )
                    )
                )
            match = next_match
    assert len(club.dashboard()["queue"]) == 6


def test_undo_cannot_recall_already_reassigned_players(club):
    courts(club, 1)
    solos(club, players(club, 4))
    match = club.rotation.generate(force=True)[0]
    club.reserve_match(match["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    engine.apply("DECLARE_WINNER", {"winner": "A", "scores": {"A": 11, "B": 1}})
    publish(club, match, engine)
    next_match = club.rotation.generate(force=True)[0]
    engine.apply("TEAM_A_UNDO")
    with pytest.raises(ValueError, match="reassigned"):
        publish(club, match, engine, "UNDO")
    with club.db.transaction() as s:
        assert s.get(Court, 1).active_match_id == next_match["id"]


def test_heartbeat_identity_and_offline_retains_active_match(club):
    courts(club, 1)
    solos(club, players(club, 4))
    match = club.rotation.generate(force=True)[0]
    with pytest.raises(ValueError, match="identity"):
        club.publish_state(
            {
                "device_id": 1,
                "event_type": "HEARTBEAT",
                "snapshot": {"identity": {"court_id": 7}},
            }
        )
    with club.db.transaction() as s:
        s.get(ScoreboardDevice, 1).last_seen_at = utcnow() - timedelta(minutes=5)
    assert club.rotation.courts()[0]["status"] == "OFFLINE"
    with club.db.transaction() as s:
        assert s.get(Court, 1).active_match_id == match["id"]


def pure_players(count=4):
    return [
        dict(
            id=i + 1,
            position=i + 1,
            waiting_minutes=0,
            skill_rating=3,
            entry_type="SOLO",
            games_today=0,
        )
        for i in range(count)
    ]


def test_grace_period_poor_fixed_team_and_long_wait_override():
    players = pure_players()
    for p in players:
        p.update(
            group_id=1 if p["id"] < 3 else 2,
            entry_type="TEAM",
            skill_rating=2 if p["id"] < 3 else 5,
        )
    settings = MatchingSettings()
    assert strategy("FAIR_BALANCED").select(players, settings, grace_elapsed=0) is None
    assert strategy("FAIR_BALANCED").select(players, settings, grace_elapsed=120)
    players[0]["waiting_minutes"] = 21
    assert strategy("FAIR_BALANCED").select(players, settings, grace_elapsed=0)


@pytest.mark.parametrize(
    "mode", ["FAIR_BALANCED", "QUEUE", "RANDOM", "SKILL_BASED", "WINNERS_STAY"]
)
def test_all_active_strategies_preserve_fixed_team(mode):
    p = pure_players(8)
    p[0].update(group_id=1, entry_type="TEAM")
    p[1].update(group_id=1, entry_type="TEAM")
    selected = strategy(mode).select(p, MatchingSettings(), grace_elapsed=120)
    assert selected
    assert len(set(selected["team_a"] + selected["team_b"])) == 4
    if 1 in selected["team_a"] + selected["team_b"]:
        assert any({1, 2} == set(selected[key]) for key in ("team_a", "team_b"))


def test_queue_mode_uses_first_four_and_skill_mode_balances():
    p = pure_players(8)
    for player, rating in zip(p, [2, 2, 4, 4, 3, 3, 3, 3]):
        player["skill_rating"] = rating
    selected = strategy("QUEUE").select(p, MatchingSettings())
    assert set(selected["team_a"] + selected["team_b"]) == {1, 2, 3, 4}
    selected = strategy("SKILL_BASED").select(p, MatchingSettings(), grace_elapsed=120)
    assert selected["imbalance"] == 0
    assert sum(selected["breakdown"].values()) == pytest.approx(selected["quality"])


def test_maximum_wait_not_starved_beyond_candidate_limit():
    p = pure_players(24)
    p[-1].update(waiting_minutes=30, skill_rating=8)
    for mode in ("FAIR_BALANCED", "SKILL_BASED", "RANDOM"):
        selected = strategy(mode).select(p, MatchingSettings(), grace_elapsed=0)
        assert 24 in selected["team_a"] + selected["team_b"]


@pytest.mark.parametrize("mode", ["MANUAL", "KING_OF_COURT"])
def test_manual_and_future_king_do_not_automatically_match(club, mode):
    courts(club, 5)
    solos(club, players(club, 20))
    configure(club, mode, automatic_assignment=True)
    assert club.rotation.generate(force=True) == []


def test_pair_break_and_whole_team_queue_operations(club):
    a, b = players(club, 2)
    g1 = club.rotation.checkin([a])
    g2 = club.rotation.checkin([b])
    club.rotation.group_action(g1["id"], "pair", g2["id"])
    club.queue_action(a, "priority")
    assert all(
        p["priority"] == 1 and p["entry_type"] == "TEAM"
        for p in club.dashboard()["queue"]
    )
    club.rotation.group_action(g1["id"], "break")
    assert all(p["entry_type"] == "SOLO" for p in club.dashboard()["queue"])


def test_migration_idempotent_preserves_state_and_settings(club):
    ids = players(club, 4)
    solos(club, ids)
    configure(club, "SKILL_BASED")
    before = club.rotation.configuration()
    club.db.migrate()
    club.db.migrate()
    assert club.rotation.configuration() == before
    assert len(club.dashboard()["queue"]) == 4
    with club.db.transaction() as s:
        assert s.get(SchemaMigration, 3)
        assert s.scalar(select(func.count()).select_from(QueueGroup)) == 4


def test_fewer_courts_disabled_not_deleted(club):
    courts(club, 5)
    club.rotation.configure_courts({"count": 3})
    assert [c["enabled"] for c in club.rotation.courts()] == [1, 1, 1, 0, 0]
    club.rotation.configure_courts({"count": 5})
    assert [c["id"] for c in club.rotation.courts()] == [1, 2, 3, 4, 5]


def test_recent_partners_prefer_alternative_partnership():
    p = pure_players()
    p[0]["recent_partners"] = [2]
    p[1]["recent_partners"] = [1]
    p[2]["recent_partners"] = [4]
    p[3]["recent_partners"] = [3]
    selected = strategy("FAIR_BALANCED").select(
        p, MatchingSettings(), grace_elapsed=120
    )
    assert set(selected["team_a"]) not in ({1, 2}, {3, 4})


def test_cancel_old_draft_does_not_release_other_assignment(club):
    courts(club, 1)
    ids = players(club, 4)
    solos(club, ids)
    draft = club.prepare_match({"court_id": 1, "players": {"A": ids[:2], "B": ids[2:]}})
    reserved = club.rotation.generate(force=True)[0]
    club.rotation.cancel(draft["id"])
    assert not club.dashboard()["queue"]
    assert club.rotation.courts()[0]["active_match_id"] == reserved["id"]


def test_scheduler_does_not_dispatch_operator_review_reservations(club):
    from pickleball_scoreboard.web.scheduler import tick

    courts(club, 1)
    solos(club, players(club, 4))
    match = club.rotation.generate(force=True)[0]

    class State:
        def start_match(self, *args):
            raise AssertionError("Must wait for operator")

    class App:
        extensions = {"rotation": club.rotation, "match_state": State()}

    tick(App())
    assert club.rotation.courts()[0]["active_match_id"] == match["id"]


def test_profile_edit_cannot_release_reserved_player(club):
    courts(club, 1)
    ids = players(club, 4)
    solos(club, ids)
    club.rotation.generate(force=True)
    club.save_player({"display_name": "Updated Name", "status": "ACTIVE"}, ids[0])
    with club.db.transaction() as s:
        assert s.get(Player, ids[0]).status == "ASSIGNED"
    with pytest.raises(ValueError):
        club.save_player({"display_name": "Updated Name", "status": "INACTIVE"}, ids[0])


def test_public_preview_shows_reserved_roster(club):
    courts(club, 1)
    ids = players(club, 4)
    solos(club, ids)
    match = club.rotation.generate(force=True)[0]
    data = club.courtside()
    assert data["confirmed_matches"][0]["id"] == match["id"]
    assert (
        data["courts"][0]["assignment"]["players_a"]
        == match["settings_json"]["players_a"]
    )
    assert data["courts"][0]["status"] == "RESERVED"


def test_pair_after_draft_preserved_on_completion(club):
    courts(club, 1)
    ids = players(club, 4)
    solos(club, ids)
    draft = club.prepare_match({"court_id": 1, "players": {"A": ids[:2], "B": ids[2:]}})
    queue = club.dashboard()["queue"]
    club.rotation.group_action(queue[0]["group_id"], "pair", queue[1]["group_id"])
    claimed = club.reserve_match(draft["id"])
    engine = ScoringEngine(Settings(**claimed["settings_json"]))
    engine.apply("DECLARE_WINNER", {"winner": "A", "scores": {"A": 11, "B": 2}})
    publish(club, claimed, engine)
    queue = club.dashboard()["queue"]
    team = [p for p in queue if p["id"] in ids[:2]]
    assert team[0]["group_id"] == team[1]["group_id"]
    assert all(p["entry_type"] == "TEAM" for p in team)


def test_winners_cap_returns_fixed_team_even_remove_policy(club):
    courts(club, 1)
    configure(club, "WINNERS_STAY", maximum_consecutive_wins=1)
    club.set_options({"after_match": "REMOVE_PLAYERS_FROM_QUEUE", "weights": {}})
    ids = players(club, 4)
    club.rotation.checkin(ids[:2])
    club.rotation.checkin(ids[2:])
    match = club.rotation.generate(force=True)[0]
    club.reserve_match(match["id"])
    engine = ScoringEngine(Settings(**match["settings_json"]))
    engine.apply("DECLARE_WINNER", {"winner": "A", "scores": {"A": 11, "B": 2}})
    publish(club, match, engine)
    queue = club.dashboard()["queue"]
    assert len(queue) == 2 and queue[0]["group_id"] == queue[1]["group_id"]
    assert all(p["entry_type"] == "TEAM" for p in queue)


def test_legacy_migration_adds_columns_and_preserves_records(tmp_path):
    import sqlite3
    from pathlib import Path
    from sqlalchemy.schema import CreateTable
    from sqlalchemy.dialects.sqlite import dialect
    from pickleball_scoreboard.web.models import Base

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    new_tables = {"queue_groups", "play_sessions", "scheduler_lock", "winner_holds"}
    new_columns = {
        "enabled",
        "available_since",
        "level",
        "group_id",
        "matchmaking_mode",
        "session_id",
        "matchmaking_json",
        "queue_groups_json",
    }
    for table in Base.metadata.sorted_tables:
        if table.name in new_tables:
            continue
        definition = str(CreateTable(table).compile(dialect=dialect()))
        lines = [
            line
            for line in definition.splitlines()
            if not any(
                line.strip().startswith(name + " ")
                or ("FOREIGN KEY(" + name + ")") in line
                for name in new_columns
            )
        ]
        definition = "\n".join(lines).replace(", \n)", "\n)")
        connection.execute(definition)
    connection.execute(
        "INSERT INTO courts (id,name,number,status,created_at,updated_at) VALUES (17,'Historic Court',7,'AVAILABLE',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "INSERT INTO players (id,first_name,last_name,display_name,nickname,gender,rating_type,games_played,wins,losses,status,created_at,updated_at) VALUES (9,'','','Saved Player','','','Manual',2,1,1,'WAITING',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "INSERT INTO player_checkins(id,player_id,checkin_time,status) VALUES(3,9,CURRENT_TIMESTAMP,'WAITING')"
    )
    connection.execute(
        "INSERT INTO player_queue(id,player_id,checkin_id,joined_at,position,priority,status) VALUES(5,9,3,CURRENT_TIMESTAMP,1,0,'WAITING')"
    )
    connection.commit()
    connection.close()
    db = Database("sqlite:///" + path.as_posix())
    db.migrate()
    db.migrate()
    with db.transaction() as s:
        assert s.get(Court, 17).number == 7
        assert s.get(Player, 9).wins == 1
        assert s.get(QueueEntry, 5).group_id
        assert s.get(SchemaMigration, 3)
    db.engine.dispose()

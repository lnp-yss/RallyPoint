"""Club-local tournaments using the existing authoritative match engine."""
from .auth_context import host_identity, effective_role

from copy import deepcopy
from itertools import combinations
from flask import jsonify, request, render_template, session
from sqlalchemy import select
from .models import Tournament, Player, Match, SystemSetting
from .club_service import row_dict
from ..config import Settings


def register_tournaments(app, db, club, operator):
    def require(s, tid):
        tournament = s.get(Tournament, tid)
        if tournament is None:
            raise ValueError("Tournament not found")
        return tournament

    def refresh(s, t):
        fixtures = deepcopy(t.fixtures_json)
        review = False
        for f in fixtures:
            if t.format == "SINGLE_ELIMINATION" and f.get("sources"):
                resolved = [
                    next(x for x in fixtures if x["id"] == parent).get("winner")
                    for parent in f["sources"]
                ]
                if f.get("match_id") and resolved != [f["a"], f["b"]]:
                    review = True
                elif not f.get("match_id"):
                    f["a"], f["b"] = resolved
            m = s.get(Match, f["match_id"]) if f.get("match_id") else None
            f["status"] = (
                m.status
                if m
                else (
                    "READY" if f["a"] is not None and f["b"] is not None else "WAITING"
                )
            )
            f["winner"] = (
                f[m.winner_team.lower()]
                if m and m.status == "COMPLETE" and m.winner_team
                else None
            )
        t.fixtures_json = fixtures
        if fixtures:
            t.status = (
                "NEEDS_REVIEW"
                if review
                else (
                    "COMPLETE"
                    if all(f["winner"] is not None for f in fixtures)
                    else "ACTIVE"
                )
            )

    @app.get("/tournaments")
    @operator
    def tournaments_page():
        return render_template(
            "page.html",
            page="tournaments",
            csrf=session["csrf"],
            role=effective_role(),
        )

    @app.route("/api/tournaments", methods=["GET", "POST"])
    @operator
    def tournaments():
        if request.method == "POST":
            data = request.get_json() or {}
            name = str(data.get("name", "")).strip()
            if not name or len(name) > 100:
                raise ValueError("Enter a tournament name up to 100 characters")
            settings = Settings(
                game_type=data.get("game_type", "Doubles"),
                points_to_win=int(data.get("points_to_win", 11)),
                best_of=int(data.get("best_of", 1)),
                scoring_mode=data.get("scoring_mode", "Side-Out"),
                win_by=int(data.get("win_by", 2)),
            ).validate()
            with db.assignment_transaction() as s:
                event = club.events.require_active(s)
                if s.get(SystemSetting, "event_lifecycle") and event.event_type != "TOURNAMENT":
                    raise ValueError("Start a Tournament event on Dashboard first")
                format = data.get("format", "ROUND_ROBIN")
                if format not in ("ROUND_ROBIN", "SINGLE_ELIMINATION"):
                    raise ValueError("Unknown tournament format")
                t = Tournament(
                    session_id=event.id,
                    name=name,
                    format=format,
                    settings_json={
                        "game_type": settings.game_type,
                        "points_to_win": settings.points_to_win,
                        "best_of": settings.best_of,
                        "scoring_mode": settings.scoring_mode,
                        "win_by": settings.win_by,
                    },
                )
                s.add(t)
                s.flush()
                return jsonify(row_dict(t)), 201
        with db.assignment_transaction() as s:
            result = []
            for t in s.scalars(select(Tournament).order_by(Tournament.id.desc())):
                refresh(s, t)
                result.append(row_dict(t))
            return jsonify(result)

    @app.post("/api/tournaments/<int:tid>/entries")
    @operator
    def entry(tid):
        data = request.get_json() or {}
        ids = [int(i) for i in data.get("players", [])]
        with db.assignment_transaction() as s:
            t = require(s, tid)
            event = club.events.require_active(s)
            if t.session_id and event.id != t.session_id:
                raise ValueError("This tournament belongs to an archived event")
            count = 1 if t.settings_json["game_type"] == "Singles" else 2
            if (
                t.status != "REGISTRATION"
                or len(ids) != count
                or len(set(ids)) != count
            ):
                raise ValueError(
                    "Select the correct number of players during registration"
                )
            if len(t.entries_json) >= 32:
                raise ValueError("Maximum 32 entries per round-robin event")
            existing = {i for e in t.entries_json for i in e["players"]}
            players = [s.get(Player, i) for i in ids]
            if existing.intersection(ids) or any(p is None for p in players):
                raise ValueError("Players must belong to this club and enter only once")
            club.events.require_attendance(s, ids)
            name = str(data.get("name", "")).strip() or " / ".join(
                p.display_name for p in players
            )
            if len(name) > 100:
                raise ValueError("Team name is too long")
            t.entries_json = t.entries_json + [{"name": name, "players": ids}]
            return jsonify(row_dict(t))

    @app.post("/api/tournaments/<int:tid>/generate")
    @operator
    def generate(tid):
        with db.assignment_transaction() as s:
            t = require(s, tid)
            event = club.events.require_active(s)
            if t.session_id and event.id != t.session_id:
                raise ValueError("This tournament belongs to an archived event")
            if t.status != "REGISTRATION" or len(t.entries_json) < 2:
                raise ValueError(
                    "Register at least two entries before generating fixtures"
                )
            if t.format == "SINGLE_ELIMINATION" and len(t.entries_json) & (
                len(t.entries_json) - 1
            ):
                raise ValueError(
                    "Single elimination currently requires 2, 4, 8, 16 or 32 entries; use round robin for other counts"
                )
            t.fixtures_json = [
                {
                    "id": n,
                    "a": a,
                    "b": b,
                    "match_id": None,
                    "winner": None,
                    "status": "READY",
                }
                for n, (a, b) in enumerate(
                    combinations(range(len(t.entries_json)), 2), 1
                )
            ]
            if t.format == "SINGLE_ELIMINATION":
                fixtures = []
                previous = []
                for a in range(0, len(t.entries_json), 2):
                    fixtures.append(
                        {
                            "id": len(fixtures) + 1,
                            "a": a,
                            "b": a + 1,
                            "match_id": None,
                            "winner": None,
                            "status": "READY",
                        }
                    )
                    previous.append(fixtures[-1]["id"])
                while len(previous) > 1:
                    following = []
                    for a in range(0, len(previous), 2):
                        fixtures.append(
                            {
                                "id": len(fixtures) + 1,
                                "a": None,
                                "b": None,
                                "sources": previous[a : a + 2],
                                "match_id": None,
                                "winner": None,
                                "status": "WAITING",
                            }
                        )
                        following.append(fixtures[-1]["id"])
                    previous = following
                t.fixtures_json = fixtures
            t.status = "ACTIVE"
            return jsonify(row_dict(t))

    @app.post("/api/tournaments/<int:tid>/fixtures/<int:fid>/assign")
    @operator
    def assign(tid, fid):
        data = request.get_json() or {}
        with db.assignment_transaction() as s:
            t = require(s, tid)
            event = club.events.require_active(s)
            if t.session_id and event.id != t.session_id:
                raise ValueError("This tournament belongs to an archived event")
            refresh(s, t)
            fixtures = deepcopy(t.fixtures_json)
            f = next((f for f in fixtures if f["id"] == fid), None)
            if (
                t.status == "NEEDS_REVIEW"
                or f is None
                or f["match_id"]
                or f["a"] is None
                or f["b"] is None
            ):
                raise ValueError("Fixture is missing or already assigned")
            a, b = t.entries_json[f["a"]], t.entries_json[f["b"]]
            match = club.prepare_match(
                {
                    "court_id": int(data["court_id"]),
                    "players": {"A": a["players"], "B": b["players"]},
                    "settings": {
                        **t.settings_json,
                        "team_a": a["name"],
                        "team_b": b["name"],
                    },
                },
                session=s,
            )
            f["match_id"] = match["id"]
            t.fixtures_json = fixtures
            return jsonify(match), 201

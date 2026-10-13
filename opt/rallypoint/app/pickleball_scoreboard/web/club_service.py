"""Transactional club operations. This module never imports Pygame."""

from contextlib import nullcontext

from dataclasses import asdict
from datetime import datetime, timedelta
from sqlalchemy import select, func, delete, update
from ..config import Settings
from .models import (
    Player,
    Court,
    Match,
    MatchPlayer,
    MatchEvent,
    TeamRecord,
    Game,
    QueueEntry,
    Checkin,
    SystemSetting,
    ScoreboardDevice,
    QueueGroup,
    WinnerHold,
    PlaySession,
    utcnow,
)
from .matchmaking import MatchmakingWeights, recommend


def row_dict(row):
    return {
        c.name: (
            getattr(row, c.name).isoformat() + "Z"
            if isinstance(getattr(row, c.name), datetime)
            else getattr(row, c.name)
        )
        for c in row.__table__.columns
    }


class ClubService:
    def __init__(self, database, club_name=None):
        self.db = database
        self.club_name = club_name
        from .rotation_service import RotationService

        self.rotation = RotationService(self)
        from .event_service import EventService
        self.events = EventService(self)

    def options(self, s):
        value = s.get(SystemSetting, "club")
        return (
            {"scoreboard_header": "RALLY POINT", **value.value_json}
            if value
            else {
                "scoreboard_header": "RALLY POINT",
                "after_match": "RETURN_PLAYERS_TO_QUEUE",
                "weights": asdict(MatchmakingWeights()),
            }
        )

    def display_branding(self):
        """Club-scoped display preferences delivered with device acknowledgements."""
        with self.db.transaction() as s:
            return {"scoreboard_header": self.options(s)["scoreboard_header"],
                    "club_name": self.club_name or "RALLY POINT PICKLEBALL CLUB"}

    def set_options(self, values):
        if values.get("after_match") not in (
            "RETURN_PLAYERS_TO_QUEUE",
            "REMOVE_PLAYERS_FROM_QUEUE",
        ):
            raise ValueError("Choose a valid after-match behavior")
        with self.db.transaction() as s:
            header = values.get("scoreboard_header", self.options(s)["scoreboard_header"])
        if not isinstance(header, str) or not header.strip() or len(header) > 100:
            raise ValueError("HDMI header is required, at most 100 characters")
        weights = MatchmakingWeights(**values.get("weights", {}))
        if (
            not 4 <= weights.candidate_limit <= 16
            or weights.max_wait_minutes <= 0
            or any(v < 0 for v in asdict(weights).values())
        ):
            raise ValueError("Invalid matchmaking weights")
        with self.db.transaction() as s:
            s.merge(
                SystemSetting(
                    key="club",
                    value_json={
                        "scoreboard_header": header.strip(),
                        "after_match": values["after_match"],
                        "weights": asdict(weights),
                    },
                )
            )

    def save_player(self, values, player_id=None):
        fields = {
            "first_name",
            "last_name",
            "display_name",
            "nickname",
            "gender",
            "skill_rating",
            "rating_type",
            "status",
        }
        if not str(values.get("display_name", "")).strip():
            raise ValueError("Display name is required")
        if len(values["display_name"]) > 100:
            raise ValueError("Name is too long")
        rating = values.get("skill_rating")
        values = dict(values)
        values["skill_rating"] = None if rating in ("", None) else float(rating)
        if values["skill_rating"] is not None and not 1 <= values["skill_rating"] <= 8:
            raise ValueError("Rating must be between 1 and 8")
        if values.get("status", "ACTIVE") not in ("ACTIVE", "INACTIVE"):
            raise ValueError(
                "Use ACTIVE or INACTIVE; check-in and play statuses are automatic"
            )
        with self.db.assignment_transaction() as s:
            player = s.get(Player, player_id) if player_id else Player()
            if player is None:
                raise ValueError("Player not found")
            if player_id and player.status in (
                "WAITING",
                "PLAYING",
                "ASSIGNED",
                "HOLDING",
            ):
                if values.get("status") == "INACTIVE":
                    raise ValueError("Check out player before deactivation")
                values.pop("status", None)
            for key in fields & values.keys():
                setattr(player, key, values[key])
            s.add(player)
            s.flush()
            return row_dict(player)

    def quick_checkin_player(self, values):
        """Create and queue a new player in one serialized transaction."""
        first = str(values.get("first_name", "")).strip()
        last = str(values.get("last_name", "")).strip()
        if not first or len(first) > 80 or len(last) > 80:
            raise ValueError("First name is required; names must be 80 characters or fewer")
        display = str(values.get("display_name", "")).strip() or first
        if len(display) > 100:
            raise ValueError("Display name must be 100 characters or fewer")
        rating = values.get("skill_rating")
        rating = None if rating in (None, "") else float(rating)
        if rating is not None and not 1 <= rating <= 8:
            raise ValueError("Rating must be between 1 and 8")
        rating_type = values.get("rating_type", "Manual")
        if rating_type not in ("Manual", "DUPR", "Self-rated"):
            raise ValueError("Choose Manual, DUPR or Self-rated")
        with self.db.assignment_transaction() as s:
            existing = s.scalar(select(Player.id).where(
                func.lower(func.trim(Player.display_name)) == display.lower()))
            if existing:
                raise ValueError("Display name already exists. Check in the existing player or use a distinct display name.")
            player = Player(first_name=first, last_name=last, display_name=display,
                            skill_rating=rating, rating_type=rating_type)
            s.add(player)
            s.flush()
            entry = self._checkin_player(s, player)
            return {"player": row_dict(player), "queue_entry": entry}

    def checkin(self, player_id):
        with self.db.assignment_transaction() as s:
            player = s.get(Player, int(player_id), with_for_update=True)
            if not player or player.status not in ("ACTIVE", "WAITING"):
                raise ValueError("Player is inactive or already playing")
            return self._checkin_player(s, player)

    def _checkin_player(self, s, player):
        event = self.events.require_active(s)
        existing = s.scalar(
            select(QueueEntry).where(QueueEntry.player_id == player.id)
        )
        if existing:
            return row_dict(existing)
        checkin = Checkin(player_id=player.id, session_id=event.id)
        group = QueueGroup(entry_type="SOLO")
        s.add(group)
        s.add(checkin)
        s.flush()
        position = (s.scalar(select(func.max(QueueEntry.position))) or 0) + 1
        entry = QueueEntry(
            player_id=player.id,
            checkin_id=checkin.id,
            position=position,
            group_id=group.id,
        )
        s.add(entry)
        player.status = "WAITING"
        s.flush()
        return row_dict(entry)

    def queue_action(self, player_id, action):
        with self.db.transaction() as s:
            grouped = s.scalar(
                select(QueueEntry).where(QueueEntry.player_id == int(player_id))
            )
            group_id = grouped.group_id if grouped else None
        if group_id:
            return self.rotation.group_action(group_id, action, None)
        with self.db.transaction() as s:
            entries = list(
                s.scalars(
                    select(QueueEntry)
                    .where(QueueEntry.status == "WAITING")
                    .order_by(
                        QueueEntry.priority.desc(), QueueEntry.position, QueueEntry.id
                    )
                    .with_for_update()
                )
            )
            entry = next((p for p in entries if p.player_id == int(player_id)), None)
            if not entry:
                raise ValueError("Player is not waiting")
            index = entries.index(entry)
            if action == "remove":
                checkin = s.get(Checkin, entry.checkin_id)
                checkin.status, checkin.checkout_time = "CHECKED_OUT", utcnow()
                s.get(Player, entry.player_id).status = "ACTIVE"
                s.delete(entry)
            elif action == "priority":
                entry.priority = 0 if entry.priority else 1
            elif action in ("up", "down"):
                other = index + (-1 if action == "up" else 1)
                if 0 <= other < len(entries):
                    entries[index], entries[other] = entries[other], entries[index]
                    for position, item in enumerate(entries):
                        item.position = position + 1
            else:
                raise ValueError("Unknown queue action")

    def leaderboard(self, event_id=None, all_time=False):
        """Rank registered players from authoritative completed match snapshots."""
        with self.db.transaction() as s:
            event, _ = self.rotation.session(s)
            selected = event_id if event_id is not None else (event.id if event else None)
            managed = s.get(SystemSetting, "event_lifecycle")
            if event_id is not None and not s.get(PlaySession, event_id):
                raise ValueError("Event not found")
            rows = s.execute(
                select(Player, MatchPlayer, Match)
                .join(MatchPlayer, MatchPlayer.player_id == Player.id)
                .join(Match, Match.id == MatchPlayer.match_id)
                .where(Match.status == "COMPLETE", Match.winner_team.in_(("A", "B")))
            ).all()
            players = {}
            for player, link, match in rows:
                if not all_time and (selected is not None or managed) and match.session_id != selected:
                    continue
                if not all_time and managed and selected is None:
                    continue
                item = players.setdefault(
                    player.id,
                    {
                        "player_id": player.id,
                        "display_name": player.display_name,
                        "matches": 0,
                        "wins": 0,
                        "losses": 0,
                        "points_for": 0,
                        "points_against": 0,
                    },
                )
                item["matches"] += 1
                item["wins" if match.winner_team == link.team else "losses"] += 1
                other = "B" if link.team == "A" else "A"
                games = (
                    (match.state_json or {}).get("state", {}).get("completed_games", [])
                )
                item["points_for"] += sum(g[link.team] for g in games)
                item["points_against"] += sum(g[other] for g in games)

        def rank_key(item):
            return (
                item["wins"],
                item["wins"] / item["matches"],
                item["point_difference"],
            )

        for item in players.values():
            item["win_percentage"] = round(100 * item["wins"] / item["matches"], 1)
            item["point_difference"] = item["points_for"] - item["points_against"]
        ranking = sorted(
            players.values(),
            key=lambda p: (
                *(-v for v in rank_key(p)),
                p["display_name"].casefold(),
                p["player_id"],
            ),
        )
        previous = None
        rank = 0
        for position, item in enumerate(ranking, 1):
            key = rank_key(item)
            if key != previous:
                rank = position
            item["rank"] = rank
            previous = key
        return {
            "players": ranking,
            "updated_at": utcnow().isoformat() + "Z",
            "scope": "All-time Â· All courts" if all_time or (not event and not managed and event_id is None) else "Event results Â· All courts",
            "event_id": None if all_time else selected,
            "rules": "Match wins, then win percentage, then point difference. Equal results share a rank.",
        }

    def statistics(self, s, player_id, event_only=False):
        links = list(
            s.scalars(select(MatchPlayer).where(MatchPlayer.player_id == player_id))
        )
        stats = {
            "matches": 0,
            "wins": 0,
            "losses": 0,
            "games_won": 0,
            "games_lost": 0,
            "points_for": 0,
            "points_against": 0,
            "recent_results": [],
            "partners": {},
            "opponents": {},
            "games_today": 0,
            "recent_partners": [],
            "recent_opponents": [],
        }
        today = utcnow().date()
        event, _ = self.rotation.session(s)
        managed = s.get(SystemSetting, "event_lifecycle")
        for link in sorted(links, key=lambda item: item.match_id, reverse=True):
            match = s.get(Match, link.match_id)
            if event_only and ((event and match.session_id != event.id) or (managed and not event)):
                continue
            if match.status != "COMPLETE" or not match.state_json:
                continue
            state = match.state_json["state"]
            other = "B" if link.team == "A" else "A"
            stats["matches"] += 1
            won = match.winner_team == link.team
            stats["wins" if won else "losses"] += 1
            stats["games_won"] += state["games_won"][link.team]
            stats["games_lost"] += state["games_won"][other]
            scores = state["completed_games"]
            stats["points_for"] += sum(g[link.team] for g in scores)
            stats["points_against"] += sum(g[other] for g in scores)
            if event_only or (match.ended_at and match.ended_at.date() == today):
                stats["games_today"] += len(scores)
            if len(stats["recent_results"]) < 10:
                stats["recent_results"].append(
                    {"match_id": match.id, "won": won, "scores": scores}
                )
            peers = list(
                s.scalars(
                    select(MatchPlayer).where(
                        MatchPlayer.match_id == match.id,
                        MatchPlayer.player_id != player_id,
                    )
                )
            )
            for peer in peers:
                kind = "partners" if peer.team == link.team else "opponents"
                key = str(peer.player_id)
                item = stats[kind].setdefault(
                    key,
                    {
                        "name": s.get(Player, peer.player_id).display_name,
                        "matches": 0,
                        "games": 0,
                        "wins": 0,
                        "losses": 0,
                    },
                )
                item["matches"] += 1
                item["games"] += len(scores)
                item["wins" if won else "losses"] += 1
                if len(stats["recent_results"]) <= 3:
                    stats["recent_" + kind].append(peer.player_id)
        stats["win_percentage"] = (
            round(100 * stats["wins"] / stats["matches"], 1) if stats["matches"] else 0
        )
        return stats

    def waiting(self, s):
        result = []
        for entry in s.scalars(
            select(QueueEntry)
            .where(QueueEntry.status == "WAITING")
            .order_by(QueueEntry.priority.desc(), QueueEntry.position, QueueEntry.id)
        ):
            player = s.get(Player, entry.player_id)
            stats = self.statistics(s, player.id, event_only=True)
            result.append(
                {
                    **row_dict(player),
                    "position": entry.position,
                    "priority": entry.priority,
                    "group_id": entry.group_id,
                    "entry_type": (
                        s.get(QueueGroup, entry.group_id).entry_type
                        if entry.group_id
                        else "SOLO"
                    ),
                    "temporary": (
                        bool(s.get(QueueGroup, entry.group_id).temporary)
                        if entry.group_id
                        else False
                    ),
                    "waiting_minutes": max(
                        0, (utcnow() - entry.joined_at).total_seconds() / 60
                    ),
                    "games_today": stats["games_today"],
                    "matches_completed": stats["matches"],
                    "recent_partners": stats["recent_partners"],
                    "recent_opponents": stats["recent_opponents"],
                    "last_partner": next(iter(stats["partners"].values()), {}).get(
                        "name", "â€”"
                    ),
                    "last_opponent": next(iter(stats["opponents"].values()), {}).get(
                        "name", "â€”"
                    ),
                }
            )
        return result

    def courtside(self):
        """Public presentation data without operator-only player/record fields."""
        dashboard = self.dashboard()
        waiting_count = len(dashboard["queue"])
        prepared = [
            m["id"]
            for m in dashboard["matches"]
            if m["status"] in ("PREPARED", "RESERVED", "STARTING")
        ]
        if prepared:
            with self.db.transaction() as s:
                reserved_players = set(
                    s.scalars(
                        select(MatchPlayer.player_id).where(
                            MatchPlayer.match_id.in_(prepared)
                        )
                    )
                )
            dashboard["queue"] = [
                p for p in dashboard["queue"] if p["id"] not in reserved_players
            ]
            available = [c for c in dashboard["courts"] if c["status"] == "AVAILABLE"]
            remaining = dashboard["queue"]
            dashboard["recommendations"] = []
            for index in range(2):
                candidate = self.rotation.recommendation(remaining)
                if not candidate:
                    break
                candidate["court_id"] = available[index]["id"] if index < len(available) else None
                dashboard["recommendations"].append(candidate)
                used = set(candidate["team_a"] + candidate["team_b"])
                remaining = [p for p in remaining if p["id"] not in used]
        players = {p["id"]: p["display_name"] for p in dashboard["queue"]}
        recommendations = []
        for suggestion in dashboard["recommendations"]:
            court = next((c for c in dashboard["courts"] if c["id"] == suggestion.get("court_id")), None)
            recommendations.append({
                "court_number": court["number"] if court else None,
                "court_name": court["name"] if court else None,
                "team_a": [players[pid] for pid in suggestion["team_a"]],
                "team_b": [players[pid] for pid in suggestion["team_b"]],
            })
        upcoming = recommendations[0] if recommendations else None
        return {
            "courts": [
                {
                    "id": c["id"],
                    "number": c["number"],
                    "name": c["name"],
                    "status": c["status"],
                    "active_match_id": c["active_match_id"],
                    "assignment": (
                        {
                            "id": c["match"]["id"],
                            "settings": c["match"]["settings_json"],
                            "players_a": c["match"]["settings_json"]["players_a"],
                            "players_b": c["match"]["settings_json"]["players_b"],
                        }
                        if c.get("match")
                        and c["match"]["status"] in ("RESERVED", "STARTING")
                        else None
                    ),
                }
                for c in dashboard["courts"]
            ],
            "event": dashboard["event"],
            "recommended_match": upcoming,
            "recommendations": recommendations,
            "on_deck": recommendations[1] if len(recommendations) > 1 else None,
            "confirmed_matches": [
                {
                    "id": m["id"],
                    "court_id": m["court_id"],
                    "court_number": next(
                        (
                            c["number"]
                            for c in dashboard["courts"]
                            if c["id"] == m["court_id"]
                        ),
                        m["court_id"],
                    ),
                    "team_a": m["settings_json"]["players_a"],
                    "team_b": m["settings_json"]["players_b"],
                    "status": m["status"],
                }
                for m in dashboard["matches"]
                if m["status"] in ("PREPARED", "RESERVED", "STARTING")
            ],
            "waiting_players": waiting_count,
            "leaderboard": self.leaderboard(),
            "updated_at": utcnow().isoformat() + "Z",
        }

    def on_deck(self, dashboard):
        first = dashboard["suggestion"]
        if not first:
            return None
        used = set(first["team_a"] + first["team_b"])
        queue = [p for p in dashboard["queue"] if p["id"] not in used]
        second = self.rotation.recommendation(queue)
        if not second:
            return None
        names = {p["id"]: p["display_name"] for p in queue}
        return {
            "team_a": [names[i] for i in second["team_a"]],
            "team_b": [names[i] for i in second["team_b"]],
        }

    def options_from_database(self):
        with self.db.transaction() as s:
            return self.options(s)

    def dashboard(self, singles=False, excluded=None):
        with self.db.transaction() as s:
            event, _ = self.rotation.session(s)
            queue = self.waiting(s)
            courts = [
                self.rotation.court_state(s, c, self.rotation.session(s)[1])
                for c in s.scalars(select(Court).order_by(Court.number))
            ]
            matches = [
                row_dict(m)
                for m in s.scalars(select(Match).order_by(Match.id.desc()).limit(30))
            ]
            present = {m["id"] for m in matches}
            for match in s.scalars(
                select(Match).where(
                    Match.status.in_(("PREPARED", "RESERVED", "STARTING", "ACTIVE"))
                )
            ):
                if match.id not in present:
                    matches.append(row_dict(match))
            if event:
                matches = [m for m in matches if m["session_id"] == event.id]
            elif s.get(SystemSetting, "event_lifecycle"):
                matches = []
            matches.sort(key=lambda m: m["id"], reverse=True)
            for court in courts:
                active = (
                    s.get(Match, court["active_match_id"])
                    if court["active_match_id"]
                    else None
                )
                court["match"] = row_dict(active) if active else None
                from .models import DeviceSnapshot

                device = s.scalar(
                    select(ScoreboardDevice).where(
                        ScoreboardDevice.court_id == court["id"]
                    )
                )
                cached = s.get(DeviceSnapshot, device.id) if device else None
                if (
                    cached
                    and not cached.snapshot_json.get("match_id")
                    and cached.snapshot_json.get("match_exists")
                    and cached.snapshot_json.get("state", {}).get("status")
                    in ("playing", "game_over")
                    and court["status"] == "AVAILABLE"
                ):
                    court["status"] = "CASUAL_IN_USE"
            available = [c for c in courts if c["status"] == "AVAILABLE"]
            recommendations = []
            remaining = queue
            for index in range(2):
                candidate = self.rotation.recommendation(remaining, singles, excluded)
                if not candidate:
                    break
                candidate["court_id"] = available[index]["id"] if index < len(available) else None
                recommendations.append(candidate)
                used = set(candidate["team_a"] + candidate["team_b"])
                remaining = [p for p in remaining if p["id"] not in used]
            suggestion = recommendations[0] if recommendations else None
            return {
                "event": row_dict(event) if event else None,
                "courts": courts,
                "queue": queue,
                "matches": matches,
                "suggestion": suggestion,
                "recommendations": recommendations,
                "metrics": {
                    "active_courts": sum(
                        c["status"] in ("PLAYING", "IN_USE", "CASUAL_IN_USE")
                        for c in courts
                    ),
                    "waiting_players": len(queue),
                    "matches_today": s.scalar(select(func.count()).select_from(Match).where(Match.session_id == event.id, Match.started_at.is_not(None))) if event else (0 if s.get(SystemSetting, "event_lifecycle") else s.scalar(
                        select(func.count())
                        .select_from(Match)
                        .where(
                            Match.started_at
                            >= datetime.combine(utcnow().date(), datetime.min.time())
                        )
                    )),
                    "games_completed": s.scalar(
                        select(func.count())
                        .select_from(Game)
                        .join(Match, Match.id == Game.match_id)
                        .where(Game.ended_at.is_not(None), Match.session_id == event.id if event else (False if s.get(SystemSetting, "event_lifecycle") else True))
                    ),
                    "checked_in": s.scalar(
                        select(func.count())
                        .select_from(Checkin)
                        .where(Checkin.checkout_time.is_(None))
                    ),
                },
            }

    def prepare_match(self, payload, session=None):
        allowed = {
            "team_a",
            "team_b",
            "game_type",
            "scoring_mode",
            "points_to_win",
            "win_by",
            "best_of",
            "starting_team",
            "timeout_limit",
        }
        settings = Settings(
            **{
                key: value
                for key, value in payload.get("settings", payload).items()
                if key in allowed
            }
        ).resolve_first_serve()
        if self.club_name:
            settings.club_name = self.club_name
        side = payload.get("starting_side", "Right")
        if side not in ("Left", "Right"):
            raise ValueError("Invalid starting side")
        ids = payload.get("players", {"A": [], "B": []})
        if isinstance(ids, dict) and set(ids) == {"A", "B"}:
            ids = {team: [int(pid) for pid in ids[team]] for team in ("A", "B")}
        expected = 1 if settings.game_type == "Singles" else 2
        if set(ids) != {"A", "B"} or any(len(ids[t]) != expected for t in ids):
            raise ValueError(f"Select {expected} registered player(s) for each team")
        if len(set(ids["A"] + ids["B"])) != expected * 2:
            raise ValueError("A player cannot be on both teams")
        with (
            self.db.assignment_transaction()
            if session is None
            else nullcontext(session)
        ) as s:
            court = s.get(Court, int(payload["court_id"]))
            if not court or not court.enabled or court.status != "AVAILABLE":
                raise ValueError("Court is not available")
            if s.scalar(
                select(ScoreboardDevice.id).where(ScoreboardDevice.court_id == court.id)
            ):
                self.rotation.require_available(s, court, self.rotation.session(s)[1])
            if self.rotation.has_hold(s, court.id):
                raise ValueError(
                    "Court has retained winners; use its challenger assignment"
                )
            self.events.require_attendance(s, ids["A"] + ids["B"])
            names = {}
            for team in ("A", "B"):
                players = [s.get(Player, int(pid)) for pid in ids[team]]
                if any(not p or p.status not in ("ACTIVE", "WAITING") for p in players):
                    raise ValueError("Selected player is unavailable")
                for p in players:
                    entry = s.scalar(
                        select(QueueEntry).where(QueueEntry.player_id == p.id)
                    )
                    if entry and entry.group_id:
                        group = s.get(QueueGroup, entry.group_id)
                        members = set(
                            s.scalars(
                                select(QueueEntry.player_id).where(
                                    QueueEntry.group_id == group.id
                                )
                            )
                        )
                        if group.entry_type == "TEAM" and not members.issubset(
                            set(ids[team])
                        ):
                            raise ValueError("A fixed team cannot be split")
                names[team] = " / ".join(p.display_name for p in players)
            settings.play_mode = "Club Managed"
            settings.players_a, settings.players_b = names["A"], names["B"]
            settings.validate()
            play_session = self.events.require_active(s)
            _, matching = self.rotation.session(s)
            groups = []
            for pid in ids["A"] + ids["B"]:
                entry = s.scalar(select(QueueEntry).where(QueueEntry.player_id == pid))
                if (
                    entry
                    and entry.group_id
                    and not any(g["id"] == entry.group_id for g in groups)
                ):
                    group = s.get(QueueGroup, entry.group_id)
                    groups.append(
                        {
                            "id": group.id,
                            "entry_type": group.entry_type,
                            "temporary": group.temporary,
                            "players": list(
                                s.scalars(
                                    select(QueueEntry.player_id).where(
                                        QueueEntry.group_id == group.id
                                    )
                                )
                            ),
                        }
                    )
            match = Match(
                matchmaking_mode=matching.mode,
                session_id=play_session.id,
                matchmaking_json={
                    "settings": asdict(matching),
                    "explanation": {"reasons": ["Operator selection"]},
                },
                queue_groups_json=groups,
                court_id=court.id,
                match_type=settings.game_type,
                scoring_type=settings.scoring_mode,
                points_to_win=settings.points_to_win,
                win_by=settings.win_by,
                best_of=settings.best_of,
                settings_json=settings.to_dict(),
                notes=str(payload.get("notes", ""))[:2000],
                starting_side=side,
            )
            s.add(match)
            s.flush()
            for team in ("A", "B"):
                s.add(
                    TeamRecord(
                        match_id=match.id,
                        side=team,
                        name=getattr(settings, "team_" + team.lower()),
                    )
                )
                for position, pid in enumerate(ids[team], 1):
                    s.add(
                        MatchPlayer(
                            match_id=match.id,
                            player_id=int(pid),
                            team=team,
                            position=position,
                        )
                    )
            from uuid import uuid4

            s.add(
                MatchEvent(
                    event_id=str(uuid4()),
                    match_id=match.id,
                    sequence_no=1,
                    event_type="MATCH_CREATED",
                    payload_json=payload,
                )
            )
            return row_dict(match)

    def reserve_match(self, match_id):
        with self.db.assignment_transaction() as s:
            match = s.get(Match, match_id, with_for_update=True)
            if match and match.status == "RESERVED":
                court = s.get(Court, match.court_id)
                self.rotation.require_available(
                    s, court, self.rotation.session(s)[1], True
                )
                if court.active_match_id != match.id:
                    raise ValueError("Court reservation changed")
                match.status = "STARTING"
                for link in s.scalars(
                    select(MatchPlayer).where(MatchPlayer.match_id == match.id)
                ):
                    player = s.get(Player, link.player_id)
                    if player.status != "ASSIGNED":
                        raise ValueError("Player reservation changed")
                    player.status = "PLAYING"
                return row_dict(match)
            if not match or match.status != "PREPARED":
                raise ValueError("Match is not prepared")
            court = s.get(Court, match.court_id)
            if not court.enabled:
                raise ValueError("Court is disabled")
            if s.scalar(
                select(ScoreboardDevice.id).where(ScoreboardDevice.court_id == court.id)
            ):
                self.rotation.require_available(s, court, self.rotation.session(s)[1])
            claimed = s.execute(
                update(Court)
                .where(
                    Court.id == match.court_id,
                    Court.status == "AVAILABLE",
                    Court.active_match_id.is_(None),
                )
                .values(status="RESERVED", active_match_id=match.id)
            )
            if claimed.rowcount != 1:
                raise ValueError("Court was assigned by another operator")
            if self.rotation.has_hold(s, court.id):
                raise ValueError(
                    "Court has retained winners; use its challenger assignment"
                )
            current_groups = []
            for link in s.scalars(
                select(MatchPlayer).where(MatchPlayer.match_id == match.id)
            ):
                player = s.get(Player, link.player_id, with_for_update=True)
                if player.status not in ("ACTIVE", "WAITING"):
                    raise ValueError("Player is no longer available")
                player.status = "PLAYING"
                entry = s.scalar(
                    select(QueueEntry).where(QueueEntry.player_id == player.id)
                )
                if entry:
                    group = (
                        s.get(QueueGroup, entry.group_id) if entry.group_id else None
                    )
                    if group and not any(g["id"] == group.id for g in current_groups):
                        current_groups.append(
                            {
                                "id": group.id,
                                "entry_type": group.entry_type,
                                "temporary": group.temporary,
                                "players": list(
                                    s.scalars(
                                        select(QueueEntry.player_id).where(
                                            QueueEntry.group_id == group.id
                                        )
                                    )
                                ),
                            }
                        )
                    if group and group.entry_type == "TEAM":
                        members = set(
                            s.scalars(
                                select(QueueEntry.player_id).where(
                                    QueueEntry.group_id == group.id
                                )
                            )
                        )
                        side = set(
                            s.scalars(
                                select(MatchPlayer.player_id).where(
                                    MatchPlayer.match_id == match.id,
                                    MatchPlayer.team == link.team,
                                )
                            )
                        )
                        if not members.issubset(side):
                            raise ValueError("A fixed team cannot be split")
                    entry.status = "ASSIGNED"
                    s.get(Checkin, entry.checkin_id).status = "ASSIGNED"
            match.queue_groups_json = current_groups
            match.status = "STARTING"
            return row_dict(match)

    def cancel_reservation(self, match_id):
        with self.db.assignment_transaction() as s:
            match = s.get(Match, match_id, with_for_update=True)
            if not match or match.status not in ("STARTING", "RESERVED"):
                return
            match.status = "PREPARED"
            court = s.get(Court, match.court_id)
            court.status, court.active_match_id = "AVAILABLE", None
            for link in s.scalars(
                select(MatchPlayer).where(MatchPlayer.match_id == match.id)
            ):
                entry = s.scalar(
                    select(QueueEntry).where(QueueEntry.player_id == link.player_id)
                )
                s.get(Player, link.player_id).status = "WAITING" if entry else "ACTIVE"
                if entry:
                    entry.status = "WAITING"
                    s.get(Checkin, entry.checkin_id).status = "WAITING"

    def publish_state(self, event):
        """Idempotent snapshot projection; Undo also recomputes derived results."""
        with self.db.assignment_transaction() as s:
            from .models import DeviceSnapshot

            device_id = event.get("device_id")
            if device_id:
                device = s.get(ScoreboardDevice, int(device_id))
                identity = event["snapshot"].get("identity", {})
                if not device or (
                    identity.get("court_id")
                    and int(identity["court_id"]) != device.court_id
                ):
                    raise ValueError(
                        "Device identity does not match its registered court"
                    )
                if (
                    identity.get("device_id")
                    and int(identity["device_id"]) != device.id
                ):
                    raise ValueError("Device identity does not match the heartbeat")
                if (
                    device.hostname
                    and identity.get("hostname")
                    and device.hostname != identity["hostname"]
                ):
                    raise ValueError(
                        "Hostname differs from registered device; verify clone configuration"
                    )
                cached = s.get(DeviceSnapshot, int(device_id), with_for_update=True)
                if not cached:
                    cached = DeviceSnapshot(
                        device_id=int(device_id), snapshot_json=event["snapshot"]
                    )
                    s.add(cached)
                elif event["snapshot"].get("updated_at", 0) >= cached.snapshot_json.get(
                    "updated_at", 0
                ):
                    cached.snapshot_json = event["snapshot"]
                cached.received_at = utcnow()
                device = s.get(ScoreboardDevice, int(device_id))
                if device:
                    device.last_seen_at, device.status = utcnow(), "ONLINE"
            if not event.get("match_id"):
                return
            if event.get("event_type") == "HEARTBEAT":
                return
            if s.scalar(
                select(MatchEvent.id).where(MatchEvent.event_id == event["event_id"])
            ):
                return
            match = s.get(Match, int(event["match_id"]), with_for_update=True)
            if not match:
                raise ValueError("Match record missing")
            if device_id and device.court_id != match.court_id:
                raise ValueError(
                    "Device cannot report a match assigned to another court"
                )
            play_event = s.get(PlaySession, match.session_id) if match.session_id else None
            if (play_event and play_event.ended_at) or self.events.historical_match(s, match):
                # Late HDMI retries must not reopen attendance or alter archived results.
                return
            snapshot = event["snapshot"]
            state = snapshot["state"]
            from datetime import timezone

            occurred_at = datetime.fromtimestamp(
                event.get(
                    "occurred_at", utcnow().replace(tzinfo=timezone.utc).timestamp()
                ),
                timezone.utc,
            ).replace(tzinfo=None)
            complete = state["status"] in ("complete", "ended")
            was_complete = match.status in ("COMPLETE", "ENDED")
            if was_complete and not complete:
                player_ids = select(MatchPlayer.player_id).where(
                    MatchPlayer.match_id == match.id
                )
                other_assignment = s.scalar(
                    select(MatchPlayer.id)
                    .join(Match, Match.id == MatchPlayer.match_id)
                    .where(
                        MatchPlayer.player_id.in_(player_ids),
                        Match.id != match.id,
                        Match.status.in_(("ACTIVE", "STARTING", "RESERVED")),
                    )
                    .limit(1)
                )
                if other_assignment:
                    raise ValueError(
                        "Players have been reassigned; operator reconciliation required before Undo projection"
                    )
            if event.get("event_type") == "FIRST_SERVE_CHANGED" and snapshot.get("ready_to_play"):
                selected = snapshot.get("settings", {}).get("first_serving_team")
                if selected in ("A", "B"):
                    settings = dict(match.settings_json)
                    settings.update(starting_team=selected, first_serving_team=selected)
                    match.settings_json = settings
            match.state_json = snapshot
            match.status = (
                "COMPLETE"
                if state["status"] == "complete"
                else "ENDED" if state["status"] == "ended"
                else "STARTING" if snapshot.get("ready_to_play") else "ACTIVE"
            )
            if not snapshot.get("ready_to_play") and not snapshot.get("cancelled_before_start"):
                match.started_at = match.started_at or (
                    datetime.fromtimestamp(snapshot["started_at"], timezone.utc).replace(tzinfo=None)
                    if snapshot.get("started_at") else occurred_at
                )
            match.ended_at = (
                occurred_at
                if complete and not was_complete
                else match.ended_at if complete else None
            )
            match.winner_team = (
                state["winner"] if state["status"] == "complete" else None
            )
            court = s.get(Court, match.court_id, with_for_update=True)
            if court.active_match_id not in (None, match.id):
                raise ValueError(
                    "Court has a newer active match; operator reconciliation required"
                )
            court.status, court.active_match_id = (
                ("AVAILABLE" if court.enabled else "DISABLED", None)
                if complete
                else ("IN_USE", match.id)
            )
            if complete and not was_complete:
                court.available_since = utcnow()
            if was_complete and not complete:
                hold = s.get(WinnerHold, court.id)
                if hold and hold.active and hold.match_id == match.id:
                    hold.active = 0
            waiting = snapshot.get("ready_to_play") or snapshot.get("cancelled_before_start")
            scores = [] if waiting else list(state["completed_games"])
            if not waiting and len(scores) < state["game_number"]:
                scores.append(state["game"]["scores"])
            for number, score in enumerate(scores, 1):
                game = s.scalar(
                    select(Game).where(
                        Game.match_id == match.id, Game.game_number == number
                    )
                )
                if not game:
                    game = Game(
                        match_id=match.id, game_number=number, started_at=utcnow()
                    )
                    s.add(game)
                game.team_a_score, game.team_b_score = score["A"], score["B"]
                game.winner_team = (
                    ("A" if score["A"] > score["B"] else "B")
                    if number <= len(state["completed_games"])
                    else None
                )
                if number == len(state["completed_games"]) and state.get(
                    "manual_game_winner"
                ):
                    game.winner_team = state["manual_game_winner"]
                game.ended_at = (
                    (game.ended_at or utcnow()) if game.winner_team else None
                )
            s.execute(
                delete(Game).where(
                    Game.match_id == match.id, Game.game_number > len(scores)
                )
            )
            sequence = (
                s.scalar(
                    select(func.max(MatchEvent.sequence_no)).where(
                        MatchEvent.match_id == match.id
                    )
                )
                or 0
            ) + 1
            s.add(
                MatchEvent(
                    event_id=event["event_id"],
                    match_id=match.id,
                    sequence_no=sequence,
                    event_type=event["event_type"],
                    payload_json=event,
                    team=event.get("team"),
                    created_at=occurred_at,
                )
            )
            for link in s.scalars(
                select(MatchPlayer).where(MatchPlayer.match_id == match.id)
            ):
                player = s.get(Player, link.player_id)
                entry = s.scalar(
                    select(QueueEntry).where(QueueEntry.player_id == player.id)
                )
                if complete and not was_complete:
                    if self.options(s)["after_match"] == "RETURN_PLAYERS_TO_QUEUE":
                        if not entry:
                            checkin = Checkin(player_id=player.id, session_id=match.session_id)
                            s.add(checkin)
                            s.flush()
                            entry = QueueEntry(
                                player_id=player.id,
                                checkin_id=checkin.id,
                                position=(
                                    s.scalar(select(func.max(QueueEntry.position))) or 0
                                )
                                + 1,
                            )
                            s.add(entry)
                        entry.status, entry.joined_at = "WAITING", utcnow()
                        s.get(Checkin, entry.checkin_id).status = "WAITING"
                        player.status = "WAITING"
                    else:
                        player.status = "ACTIVE"
                        if entry:
                            checkin = s.get(Checkin, entry.checkin_id)
                            checkin.status, checkin.checkout_time = "FINISHED", utcnow()
                            s.delete(entry)
                elif not complete:
                    player.status = "PLAYING"
                    if entry:
                        entry.status = "PLAYING"
                        s.get(Checkin, entry.checkin_id).status = "PLAYING"
                s.flush()
                if complete or was_complete:
                    stats = self.statistics(s, player.id, event_only=True)
                    player.games_played, player.wins, player.losses = (
                        stats["games_won"] + stats["games_lost"],
                        stats["wins"],
                        stats["losses"],
                    )
            self.rotation.restore_groups(s, match)
            self.rotation.after_completion(s, match, was_complete)
            device_id = event.get("device_id")
            if device_id:
                device = s.get(ScoreboardDevice, int(device_id))
                if device:
                    device.last_seen_at, device.status = utcnow(), "ONLINE"

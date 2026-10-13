"""Database-serialized queue, court and multi-court session matchmaking."""

from dataclasses import asdict
from datetime import timedelta
from uuid import uuid4
import logging
from sqlalchemy import select, func
from ..config import Settings
from .models import (
    Court,
    Player,
    QueueEntry,
    QueueGroup,
    Checkin,
    Match,
    MatchPlayer,
    MatchEvent,
    TeamRecord,
    ScoreboardDevice,
    SystemSetting,
    DeviceSnapshot,
    PlaySession,
    WinnerHold,
    utcnow,
)
from .club_service import row_dict
from .matching import MatchingSettings, strategy


class RotationService:
    def __init__(self, club):
        self.club, self.db = club, club.db

    def session(self, s, create=False):
        row = s.scalar(
            select(PlaySession)
            .where(PlaySession.active == 1)
            .order_by(PlaySession.id.desc())
        )
        if not row and create:
            if s.get(SystemSetting, "event_lifecycle"):
                raise ValueError("Start a new event on Dashboard before checking in or assigning players")
            row = PlaySession(
                name="Open Play",
                settings_json=asdict(MatchingSettings()),
                court_ids_json=[],
            )
            s.add(row)
            s.flush()
        settings = MatchingSettings(**(row.settings_json if row else {})).validate()
        return row, settings

    def configuration(self) -> dict:
        with self.db.transaction() as s:
            row, settings = self.session(s)
            return {
                "session": row_dict(row) if row else None,
                "settings": asdict(settings),
                "modes": [
                    "FAIR_BALANCED",
                    "QUEUE",
                    "RANDOM",
                    "SKILL_BASED",
                    "WINNERS_STAY",
                    "KING_OF_COURT",
                    "MANUAL",
                ],
                "king_of_court_available": False,
            }

    def save_configuration(self, values: dict) -> dict:
        with self.db.assignment_transaction() as s:
            current, old = self.session(s)
            settings = MatchingSettings(
                **{**asdict(old), **values.get("settings", {})}
            ).validate()
            name = str(
                values.get("name", current.name if current else "Open Play")
            ).strip()
            if not name or len(name) > 100:
                raise ValueError("Session name must contain 1–100 characters")
            ids = values.get("court_ids", current.court_ids_json if current else [])
            if not isinstance(ids, list) or any(
                type(i) is not int or not s.get(Court, i) for i in ids
            ):
                raise ValueError("Select valid court database IDs")
            managed = s.get(SystemSetting, "event_lifecycle")
            if managed and (values.get("new_session") or not current or current.name != name):
                raise ValueError("Create or close events on Dashboard; matchmaking settings cannot replace an event")
            if values.get("new_session") or not current or current.name != name:
                if current:
                    current.active = 0
                current = PlaySession(
                    name=name,
                    settings_json=asdict(settings),
                    court_ids_json=list(set(ids)),
                )
                s.add(current)
            else:
                current.settings_json = asdict(settings)
                current.court_ids_json = list(set(ids))
            if settings.mode != "WINNERS_STAY":
                for hold in s.scalars(select(WinnerHold).where(WinnerHold.active == 1)):
                    self.release_hold(s, hold)
            s.flush()
            return row_dict(current)

    def court_state(self, s, court, settings):
        device = s.scalar(
            select(ScoreboardDevice).where(ScoreboardDevice.court_id == court.id)
        )
        snapshot = s.get(DeviceSnapshot, device.id) if device else None
        last_seen = (
            snapshot.received_at
            if snapshot
            else device.last_seen_at if device else None
        )
        online = bool(
            last_seen
            and last_seen
            >= utcnow() - timedelta(seconds=settings.heartbeat_timeout_seconds)
        )
        status = (
            "DISABLED"
            if not court.enabled or court.status == "DISABLED"
            else court.status
        )
        live = snapshot.snapshot_json if snapshot else {}
        if status != "DISABLED" and not online:
            status = "OFFLINE"
        elif (
            status == "AVAILABLE"
            and live.get("match_exists")
            and live.get("state", {}).get("status") in ("playing", "game_over")
        ):
            status = "PLAYING" if live.get("match_id") else "CASUAL_IN_USE"
        elif status == "IN_USE":
            status = "PLAYING"
        return {
            **row_dict(court),
            "status": status,
            "stored_status": court.status,
            "online": online,
            "device": row_dict(device) if device else None,
            "last_heartbeat": last_seen.isoformat() + "Z" if last_seen else None,
        }

    def courts(self) -> list[dict]:
        with self.db.transaction() as s:
            _, settings = self.session(s)
            return [
                self.court_state(s, c, settings)
                for c in s.scalars(select(Court).order_by(Court.number))
            ]

    def require_available(self, s, court, settings, allow_reserved=False):
        if not court or not court.enabled:
            raise ValueError("Court is disabled or missing")
        info = self.court_state(s, court, settings)
        accepted = ("AVAILABLE", "RESERVED") if allow_reserved else ("AVAILABLE",)
        if not info["online"] or info["status"] not in accepted:
            raise ValueError("Court must be online and available")
        if court.active_match_id and not allow_reserved:
            raise ValueError("Court already has a match")

    def configure_courts(self, values: dict) -> list[dict]:
        with self.db.assignment_transaction() as s:
            if "count" in values:
                count = values["count"]
                if type(count) is not int or count < 1 or count > 1000:
                    raise ValueError("Enter 1–1000 enabled courts")
                courts = list(s.scalars(select(Court).order_by(Court.number)))
                # Count operates on sorted existing records, including non-contiguous numbers.
                for court in courts[count:]:
                    if court.active_match_id or self.has_hold(s, court.id):
                        raise ValueError(
                            "Finish/cancel assignments before disabling occupied courts"
                        )
                while len(courts) < count:
                    number = max([c.number for c in courts], default=0) + 1
                    court = Court(
                        number=number,
                        name="Court " + str(number),
                        available_since=utcnow(),
                    )
                    s.add(court)
                    s.flush()
                    courts.append(court)
                for index, court in enumerate(courts):
                    court.enabled = int(index < count)
                    if not court.enabled:
                        court.status = "DISABLED"
                    elif court.status == "DISABLED":
                        court.status = "AVAILABLE"
                        court.available_since = utcnow()
            else:
                court = s.get(Court, int(values["id"]), with_for_update=True)
                if not court:
                    raise ValueError("Court not found")
                name = str(values.get("name", court.name)).strip()
                if not name or len(name) > 100:
                    raise ValueError("Enter a court name of 1–100 characters")
                enabled = values.get("enabled", bool(court.enabled))
                if type(enabled) is not bool:
                    raise ValueError("Enabled must be true or false")
                if not enabled and (
                    court.active_match_id or self.has_hold(s, court.id)
                ):
                    raise ValueError("Occupied courts cannot be disabled")
                court.name = name
                court.enabled = int(enabled)
                level = values.get("level", court.level)
                if type(level) is not int or level < 0:
                    raise ValueError("Court level must be a non-negative integer")
                court.level = level
                if not enabled:
                    court.status = "DISABLED"
                elif court.status == "DISABLED":
                    court.status = "AVAILABLE"
                    court.available_since = utcnow()
                if values.get("mark_available"):
                    if court.active_match_id or self.has_hold(s, court.id):
                        raise ValueError("Cannot clear a court with an assignment")
                    _, settings = self.session(s)
                    info = self.court_state(s, court, settings)
                    if not info["online"] or info["status"] in (
                        "PLAYING",
                        "CASUAL_IN_USE",
                    ):
                        raise ValueError(
                            "Reconnect/finish the court before marking available"
                        )
                    court.status = "AVAILABLE"
                    court.available_since = utcnow()
        return self.courts()

    def checkin(self, ids: list[int], temporary: bool = False) -> dict:
        if (
            not isinstance(ids, list)
            or len(ids) not in (1, 2)
            or len(set(ids)) != len(ids)
            or any(type(i) is not int for i in ids)
        ):
            raise ValueError(
                "Join Solo requires one player; Join as Team requires two distinct players"
            )
        with self.db.assignment_transaction() as s:
            event = self.club.events.require_active(s)
            players = [s.get(Player, i, with_for_update=True) for i in sorted(ids)]
            if any(not p or p.status not in ("ACTIVE", "WAITING") for p in players):
                raise ValueError("Player is unavailable or already assigned")
            if any(
                s.scalar(select(QueueEntry).where(QueueEntry.player_id == i))
                for i in ids
            ):
                raise ValueError(
                    "Player is already checked in; pair waiting solos using the queue action"
                )
            group = QueueGroup(
                entry_type="TEAM" if len(ids) == 2 else "SOLO", temporary=int(temporary)
            )
            s.add(group)
            s.flush()
            position = (s.scalar(select(func.max(QueueEntry.position))) or 0) + 1
            for player in players:
                checkin = Checkin(player_id=player.id, session_id=event.id)
                s.add(checkin)
                s.flush()
                s.add(
                    QueueEntry(
                        player_id=player.id,
                        checkin_id=checkin.id,
                        group_id=group.id,
                        position=position,
                    )
                )
                player.status = "WAITING"
            return {"id": group.id, "entry_type": group.entry_type, "player_ids": ids}

    def group_action(
        self, group_id: int, action: str, partner_id: int | None = None
    ) -> None:
        with self.db.assignment_transaction() as s:
            group = s.get(QueueGroup, group_id, with_for_update=True)
            members = list(
                s.scalars(
                    select(QueueEntry)
                    .where(QueueEntry.group_id == group_id)
                    .order_by(QueueEntry.id)
                )
            )
            if not group or not members or any(e.status != "WAITING" for e in members):
                raise ValueError("Entry must be waiting")
            if action == "pair":
                other = s.get(QueueGroup, int(partner_id))
                peers = list(
                    s.scalars(
                        select(QueueEntry).where(QueueEntry.group_id == partner_id)
                    )
                )
                if (
                    group.entry_type != "SOLO"
                    or not other
                    or other.entry_type != "SOLO"
                    or len(peers) != 1
                    or peers[0].status != "WAITING"
                    or partner_id == group_id
                ):
                    raise ValueError("Pair two different waiting solo entries")
                group.entry_type = "TEAM"
                group.temporary = 1
                peers[0].group_id = group.id
                position = min(peers[0].position, members[0].position)
                joined_at = min(peers[0].joined_at, members[0].joined_at)
                priority = max(peers[0].priority, members[0].priority)
                for entry in (peers[0], members[0]):
                    entry.position, entry.joined_at, entry.priority = (
                        position,
                        joined_at,
                        priority,
                    )
                other.status = "CLOSED"
            elif action == "break":
                if group.entry_type != "TEAM" or not group.temporary:
                    raise ValueError("Only a confirmed temporary team can be broken")
                for entry in members:
                    solo = QueueGroup(entry_type="SOLO")
                    s.add(solo)
                    s.flush()
                    entry.group_id = solo.id
                group.status = "CLOSED"
            elif action == "remove":
                for entry in members:
                    checkin = s.get(Checkin, entry.checkin_id)
                    checkin.status = "CHECKED_OUT"
                    checkin.checkout_time = utcnow()
                    s.get(Player, entry.player_id).status = "ACTIVE"
                    s.delete(entry)
                group.status = "CLOSED"
            elif action == "priority":
                value = 0 if members[0].priority else 1
                for entry in members:
                    entry.priority = value
            elif action in ("up", "down"):
                ordered = list(
                    s.scalars(
                        select(QueueEntry)
                        .where(QueueEntry.status == "WAITING")
                        .order_by(QueueEntry.position, QueueEntry.id)
                    )
                )
                keys = list(dict.fromkeys(e.group_id for e in ordered))
                at = keys.index(group_id)
                other = at + (-1 if action == "up" else 1)
                if 0 <= other < len(keys):
                    keys[at], keys[other] = keys[other], keys[at]
                for entry in ordered:
                    entry.position = keys.index(entry.group_id) + 1
            else:
                raise ValueError("Unknown queue action")

    def waiting(self, s):
        return self.club.waiting(s)

    def recommendation(self, players, singles=False, excluded=None):
        with self.db.transaction() as s:
            event, settings = self.session(s)
            if s.get(SystemSetting, "event_lifecycle") and (not event or event.event_type == "TOURNAMENT"):
                return None
        return strategy(settings.mode).select(
            players,
            settings,
            singles=singles,
            grace_elapsed=settings.grace_period_seconds,
            excluded=excluded,
        )

    def has_hold(self, s, court_id):
        hold = s.get(WinnerHold, court_id)
        return bool(hold and hold.active)

    def release_hold(self, s, hold):
        for entry in s.scalars(
            select(QueueEntry).where(
                QueueEntry.player_id.in_(hold.player_ids_json),
                QueueEntry.status == "HOLDING",
            )
        ):
            entry.status = "WAITING"
            entry.joined_at = utcnow()
            s.get(Player, entry.player_id).status = "WAITING"
            s.get(Checkin, entry.checkin_id).status = "WAITING"
            if entry.group_id:
                s.get(QueueGroup, entry.group_id).status = "WAITING"
        hold.active = 0

    def create_reserved(self, s, court, a, b, session, settings, explanation=None):
        ids = a + b
        self.club.events.require_attendance(s, ids)
        if len(ids) != 4 or len(set(ids)) != 4:
            raise ValueError("Doubles requires four different players")
        players = {i: s.get(Player, i, with_for_update=True) for i in sorted(ids)}
        hold = s.get(WinnerHold, court.id)
        held = set(hold.player_ids_json) if hold and hold.active else set()
        if held and held not in (set(a), set(b)):
            raise ValueError("The retained winner team must stay on this court")
        groups = []
        for team in (a, b):
            for i in team:
                player = players[i]
                if (
                    not player
                    or player.status not in ("ACTIVE", "WAITING", "HOLDING")
                    or (player.status == "HOLDING" and i not in held)
                ):
                    raise ValueError("Player has already been assigned")
                entry = s.scalar(select(QueueEntry).where(QueueEntry.player_id == i))
                if entry:
                    if entry.status not in ("WAITING", "HOLDING"):
                        raise ValueError("Queue entry has already been assigned")
                    group = (
                        s.get(QueueGroup, entry.group_id) if entry.group_id else None
                    )
                    if group and group.entry_type == "TEAM":
                        members = set(
                            s.scalars(
                                select(QueueEntry.player_id).where(
                                    QueueEntry.group_id == group.id
                                )
                            )
                        )
                        if not members.issubset(set(team)) or len(members) != 2:
                            raise ValueError("A fixed team cannot be split")
                    if group and not any(g["id"] == group.id for g in groups):
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
        scoring = Settings(
            play_mode="Club Managed",
            players_a=" / ".join(players[i].display_name for i in a),
            players_b=" / ".join(players[i].display_name for i in b),
            scoring_mode=settings.scoring_mode,
            points_to_win=settings.points_to_win,
            win_by=settings.win_by,
            best_of=settings.best_of,
        ).resolve_first_serve()
        if self.club.club_name:
            scoring.club_name = self.club.club_name
        match = Match(
            court_id=court.id,
            match_type="Doubles",
            scoring_type=scoring.scoring_mode,
            points_to_win=scoring.points_to_win,
            win_by=scoring.win_by,
            best_of=scoring.best_of,
            status="RESERVED",
            settings_json=scoring.to_dict(),
            session_id=session.id,
            matchmaking_mode=settings.mode,
            matchmaking_json={
                "settings": asdict(settings),
                "explanation": explanation
                or {"reasons": ["Manually selected by operator"]},
                "hold_streak": hold.streak if held else 0,
                "held_players": sorted(held),
                "dispatch_automatically": bool(
                    explanation and settings.automatic_assignment
                ),
            },
            queue_groups_json=groups,
        )
        s.add(match)
        s.flush()
        for side, team in (("A", a), ("B", b)):
            s.add(
                TeamRecord(
                    match_id=match.id,
                    side=side,
                    name=scoring.team_a if side == "A" else scoring.team_b,
                )
            )
            for pos, i in enumerate(team, 1):
                s.add(
                    MatchPlayer(match_id=match.id, player_id=i, team=side, position=pos)
                )
                players[i].status = "ASSIGNED"
                entry = s.scalar(select(QueueEntry).where(QueueEntry.player_id == i))
                if entry:
                    entry.status = "ASSIGNED"
                    s.get(Checkin, entry.checkin_id).status = "ASSIGNED"
                    if entry.group_id:
                        s.get(QueueGroup, entry.group_id).status = "ASSIGNED"
        court.status = "RESERVED"
        court.active_match_id = match.id
        if held:
            hold.active = 0
        s.add(
            MatchEvent(
                event_id=str(uuid4()),
                match_id=match.id,
                sequence_no=1,
                event_type="MATCH_ASSIGNED",
                payload_json=match.matchmaking_json,
            )
        )
        s.flush()
        logging.info(
            "MATCH_RESERVED match=%s court=%s mode=%s players=%s quality=%s",
            match.id,
            court.id,
            settings.mode,
            ids,
            (explanation or {}).get("quality"),
        )
        return row_dict(match)

    def generate(self, force: bool = False) -> list[dict]:
        result = []
        with self.db.assignment_transaction() as s:
            current, _ = self.session(s)
            if s.get(SystemSetting, "event_lifecycle") and (not current or current.event_type == "TOURNAMENT"):
                return result
            session, settings = self.session(s, True)
            if settings.mode in ("MANUAL", "KING_OF_COURT") or (
                not force and not settings.automatic_assignment
            ):
                return result
            courts = list(
                s.scalars(
                    select(Court)
                    .where(
                        Court.enabled == 1,
                        Court.status == "AVAILABLE",
                        Court.active_match_id.is_(None),
                    )
                    .order_by(Court.number)
                    .with_for_update()
                )
            )
            waiting = self.waiting(s)
            for court in courts:
                if session.court_ids_json and court.id not in session.court_ids_json:
                    continue
                if self.court_state(s, court, settings)["status"] != "AVAILABLE":
                    continue
                hold = s.get(WinnerHold, court.id)
                if hold and hold.active and settings.mode != "WINNERS_STAY":
                    self.release_hold(s, hold)
                held = []
                if hold and hold.active:
                    for i in hold.player_ids_json:
                        p = s.get(Player, i)
                        if p.status != "HOLDING":
                            raise ValueError(
                                "Winner hold requires operator reconciliation"
                            )
                        held.append(
                            {
                                "id": p.id,
                                "skill_rating": p.skill_rating,
                                "waiting_minutes": 0,
                                "position": 0,
                                "games_today": self.club.statistics(s, p.id)[
                                    "games_today"
                                ],
                                "entry_type": "TEAM",
                            }
                        )
                elapsed = (
                    utcnow() - (court.available_since or court.updated_at or utcnow())
                ).total_seconds()
                candidate = strategy(settings.mode).select(
                    waiting, settings, held=held, grace_elapsed=elapsed
                )
                if not candidate:
                    continue
                result.append(
                    self.create_reserved(
                        s,
                        court,
                        candidate["team_a"],
                        candidate["team_b"],
                        session,
                        settings,
                        candidate,
                    )
                )
                assigned = set(candidate["team_a"] + candidate["team_b"])
                waiting = [p for p in waiting if p["id"] not in assigned]
        return result

    def manual(self, values: dict) -> dict:
        with self.db.assignment_transaction() as s:
            session, settings = self.session(s, True)
            court = s.get(Court, int(values["court_id"]), with_for_update=True)
            self.require_available(s, court, settings)
            ids = values.get("players", {})
            if set(ids) != {"A", "B"} or any(
                not isinstance(ids[k], list) or any(type(i) is not int for i in ids[k])
                for k in ids
            ):
                raise ValueError("Select players for both teams")
            return self.create_reserved(s, court, ids["A"], ids["B"], session, settings)

    def cancel(self, match_id: int, new_court_id: int | None = None) -> dict:
        with self.db.assignment_transaction() as s:
            match = s.get(Match, match_id, with_for_update=True)
            if not match or match.status not in ("PREPARED", "RESERVED"):
                raise ValueError("Only a match that has not started can be changed")
            old = s.get(Court, match.court_id, with_for_update=True)
            retained = (match.matchmaking_json or {}).get("held_players", [])
            if retained and new_court_id is not None:
                raise ValueError("Retained winners cannot move courts; cancel first")
            if new_court_id is not None:
                _, settings = self.session(s)
                target = s.get(Court, int(new_court_id), with_for_update=True)
                self.require_available(s, target, settings)
                if self.has_hold(s, target.id):
                    raise ValueError("Target court has retained winners")
                if match.status == "RESERVED":
                    target.status = "RESERVED"
                    target.active_match_id = match.id
                match.court_id = target.id
            else:
                was_reserved = match.status == "RESERVED"
                match.status = "CANCELLED"
                if not was_reserved:
                    return {"ok": True}
                for link in s.scalars(
                    select(MatchPlayer).where(MatchPlayer.match_id == match.id)
                ):
                    entry = s.scalar(
                        select(QueueEntry).where(QueueEntry.player_id == link.player_id)
                    )
                    s.get(Player, link.player_id).status = (
                        "WAITING" if entry else "ACTIVE"
                    )
                    if entry:
                        entry.status = "WAITING"
                        s.get(Checkin, entry.checkin_id).status = "WAITING"
                        if entry.group_id:
                            s.get(QueueGroup, entry.group_id).status = "WAITING"
                if retained:
                    hold = s.get(WinnerHold, old.id)
                    if hold:
                        hold.active = 1
                        for i in retained:
                            entry = s.scalar(
                                select(QueueEntry).where(QueueEntry.player_id == i)
                            )
                            if entry:
                                entry.status = "HOLDING"
                                s.get(Player, i).status = "HOLDING"
                                s.get(Checkin, entry.checkin_id).status = "HOLDING"
                                s.get(QueueGroup, entry.group_id).status = "HOLDING"
            if old.active_match_id == match.id:
                old.status = "AVAILABLE" if old.enabled else "DISABLED"
                old.active_match_id = None
                old.available_since = utcnow()
        return {"ok": True}

    def restore_groups(self, s, match):
        """Retain queue identity through completion and scoring Undo."""
        for metadata in match.queue_groups_json or []:
            group = s.get(QueueGroup, metadata["id"])
            if not group:
                continue
            for i in metadata["players"]:
                entry = s.scalar(select(QueueEntry).where(QueueEntry.player_id == i))
                if entry:
                    entry.group_id = group.id
                    group.status = entry.status
        for entry in s.scalars(
            select(QueueEntry)
            .join(MatchPlayer, MatchPlayer.player_id == QueueEntry.player_id)
            .where(MatchPlayer.match_id == match.id)
        ):
            if not entry.group_id:
                group = QueueGroup(entry_type="SOLO", status=entry.status)
                s.add(group)
                s.flush()
                entry.group_id = group.id

    def after_completion(self, s, match, was_complete):
        _, current = self.session(s)
        if was_complete or match.status != "COMPLETE":
            return
        for entry in s.scalars(
            select(QueueEntry)
            .join(MatchPlayer, MatchPlayer.player_id == QueueEntry.player_id)
            .where(MatchPlayer.match_id == match.id)
        ):
            if entry.group_id:
                s.get(QueueGroup, entry.group_id).status = entry.status
        if match.matchmaking_mode != "WINNERS_STAY" or current.mode != "WINNERS_STAY":
            return
        winners = list(
            s.scalars(
                select(MatchPlayer.player_id).where(
                    MatchPlayer.match_id == match.id,
                    MatchPlayer.team == match.winner_team,
                )
            )
        )
        metadata = match.matchmaking_json or {}
        streak = (
            (metadata.get("hold_streak", 0) + 1)
            if set(winners) == set(metadata.get("held_players", []))
            else 1
        )
        limit = metadata.get("settings", {}).get(
            "maximum_consecutive_wins", current.maximum_consecutive_wins
        )
        old = s.get(WinnerHold, match.court_id)
        hold = old or WinnerHold(court_id=match.court_id)
        hold.player_ids_json = winners
        hold.streak = streak
        hold.match_id = match.id
        hold.active = int(streak < limit)
        s.add(hold)
        for i in winners:
            entry = s.scalar(select(QueueEntry).where(QueueEntry.player_id == i))
            if not entry:
                metadata = next(
                    (g for g in match.queue_groups_json or [] if i in g["players"]),
                    None,
                )
                group = s.get(QueueGroup, metadata["id"]) if metadata else None
                if not group:
                    group = QueueGroup(entry_type="SOLO")
                    s.add(group)
                    s.flush()
                checkin = Checkin(player_id=i, session_id=match.session_id)
                s.add(checkin)
                s.flush()
                entry = QueueEntry(
                    player_id=i,
                    checkin_id=checkin.id,
                    group_id=group.id,
                    position=(s.scalar(select(func.max(QueueEntry.position))) or 0) + 1,
                )
                s.add(entry)
            status = "HOLDING" if hold.active else "WAITING"
            entry.status = status
            s.get(Player, i).status = status
            s.get(Checkin, entry.checkin_id).status = status
            if entry.group_id:
                s.get(QueueGroup, entry.group_id).status = status

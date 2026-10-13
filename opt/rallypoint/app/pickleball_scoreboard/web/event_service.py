"""Club-local event lifecycle using serialized matchmaking sessions."""
from dataclasses import asdict
from datetime import date, datetime
from flask import jsonify, request
from sqlalchemy import select, delete, func
from .models import (PlaySession, SystemSetting, Checkin, QueueEntry, QueueGroup,
                     Player, Match, Court, WinnerHold, DeviceSnapshot, AuditRecord, utcnow)
from .matching import MatchingSettings
from .club_service import row_dict


class EventService:
    def __init__(self, club):
        self.club, self.db = club, club.db

    def require_active(self, s):
        return self.club.rotation.session(s, True)[0]

    def require_attendance(self, s, ids):
        event = self.require_active(s)
        if s.get(SystemSetting, "event_lifecycle"):
            present = set(s.scalars(select(Checkin.player_id).where(Checkin.session_id == event.id, Checkin.checkout_time.is_(None))))
            if not set(ids).issubset(present):
                raise ValueError("Check in all selected players for the active event first")
        return event

    def listing(self):
        with self.db.transaction() as s:
            rows = list(s.scalars(select(PlaySession).order_by(PlaySession.id.desc())))
            events = []
            for row in rows:
                saved = s.get(SystemSetting, f"event_queue_{row.id}") if not row.active else None
                events.append({**row_dict(row), "resume_queue_available": saved is not None})
            return {"active": next((r for r in events if r["active"]), None),
                    "events": events}

    def start(self, values, actor="operator"):
        name = str(values.get("name", "")).strip()
        kind = values.get("event_type", "OPEN_PLAY")
        day = str(values.get("event_date", ""))
        if not name or len(name) > 100:
            raise ValueError("Enter an event name up to 100 characters")
        if kind not in ("OPEN_PLAY", "TOURNAMENT"):
            raise ValueError("Choose Open Play or Tournament")
        try:
            if date.fromisoformat(day).isoformat() != day:
                raise ValueError()
        except ValueError:
            raise ValueError("Choose a valid event date") from None
        with self.db.assignment_transaction() as s:
            current, settings = self.club.rotation.session(s)
            if current:
                raise ValueError("End the current event before starting a new event")
            self._ensure_idle(s)
            self._clear_attendance(s)
            previous = s.scalar(select(PlaySession).order_by(PlaySession.id.desc()).limit(1))
            if previous:
                settings = MatchingSettings(**previous.settings_json).validate()
            event = PlaySession(name=name, event_type=kind, event_date=day,
                                settings_json=asdict(settings), court_ids_json=list(previous.court_ids_json) if previous else [])
            s.add(event)
            s.merge(SystemSetting(key="event_lifecycle", value_json={"enabled": True}))
            s.flush()
            s.add(AuditRecord(actor=actor, action="EVENT_STARTED", target=str(event.id),
                              details={"name":name,"event_type":kind,"event_date":day}))
            return row_dict(event)

    def close(self, event_id, actor="operator"):
        with self.db.assignment_transaction() as s:
            event = s.get(PlaySession, event_id, with_for_update=True)
            if not event or not event.active:
                raise ValueError("Event is missing or already closed")
            self._ensure_idle(s)
            self._save_attendance(s, event.id)
            # Unsent drafts are safe to archive; never cancel a delivered match here.
            for match in s.scalars(select(Match).where(Match.status == "PREPARED")):
                match.status = "CANCELLED"
                match.ended_at = utcnow()
            self._clear_attendance(s)
            event.active, event.ended_at = 0, utcnow()
            s.merge(SystemSetting(key="event_lifecycle", value_json={"enabled": True}))
            s.add(AuditRecord(actor=actor, action="EVENT_ENDED", target=str(event.id), details={"name":event.name}))
            return row_dict(event)

    def resume(self, event_id, actor="operator"):
        """Restore saved check-in and queue without restarting completed matches."""
        with self.db.assignment_transaction() as s:
            event = s.get(PlaySession, event_id, with_for_update=True)
            if not event:
                raise ValueError("Event not found")
            if event.active:
                raise ValueError("This event is already active")
            current, _ = self.club.rotation.session(s)
            if current:
                raise ValueError("End the current event before resuming another event")
            self._ensure_idle(s)
            self._clear_attendance(s)
            restored = self._restore_attendance(s, event.id)
            # Completed/cancelled matches remain historical after reopening the event.
            last_match = s.scalar(select(func.max(Match.id)).where(Match.session_id == event.id)) or 0
            s.merge(SystemSetting(key=f"event_resume_{event.id}",
                                  value_json={"last_archived_match_id": last_match}))
            event.active, event.ended_at = 1, None
            s.merge(SystemSetting(key="event_lifecycle", value_json={"enabled": True}))
            s.add(AuditRecord(actor=actor, action="EVENT_RESUMED", target=str(event.id),
                              details={"name": event.name, "event_type": event.event_type,
                                       "event_date": event.event_date,
                                       "last_archived_match_id": last_match, **restored}))
            s.flush()
            return {**row_dict(event), **restored}

    @staticmethod
    def _save_attendance(s, event_id):
        """Save roster and queue atomically before closing the event."""
        entries = list(s.scalars(select(QueueEntry).order_by(QueueEntry.id)))
        group_ids = {entry.group_id for entry in entries if entry.group_id}
        snapshot = {
            "saved_at": utcnow().isoformat() + "Z",
            "checkins": [row_dict(row) for row in s.scalars(
                select(Checkin).where(Checkin.checkout_time.is_(None)))],
            "queue": [row_dict(row) for row in entries],
            "groups": [row_dict(row) for row in s.scalars(
                select(QueueGroup).where(QueueGroup.id.in_(group_ids)))],
            "holds": [row_dict(row) for row in s.scalars(
                select(WinnerHold).where(WinnerHold.active == 1))],
        }
        s.merge(SystemSetting(key=f"event_queue_{event_id}", value_json=snapshot))

    @staticmethod
    def _restore_attendance(s, event_id):
        saved = s.get(SystemSetting, f"event_queue_{event_id}")
        if saved is None:
            # Earlier versions deleted queue rows. Never guess past attendance.
            return {"queue_snapshot_available": False, "restored_players": 0,
                    "restored_queue": 0}
        snapshot = saved.value_json
        player_ids = {row["player_id"] for row in snapshot["checkins"]}
        for player_id in player_ids:
            player = s.get(Player, player_id)
            if not player or player.status == "INACTIVE":
                raise ValueError("A saved checked-in player is inactive or missing. Reactivate the player before resuming this event.")
        def timestamp(value):
            return datetime.fromisoformat(value.removesuffix("Z"))
        paused = utcnow() - timestamp(snapshot["saved_at"])
        for row in snapshot["groups"]:
            group = s.get(QueueGroup, row["id"])
            if group is None:
                raise ValueError("A saved queue group is missing; event restoration requires reconciliation")
            group.entry_type, group.temporary, group.status = (
                row["entry_type"], row["temporary"], row["status"])
        for row in snapshot["checkins"]:
            checkin = s.get(Checkin, row["id"])
            if checkin is None or checkin.player_id != row["player_id"]:
                raise ValueError("A saved check-in is missing; event restoration requires reconciliation")
            checkin.session_id = event_id
            checkin.checkout_time = None
            checkin.status = row["status"]
            s.get(Player, row["player_id"]).status = row["status"]
        s.flush()
        for row in snapshot["queue"]:
            s.add(QueueEntry(player_id=row["player_id"], checkin_id=row["checkin_id"],
                             group_id=row["group_id"], position=row["position"],
                             priority=row["priority"], status=row["status"],
                             joined_at=timestamp(row["joined_at"]) + paused))
        for row in snapshot["holds"]:
            hold = s.get(WinnerHold, row["court_id"])
            if hold is None:
                hold = WinnerHold(court_id=row["court_id"])
                s.add(hold)
            hold.player_ids_json = row["player_ids_json"]
            hold.streak, hold.match_id, hold.active = row["streak"], row["match_id"], 1
        return {"queue_snapshot_available": True, "restored_players": len(player_ids),
                "restored_queue": len(snapshot["queue"])}


    @staticmethod
    def historical_match(s, match):
        """Keep old HDMI retries and scoring controls out of resumed match history."""
        boundary = s.get(SystemSetting, f"event_resume_{match.session_id}") if match.session_id else None
        return bool(boundary and match.id <= boundary.value_json["last_archived_match_id"])

    @staticmethod
    def _ensure_idle(s):
        if s.scalar(select(Match.id).where(Match.status.in_(("ACTIVE","STARTING","RESERVED"))).limit(1)):
            raise ValueError("Finish or explicitly end all assigned matches before ending or starting an event")
        if s.scalar(select(Court.id).where(Court.active_match_id.is_not(None)).limit(1)):
            raise ValueError("A court still has an assigned match. Resolve it on Controls first")
        # Casual HDMI matches are authoritative even when no managed match exists.
        for snap in s.scalars(select(DeviceSnapshot)):
            live = snap.snapshot_json or {}
            if live.get("match_exists") and live.get("state", {}).get("status") in ("playing", "game_over"):
                raise ValueError("A court has an unfinished HDMI match. End it on that court first")

    @staticmethod
    def _clear_attendance(s):
        for row in s.scalars(select(Checkin).where(Checkin.checkout_time.is_(None))):
            row.status, row.checkout_time = "CHECKED_OUT", utcnow()
        s.execute(delete(QueueEntry))
        for row in s.scalars(select(QueueGroup).where(QueueGroup.status != "CHECKED_OUT")):
            row.status = "CHECKED_OUT"
        for row in s.scalars(select(WinnerHold)):
            row.active = 0
        for row in s.scalars(select(Player).where(Player.status.in_(("WAITING","ASSIGNED","PLAYING","HOLDING")))):
            row.status = "ACTIVE"


def register_events(app, club, operator, viewer):
    @app.get("/api/play-events")
    @viewer
    def events_list():
        return jsonify(club.events.listing())

    @app.post("/api/play-events")
    @operator
    def events_start():
        data = request.get_json() or {}
        if data.get("confirmed") is not True:
            raise ValueError("Confirm starting the new event")
        return jsonify(club.events.start(data)), 201

    @app.post("/api/play-events/<int:event_id>/close")
    @operator
    def events_close(event_id):
        if (request.get_json() or {}).get("confirmed") is not True:
            raise ValueError("Confirm ending the event and checking everyone out")
        return jsonify(club.events.close(event_id))


    @app.post("/api/play-events/<int:event_id>/resume")
    @operator
    def events_resume(event_id):
        if (request.get_json() or {}).get("confirmed") is not True:
            raise ValueError("Confirm resuming the event and restoring saved check-in and queue")
        return jsonify(club.events.resume(event_id))

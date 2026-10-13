"""Validated web facade for prepared matches and authenticated loopback commands."""

import json
from copy import deepcopy
from threading import Lock
from time import monotonic
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from uuid import uuid4
from .models import Court, Match, ScoreboardDevice, MatchPlayer, Player
from sqlalchemy import select


class MatchStateService:
    def __init__(self, club, config):
        self.club, self.config = club, config
        self._preview_lock = Lock()
        self._preview = {}

    def get_preview_state(self, court_id=None):
        """Share spectator reads; control validation always reads fresh state.

        One poll per court per 100 ms regardless of spectator count. Offline
        courts retry once a second without blocking reads of another court.
        """
        with self._preview_lock:
            entry = self._preview.setdefault(court_id, {"lock": Lock(), "until": 0})
        with entry["lock"]:
            if monotonic() >= entry["until"]:
                data = self.get_current_state(court_id)
                entry["data"] = deepcopy(data)
                entry["until"] = monotonic() + (0.1 if data.get("connected") else 1.0)
            return deepcopy(entry["data"])

    def request(self, path, payload=None, url=None):
        url = url or self.config["BRIDGE_URL"]
        parsed = urlparse(url)
        if parsed.scheme != "http" or parsed.hostname not in (
            "127.0.0.1",
            "localhost",
            "::1",
        ):
            raise ValueError("Scoreboard IPC must use a loopback address")
        req = Request(
            url + path,
            data=json.dumps(payload).encode() if payload else None,
            headers={
                "Authorization": "Bearer " + self.config["BRIDGE_TOKEN"],
                "Content-Type": "application/json",
            },
        )
        with urlopen(req, timeout=1) as response:
            return json.load(response)

    def device_url(self, court_id=None):
        if court_id is None:
            if self.config.get("SHARED_ENABLED"):
                with self.club.db.transaction() as s:
                    device = s.scalar(
                        select(ScoreboardDevice)
                        .join(Court, Court.id == ScoreboardDevice.court_id)
                        .where(
                            Court.enabled == 1,
                            Court.status.not_in(("MOVING", "DISABLED")),
                        )
                        .order_by(Court.number)
                    )
                    if not device:
                        raise ValueError("No physical court is assigned to this club")
                    return device.bridge_url
            return self.config["BRIDGE_URL"]
        with self.club.db.transaction() as s:
            device = s.scalar(
                select(ScoreboardDevice).where(ScoreboardDevice.court_id == court_id)
            )
            if not device:
                raise ValueError("No scoreboard device is assigned to this court")
            return device.bridge_url

    def get_current_state(self, court_id=None):
        try:
            return self.request("/state", url=self.device_url(court_id))
        except (OSError, ValueError):
            if court_id is not None:
                with self.club.db.transaction() as s:
                    from .models import DeviceSnapshot

                    device = s.scalar(
                        select(ScoreboardDevice).where(
                            ScoreboardDevice.court_id == court_id
                        )
                    )
                    cached = s.get(DeviceSnapshot, device.id) if device else None
                    if cached:
                        return {**cached.snapshot_json, "connected": False}
                    match = s.scalar(
                        select(Match)
                        .where(
                            Match.court_id == court_id, Match.state_json.is_not(None)
                        )
                        .order_by(Match.id.desc())
                        .limit(1)
                    )
                    if match:
                        return {**match.state_json, "connected": False}
            return {"connected": False, "match_id": None}

    def prepare_match(self, payload):
        return self.club.prepare_match(payload)

    def start_match(self, match_id, replace=False):
        with self.club.db.transaction() as s:
            match = s.get(Match, match_id)
            if not match:
                raise ValueError("Match not found")
            court_id = match.court_id
            court_name = s.get(Court, court_id).name
            roster = {
                side: [
                    {"id": p.id, "name": p.display_name}
                    for p in s.scalars(
                        select(Player)
                        .join(MatchPlayer, MatchPlayer.player_id == Player.id)
                        .where(
                            MatchPlayer.match_id == match.id, MatchPlayer.team == side
                        )
                        .order_by(MatchPlayer.position)
                    )
                ]
                for side in ("A", "B")
            }
        url = self.device_url(court_id)
        current = self.request("/state", url=url)
        if current.get("match_exists", bool(current.get("match_id"))) and current.get(
            "state", {}
        ).get("status") not in (
            "complete",
            "ended",
        ):
            raise ValueError("End the active match before starting another")
        match = self.club.reserve_match(match_id)
        try:
            receipt = self.request(
                "/commands",
                {
                    "request_id": str(uuid4()),
                    "name": "START_MATCH",
                    "payload": {
                        "match_id": match_id,
                        "court_id": court_id,
                        "court_name": court_name,
                        "teams": roster,
                        "matchmaking_mode": match["matchmaking_mode"],
                        "settings": match["settings_json"],
                        "starting_side": match["starting_side"],
                        "replace_confirmed": replace,
                    },
                },
                url,
            )
            receipt["announcement"] = {
                "match_id": match_id, "court_id": court_id,
                "court_name": court_name, "teams": roster,
            }
            return receipt
        except OSError:
            # Request delivery may be uncertain. Leave RESERVED for receipt/reconciliation.
            # Never free a court automatically when a start may have reached HDMI.
            raise ValueError(
                "Scoreboard response lost; court remains reserved. Check live state before retrying."
            )

    def submit_command(
        self, match_id, name, confirmed=False, request_id=None, payload=None, actor=None,
        host_override=False
    ):
        from ..services.scoreboard_bridge import ScoreboardBridge

        if name not in ScoreboardBridge.allowed_commands - {"START_MATCH"}:
            raise ValueError("Unsupported control")
        if (
            name in ("RESET", "END_GAME", "END_MATCH", "SET_SCORES", "DECLARE_WINNER")
            and not confirmed
        ):
            raise ValueError("Confirmation is required")
        details = {}
        # Only the authenticated server route can grant this capability.
        if host_override and name in ("END_GAME", "END_MATCH"):
            details["host_override"] = True
        if name in ("SET_SCORES", "DECLARE_WINNER"):
            from ..scoring.scoring_engine import ScoringEngine

            if not isinstance(payload, dict):
                raise ValueError("Manual result details are required")
            revision = payload.get("expected_revision")
            if type(revision) is not int or revision < 0:
                raise ValueError(
                    "Refresh the live scoreboard before changing the result"
                )
            details["expected_revision"] = revision
            if "scores" in payload:
                details["scores"] = ScoringEngine.validate_scores(payload["scores"])
            elif name == "SET_SCORES":
                raise ValueError("Both team scores are required")
            if name == "DECLARE_WINNER":
                if payload.get("winner") not in ("A", "B"):
                    raise ValueError("Select Team A or Team B as match winner")
                details["winner"] = payload["winner"]
        with self.club.db.transaction() as s:
            match = s.get(Match, int(match_id))
            if not match or match.status not in (
                "ACTIVE",
                "COMPLETE",
                "ENDED",
                "STARTING",
            ):
                raise ValueError("Match is not active on a scoreboard")
            from .models import PlaySession
            play_event = s.get(PlaySession, match.session_id) if match.session_id else None
            if (play_event and play_event.ended_at) or self.club.events.historical_match(s, match):
                raise ValueError("This event is archived; scoring controls are closed")
            court_id = match.court_id
        return self.request(
            "/commands",
            {
                "request_id": request_id or str(uuid4()),
                "name": name,
                "payload": {**details, "match_id": int(match_id), "actor": actor},
            },
            self.device_url(court_id),
        )

    def finish_match(self, match_id, confirmed=False):
        return self.submit_command(match_id, "END_MATCH", confirmed)

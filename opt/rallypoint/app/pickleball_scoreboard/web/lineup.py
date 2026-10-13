"""Operator lineup corrections, with durable delivery through device heartbeats."""
from copy import deepcopy
from flask import jsonify, request
from sqlalchemy import select, delete, func
from .models import (Match, MatchPlayer, Player, QueueEntry, QueueGroup, Checkin,
                     TeamRecord, AuditRecord, utcnow)


def lineup(s, match_id):
    result = {"A": [], "B": []}
    for link in s.scalars(select(MatchPlayer).where(MatchPlayer.match_id == match_id)
                          .order_by(MatchPlayer.team, MatchPlayer.position)):
        result[link.team].append(link.player_id)
    return result


def device_roster(db, event):
    """Return desired names only for the reporting device's current live match."""
    with db.transaction() as s:
        match = s.get(Match, event.get("match_id")) if event.get("match_id") else None
        if not match or match.status not in ("STARTING", "ACTIVE"):
            return None
        settings = match.settings_json
        # Only operator-edited lineups are authoritative; normal setup is unchanged.
        if not (match.matchmaking_json or {}).get("lineup_edited"):
            return None
        return {"match_id": match.id, "settings": {
            k: settings[k] for k in ("team_a", "team_b", "players_a", "players_b")}}


def register(app, db, club, state, operator, actor):
    @app.get("/api/matches/<int:match_id>/lineup")
    @operator
    def get_lineup(match_id):
        with db.transaction() as s:
            match = s.get(Match, match_id)
            if not match or match.status not in ("STARTING", "ACTIVE"):
                raise ValueError("Only an ongoing match can have its players changed")
            current = lineup(s, match_id)
            ids = current["A"] + current["B"]
            choices = [{"id": p.id, "name": p.display_name} for p in s.scalars(
                select(Player).where((Player.id.in_(ids)) | (Player.status.in_(("ACTIVE", "WAITING"))))
                .order_by(Player.display_name))]
            return jsonify(players=current, choices=choices, game_type=match.match_type)

    @app.post("/api/matches/<int:match_id>/lineup")
    @operator
    def change_lineup(match_id):
        values = request.get_json() or {}
        if values.get("confirmed") is not True:
            raise ValueError("Confirm the player change first")
        ids = values.get("players")
        if not isinstance(ids, dict) or set(ids) != {"A", "B"}:
            raise ValueError("Select players for both teams")
        if any(not isinstance(ids[t], list) or any(type(i) is not int for i in ids[t]) for t in ids):
            raise ValueError("Select registered players")
        with db.transaction() as s:
            match = s.get(Match, match_id)
            if not match:
                raise ValueError("Match not found")
            court_id = match.court_id
        live = state.get_current_state(court_id)
        if (not live.get("connected") or live.get("match_id") != match_id
                or live.get("state", {}).get("status") not in ("playing", "game_over")
                or live.get("sync", {}).get("online") is False
                or live.get("sync", {}).get("pending", 0)):
            raise ValueError("Wait until this court is online and fully synced with an ongoing match")
        with db.assignment_transaction() as s:
            match = s.get(Match, match_id, with_for_update=True)
            if match.status not in ("STARTING", "ACTIVE") or club.events.historical_match(s, match):
                raise ValueError("This match is no longer active")
            old = lineup(s, match_id)
            if values.get("expected_players") != old:
                raise ValueError("Another operator changed this lineup; reopen Change players")
            count = 1 if match.match_type == "Singles" else 2
            selected = ids["A"] + ids["B"]
            if any(len(ids[t]) != count for t in ids) or len(set(selected)) != count * 2:
                raise ValueError(f"Choose {count} different player(s) for each team")
            previous = set(old["A"] + old["B"])
            players = {i: s.get(Player, i, with_for_update=True) for i in set(selected) | previous}
            for i in selected:
                p = players[i]
                if not p or (i not in previous and p.status not in ("ACTIVE", "WAITING")):
                    raise ValueError("A selected player is unavailable or playing on another court")
                other = s.scalar(select(MatchPlayer.id).join(Match, Match.id == MatchPlayer.match_id).where(
                    MatchPlayer.player_id == i, Match.id != match_id,
                    Match.status.in_(("RESERVED", "STARTING", "ACTIVE"))).limit(1))
                if other:
                    raise ValueError("A selected player is reserved or playing on another court")
            if ids == old:
                return jsonify(ok=True, unchanged=True)
            # Avoid splitting a waiting fixed pair when selecting just one partner.
            for i in set(selected) - previous:
                entry = s.scalar(select(QueueEntry).where(QueueEntry.player_id == i))
                if entry and entry.group_id:
                    group = s.get(QueueGroup, entry.group_id)
                    if group and group.entry_type == "TEAM":
                        partners = set(s.scalars(select(QueueEntry.player_id).where(QueueEntry.group_id == group.id)))
                        if not any(partners.issubset(set(ids[t])) for t in ids):
                            raise ValueError("Select both players of a waiting fixed team on the same side")
            settings = deepcopy(match.settings_json)
            for team in ("A", "B"):
                label = " / ".join(players[i].display_name for i in ids[team])
                settings["players_" + team.lower()] = label
                # Preserve operator team branding; update automatically generated names.
                old_label = " / ".join(players[i].display_name for i in old[team])
                if settings.get("team_" + team.lower()) == old_label:
                    settings["team_" + team.lower()] = label[:100]
            match.settings_json = settings
            metadata = deepcopy(match.matchmaking_json or {})
            metadata["lineup_edited"] = True
            match.matchmaking_json = metadata
            s.execute(delete(MatchPlayer).where(MatchPlayer.match_id == match_id))
            for team in ("A", "B"):
                for position, i in enumerate(ids[team], 1):
                    s.add(MatchPlayer(match_id=match_id, player_id=i, team=team, position=position))
                record = s.scalar(select(TeamRecord).where(TeamRecord.match_id == match_id, TeamRecord.side == team))
                if record:
                    record.name = settings["team_" + team.lower()]
            # Edited lineups no longer restore their original pre-match queue groups.
            match.queue_groups_json = []
            for i in previous | set(selected):
                entry = s.scalar(select(QueueEntry).where(QueueEntry.player_id == i))
                status = "PLAYING" if i in selected else "WAITING"
                if not entry:
                    checkin = Checkin(player_id=i, session_id=match.session_id, status=status)
                    group = QueueGroup(entry_type="SOLO", status=status)
                    s.add_all([checkin, group]); s.flush()
                    entry = QueueEntry(player_id=i, checkin_id=checkin.id, group_id=group.id,
                                       position=(s.scalar(select(func.max(QueueEntry.position))) or 0) + 1, status=status)
                    s.add(entry)
                if i in previous:
                    # A changed lineup must not leave a half-pair in the queue.
                    group = QueueGroup(entry_type="SOLO", status=status)
                    s.add(group); s.flush()
                    entry.group_id = group.id
                    if i not in selected:
                        entry.joined_at = utcnow()
                elif entry.group_id:
                    s.get(QueueGroup, entry.group_id).status = status
                entry.status = status
                players[i].status = status
                s.get(Checkin, entry.checkin_id).status = status
            s.add(AuditRecord(actor=(actor() or {}).get("username", "operator"), action="CHANGE_PLAYERS",
                              target=f"match:{match_id}", details={"before": old, "after": ids,
                              "result_credit": "updated lineup", "scores_preserved": True}))
        # The durable desired lineup is returned on every heartbeat until HDMI agrees.
        return jsonify(ok=True, syncing=True)

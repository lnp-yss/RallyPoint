"""Read-only PA feed; never polls court devices or changes match state."""
from datetime import timedelta
from sqlalchemy import select
from .models import Match, Court, Player, MatchPlayer, AnnouncementEvent, utcnow


def pending_matches(db):
    """Return court-confirmed matches using the club's own database."""
    result = []
    with db.transaction() as session:
        matches = session.scalars(select(Match).where(
            Match.status.in_(("STARTING", "ACTIVE"))
        ).order_by(Match.id.desc()).limit(50))
        for match in matches:
            snapshot = match.state_json or {}
            if match.status != "ACTIVE" and not snapshot.get("ready_to_play"):
                continue
            court = session.get(Court, match.court_id)
            teams = {}
            for side in ("A", "B"):
                names = list(session.scalars(select(Player.display_name).join(
                    MatchPlayer, MatchPlayer.player_id == Player.id
                ).where(MatchPlayer.match_id == match.id, MatchPlayer.team == side
                ).order_by(MatchPlayer.position)))
                settings = match.settings_json or {}
                teams[side] = " and ".join(names) or str(settings.get(
                    "players_" + side.lower()) or settings.get(
                    "team_" + side.lower()) or "Team " + side)
            at = match.started_at or match.updated_at
            result.append({"match_id": match.id,
                "at": at.isoformat() + "Z", "court": court.name,
                "team_a": teams["A"], "team_b": teams["B"]})
        replays = session.scalars(select(AnnouncementEvent).where(
            AnnouncementEvent.announcement_type == 'PREVIEW_REPLAY',
            AnnouncementEvent.announced_at >= utcnow() - timedelta(seconds=180)
        ).order_by(AnnouncementEvent.announced_at.desc()).limit(30))
        from .announcements import match_information
        for replay in replays:
            info = match_information(db, replay.match_id)
            teams = {side: ' and '.join(p['name'] for p in info['teams'][side])
                     or 'Team ' + side for side in ('A', 'B')}
            result.append({'event_id': 'replay:' + replay.id,
                'match_id': replay.match_id, 'at': replay.announced_at.isoformat() + 'Z',
                'court': info['court_name'], 'team_a': teams['A'], 'team_b': teams['B']})
    return sorted(result, key=lambda row: row['at'], reverse=True)

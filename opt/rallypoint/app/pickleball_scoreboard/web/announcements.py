"""Operator-only PA metadata and playback reports; no scoring or audio generation."""
from uuid import UUID
from flask import jsonify, request, render_template, session
from sqlalchemy import select
from .models import Match, Court, Player, MatchPlayer, AnnouncementEvent, SystemSetting, utcnow
from .auth_context import effective_role


def match_information(db, match_id):
    with db.transaction() as s:
        match = s.get(Match, match_id)
        if not match or match.status not in ('STARTING','ACTIVE','COMPLETE','ENDED'):
            raise ValueError('Only a dispatched match can be announced')
        court = s.get(Court, match.court_id)
        teams = {team:[{'id':player.id,'name':player.display_name} for player in s.scalars(select(Player).join(MatchPlayer,MatchPlayer.player_id==Player.id).where(MatchPlayer.match_id==match.id,MatchPlayer.team==team).order_by(MatchPlayer.position))] for team in ('A','B')}
        return {'match_id':match.id,'court_id':court.id,'court_name':court.name,'teams':teams}


def preview_voice(db):
    with db.transaction() as s:
        setting = s.get(SystemSetting, 'preview_voice')
        return (setting.value_json.get('voice', 'female') if setting else 'female')


def register(app, db, operator, actor):
    @app.get('/api/announcements/preview-voice')
    @operator
    def preview_voice_get():
        return jsonify(voice=preview_voice(db))

    @app.post('/api/announcements/preview-voice')
    @operator
    def preview_voice_save():
        voice = (request.get_json() or {}).get('voice')
        if voice not in ('female', 'male'):
            raise ValueError('Choose Female or Male')
        with db.assignment_transaction() as s:
            setting = s.get(SystemSetting, 'preview_voice')
            if setting is None:
                setting = SystemSetting(key='preview_voice', value_json={})
                s.add(setting)
            setting.value_json = {'voice': voice}
        return jsonify(voice=voice)

    @app.get('/settings/announcements')
    @operator
    def announcement_page():
        return render_template('page.html',page='announcements',csrf=session['csrf'],role=effective_role())

    @app.get('/api/announcements/matches/<int:match_id>')
    @operator
    def announcement_match(match_id):
        return jsonify(match_information(db,match_id))

    @app.post('/api/announcements/preview-replay/<int:match_id>')
    @operator
    def preview_replay(match_id):
        event_id = str(UUID(str((request.get_json() or {}).get('request_id', ''))))
        info = match_information(db, match_id)
        who = actor()['username']
        with db.assignment_transaction() as s:
            existing = s.get(AnnouncementEvent, event_id)
            if existing:
                if (existing.match_id != match_id or existing.actor != who
                        or existing.announcement_type != 'PREVIEW_REPLAY'):
                    raise ValueError('Replay request identity cannot change')
            else:
                s.add(AnnouncementEvent(id=event_id, match_id=match_id,
                    court_id=info['court_id'], actor=who,
                    announcement_type='PREVIEW_REPLAY', playback_status='REQUESTED'))
        return jsonify(ok=True, request_id=event_id), 202

    @app.post('/api/announcements/events')
    @operator
    def announcement_event():
        data = request.get_json() or {}
        event_id = str(UUID(str(data.get('event_id',''))))
        if data.get('announcement_type') not in ('MATCH_START','REPLAY') or data.get('playback_status') not in ('STARTED','COMPLETED','FAILED','CANCELLED'):
            raise ValueError('Invalid announcement event')
        if type(data.get('match_id')) is not int:
            raise ValueError('Match ID is required')
        match = match_information(db,data['match_id'])
        who = actor()["username"]
        with db.assignment_transaction() as s:
            event = s.get(AnnouncementEvent,event_id)
            if event:
                if event.actor != who or event.match_id != match['match_id'] or event.announcement_type != data['announcement_type']:
                    raise ValueError('Announcement event identity cannot change')
                if event.playback_status != 'STARTED':
                    return jsonify(ok=True)
            else:
                event = AnnouncementEvent(id=event_id,match_id=match['match_id'],court_id=match['court_id'],actor=who,announcement_type=data['announcement_type'])
                s.add(event)
            event.playback_status = data['playback_status']
            event.error = str(data.get('error',''))[:150]
            event.updated_at = utcnow()
        return jsonify(ok=True)

    @app.get('/api/announcements/events')
    @operator
    def announcement_events():
        with db.transaction() as s:
            rows = s.scalars(select(AnnouncementEvent).order_by(AnnouncementEvent.announced_at.desc()).limit(50))
            return jsonify([{'event_id':r.id,'match_id':r.match_id,'court_id':r.court_id,'operator':r.actor,'announcement_type':r.announcement_type,'playback_status':r.playback_status,'announced_at':r.announced_at.isoformat()+'Z','error':r.error} for r in rows])

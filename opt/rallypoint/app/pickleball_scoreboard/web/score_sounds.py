"""Club score beep destinations and recent accepted scoring events."""
from datetime import timedelta
from flask import jsonify, request
from sqlalchemy import select
from .models import SystemSetting, MatchEvent, utcnow

MODES = ('off', 'preview', 'operator', 'both')


def sound_settings(db):
    with db.transaction() as s:
        row = s.get(SystemSetting, 'score_sound_output')
        return {'output': row.value_json.get('output', 'both') if row else 'both'}


def recent_sounds(db, output=None):
    if (output or sound_settings(db)['output']) not in ('preview', 'both'):
        return []
    with db.transaction() as s:
        # Read only small metadata from the newest indexed rows, never scan
        # full match history or deserialize large scoring snapshots per poll.
        events = s.execute(select(MatchEvent.event_id, MatchEvent.match_id,
            MatchEvent.created_at, MatchEvent.event_type).order_by(
            MatchEvent.id.desc()).limit(100))
        cutoff = utcnow()-timedelta(seconds=3)
        kinds = {'POINT','SIDE_OUT','SERVER_CHANGE','GAME_WON','MATCH_WON'}
        return [{'event_id':'score:'+e.event_id, 'match_id':e.match_id,
                 'at':e.created_at.isoformat()+'Z', 'kind':e.event_type} for e in events
                if e.created_at >= cutoff and e.event_type in kinds]



def register(app, db, operator):
    @app.get('/api/score-sounds')
    @operator
    def get_score_sounds():
        return jsonify(sound_settings(db))

    @app.post('/api/score-sounds')
    @operator
    def save_score_sounds():
        output=(request.get_json() or {}).get('output')
        if output not in MODES:
            raise ValueError('Choose Off, Preview speaker, This device, or Both')
        with db.assignment_transaction() as s:
            row=s.get(SystemSetting,'score_sound_output')
            if row is None:
                row=SystemSetting(key='score_sound_output',value_json={});s.add(row)
            row.value_json={'output':output}
        return jsonify(output=output)

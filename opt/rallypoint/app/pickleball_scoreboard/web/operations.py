"""Court phone access, official result corrections, and staff audit routes."""
from .auth_context import host_identity, effective_role
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
from secrets import token_urlsafe
from uuid import uuid4
from flask import jsonify, request, session, render_template, redirect, Response
from sqlalchemy import select
from werkzeug.security import generate_password_hash, check_password_hash
from .models import User, Court, Match, SystemSetting, PhoneAccess, AuditRecord, utcnow
from .club_service import row_dict

PHONE_COMMANDS = {"TEAM_A_POINT", "TEAM_B_POINT", "TEAM_A_SERVICE", "TEAM_B_SERVICE", "TEAM_A_UNDO", "TEAM_B_UNDO", "TEAM_A_TIMEOUT", "TEAM_B_TIMEOUT", "RESUME"}


def register_operations(app, db, club, state, admin, operator, live_authorized):
    def actor():
        if host_identity():
            return {"id": None, "username": "host-admin:" + host_identity()["username"]}
        with db.transaction() as s:
            user = s.get(User, session.get("user_id")) if session.get("user_id") else None
            return {"id": user.id, "username": user.username} if user else None

    @app.after_request
    def audit(response):
        if request.path.startswith('/api/') and request.method == 'POST' and response.status_code < 300 and (host_identity() or session.get('user_id')):
            who = actor()
            values = request.get_json(silent=True) or {}
            safe = {k: values[k] for k in ('name', 'username', 'role', 'device_name', 'court_id', 'reason', 'winner', 'scores') if k in values}
            if values.get('password'):
                safe['password_changed'] = True
            with db.transaction() as s:
                s.add(AuditRecord(actor=who['username'], action='QUEUED' if response.status_code == 202 else 'APPLIED', target=request.path, details=safe))
        return response

    @app.get('/api/audit')
    @admin
    def audit_list():
        with db.transaction() as s:
            return jsonify([row_dict(r) for r in s.scalars(select(AuditRecord).order_by(AuditRecord.id.desc()).limit(200))])

    @app.get('/court/<int:court_id>')
    def phone_page(court_id):
        if not live_authorized():
            return redirect('/login')
        with db.transaction() as s:
            if not s.get(Court, court_id):
                return 'Court not found', 404
        return render_template('page.html', page='phone', csrf=session['csrf'], role=effective_role())

    @app.get('/api/courts/<int:court_id>/qr.svg')
    def qr(court_id):
        if not live_authorized():
            return jsonify(error='Login required'), 401
        with db.transaction() as s:
            if not s.get(Court, court_id):
                return jsonify(error='Court not found'), 404
        import qrcode
        import qrcode.image.svg
        output = BytesIO()
        qrcode.make(request.url_root.rstrip('/') + '/court/' + str(court_id), image_factory=qrcode.image.svg.SvgPathImage).save(output)
        return Response(output.getvalue(), mimetype='image/svg+xml')

    def approved(court_id):
        with db.transaction() as s:
            grant = s.get(PhoneAccess, session.get('phone_grant', ''))
            return bool(grant and grant.court_id == court_id and grant.status == 'APPROVED' and grant.expires_at > utcnow())

    @app.get('/api/courts/<int:court_id>/phone')
    def phone_status(court_id):
        if not live_authorized():
            return jsonify(error='Login required'), 401
        with db.transaction() as s:
            grant = s.get(PhoneAccess, session.get('phone_grant', ''))
            config = s.get(SystemSetting, 'phone_pin_' + str(court_id))
            status = grant.status if grant and grant.court_id == court_id and grant.expires_at > utcnow() else 'VIEW_ONLY'
            return jsonify(request_code=grant.id[:6] if grant and grant.court_id == court_id else None, status=status, enabled=bool(config and config.value_json.get('enabled')), expires_at=(grant.expires_at.isoformat() + 'Z') if status == 'APPROVED' else None)

    @app.post('/api/courts/<int:court_id>/phone/request')
    def phone_request(court_id):
        if not live_authorized():
            return jsonify(error='Login required'), 401
        # Session attempts are bounded; also bound attempts by source address in DB.
        key = 'phone_attempt_' + request.remote_addr[:60]
        with db.transaction() as s:
            rate = s.get(SystemSetting, key, with_for_update=True)
            now = utcnow().timestamp()
            attempts = rate.value_json if rate else {'at': now, 'count': 0}
            if now - attempts['at'] > 60:
                attempts = {'at': now, 'count': 0}
            if attempts['count'] >= 5:
                return jsonify(error='Too many attempts; wait one minute'), 429
            attempts = {**attempts, 'count': attempts['count'] + 1}
            if rate:
                rate.value_json = attempts
            else:
                s.add(SystemSetting(key=key, value_json=attempts))
            config = s.get(SystemSetting, 'phone_pin_' + str(court_id))
            pin = (request.json or {}).get('pin', '')
            if not isinstance(pin, str) or not config or not config.value_json.get('enabled') or not check_password_hash(config.value_json['hash'], pin):
                return jsonify(error='Invalid PIN or phone scoring disabled'), 403
            old = s.get(PhoneAccess, session.get('phone_grant', ''))
            if old:
                old.status = 'REVOKED'
            grant = PhoneAccess(id=token_urlsafe(24), court_id=court_id, expires_at=utcnow() + timedelta(minutes=10))
            s.add(grant)
            session['phone_grant'] = grant.id
        return jsonify(status='PENDING')

    @app.get('/api/courts/<int:court_id>/phone/admin')
    @operator
    def phone_admin(court_id):
        with db.transaction() as s:
            grants = s.scalars(select(PhoneAccess).where(PhoneAccess.court_id == court_id, PhoneAccess.expires_at > utcnow(), PhoneAccess.status.in_(('PENDING', 'APPROVED'))))
            config = s.get(SystemSetting, 'phone_pin_' + str(court_id))
            return jsonify(enabled=bool(config and config.value_json.get('enabled')), requests=[row_dict(g) for g in grants])

    @app.post('/api/courts/<int:court_id>/phone/config')
    @operator
    def phone_config(court_id):
        values = request.json or {}
        enabled = values.get('enabled') is True
        pin = values.get('pin', '')
        if enabled and (not isinstance(pin, str) or len(pin) != 6 or not pin.isascii() or not pin.isdigit()):
            raise ValueError('Set a six-digit court PIN')
        with db.transaction() as s:
            if not s.get(Court, court_id):
                raise ValueError('Court not found')
            key = 'phone_pin_' + str(court_id)
            config = s.get(SystemSetting, key) or SystemSetting(key=key)
            config.value_json = {'enabled': enabled, 'hash': generate_password_hash(pin) if enabled else ''}
            s.add(config)
            for grant in s.scalars(select(PhoneAccess).where(PhoneAccess.court_id == court_id)):
                grant.status = 'REVOKED'
        return jsonify(ok=True)

    @app.post('/api/courts/<int:court_id>/phone/approve')
    @operator
    def phone_approve(court_id):
        with db.transaction() as s:
            grant = s.get(PhoneAccess, request.json.get('id', ''), with_for_update=True)
            if not grant or grant.court_id != court_id or grant.expires_at <= utcnow():
                raise ValueError('Request expired or not found')
            config = s.get(SystemSetting, 'phone_pin_' + str(court_id))
            if request.json.get('approved') is True and (grant.status not in ('PENDING', 'APPROVED') or not config or not config.value_json.get('enabled')):
                raise ValueError('Enable phone scoring and request fresh approval')
            grant.status = 'APPROVED' if request.json.get('approved') is True else 'REVOKED'
            grant.expires_at = utcnow() + timedelta(minutes=60)
        return jsonify(ok=True)

    @app.post('/api/courts/<int:court_id>/phone/commands')
    def phone_command(court_id):
        if not live_authorized() or not approved(court_id):
            return jsonify(error='Staff approval required'), 403
        values = request.json or {}
        name = values.get('name')
        if name not in PHONE_COMMANDS:
            raise ValueError('Unsupported phone control')
        live = state.get_current_state(court_id)
        if not live.get('connected') or not live.get('match_exists', bool(live.get('match_id'))):
            raise ValueError('No online match at this court')
        if type(values.get('revision')) is not int or values['revision'] != live.get('revision'):
            raise ValueError('Score changed; refresh before scoring')
        receipt = state.request('/commands', {'request_id': str(uuid4()), 'name': name, 'payload': {'match_id': live.get('match_id'), 'expected_revision': values['revision'], 'phone_control': True, 'actor': {'username': 'Approved court phone'}}}, state.device_url(court_id))
        return jsonify(receipt), 202

    @app.get('/api/courts/<int:court_id>/phone/receipts/<request_id>')
    def phone_receipt(court_id, request_id):
        if not live_authorized() or not approved(court_id):
            return jsonify(error='Staff approval required'), 403
        from uuid import UUID
        UUID(request_id)
        return jsonify(state.request('/receipts/' + request_id, url=state.device_url(court_id)))

    @app.post('/api/matches/<int:match_id>/correct')
    @operator
    def correct_result(match_id):
        from ..scoring.scoring_engine import ScoringEngine
        from ..scoring.game_state import MatchState
        from ..config import Settings
        values = request.json or {}
        reason = str(values.get('reason', '')).strip()[:500]
        if not reason or values.get('confirmed') is not True:
            raise ValueError('Confirm the correction and give a reason')
        with db.transaction() as s:
            match = s.get(Match, match_id)
            if not match or match.status != 'COMPLETE' or not match.state_json:
                raise ValueError('Only completed results can be corrected')
            saved = deepcopy(match.state_json)
            court_id = match.court_id
        live = state.get_current_state(court_id)
        if not live.get('connected'):
            raise ValueError('Reconnect the court before correcting its result')
        if live.get('sync', {}).get('pending', 0):
            raise ValueError('Wait for court synchronization before correcting history')
        scores = ScoringEngine.validate_scores(values.get('scores'))
        details = {'scores': scores, 'winner': values.get('winner'), 'reason': reason, 'actor': actor(), 'match_id': match_id}
        engine = ScoringEngine(Settings(**saved['settings']), MatchState.from_dict(deepcopy(saved['state'])))
        engine.apply('CORRECT_RESULT', details)
        if live.get('match_id') == match_id:
            if values.get('revision') != live.get('revision'):
                raise ValueError('Reload the current score before correcting')
            details['expected_revision'] = values['revision']
            return jsonify(state.request('/commands', {'name': 'CORRECT_RESULT', 'request_id': str(uuid4()), 'payload': details}, state.device_url(court_id))), 202
        if values.get('record_revision') != saved.get('revision', 0):
            raise ValueError('History result changed; reload before correcting')
        with db.transaction() as s:
            match = s.get(Match, match_id, with_for_update=True)
            if match.state_json != saved:
                raise ValueError('Result changed; reload history')
            saved_before = deepcopy(saved)
            saved['state'] = engine.state.to_dict()
            saved['actor'] = details['actor']
            saved['revision'] = saved.get('revision', 0) + 1
            match.state_json = saved
            match.winner_team = values['winner']
            from .models import Game, MatchEvent
            games = list(s.scalars(select(Game).where(Game.match_id == match_id).order_by(Game.game_number)))
            if games:
                games[-1].team_a_score, games[-1].team_b_score = scores['A'], scores['B']
                games[-1].winner_team = values['winner']
            from .models import Player, MatchPlayer
            s.flush()
            for link in s.scalars(select(MatchPlayer).where(MatchPlayer.match_id == match_id)):
                player = s.get(Player, link.player_id)
                stats = club.statistics(s, player.id)
                player.games_played, player.wins, player.losses = stats['games_won'] + stats['games_lost'], stats['wins'], stats['losses']
            sequence = max([e.sequence_no for e in s.scalars(select(MatchEvent).where(MatchEvent.match_id == match_id))], default=0) + 1
            s.add(MatchEvent(event_id=str(uuid4()), match_id=match_id, sequence_no=sequence, event_type='RESULT_CORRECTED', payload_json={'actor': details['actor'], 'reason': reason, 'before': deepcopy(saved_before), 'snapshot': saved}))
        return jsonify(ok=True)

    return actor

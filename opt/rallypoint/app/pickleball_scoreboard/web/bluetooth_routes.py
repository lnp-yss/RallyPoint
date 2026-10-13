"""Authenticated host Bluetooth management; work happens outside web/scoring."""
import json
import logging
import socket
from flask import jsonify, render_template, request, session
from .auth_context import effective_role


def broker_request(data: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(2)
        connection.connect('/run/rallypoint-bluetooth/control.sock')
        connection.sendall(json.dumps(data).encode() + b'\n')
        with connection.makefile('rb') as response:
            return json.loads(response.readline(1048576))


def register(app, operator):
    @app.get('/settings/bluetooth')
    @operator
    def bluetooth_page():
        return render_template('page.html', page='bluetooth', csrf=session['csrf'],
                               role=effective_role())

    @app.get('/api/bluetooth')
    @operator
    def bluetooth_status():
        try:
            return jsonify(broker_request({'action': 'status'}))
        except (OSError, ValueError):
            return jsonify(available=False, error='Host Bluetooth management is offline.',
                           devices=[], adapter={}, busy=False, job=None)

    @app.post('/api/bluetooth')
    @operator
    def bluetooth_action():
        from ..bluetooth_validation import validate_action
        data = request.get_json() or {}
        action, address = validate_action(data.get('action'), data.get('address', ''))
        try:
            result = broker_request({'action': action, 'address': address})
        except (OSError, ValueError):
            return jsonify(error='Host Bluetooth management is offline.'), 503
        if result.get('error'):
            return jsonify(error=result['error']), result.get('code', 400)
        logging.info('Staff user %s workspace %s requested host Bluetooth %s %s',
                     session.get('user_id'), request.script_root or '/', action, address)
        return jsonify(result), 202
"""Club-scoped display preferences propagated to every Preview screen."""
from flask import jsonify, request
from .models import SystemSetting


def display_settings(db):
    with db.transaction() as s:
        row = s.get(SystemSetting, 'preview_display')
        return {'show_header': row.value_json.get('show_header', True) if row else True}


def register(app, db, operator):
    @app.post('/api/preview-display')
    @operator
    def save_preview_display():
        value = (request.get_json() or {}).get('show_header')
        if type(value) is not bool:
            raise ValueError('show_header must be true or false')
        with db.assignment_transaction() as s:
            row = s.get(SystemSetting, 'preview_display')
            if row is None:
                row = SystemSetting(key='preview_display', value_json={})
                s.add(row)
            row.value_json = {'show_header': value}
        return jsonify(show_header=value)

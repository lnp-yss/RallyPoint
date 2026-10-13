from test_web_club import web, signed_in
from test_application import Application
from pickleball_scoreboard.services.scoreboard_bridge import WebCommand


def test_admin_display_branding_validation_and_permissions(web):
    operator, csrf = signed_in(web,"OPERATOR")
    assert operator.post('/api/settings',json={'scoreboard_header':'No'},headers={'X-CSRF-Token':csrf}).status_code == 403
    admin, csrf = signed_in(web)
    options = admin.get('/api/settings').json['options']
    assert options['scoreboard_header'] == 'RALLY POINT'
    options['scoreboard_header'] = 'COURTSIDE PICKLEBALL'
    assert admin.post('/api/settings',json=options,headers={'X-CSRF-Token':csrf}).status_code == 200
    assert web.extensions['club'].display_branding()['scoreboard_header'] == 'COURTSIDE PICKLEBALL'
    options['scoreboard_header'] = ' '
    assert admin.post('/api/settings',json=options,headers={'X-CSRF-Token':csrf}).status_code == 400
    assert web.extensions['club'].display_branding()['scoreboard_header'] == 'COURTSIDE PICKLEBALL'


def test_branding_updates_live_and_ready_without_scoring_changes(tmp_path):
    app = Application(tmp_path,keyboard=True,windowed=True)
    try:
        app.start()
        app.act('TEAM_A_POINT')
        app.request_start()
        state = app.engine.state.to_dict()
        history = len(app.engine.history)
        app.bridge = object()
        app.web_command(WebCommand('UPDATE_BRANDING',{'scoreboard_header':'CLUB ARENA','club_name':'Garden Club'},'branding'))
        assert app.engine.settings.scoreboard_header == 'CLUB ARENA'
        assert app.engine.settings.club_name == 'Garden Club'
        assert app.dialog.settings.club_name == 'Garden Club'
        assert app.engine.state.to_dict() == state
        assert len(app.engine.history) == history
        assert app.store.settings().scoreboard_header == 'CLUB ARENA'
    finally:
        app.bridge = None
        app.close()

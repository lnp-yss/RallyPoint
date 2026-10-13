from uuid import uuid4
from copy import deepcopy
from sqlalchemy import select,func
from test_web_club import web, roster, payload, signed_in
from pickleball_scoreboard.web.models import AnnouncementEvent,Court,Match


def dispatched(web):
    club=web.extensions['club']; players=roster(web)
    match=club.prepare_match(payload(players));club.reserve_match(match['id'])
    return match


def test_announcement_permissions_and_actual_custom_court(web):
    match=dispatched(web)
    with web.extensions['database'].transaction() as s:s.get(Court,1).name='Championship Court'
    assert web.test_client().get('/settings/announcements').status_code==302
    viewer,_=signed_in(web,'VIEWER')
    assert viewer.get('/api/announcements/matches/'+str(match['id'])).status_code==403
    client,_=signed_in(web,'OPERATOR')
    assert client.get('/settings/announcements').status_code==200
    data=client.get('/api/announcements/matches/'+str(match['id'])).json
    assert data['court_name']=='Championship Court'
    assert [p['name'] for p in data['teams']['A']]==['Dominic','Chel']
    assert [p['name'] for p in data['teams']['B']]==['Mark','Anna']


def test_replay_read_does_not_modify_match_and_logs_are_idempotent(web):
    match=dispatched(web);client,token=signed_in(web,'OPERATOR')
    with web.extensions['database'].transaction() as s:
        before=deepcopy(s.get(Match,match['id']).settings_json)
    data={'event_id':str(uuid4()),'match_id':match['id'],'announcement_type':'REPLAY','playback_status':'STARTED'}
    assert client.post('/api/announcements/events',json=data).status_code==403
    for phase in ['STARTED','COMPLETED','STARTED']:
        data['playback_status']=phase
        assert client.post('/api/announcements/events',json=data,headers={'X-CSRF-Token':token}).status_code==200
    with web.extensions['database'].transaction() as s:
        assert s.scalar(select(func.count()).select_from(AnnouncementEvent))==1
        entry=s.scalar(select(AnnouncementEvent))
        assert entry.playback_status=='COMPLETED' and entry.actor=='OPERATOR' and entry.court_id==1
        assert s.get(Match,match['id']).settings_json==before
        assert s.get(Match,match['id']).status=='STARTING'
    assert len(client.get('/api/announcements/events').json)==1


def test_prepared_match_cannot_be_announced_and_invalid_log_rejected(web):
    players=roster(web);match=web.extensions['club'].prepare_match(payload(players))
    client,token=signed_in(web)
    assert client.get('/api/announcements/matches/'+str(match['id'])).status_code==400
    assert client.post('/api/announcements/events',json={'event_id':str(uuid4()),'announcement_type':'FAKE'},headers={'X-CSRF-Token':token}).status_code==400


def test_start_response_contains_confirmed_database_roster(web,monkeypatch):
    players=roster(web);match=web.extensions['club'].prepare_match(payload(players))
    state=web.extensions['match_state']
    monkeypatch.setattr(state,'request',lambda path,*args,**kwargs:{'match_exists':False} if path=='/state' else {'request_id':str(uuid4()),'status':'PENDING'})
    result=state.start_match(match['id'])
    assert result['announcement']['match_id']==match['id']
    assert result['announcement']['court_name']=='Court 1'
    assert result['announcement']['teams']['A'][0]['name']=='Dominic'

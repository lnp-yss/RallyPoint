"""LAN validation, host-only permissions and asynchronous connection jobs."""
import json
import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response
from test_shared_system import host, login
from pickleball_scoreboard.web import network_settings as network
from scripts import court_device_agent as agent

@pytest.mark.parametrize('value',['rallypoint.local','rallypoint','192.168.2.204','court-2.local'])
def test_valid_addresses(value):
    assert network.address(value) == value
    assert agent.network_address(value) == value

@pytest.mark.parametrize('value',['http://rallypoint.local','8.8.8.8','127.0.0.1','0.0.0.0','-oProxyCommand=x','a;reboot','example.com','192.168.2.204:80','::1',None,''])
def test_invalid_addresses(value):
    for validate in (network.address,agent.network_address):
        with pytest.raises(ValueError): validate(value)

@pytest.fixture
def inventory(tmp_path,monkeypatch):
    path = tmp_path/'inventory.json'
    path.write_text(json.dumps({'1':{'local':True},'2':{'ssh':'rallypoint@court-2.local'}}))
    monkeypatch.setattr(network,'INVENTORY',path)
    return path


def test_host_permissions_queue_and_saved_draft(host,inventory):
    client = Client(host,Response)
    assert client.get('/api/network-settings').status_code == 403
    login(client,'/clubs/alpha')
    assert client.get('/api/network-settings').status_code == 403
    token = login(client,username='host')
    assert client.get('/network-settings').status_code == 200
    data = client.get('/api/network-settings').json
    assert data['clients'][0]['address'] == 'court-2.local'
    payload={'mode':'check','hub_address':'192.168.2.204','clients':{'2':'192.168.2.124'}}
    assert client.post('/api/network-settings',json=payload).status_code == 403
    response=client.post('/api/network-settings',json=payload,headers={'X-CSRF-Token':token})
    assert response.status_code == 202
    assert client.post('/api/network-settings',json=payload,headers={'X-CSRF-Token':token}).status_code == 400
    calls=[]
    def rpc(items,physical,request):
        calls.append((items,physical,request))
        return {'reachable':True}
    assert network.once(host,rpc)
    assert len(calls)==2
    assert calls[1][0]['2']['host_key_alias']=='court-2.local'
    assert calls[1][0]['2']['ssh']=='rallypoint@192.168.2.124'
    assert json.loads(inventory.read_text())['2']['ssh']=='rallypoint@court-2.local'
    assert client.get('/api/network-settings').json['jobs'][0]['status']=='DONE'


def test_apply_checks_before_changes_and_blocks_busy(host,inventory):
    with host.connect() as db:
        db.execute("INSERT INTO network_jobs(id,payload,status,message) VALUES ('job',?,'QUEUED','')",(json.dumps({'mode':'apply','hub_address':'rallypoint.local','clients':{'2':'192.168.2.124'}}),))
    actions=[]
    def busy(items,physical,request):
        actions.append(request['action'])
        raise ValueError('Match in progress')
    network.once(host,busy)
    assert actions==['network_check']
    with host.connect() as db:
        assert db.execute("SELECT status FROM network_jobs WHERE id='job'").fetchone()[0]=='FAILED'
        db.execute("UPDATE network_jobs SET status='QUEUED' WHERE id='job'")
    actions.clear()
    def success(items,physical,request):
        actions.append((physical,request))
        return {'reachable':True,'reconnected':True}
    network.once(host,success)
    assert [a[1]['action'] for a in actions]==['network_check','network_check','network_apply','network_apply']
    assert actions[2][0]==2 and actions[3][0]==1
    assert actions[3][1]['inventory']['2']['host_key_alias']=='court-2.local'


def test_agent_busy_blocks_network_apply_before_restart(monkeypatch):
    monkeypatch.setattr(agent,'network_check',lambda *args: {'reachable':True})
    monkeypatch.setattr(agent,'current',lambda: {'connected':True,'match_exists':True,'state':{'status':'playing'}})
    monkeypatch.setattr(agent.subprocess,'run',lambda *args,**kwargs: pytest.fail('Must not restart active court'))
    with pytest.raises(ValueError,match='Match in progress'):
        agent.network_apply({'hub_address':'192.168.2.204','job_id':'6e1ec4c6-b385-4ba6-a75d-01a07c04aec4'},{'launcher':'client'})


def test_agent_restores_configuration_when_sync_fails(tmp_path,monkeypatch):
    conf=tmp_path/'court-agent.json';conf.write_text('{"launcher":"client","hub_hostname":"rallypoint.local"}')
    monkeypatch.setattr(agent.shutil,'chown',lambda *args,**kwargs: None)
    monkeypatch.setattr(agent,'CONF',conf)
    monkeypatch.setattr(agent,'TUNNEL_UNIT',tmp_path/'tunnel.service')
    monkeypatch.setattr(agent,'BACKUPS',tmp_path/'backups')
    monkeypatch.setattr(agent,'network_check',lambda *args: {'reachable':True})
    old={'connected':True,'data_root':str(tmp_path),'identity':{'device_id':2},'state':{'status':'complete'},'match_exists':True,'sync':{'online':True,'pending':0}}
    monkeypatch.setattr(agent,'current',lambda: old)
    monkeypatch.setattr(agent,'valid_root',lambda value: tmp_path)
    monkeypatch.setattr(agent,'saved_idle',lambda root: None)
    commands=[];locks=[]
    monkeypatch.setattr(agent,'maintenance',lambda root,enabled: locks.append(enabled))
    monkeypatch.setattr(agent.subprocess,'run',lambda cmd,**kwargs: commands.append(cmd))
    def fail(): raise ValueError('Sync failed')
    monkeypatch.setattr(agent,'wait_current',fail)
    with pytest.raises(ValueError,match='Sync failed'):
        agent.network_apply({'hub_address':'192.168.2.204','job_id':'6e1ec4c6-b385-4ba6-a75d-01a07c04aec4'},{'launcher':'client','hub_hostname':'rallypoint.local'})
    assert json.loads(conf.read_text())['hub_hostname']=='rallypoint.local'
    assert locks==[True,False]
    assert ['systemctl','restart',agent.SERVICE] in commands


def test_network_repair_allows_queued_scores_but_never_active_match():
    agent.network_idle({'connected':True,'match_exists':True,'state':{'status':'complete'},'sync':{'pending':5,'online':False}})
    with pytest.raises(ValueError,match='Match in progress'):
        agent.network_idle({'connected':True,'match_exists':True,'state':{'status':'playing'},'sync':{'pending':0}})

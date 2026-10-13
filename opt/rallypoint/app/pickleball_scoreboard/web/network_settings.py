"""Host-only LAN addresses and serialized network jobs, outside scoring threads."""
import ipaddress
import json
import re
from pathlib import Path
from uuid import uuid4
from flask import jsonify, request, render_template, session, redirect

INVENTORY = Path("/etc/rallypoint/court-manager/inventory.json")


def address(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("Enter a hostname or local IPv4 address without http:// or a port")
    value = value.strip().lower()
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*", value):
            raise ValueError("Invalid hostname")
        if '.' in value and not value.endswith('.local'):
            raise ValueError("Use a local hostname (.local) or local IPv4 address")
    else:
        if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError("Use a private LAN IPv4 address")
    return value


def hub_url():
    path = Path("/etc/rallypoint/web.env")
    if path.exists():
        for line in path.read_text().splitlines():
            if line.startswith("RALLYPOINT_HUB_PUBLIC_URL="):
                return line.split("=",1)[1].rstrip("/")
    return "http://rallypoint.local"


def initialize(host):
    with host.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS network_jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")


def register(host, app):
    initialize(host)

    @app.get('/network-settings')
    def network_page():
        if not host.administrator(app):
            return redirect('/login?next=/network-settings')
        return render_template('page.html', page='network-settings', csrf=session['csrf'], role=session.get('role'))

    @app.get('/api/network-settings')
    def network_get():
        if not host.administrator(app):
            return jsonify(error='Host administrator access required'), 403
        inventory = json.loads(INVENTORY.read_text()) if INVENTORY.exists() else {}
        from urllib.parse import urlparse
        hub = urlparse(app.config.get('HUB_PUBLIC_URL') or 'http://rallypoint.local').hostname
        with host.connect() as db:
            names = {str(row['id']): row['name'] for row in db.execute('SELECT id,name FROM physical_courts')}
            jobs = [dict(row) for row in db.execute('SELECT id,status,message,created_at FROM network_jobs ORDER BY created_at DESC,rowid DESC LIMIT 10')]
            last = db.execute('SELECT payload FROM network_jobs ORDER BY rowid DESC LIMIT 1').fetchone()
            draft = json.loads(last['payload']) if last else None
            if draft:
                draft = {key:draft[key] for key in ('hub_address','clients')}
        return jsonify(hub_address=hub, draft=draft, clients=[{'id':key,'name':names.get(key,'Device '+key),'address':item['ssh'].split('@',1)[1]} for key,item in inventory.items() if not item.get('local')], jobs=jobs)

    @app.post('/api/network-settings')
    def network_post():
        if not host.administrator(app):
            return jsonify(error='Host administrator access required'), 403
        data = request.get_json() or {}
        mode = data.get('mode')
        if mode not in ('check','apply'):
            raise ValueError('Choose check or apply')
        inventory = json.loads(INVENTORY.read_text())
        clients = data.get('clients', {})
        expected = {key for key,item in inventory.items() if not item.get('local')}
        if not isinstance(clients,dict) or set(clients) != expected:
            raise ValueError('Reload the page: paired devices changed')
        payload = {'mode':mode,'hub_address':address(data.get('hub_address')),'clients':{key:address(value) for key,value in clients.items()},'actor':'host-admin:'+str(session['user_id'])}
        with host.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM network_jobs WHERE status IN ('QUEUED','RUNNING')").fetchone() or db.execute("SELECT 1 FROM court_moves WHERE status IN ('QUEUED','MOVING','NEEDS_REVIEW')").fetchone():
                raise ValueError('A network change or court move is already pending')
            job = str(uuid4())
            db.execute('INSERT INTO network_jobs(id,payload,status,message) VALUES (?,?,?,?)',(job,json.dumps(payload),'QUEUED','Waiting for connection checks'))
        return jsonify(id=job,status='QUEUED'),202


def once(host, transport):
    """One bounded job; matching/scoring and web requests continue independently."""
    with host.connect() as db:
        row = db.execute("SELECT * FROM network_jobs WHERE status IN ('QUEUED','RUNNING') ORDER BY created_at,rowid LIMIT 1").fetchone()
        if not row:
            return False
        db.execute("UPDATE network_jobs SET status='RUNNING',message='Checking paired devices' WHERE id=?",(row['id'],))
    data = json.loads(row['payload'])
    changes_started = False
    try:
        inventory = json.loads(INVENTORY.read_text())
        candidate = json.loads(json.dumps(inventory))
        for key,value in data['clients'].items():
            old = candidate[key]['ssh'].split('@',1)
            candidate[key]['host_key_alias'] = candidate[key].get('host_key_alias',old[1])
            candidate[key]['ssh'] = old[0]+'@'+value
        # Verify exact pinned devices through the proposed addresses before saving.
        def invoke(key, action, **extra):
            item = candidate[str(key)]
            label = 'Host HDMI device' if item.get('local') else 'Court device ' + str(key) + ' (' + item['ssh'].split('@',1)[1] + ')'
            with host.connect() as db:
                db.execute("UPDATE network_jobs SET message=? WHERE id=?",(label + ': ' + ('checking connection' if action=='network_check' else 'applying settings'),row['id']))
            try:
                return transport(candidate,int(key),{'action':action,'hub_address':data['hub_address'],**extra})
            except Exception as error:
                raise ValueError(label + ': ' + str(error)) from error

        for key in candidate:
            reply = invoke(key,'network_check',idle_required=data['mode']=='apply')
            if not reply.get('reachable'):
                raise ValueError('Device '+key+' could not verify the host')
        if data['mode'] == 'apply':
            changes_started = True
            # Clients first; local host commits the inventory and public URL last.
            for key,item in candidate.items():
                if not item.get('local'):
                    invoke(key,'network_apply',job_id=row['id'])
            local = next(int(key) for key,item in candidate.items() if item.get('local'))
            invoke(local,'network_apply',inventory=candidate,job_id=row['id'])
        status,message = 'DONE',('Connection check passed. Addresses are reachable and device keys match.' if data['mode']=='check' else 'Network settings applied. Clients reconnected; refresh this page.')
    except Exception as exc:
        status,message = 'FAILED',str(exc)+(' No active settings changed. Entered addresses are saved as a draft.' if not changes_started else ' The failing device restores its previous settings; any already reconnected clients retain theirs. Correct the address and retry.')
    with host.connect() as db:
        db.execute('UPDATE network_jobs SET status=?,message=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',(status,message,row['id']))
    return True

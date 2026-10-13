#!/usr/bin/env python3
"""Restricted local/forced-SSH court configuration agent. Standard library only."""

import json, os, re, shutil, socket, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID

ROOT = Path("/opt/rallypoint/app")
CONF = Path("/etc/rallypoint/court-agent.json")
ENV = Path("/etc/rallypoint/managed-court.env")
DROP = Path("/etc/systemd/system/rallypoint-scoreboard.service.d/zz-managed-court.conf")
STATE = Path("/etc/rallypoint/court-agent-state.json")
SERVICE = "rallypoint-scoreboard.service"
BACKUPS = Path("/opt/rallypoint/backups/court-switches")
TUNNEL_UNIT = Path("/etc/systemd/system/rallypoint-tunnel.service")


def sync_directory(path):
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic(path, data, mode=0o600, owner=None, group=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(data)
        stream.flush()
        temporary.chmod(mode)
        if owner is not None:
            shutil.chown(temporary, user=owner, group=group)
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    sync_directory(path.parent)


def restore_files(backup):
    for file, key in ((ENV, "env"), (DROP, "drop")):
        copy = backup / key
        if copy.exists():
            atomic(
                file,
                copy.read_text(),
                copy.stat().st_mode & 0o777,
                owner="root",
                group="rallypoint" if file == ENV else "root",
            )
        else:
            file.unlink(missing_ok=True)
            sync_directory(file.parent)


def environment():
    values = {}
    for file in ("web.env", "client.env", "dwg-court1.env", "managed-court.env"):
        path = Path("/etc/rallypoint") / file
        if path.exists():
            for line in path.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    values[key] = value.strip().strip('"').strip("'")
    return values


def current():
    env = environment()
    with urlopen(
        Request(
            "http://127.0.0.1:8765/state",
            headers={
                "Authorization": "Bearer " + env.get("RALLYPOINT_BRIDGE_TOKEN", "")
            },
        ),
        timeout=2,
    ) as response:
        result = json.load(response)
    result["data_root"] = env.get("RALLYPOINT_SCOREBOARD_DATA_ROOT", str(ROOT))
    return result


def wait_current():
    deadline = time.monotonic() + 20
    while True:
        try:
            return current()
        except OSError:
            if time.monotonic() >= deadline:
                raise ValueError("Scoreboard did not reconnect")
            time.sleep(0.2)


def require_idle(snapshot):
    if not snapshot.get("connected"):
        raise ValueError("Scoreboard is not connected")
    if snapshot.get("match_exists", bool(snapshot.get("match_id"))) and snapshot.get(
        "state", {}
    ).get("status") not in ("complete", "ended"):
        raise ValueError("Match in progress. Finish or end it before switching.")
    if snapshot.get("sync", {}).get("pending", 0):
        raise ValueError("Score updates are still pending. Try again shortly.")


def saved_idle(root):
    path = root / "data/current_match.json"
    if path.exists() and json.loads(path.read_text())["state"]["status"] not in (
        "complete",
        "ended",
    ):
        raise ValueError(
            "An unfinished saved match prevents switching. Resume and finish it first."
        )


def maintenance(root, enabled):
    path = root / "data/court_switch.lock"
    if enabled:
        atomic(path, "Court is moving between clubs. Please wait.\n")
    else:
        path.unlink(missing_ok=True)


def valid_root(value):
    root = Path(value).resolve()
    if root != ROOT and ROOT / "device-workspaces" not in root.parents:
        raise ValueError("Invalid device storage path")
    return root


def checked(request, settings):
    job = str(UUID(request["job_id"]))
    profile = str(request["profile"])
    if profile != "legacy" and not re.fullmatch("[a-z][a-z0-9-]{1,39}", profile):
        raise ValueError("Invalid club profile")
    token = request["token"]
    if not re.fullmatch("[A-Za-z0-9_-]{32,128}", token):
        raise ValueError("Invalid device token")
    device = int(request["device_id"])
    court = int(request["court_id"])
    number = int(request["court_number"])
    if not (device > 0 and court > 0 and 1 <= number <= 99):
        raise ValueError("Invalid court assignment")
    url = urlparse(request["hub_url"])
    if (
        url.scheme != "http"
        or url.hostname not in ("127.0.0.1", settings.get("hub_hostname", "rallypoint.local"), settings["hub_ip"])
        or url.port not in (None, 80)
        or url.username
        or url.query
        or url.fragment
    ):
        raise ValueError("Unsupported club server URL")
    expected = "" if profile == "legacy" else "/clubs/" + profile
    if url.path.rstrip("/") != expected:
        raise ValueError("Club URL does not match profile")
    for key in ("club_name", "court_name"):
        if (
            not isinstance(request[key], str)
            or not 1 <= len(request[key]) <= 100
            or "\n" in request[key]
            or "\r" in request[key]
        ):
            raise ValueError("Invalid display name")
    return job, profile


def result(snapshot):
    return {
        "connected": snapshot.get("connected"),
        "match_exists": snapshot.get("match_exists", bool(snapshot.get("match_id"))),
        "view": snapshot.get("view"),
        "hardware": snapshot.get("hardware", {}),
        "identity": snapshot.get("identity"),
        "club_name": snapshot.get("settings", {}).get("club_name"),
        "status": snapshot.get("state", {}).get("status"),
        "sync": snapshot.get("sync"),
        "version": 1,
    }


def network_address(value):
    import ipaddress
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ValueError("Invalid network address")
    value = value.strip().lower()
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*", value) or ('.' in value and not value.endswith('.local')):
            raise ValueError("Use a local hostname or private IPv4 address")
    else:
        if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
            raise ValueError("Use a private LAN IPv4 address")
    return value


def network_idle(snapshot):
    """Keep unfinished games intact; queued scores can flush after repair."""
    if not snapshot.get('connected'):
        raise ValueError('Scoreboard is not connected locally')
    if snapshot.get('match_exists',bool(snapshot.get('match_id'))) and snapshot.get('state',{}).get('status') not in ('complete','ended'):
        raise ValueError('Match in progress. Finish or end it before changing network settings')


def network_check(request, settings):
    import ipaddress, shlex
    address = network_address(request.get('hub_address'))
    resolved = {item[4][0] for item in socket.getaddrinfo(address, 80, family=socket.AF_INET)}
    if not resolved or any(not ipaddress.ip_address(item).is_private or ipaddress.ip_address(item).is_loopback for item in resolved):
        raise ValueError("Host must resolve to a private LAN address")
    if request.get('idle_required'):
        network_idle(current())
    with urlopen('http://' + address + '/login', timeout=4) as response:
        if response.status != 200:
            raise ValueError("Host web server is unavailable")
    if settings['launcher'] != 'hub':
        unit = TUNNEL_UNIT.read_text()
        line = next(line for line in unit.splitlines() if line.startswith('ExecStart='))
        parts = shlex.split(line.split('=',1)[1])
        old = parts[-1].split('@',1)
        alias = next((parts[i+1].split('=',1)[1] for i,p in enumerate(parts[:-1]) if p == '-o' and parts[i+1].startswith('HostKeyAlias=')), old[1])
        key = parts[parts.index('-i')+1]
        # The tunnel key permits forwarding only, so verify authentication
        # without requesting a shell command or opening forwarding ports.
        import signal
        command = ['runuser','-u','rallypoint','--','/usr/bin/ssh','-v','-N','-i',key,'-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','StrictHostKeyChecking=yes','-o','CheckHostIP=no','-o','ConnectTimeout=4','-o','HostKeyAlias='+alias,old[0]+'@'+address]
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True) as process:
            try:
                _, output = process.communicate(timeout=7)
                if process.returncode != 0:
                    raise ValueError('Host SSH verification failed. Check address and trusted host key')
            except subprocess.TimeoutExpired:
                # End the whole verifier process group, including the SSH child.
                os.killpg(process.pid,signal.SIGTERM)
                try:
                    _, output = process.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL)
                    _, output = process.communicate()
                if b'Authenticated to ' not in output:
                    raise ValueError('Host SSH verification timed out. Check address and trusted host key')
    return {'reachable': True}


def network_apply(request, settings):
    """Back up and reconnect a single idle device, restoring it on failure."""
    import shlex
    address = network_address(request.get('hub_address'))
    job = str(UUID(request.get('job_id','')))
    network_check({**request,'idle_required':True},settings)
    old = current()
    network_idle(old)
    root = valid_root(old['data_root'])
    inventory_path = CONF.parent / 'court-manager/inventory.json'
    candidate = request.get('inventory')
    if candidate is not None:
        if settings['launcher'] != 'hub':
            raise ValueError('Only the host can change paired addresses')
        existing = json.loads(inventory_path.read_text())
        if set(existing) != set(candidate):
            raise ValueError('Paired inventory changed')
        for key,item in candidate.items():
            prior = existing[key]
            if prior.get('local'):
                if item != prior:
                    raise ValueError('Local host binding cannot change')
            else:
                user, destination = item['ssh'].split('@',1)
                if user != prior['ssh'].split('@',1)[0] or item.get('host_key_alias') != prior.get('host_key_alias',prior['ssh'].split('@',1)[1]):
                    raise ValueError('SSH user and trusted device key cannot change')
                network_address(destination)
    paths = [CONF, *CONF.parent.glob('*.env')]
    unit = TUNNEL_UNIT
    if unit.exists(): paths.append(unit)
    if candidate is not None: paths.append(inventory_path)
    backup = BACKUPS / ('network-'+job)
    backup.mkdir(parents=True,exist_ok=True,mode=0o700)
    originals = {path:(path.read_text(),path.stat()) for path in paths}
    for index,(path,(text,stat)) in enumerate(originals.items()):
        atomic(backup/str(index),text)
    atomic(backup/'manifest.json',json.dumps([str(path) for path in originals]))
    maintenance(root,True)
    try:
        subprocess.run(['systemctl','stop',SERVICE],check=True)
        saved_idle(root)
        for path,(text,stat) in originals.items():
            if path.suffix == '.env':
                lines = []
                for line in text.splitlines():
                    if line.startswith(('RALLYPOINT_HUB_URL=','RALLYPOINT_HUB_PUBLIC_URL=')):
                        key,value = line.split('=',1)
                        line = key+'=http://'+address+urlparse(value).path.rstrip('/')
                    lines.append(line)
                if path.name == 'web.env' and settings['launcher'] == 'hub' and not any(line.startswith('RALLYPOINT_HUB_PUBLIC_URL=') for line in lines):
                    lines.append('RALLYPOINT_HUB_PUBLIC_URL=http://'+address)
                atomic(path,'\n'.join(lines)+'\n',stat.st_mode & 0o777,stat.st_uid,stat.st_gid)
        settings['hub_hostname'] = address
        atomic(CONF,json.dumps(settings))
        if unit.exists():
            text,stat = originals[unit]
            lines = text.splitlines()
            for i,line in enumerate(lines):
                if not line.startswith('ExecStart='): continue
                parts = shlex.split(line.split('=',1)[1]);old_address=parts[-1].split('@',1)
                if not any('HostKeyAlias=' in part for part in parts):
                    parts[-1:-1] = ['-o','HostKeyAlias='+old_address[1]]
                parts[-1] = old_address[0]+'@'+address
                lines[i] = 'ExecStart='+shlex.join(parts)
            atomic(unit,'\n'.join(lines)+'\n',stat.st_mode & 0o777,stat.st_uid,stat.st_gid)
        if candidate is not None:
            stat = originals[inventory_path][1]
            atomic(inventory_path,json.dumps(candidate),stat.st_mode & 0o777,stat.st_uid,stat.st_gid)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        if unit.exists(): subprocess.run(['systemctl','restart','rallypoint-tunnel'],check=True)
        subprocess.run(['systemctl','start',SERVICE],check=True)
        fresh = wait_current()
        deadline = time.monotonic() + 20
        while not fresh.get('connected') or not fresh.get('sync',{}).get('online',False) or fresh.get('sync',{}).get('pending',0):
            if time.monotonic() >= deadline:
                raise ValueError('Device could not sync with the host; restoring previous settings')
            time.sleep(.2)
            fresh = current()
        if fresh.get('identity') != old.get('identity'):
            raise ValueError('Device identity changed unexpectedly')
        if settings['launcher'] == 'hub':
            subprocess.run(['systemctl','restart','rallypoint-web','rallypoint-matchmaking'],check=True)
        return {'reconnected':True,'backup':str(backup)}
    except Exception:
        for path,(text,stat) in originals.items():
            atomic(path,text,stat.st_mode & 0o777,stat.st_uid,stat.st_gid)
        subprocess.run(['systemctl','daemon-reload'],check=True)
        if unit.exists(): subprocess.run(['systemctl','restart','rallypoint-tunnel'],check=True)
        subprocess.run(['systemctl','restart',SERVICE],check=True)
        if settings['launcher'] == 'hub':
            subprocess.run(['systemctl','restart','rallypoint-web','rallypoint-matchmaking'],check=True)
        raise
    finally:
        maintenance(root,False)


def run(request):
    settings = json.loads(CONF.read_text())
    action = request.get("action")
    if action == "inspect":
        return result(current())
    if action == "network_check":
        return network_check(request, settings)
    if action == "network_apply":
        return network_apply(request, settings)
    job = str(UUID(request.get("job_id", "")))
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if action in ("finalize", "rollback"):
        if state.get("job_id") != job:
            if action == "rollback" and state.get("phase") not in (
                "PREPARING",
                "APPLIED",
            ):
                return {"restored": True, "no_change": True}
            raise ValueError("Unknown move; cannot restore unrelated configuration")
        oldroot = valid_root(state["old_root"])
        newroot = valid_root(state["new_root"])
        if action == "rollback":
            if state.get("phase") == "ROLLED_BACK":
                return {"restored": True}
            try:
                require_idle(current())
            except OSError:
                saved_idle(newroot)
            subprocess.run(["systemctl", "stop", SERVICE], check=True)
            backup = Path(state["backup"])
            restore_files(backup)
            maintenance(oldroot, False)
            maintenance(newroot, False)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "start", SERVICE], check=True)
            wait_current()
            state["phase"] = "ROLLED_BACK"
            atomic(STATE, json.dumps(state))
            return {"restored": True}
        maintenance(oldroot, False)
        maintenance(newroot, False)
        state["phase"] = "DONE"
        atomic(STATE, json.dumps(state))
        return {"released": True}
    if action != "apply":
        raise ValueError("Unsupported operation")
    job, profile = checked(request, settings)
    if state.get("job_id") == job and state.get("phase") in ("APPLIED", "DONE"):
        try:
            return result(current())
        except OSError:
            subprocess.run(["systemctl", "start", SERVICE], check=True)
            return result(wait_current())
    if state.get("job_id") == job and state.get("phase") == "PREPARING":
        try:
            fresh = current()
            if (
                environment().get("RALLYPOINT_BRIDGE_TOKEN") == request["token"]
                and int(fresh.get("identity", {}).get("court_id", 0))
                == int(request["court_id"])
                and int(fresh.get("identity", {}).get("device_id", 0))
                == int(request["device_id"])
            ):
                state["phase"] = "APPLIED"
                atomic(STATE, json.dumps(state))
                return result(fresh)
        except OSError:
            pass
        # Recover a power loss before restart using only the recorded files.
        restore_files(Path(state["backup"]))
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "restart", SERVICE], check=True)
        wait_current()
    old = current()
    require_idle(old)
    oldroot = valid_root(old["data_root"])
    newroot = (
        ROOT
        if profile == "legacy"
        and int(request["court_number"]) == int(settings["original_court_number"])
        else ROOT
        / "device-workspaces"
        / f"{profile}-court{int(request['court_number'])}"
    )
    saved_idle(newroot)
    backup = BACKUPS / job
    backup.mkdir(parents=True, exist_ok=True, mode=0o700)
    for file, key in ((ENV, "env"), (DROP, "drop")):
        if state.get("job_id") != job and file.exists():
            shutil.copy2(file, backup / key)
            with (backup / key).open("rb") as saved:
                os.fsync(saved.fileno())
    sync_directory(backup)
    sync_directory(backup.parent)
    state = {
        "job_id": job,
        "old_root": str(oldroot),
        "new_root": str(newroot),
        "backup": str(backup),
        "phase": "PREPARING",
    }
    atomic(STATE, json.dumps(state))
    maintenance(oldroot, True)
    try:
        require_idle(current())
        subprocess.run(["systemctl", "stop", SERVICE], check=True)
        saved_idle(oldroot)  # Catch a match started between the first check and stop.
        (newroot / "data").mkdir(parents=True, exist_ok=True)
        settings_file = newroot / "data/settings.json"
        hardware = (
            json.loads(settings_file.read_text())
            if settings_file.exists()
            else json.loads((oldroot / "data/settings.json").read_text())
        )
        hardware.update(
            club_name=request["club_name"], court_name=request["court_name"]
        )
        atomic(
            settings_file,
            json.dumps(hardware, indent=2),
            owner="rallypoint",
            group="rallypoint",
        )
        shutil.chown(newroot, user="rallypoint", group="rallypoint")
        shutil.chown(newroot / "data", user="rallypoint", group="rallypoint")
        maintenance(newroot, True)
        env = (
            "\n".join(
                [
                    "RALLYPOINT_BRIDGE=1",
                    "RALLYPOINT_BRIDGE_PORT=8765",
                    "RALLYPOINT_BRIDGE_TOKEN=" + request["token"],
                    "RALLYPOINT_DATABASE_URL=",
                    "RALLYPOINT_HUB_URL=" + request["hub_url"].rstrip("/"),
                    "RALLYPOINT_DEVICE_ID=" + str(int(request["device_id"])),
                    "RALLYPOINT_COURT_ID=" + str(int(request["court_id"])),
                    "RALLYPOINT_SCOREBOARD_DATA_ROOT=" + str(newroot),
                ]
            )
            + "\n"
        )
        atomic(ENV, env, 0o640, owner="root", group="rallypoint")
        launch = (
            "/usr/local/bin/rallypoint-hub-scoreboard"
            if settings["launcher"] == "hub"
            else "/usr/bin/python3 -m pickleball_scoreboard.main --data-root ${RALLYPOINT_SCOREBOARD_DATA_ROOT}"
        )
        atomic(
            DROP,
            "[Service]\nEnvironmentFile=/etc/rallypoint/managed-court.env\nExecStart=\nExecStart="
            + launch
            + "\n",
            0o644,
        )
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "start", SERVICE], check=True)
        deadline = time.monotonic() + 20
        while True:
            try:
                fresh = current()
                if int(fresh.get("identity", {}).get("device_id", 0)) != int(
                    request["device_id"]
                ) or int(fresh.get("identity", {}).get("court_id", 0)) != int(
                    request["court_id"]
                ):
                    raise ValueError("Scoreboard identity has not updated")
                break
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise ValueError("Scoreboard did not reconnect after switching")
                time.sleep(0.2)
        state["phase"] = "APPLIED"
        atomic(STATE, json.dumps(state))
        return result(fresh)
    except Exception:
        # Worker invokes rollback; allow its RPC to find the precise prior state.
        if subprocess.run(["systemctl", "is-active", "--quiet", SERVICE]).returncode:
            maintenance(oldroot, False)
            restore_files(backup)
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "start", SERVICE], check=True)
        raise


def main():
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("Request too large")
        reply = run(json.loads(raw))
        print(json.dumps({"ok": True, **reply}))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

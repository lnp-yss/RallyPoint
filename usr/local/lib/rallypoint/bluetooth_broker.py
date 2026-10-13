"""Local Bluetooth broker. Only allowlisted operations over a Unix socket."""
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import re
import socket
import socketserver
import subprocess
import threading
import time

MAC = re.compile(r'^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$')
OPERATIONS = {'enable', 'scan', 'pair', 'connect', 'disconnect', 'forget'}
SOCKET = '/run/rallypoint-bluetooth/control.sock'
LOG = logging.getLogger(__name__)


def validate(action: str, address: str = '') -> tuple[str, str]:
    if action not in OPERATIONS:
        raise ValueError('Unsupported Bluetooth action')
    if action not in {'enable', 'scan'} and not MAC.fullmatch(address):
        raise ValueError('Select a valid Bluetooth device')
    return action, address.upper()


def command(*args: str, timeout: int = 10, allow_failure: bool = False) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                            env={'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'})
    output = result.stdout + result.stderr
    output = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', output)
    if not allow_failure and (result.returncode or 'Failed' in output
                              or 'org.bluez.Error' in output):
        raise RuntimeError(output.strip()[-500:] or 'Bluetooth operation failed')
    return output


def details(text: str) -> dict:
    result = {}
    for line in text.splitlines():
        if ':' in line:
            key, value = line.strip().split(':', 1)
            result[key] = value.strip()
    return result


class BluetoothBroker:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {'available': True, 'hostname': socket.gethostname(),
                      'adapter': {}, 'devices': [], 'busy': False, 'job': None,
                      'error': '', 'updated_at': 0}
        self.refreshing = False

    def refresh(self) -> None:
        try:
            show = command('/usr/bin/bluetoothctl', 'show')
            adapter = details(show)
            if 'Controller ' not in show:
                raise RuntimeError('No Bluetooth adapter found on the host')
            devices = []
            listed = command('/usr/bin/bluetoothctl', 'devices')
            for address, name in re.findall(r'^Device ([0-9A-F:]{17}) (.*)$', listed, re.M)[:30]:
                info = details(command('/usr/bin/bluetoothctl', 'info', address,
                                       timeout=3, allow_failure=True))
                devices.append({'address': address, 'name': info.get('Name', name),
                                'paired': info.get('Paired') == 'yes',
                                'trusted': info.get('Trusted') == 'yes',
                                'connected': info.get('Connected') == 'yes',
                                'icon': info.get('Icon', '')})
            devices.sort(key=lambda d: (not d['connected'], not d['paired'], d['name'].lower()))
            with self.lock:
                self.state.update(adapter={'name': adapter.get('Alias', ''),
                    'powered': adapter.get('Powered') == 'yes',
                    'discovering': adapter.get('Discovering') == 'yes'},
                    devices=devices, error='', available=True, updated_at=time.time())
        except Exception as error:
            LOG.warning('Bluetooth status failed: %s', error)
            with self.lock:
                self.state.update(error=str(error), available=False, updated_at=time.time())
        finally:
            with self.lock:
                self.refreshing = False

    def status(self) -> dict:
        with self.lock:
            if (time.time() - self.state['updated_at'] > 5
                    and not self.state['busy'] and not self.refreshing):
                self.refreshing = True
                threading.Thread(target=self.refresh, daemon=True).start()
            return deepcopy(self.state)

    def submit(self, action: str, address: str) -> dict:
        action, address = validate(action, address)
        with self.lock:
            if self.state['busy'] or self.refreshing:
                raise BlockingIOError('Bluetooth is busy. Please try again shortly.')
            self.state.update(busy=True, job={'action': action, 'address': address,
                                            'status': 'RUNNING', 'message': 'Working…'})
        threading.Thread(target=self.execute, args=(action, address), daemon=True).start()
        return {'accepted': True}

    def execute(self, action: str, address: str) -> None:
        try:
            if action in {'enable', 'scan'}:
                command('/usr/sbin/rfkill', 'unblock', 'bluetooth')
                for attempt in range(3):
                    try:
                        command('/usr/bin/bluetoothctl', 'power', 'on')
                        break
                    except RuntimeError:
                        if attempt == 2:
                            raise
                        time.sleep(1)
                if action == 'scan':
                    command('/usr/bin/bluetoothctl', '--timeout', '12', 'scan', 'on', timeout=16)
                    command('/usr/bin/bluetoothctl', 'scan', 'off', allow_failure=True)
            elif action == 'pair':
                command('/usr/bin/bluetoothctl', '--agent', 'NoInputNoOutput',
                        '--timeout', '20', 'pair', address, timeout=24)
                command('/usr/bin/bluetoothctl', 'trust', address)
            elif action == 'forget':
                command('/usr/bin/bluetoothctl', 'remove', address)
            else:
                command('/usr/bin/bluetoothctl', '--timeout', '15', action, address, timeout=19)
            if action in {'pair', 'connect', 'disconnect'}:
                info = details(command('/usr/bin/bluetoothctl', 'info', address))
                expected = {'pair': ('Paired', 'yes'), 'connect': ('Connected', 'yes'),
                            'disconnect': ('Connected', 'no')}
                key, value = expected[action]
                if info.get(key) != value:
                    raise RuntimeError('Device did not confirm ' + action + '. Check pairing mode and range.')
            message = {'enable': 'Host Bluetooth enabled.', 'scan': 'Scan complete. Select a device below.',
                       'pair': 'Device paired and trusted. Select Connect to use it.',
                       'connect': 'Device connected.', 'disconnect': 'Device disconnected.',
                       'forget': 'Device removed.'}[action]
            status = 'DONE'
            LOG.info('Bluetooth %s %s completed', action, address)
        except Exception as error:
            status, message = 'FAILED', str(error)
            LOG.warning('Bluetooth %s %s failed: %s', action, address, error)
        finally:
            # Scan can fail partway through. Never leave discovery running.
            if action == 'scan':
                try:
                    command('/usr/bin/bluetoothctl', 'scan', 'off', allow_failure=True)
                except Exception:
                    pass
        self.refresh()
        with self.lock:
            self.state.update(busy=False, job={'action': action, 'address': address,
                                              'status': status, 'message': message})


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        try:
            data = json.loads(self.rfile.readline(4097))
            broker = self.server.broker
            if data.get('action') == 'status':
                result = broker.status()
            else:
                result = broker.submit(data.get('action', ''), data.get('address', ''))
        except BlockingIOError as error:
            result = {'error': str(error), 'code': 409}
        except (ValueError, TypeError) as error:
            result = {'error': str(error), 'code': 400}
        self.wfile.write(json.dumps(result).encode() + b'\n')


def main():
    import grp
    logging.basicConfig(level=logging.INFO)
    path = Path(SOCKET)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    with socketserver.ThreadingUnixStreamServer(SOCKET, Handler) as server:
        server.daemon_threads = True
        server.broker = BluetoothBroker()
        os.chown(path, 0, grp.getgrnam('rallypoint').gr_gid)
        os.chmod(path, 0o660)
        server.serve_forever()


if __name__ == '__main__':
    main()
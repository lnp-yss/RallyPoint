"""Protected offline application/configuration/database backups and restore checks."""
from contextlib import closing
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile


def backup(application, destination, configuration=None, database=None):
    application, destination = Path(application).resolve(), Path(destination).resolve()
    if destination == application or application in destination.parents:
        raise ValueError("Backup destination must be outside the application")
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')
    target = destination / ('rallypoint-' + stamp)
    target.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory() as staging:
        staging = Path(staging)
        sqlite_files = []
        for path in application.rglob('*'):
            if any(part in ('.venv', '.venv-web', '.venv-dev', '.git', 'artifacts', '__pycache__') for part in path.relative_to(application).parts):
                continue
            if path.is_file() and path.suffix in ('.db', '.sqlite3'):
                relative = path.relative_to(application)
                copy = staging / relative
                copy.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect('file:' + path.as_posix() + '?mode=ro', uri=True)) as source, closing(sqlite3.connect(copy)) as dest:
                    source.backup(dest)
                    if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise RuntimeError('SQLite backup integrity check failed')
                sqlite_files.append((copy, relative))
        with tarfile.open(target / 'application.tar.gz', 'w:gz', dereference=True) as archive:
            def include(info):
                if not (info.isfile() or info.isdir()):
                    return None
                if any(part in ('.venv', '.venv-web', '.venv-dev', '.git', 'artifacts', '__pycache__') for part in Path(info.name).parts):
                    return None
                if info.name.endswith(('.db', '.sqlite3', '-wal', '-shm')):
                    return None
                return info
            archive.add(application, arcname='app', filter=include)
            for copy, relative in sqlite_files:
                archive.add(copy, arcname='app/' + relative.as_posix())
            if configuration and Path(configuration).exists():
                archive.add(configuration, arcname='config')
            for wrapper in Path('/usr/local/bin').glob('rallypoint-*'):
                archive.add(wrapper, arcname='bin/' + wrapper.name)
            for managed_file in ('/usr/local/sbin/rallypoint-court-agent', '/usr/local/lib/rallypoint-court-device-agent.py', '/etc/sudoers.d/rallypoint-court-agent'):
                if configuration and Path(managed_file).is_file():
                    archive.add(managed_file, arcname='court-management/' + Path(managed_file).as_posix().lstrip('/'))
            for service in Path('/etc/systemd/system').glob('rallypoint*'):
                archive.add(service, arcname='systemd/' + service.name)
    if database:
        if not database.replace('_', '').isalnum():
            raise ValueError('Invalid database name')
        with (target / 'club.sql').open('wb') as dump:
            subprocess.run(['mariadb-dump', '--single-transaction', '--routines', '--events', database], stdout=dump, check=True)
    manifest = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in target.iterdir()}
    (target / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    verify(target, restore_database=bool(database))
    (target / "verified.json").write_text(json.dumps({"verified_at": datetime.now(timezone.utc).isoformat()}))
    # Retain only our own verified backup directories; never arbitrary targets.
    old = sorted(p for p in destination.glob('rallypoint-*') if p.is_dir() and (p / 'verified.json').is_file())[:-14]
    for folder in old:
        if folder.parent.resolve() != destination:
            raise ValueError('Unsafe retention path')
        files = list(folder.iterdir())
        if all(p.is_file() and p.name in ('application.tar.gz', 'club.sql', 'manifest.json', 'verified.json') for p in files):
            for file in files:
                file.unlink()
            folder.rmdir()
    return target


def verify(target, restore_database=False):
    target = Path(target)
    for filename, expected in json.loads((target / 'manifest.json').read_text()).items():
        if Path(filename).name != filename or hashlib.sha256((target / filename).read_bytes()).hexdigest() != expected:
            raise ValueError('Backup checksum mismatch')
    with tempfile.TemporaryDirectory() as staging:
        with tarfile.open(target / 'application.tar.gz') as archive:
            archive.extractall(staging, filter='data')
        for path in Path(staging).rglob('*'):
            if path.is_file() and path.suffix in ('.db', '.sqlite3'):
                with closing(sqlite3.connect(path)) as connection:
                    if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        raise ValueError('Restored SQLite integrity check failed')
        if not (Path(staging) / 'app/pickleball_scoreboard/app.py').is_file():
            raise ValueError('Application missing from backup')
    if restore_database:
        scratch = 'rallypoint_restorecheck_' + datetime.now().strftime('%Y%m%d%H%M%S%f')
        subprocess.run(['mariadb', '-e', 'CREATE DATABASE ' + scratch], check=True)
        try:
            with (target / 'club.sql').open('rb') as source:
                subprocess.run(['mariadb', scratch], stdin=source, check=True)
            subprocess.run(['mariadb', scratch, '-e', 'SELECT COUNT(*) FROM matches; SELECT COUNT(*) FROM scoreboard_devices; SELECT COUNT(*) FROM admin_users;'], stdout=subprocess.DEVNULL, check=True)
        finally:
            subprocess.run(['mariadb', '-e', 'DROP DATABASE ' + scratch], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--application', default='/opt/rallypoint/app')
    parser.add_argument('--destination', default='/opt/rallypoint/backups/automatic')
    parser.add_argument('--configuration', default='/etc/rallypoint')
    parser.add_argument('--database')
    parser.add_argument('--verify')
    options = parser.parse_args()
    if options.verify:
        verify(options.verify, bool(options.database))
        print('RESTORE CHECK PASSED')
    else:
        print('BACKUP VERIFIED', backup(options.application, options.destination, options.configuration, options.database))


if __name__ == '__main__':
    main()

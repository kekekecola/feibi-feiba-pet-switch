"""Current-user Windows installation; never launches the desktop client itself."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv
import time
from install_transaction import Transaction, task_snapshot
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
TASK = 'FeibiFeibaPetSwitch'


def command(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, **kwargs)


def verify_assets():
    for line in (ROOT / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        expected, relative = line.split('  ', 1)
        actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError('Asset checksum mismatch: ' + relative)


def install_pets(home, transaction=None):
    for source in (ROOT / 'pets').iterdir():
        target = home / 'pets' / source.name
        if transaction:
            for name in ('pet.json', 'spritesheet.webp'):
                transaction.remember(target / name)
        if target.exists():
            unchanged = all((target / name).exists() and (target / name).read_bytes() == (source / name).read_bytes()
                            for name in ('pet.json', 'spritesheet.webp'))
            if unchanged:
                continue
            backup = home / 'pet-backups' / (source.name + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, backup)
            print('Previous pet backed up:', backup)
        target.mkdir(parents=True, exist_ok=True)
        for name in ('pet.json', 'spritesheet.webp'):
            shutil.copy2(source / name, target / name)


def configure(args):
    sys.path.insert(0, str(ROOT / 'app'))
    import pet_registry
    roles = pet_registry.load()
    for form in ('feibi', 'feiba'):
        value = getattr(args, form + '_id')
        if value:
            if not value.startswith(('pet_', 'custom:')):
                raise ValueError('Pet ID must begin with pet_ or custom:')
            roles[form]['id'] = value
            roles[form].pop('fingerprint', None)
    if roles['feibi']['id'] == roles['feiba']['id']:
        raise ValueError('The two forms must have different IDs')
    pet_registry.save(roles)


def task_xml(pythonw, user_sid):
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user_sid)}</UserId></LogonTrigger></Triggers>
<Principals><Principal id="Author"><UserId>{escape(user_sid)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>
<Actions Context="Author"><Exec><Command>{escape(str(pythonw))}</Command><Arguments>"{escape(str(ROOT / 'app' / 'native_bridge.py'))}"</Arguments><WorkingDirectory>{escape(str(ROOT / 'app'))}</WorkingDirectory></Exec></Actions></Task>'''


def stop_service(python):
    control = ROOT / 'app' / 'bridge_control.py'
    if python.exists() and control.exists():
        try:
            subprocess.run([str(python), str(control), 'quit'], timeout=40, capture_output=True)
        except (OSError, subprocess.TimeoutExpired):
            pass  # A damaged runtime must not prevent scheduler cleanup.
    snapshot = task_snapshot()
    if snapshot.get('running'):
        command(['schtasks', '/End', '/TN', TASK], timeout=30)
        deadline = time.monotonic() + 15
        while task_snapshot().get('running'):
            if time.monotonic() >= deadline:
                raise RuntimeError('Previous F8 task did not stop; installation cancelled.')
            time.sleep(.2)


def uninstall(python):
    from manager import disable_updater, stop as stop_managed
    disable_updater()
    snapshot = task_snapshot()
    managed = None
    if snapshot.get('xml'):
        import xml.etree.ElementTree as ET
        node = ET.fromstring(snapshot['xml']).find('.//{http://schemas.microsoft.com/windows/2004/02/mit/task}WorkingDirectory')
        if node is not None and node.text and (Path(node.text) / 'current.json').exists():
            managed = Path(node.text)
    if managed:
        stop_managed(managed)
    else:
        stop_service(python)
    if task_snapshot()['exists']:
        command(['schtasks', '/Delete', '/TN', TASK, '/F'], timeout=30)
    print('F8 logon task removed; pet files and backups retained.')


def apply_install(args, home, python, xml):
    transaction = Transaction(task_snapshot())
    transaction.remember(ROOT / 'app' / 'runtime' / 'pet-roles.json')
    try:
        if transaction.snapshot.get('running'):
            transaction.task_touched = True
            stop_service(python)
        if not args.skip_pets:
            install_pets(home, transaction)
        configure(args)
        with tempfile.TemporaryDirectory(prefix='feibi-task-') as folder:
            task_file = Path(folder) / 'task.xml'
            task_file.write_text(xml, encoding='utf-16')
            transaction.task_touched = True
            command(['schtasks', '/Create', '/TN', TASK, '/XML', task_file, '/F'])
        if not args.no_start:
            command(['schtasks', '/Run', '/TN', TASK])
    except Exception as original:
        try:
            # A failed start may still have launched the new service.
            if transaction.task_touched:
                stop_service(python)
        except Exception:
            pass
        try:
            transaction.rollback()
        except Exception as recovery_error:
            raise RuntimeError(str(original) + '; ' + str(recovery_error)) from original
        raise RuntimeError(str(original) + '; managed files and scheduled task rolled back.') from original


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'uninstall', 'configure', 'verify'))
    parser.add_argument('--skip-pets', action='store_true', help='Use an existing pair instead of installing local copies')
    parser.add_argument('--no-start', action='store_true', help='Register logon task without starting now')
    parser.add_argument('--dry-run', action='store_true', help='Validate assets and show plan without modifying this machine')
    parser.add_argument('--feibi-id')
    parser.add_argument('--feiba-id')
    args = parser.parse_args()
    if args.action in ('install', 'verify'):
        verify_assets()
    if args.action == 'verify':
        print('All bundled asset checksums verified.')
        return
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    if args.dry_run:
        print(json.dumps(dict(action=args.action, task=TASK, pets_destination=str(home / 'pets'),
                              python_environment=str(ROOT / '.venv'), skip_pets=args.skip_pets), ensure_ascii=False))
        return
    if os.name != 'nt' or sys.version_info < (3, 11):
        raise RuntimeError('Windows and Python 3.11+ are required.')
    python = ROOT / '.venv' / 'Scripts' / 'python.exe'
    if args.action == 'uninstall':
        uninstall(python)
        return
    if args.action == 'configure':
        configure(args)
        print('Role mapping saved. Restart the F8 service to load it.')
        return
    import tkinter  # Fail early if Python was installed without Tcl/Tk.
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(ROOT / '.venv')
    command([python, '-m', 'pip', 'install', '-r', ROOT / 'requirements.txt'])
    command([python, '-c', 'import PIL, uiautomation, comtypes, tkinter'])
    identity = command(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True)
    sid = next(csv.reader(identity.stdout.splitlines()))[1]
    xml = task_xml(python.with_name('pythonw.exe'), sid)
    apply_install(args, home, python, xml)
    print('Installed. In the desktop client, refresh Pets and select either bundled pet, then press F8.')
    print('Keep this folder in place. The task starts only the F8 helper, never the desktop client.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Installation failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

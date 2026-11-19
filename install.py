"""Current-user Windows installation; never launches the desktop client itself."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from install_transaction import task_snapshot
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


def pet_files(source):
    metadata = json.loads((source / 'pet.json').read_text(encoding='utf-8'))
    image = metadata.get('spritesheetPath')
    if image not in ('spritesheet.png','spritesheet.webp') or not (source / image).is_file():
        raise ValueError('Missing or unsupported pet sprite sheet: ' + source.name)
    return ('pet.json', image)


def install_pets(home, transaction=None, reuse=None):
    reuse = reuse or {}
    from pet_install_guard import PETS
    for source in (ROOT / 'pets').iterdir():
        if any(source.name == identifier and form in reuse for form,(identifier,_) in PETS.items()):
            print('Reusing existing migrated pet; no local copy added: ' + source.name)
            continue
        target = home / 'pets' / source.name
        if transaction:
            for name in pet_files(source):
                transaction.remember(target / name)
        if target.exists():
            unchanged = all((target / name).exists() and (target / name).read_bytes() == (source / name).read_bytes()
                            for name in pet_files(source))
            if unchanged:
                continue
            backup = home / 'pet-backups' / (source.name + '-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target, backup)
            print('Previous pet backed up:', backup)
        target.mkdir(parents=True, exist_ok=True)
        for name in pet_files(source):
            shutil.copy2(source / name, target / name)


def configure(args, role_file=None):
    sys.path.insert(0, str(ROOT / 'app'))
    import pet_registry
    if role_file is None:
        roles = pet_registry.load()
    else:
        roles = json.loads((role_file if role_file.is_file() else pet_registry.DEFAULT_ROLES).read_text(encoding='utf-8'))
    for form in ('feibi', 'feiba'):
        value = getattr(args, form + '_id')
        if value:
            if not value.startswith(('pet_', 'custom:')):
                raise ValueError('Pet ID must begin with pet_ or custom:')
            roles[form]['id'] = value
            roles[form].pop('fingerprint', None)
    if roles['feibi']['id'] == roles['feiba']['id']:
        raise ValueError('The two forms must have different IDs')
    if role_file is None:
        pet_registry.save(roles)
    else:
        from manager import atomic
        atomic(role_file, roles)


def task_xml(pythonw, user_sid):
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
<Triggers><LogonTrigger><Enabled>true</Enabled><UserId>{escape(user_sid)}</UserId></LogonTrigger></Triggers>
<Principals><Principal id="Author"><UserId>{escape(user_sid)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><ExecutionTimeLimit>PT0S</ExecutionTimeLimit><RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure></Settings>
<Actions Context="Author"><Exec><Command>{escape(str(pythonw))}</Command><Arguments>"{escape(str(ROOT / 'app' / 'native_bridge.py'))}"</Arguments><WorkingDirectory>{escape(str(ROOT / 'app'))}</WorkingDirectory></Exec></Actions></Task>'''


def stop_service(python, source=None):
    control = (source or ROOT) / 'app' / 'bridge_control.py'
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


def installed_location(snapshot):
    """Resolve the installation registered for this Windows user."""
    if not snapshot.get('exists'):
        return None, None
    xml = ET.fromstring(snapshot['xml'])
    namespace = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}
    work = xml.findtext('.//t:Exec/t:WorkingDirectory', namespaces=namespace)
    arguments = xml.findtext('.//t:Exec/t:Arguments', namespaces=namespace, default='')
    entry = re.fullmatch(r'"([^"]+)"(?:\s+(run))?', arguments.strip(), re.IGNORECASE)
    if work and entry:
        location = Path(work).resolve()
        script = Path(entry[1]).resolve()
        if entry[2] and script == location / 'launcher.py' and (location / 'current.json').is_file():
            return 'managed', location
        if not entry[2] and location.name.casefold() == 'app' and script == location / 'native_bridge.py':
            return 'legacy', location.parent
    raise RuntimeError('无法识别已注册的菲比程序位置，已停止以保留现有宠物和配置。')


def apply_install(args, client_home, previous_root, inspection=None):
    from manager import default_home, enable
    from pet_install_guard import inspect

    if not args.skip_pets:
        inspection = inspection if inspection is not None else inspect(client_home)
        if inspection['conflicts']:
            raise RuntimeError('\n'.join(inspection['conflicts']))

    def prepare_runtime(runtime, transaction):
        if not args.skip_pets:
            install_pets(client_home, transaction, inspection['reuse'])
        if previous_root is None or args.feibi_id or args.feiba_id:
            role_file = runtime / 'pet-roles.json'
            transaction.remember(role_file)
            configure(args, role_file)

    return enable(default_home(), prepare_runtime=prepare_runtime, start=not args.no_start)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'update', 'uninstall', 'configure', 'verify', 'check-pets'))
    parser.add_argument('--skip-pets', action='store_true', help='Use an existing pair instead of installing local copies')
    parser.add_argument('--no-start', action='store_true', help='Register logon task without starting now')
    parser.add_argument('--enable-auto-updates', action='store_true', help='Resume automatic updates after opting out')
    parser.add_argument('--dry-run', action='store_true', help='Validate assets and show plan without modifying this machine')
    parser.add_argument('--feibi-id')
    parser.add_argument('--feiba-id')
    args = parser.parse_args()
    if args.action in ('install', 'update', 'verify'):
        verify_assets()
    if args.action == 'verify':
        print('All bundled asset checksums verified.')
        return
    home = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    if args.action == 'check-pets':
        from pet_install_guard import inspect
        print(json.dumps(inspect(home), ensure_ascii=False, indent=2))
        return
    updating = args.action == 'update'
    mode, previous_root = None, None
    snapshot = None
    inspection = None
    if args.action in ('install', 'update') and os.name == 'nt':
        from manager import default_home
        snapshot = task_snapshot()
        mode, previous_root = installed_location(snapshot)
        if not snapshot.get('exists') and (default_home() / 'current.json').is_file():
            mode, previous_root = 'managed', default_home()
        updating = updating or mode is not None
    if updating and previous_root is None and not args.dry_run:
        raise RuntimeError('未找到已安装的切换程序。已有两只宠物时，请用 install --skip-pets 和已有 ID 完成首次程序安装。')
    args.skip_pets = args.skip_pets or updating or bool(args.feibi_id and args.feiba_id)
    if args.action == 'install' and not args.skip_pets:
        from pet_install_guard import inspect
        inspection = inspect(home)
        if inspection['conflicts']:
            raise RuntimeError('\n'.join(inspection['conflicts']))
        for form, identifier in inspection['reuse'].items():
            if not getattr(args, form + '_id'):
                setattr(args, form + '_id', identifier)
        args.skip_pets = len(inspection['reuse']) == 2
        if inspection['reuse'] and not args.skip_pets:
            raise RuntimeError('已检测到一只现有宠物。请确认两只宠物的 ID 后使用 install --skip-pets，避免重新导入。')
    if args.dry_run:
        print(json.dumps(dict(action='update' if updating else args.action, task=TASK, pets_destination=None if args.skip_pets else str(home / 'pets'),
                              skip_pets=args.skip_pets, automatic_updates='once_per_codex_launch'), ensure_ascii=False))
        return
    if os.name != 'nt' or sys.version_info < (3, 11):
        raise RuntimeError('Windows and Python 3.11+ are required.')
    python = ROOT / '.venv' / 'Scripts' / 'python.exe'
    if args.action == 'uninstall':
        uninstall(python)
        return
    if args.action == 'configure':
        configured_mode, location = installed_location(task_snapshot())
        configure(args, location / 'runtime' / 'pet-roles.json' if configured_mode == 'managed' else None)
        print('Role mapping saved. Restart the F8 service to load it.')
        return
    if mode == 'managed':
        from manager import UpdateLock, ensure_updater, pointer, register_main_task, status, update_from_folder
        restore_main = not snapshot.get('exists')
        if restore_main:
            old_version = pointer(previous_root)['version']
            register_main_task(previous_root)
        result = (update_from_folder(previous_root, ROOT, start=False) if args.no_start
                  else update_from_folder(previous_root, ROOT))
        if restore_main and not args.no_start and result['version'] == old_version:
            command(['schtasks', '/Run', '/TN', TASK], timeout=30)
        with UpdateLock(previous_root, wait=20):
            if args.enable_auto_updates:
                (previous_root / 'updates-disabled').unlink(missing_ok=True)
            status(previous_root, automatic_updates=ensure_updater(previous_root))
        print('Switcher version ' + result['version'] + '. Your existing pets are preserved.')
        return
    apply_install(args, home, previous_root, inspection)
    if args.skip_pets:
        print('Program installed. Keep using your existing pets and press F8 to switch.')
    else:
        print('First-time installation complete. Refresh Pets in Codex and select either pet to use F8.')
    print('Automatic updates check once when you open Codex.')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('Installation failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)

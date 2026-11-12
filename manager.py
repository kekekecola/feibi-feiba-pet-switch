"""Versioned local install and opt-in GitHub release updates. No account token is used."""
import argparse
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import venv
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
API = 'https://api.github.com/repos/kekekecola/feibi-feiba-pet-switch/releases/latest'
TASK = 'FeibiFeibaPetSwitch'
UPDATE_TASK = TASK + 'Update'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


class UpdateLock:
    def __init__(self, home):
        import ctypes
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        name = 'Local\\FeibiUpdate_' + hashlib.sha256(str(home.resolve()).casefold().encode()).hexdigest()[:24]
        self.handle = self.kernel.CreateMutexW(None, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == 183:
            self.kernel.CloseHandle(self.handle)
            raise RuntimeError('An update is already running')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.kernel.CloseHandle(self.handle)


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r'v?\d+\.\d+\.\d+', value):
        raise ValueError('Unsupported stable release version')
    return tuple(map(int, value.lstrip('v').split('.')))


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def pointer(home):
    data = json.loads((home / 'current.json').read_text(encoding='utf-8'))
    for key, parent in (('release', home / 'versions'), ('python', home)):
        if not (home / data[key]).resolve().is_relative_to(parent.resolve()):
            raise ValueError('Installation pointer escapes managed directory')
    version(data['version'])
    return data


def environment(home):
    return {**os.environ, 'FEIBI_SWITCH_HOME': str(home), 'FEIBI_SWITCH_DATA_DIR': str(home / 'runtime')}


def status(home, **changes):
    path = home / 'runtime' / 'update-status.json'
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    data.update(changes)
    data['last_checked'] = time.time()
    atomic(path, data)


def run(command, **kwargs):
    kwargs.setdefault('timeout', 60)
    kwargs.setdefault('creationflags', NO_WINDOW)
    return subprocess.run([str(x) for x in command], check=True, **kwargs)


def download(url, limit=35_000_000):
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.hostname not in ('api.github.com', 'github.com', 'objects.githubusercontent.com'):
        raise ValueError('Untrusted release URL')
    request = urllib.request.Request(url, headers={'User-Agent':'FeibiFeibaPetSwitch-Updater', 'Accept':'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('Release download exceeds size limit')
    return payload


def extract_archive(payload, destination, digest):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest or ''):
        raise ValueError('GitHub did not supply a SHA-256 asset digest; update refused')
    if 'sha256:' + hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError('Release digest mismatch')
    import io
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        entries = archive.infolist()
        if len(entries) > 600 or sum(e.file_size for e in entries) > 120_000_000:
            raise ValueError('Release archive exceeds extraction limits')
        names = set()
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if (path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename
                    or '' in entry.filename.rstrip('/').split('/')
                    or any(part.rstrip('. ') != part for part in path.parts)
                    or any(part.split('.')[0].casefold() in {'con','prn','aux','nul', *('com'+str(i) for i in range(1,10)), *('lpt'+str(i) for i in range(1,10))} for part in path.parts)
                    or not path.parts or path.parts[0] != 'feibi-feiba-pet-switch'
                    or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError('Unsafe archive entry')
            key = entry.filename.casefold().rstrip('/')
            if key in names:
                raise ValueError('Duplicate archive path')
            names.add(key)
        archive.extractall(destination)
    source = destination / 'feibi-feiba-pet-switch'
    verify_source(source)
    return source


def verify_source(source):
    version((source / 'VERSION').read_text(encoding='utf-8').strip())
    for name in ('manager.py', 'launcher.py', 'requirements.txt', 'app/native_bridge.py'):
        if not (source / name).is_file():
            raise ValueError('Incomplete release: ' + name)
    for line in (source / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        digest, relative = line.split('  ', 1)
        target = (source / relative).resolve()
        if not target.is_relative_to(source.resolve()) or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError('Invalid bundled asset: ' + relative)


def prepare_python(home, source, previous=None, wheelhouse=None):
    if previous:
        old = home / previous['release']
        if (old / 'requirements.txt').read_text(encoding='utf-8').splitlines() == (source / 'requirements.txt').read_text(encoding='utf-8').splitlines():
            python = home / previous['python']
            run([python, '-c', 'import PIL, uiautomation, comtypes, tkinter'], capture_output=True)
            return python
    target = home / '.venv' if not previous else source / '.venv'
    python = target / 'Scripts' / 'python.exe'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(target)
    args = [python, '-m', 'pip', 'install', '--disable-pip-version-check']
    if wheelhouse:
        args += ['--no-index', '--find-links', wheelhouse]
    run([*args, '-r', source / 'requirements.txt'], timeout=480, capture_output=True)
    run([python, '-c', 'import PIL, uiautomation, comtypes, tkinter'], capture_output=True)
    return python


def scheduler_xml(home, python, user_sid, action):
    from install import task_xml
    namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    tag = lambda name: '{' + namespace + '}' + name
    tree = ET.fromstring(task_xml(python.with_name('pythonw.exe'), user_sid))
    tree.find('.//' + tag('Arguments')).text = '"' + str(home / 'launcher.py') + '" ' + action
    tree.find('.//' + tag('WorkingDirectory')).text = str(home)
    if action == 'update':
        trigger = ET.SubElement(tree.find(tag('Triggers')), tag('TimeTrigger'))
        repetition = ET.SubElement(trigger, tag('Repetition'))
        ET.SubElement(repetition, tag('Interval')).text = 'PT30M'
        ET.SubElement(trigger, tag('StartBoundary')).text = (datetime.now() + timedelta(minutes=2)).isoformat(timespec='seconds')
        ET.SubElement(trigger, tag('Enabled')).text = 'true'
        tree.find('.//' + tag('ExecutionTimeLimit')).text = 'PT20M'
    ET.register_namespace('', namespace)
    return ET.tostring(tree, encoding='unicode')


def register(task, xml):
    with tempfile.TemporaryDirectory(prefix='feibi-managed-') as folder:
        file = Path(folder) / 'task.xml'
        file.write_text(xml, encoding='utf-16')
        run(['schtasks', '/Create', '/TN', task, '/XML', file, '/F'], capture_output=True)


def stop(home):
    current = pointer(home)
    python = home / current['python']
    source = home / current['release']
    try:
        run([python, source / 'app' / 'bridge_control.py', 'quit', '--timeout', '40'], env=environment(home),
            capture_output=True, timeout=45)
    except (OSError, subprocess.SubprocessError):
        pass
    from install_transaction import task_snapshot
    if task_snapshot().get('running'):
        run(['schtasks', '/End', '/TN', TASK], capture_output=True)
    deadline = time.monotonic() + 15
    while task_snapshot().get('running'):
        if time.monotonic() > deadline:
            raise RuntimeError('Previous service did not stop')
        time.sleep(.2)
    # Also handle interrupted transitions after a forced scheduler stop.
    run([python, source / 'app' / 'recover.py'], env=environment(home), capture_output=True, timeout=20)


def healthy(home, expected, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            state = json.loads((home / 'runtime' / 'bridge-status.json').read_text(encoding='utf-8'))
            if (state.get('running') and time.time() - state.get('updated_at', 0) < 6
                    and state.get('version') == expected and state.get('hotkey_registered') and state.get('tray_registered')):
                return
        except (OSError, ValueError):
            pass
        time.sleep(.3)
    raise RuntimeError('New service did not pass startup health check')


def recover_pending(home):
    journal = home / 'pending-update.json'
    if not journal.exists():
        return
    pending = json.loads(journal.read_text(encoding='utf-8'))
    previous = pending.get('previous')
    current = pointer(home)
    if current not in (previous, pending['new']):
        raise RuntimeError('Pending update does not match active pointer; recovery requires inspection')
    if current == pending['new']:
        try:
            healthy(home, current['version'], timeout=1)
            journal.unlink()
            return
        except RuntimeError:
            stop(home)
            if previous is None:
                raise RuntimeError('First installation did not start; no prior version to restore')
            atomic(home / 'current.json', previous)
    run(['schtasks', '/Run', '/TN', TASK], capture_output=True)
    journal.unlink()


def activate(home, source, python, previous):
    new = dict(version=(source / 'VERSION').read_text().strip(), release=str(source.relative_to(home)),
               python=str(python.relative_to(home)))
    atomic(home / 'pending-update.json', dict(previous=previous, new=new))
    if previous:
        stop(home)
    atomic(home / 'current.json', new)
    try:
        run(['schtasks', '/Run', '/TN', TASK], capture_output=True)
        healthy(home, new['version'])
    except Exception:
        try:
            stop(home)
        finally:
            if previous:
                atomic(home / 'current.json', previous)
                run(['schtasks', '/Run', '/TN', TASK], capture_output=True)
                (home / 'pending-update.json').unlink(missing_ok=True)
        raise
    if previous:
        atomic(home / 'previous.json', previous)
    (home / 'pending-update.json').unlink(missing_ok=True)
    return new


def update(home):
    with UpdateLock(home):
        return update_locked(home)


def update_locked(home):
    recover_pending(home)
    current = pointer(home)
    status(home, phase='checking', installed_version=current['version'], error=None)
    release = json.loads(download(API, limit=2_000_000))
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('Only stable releases are accepted')
    latest = release['tag_name'].lstrip('v')
    version(latest)
    status(home, latest_version=latest)
    if version(latest) <= version(current['version']):
        status(home, phase='current')
        return current
    filename = f'feibi-feiba-pet-switch-v{latest}-windows.zip'
    assets = [a for a in release.get('assets', []) if a['name'] == filename and a.get('state') == 'uploaded']
    if len(assets) != 1:
        raise ValueError('Stable Windows release asset is missing or ambiguous')
    asset = assets[0]
    expected_url = 'https://github.com/kekekecola/feibi-feiba-pet-switch/releases/download/' + release['tag_name'] + '/' + filename
    if asset['browser_download_url'] != expected_url:
        raise ValueError('Unexpected release asset URL')
    status(home, phase='downloading')
    payload = download(asset['browser_download_url'])
    if len(payload) != asset['size']:
        raise ValueError('Release size mismatch')
    target = home / 'versions' / ('v' + latest + '-' + uuid.uuid4().hex[:8])
    source = extract_archive(payload, target, asset.get('digest'))
    if (source / 'VERSION').read_text().strip() != latest:
        raise ValueError('Release version and archive version disagree')
    status(home, phase='preparing')
    python = prepare_python(home, source, current)
    status(home, phase='applying')
    result = activate(home, source, python, current)
    status(home, phase='current', installed_version=result['version'], error=None)
    return result


def enable(home, wheelhouse=None, data_from=None):
    verify_source(ROOT)
    from install_transaction import task_snapshot, restore_task
    if (home / 'current.json').exists():
        raise RuntimeError('Managed install already exists. Use update, not enable again.')
    snapshots = {name:task_snapshot(name) for name in (TASK, UPDATE_TASK)}
    if any(snapshot.get('running') for snapshot in snapshots.values()):
        raise RuntimeError('Exit the existing switch service before enabling managed updates.')
    target = home / 'versions' / ('v' + (ROOT / 'VERSION').read_text().strip() + '-' + uuid.uuid4().hex[:8])
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns('.git', '.venv', 'runtime', '__pycache__', 'dist', 'managed-link.json'))
    python = prepare_python(home, target, wheelhouse=wheelhouse)
    home.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'launcher.py', home / 'launcher.py')
    data = home / 'runtime'
    data.mkdir(exist_ok=True)
    old_data = Path(data_from) if data_from else ROOT / 'app' / 'runtime'
    for name in ('pet-roles.json', 'settings.json', 'hotkey-last-good.json'):
        if (old_data / name).exists():
            shutil.copy2(old_data / name, data / name)
    identity = run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True)
    import csv
    sid = next(csv.reader(identity.stdout.splitlines()))[1]
    try:
        for name, action in ((TASK, 'run'), (UPDATE_TASK, 'update')):
            register(name, scheduler_xml(home, python, sid, action))
        result = activate(home, target, python, None)
    except Exception:
        for name, snapshot in snapshots.items():
            restore_task(snapshot, name)
        (home / 'current.json').unlink(missing_ok=True)
        (home / 'pending-update.json').unlink(missing_ok=True)
        raise
    status(home, phase='enabled', installed_version=result['version'], error=None)
    atomic(ROOT / 'managed-link.json', {'home':str(home)})
    return result


def disable_updater():
    from install_transaction import task_snapshot
    if task_snapshot(UPDATE_TASK).get('running'):
        run(['schtasks', '/End', '/TN', UPDATE_TASK], capture_output=True)
    if task_snapshot(UPDATE_TASK)['exists']:
        run(['schtasks', '/Delete', '/TN', UPDATE_TASK, '/F'], capture_output=True)


def relay_client(command, env=None):
    result = subprocess.run([str(x) for x in command], env=env, creationflags=NO_WINDOW, capture_output=True)
    if sys.stdout:
        sys.stdout.buffer.write(result.stdout)
    if sys.stderr:
        sys.stderr.buffer.write(result.stderr)
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('enable','update','run','control','diagnose','settings','stop','disable-updates'))
    parser.add_argument('command', nargs='?', default='status')
    parser.add_argument('--home', type=Path)
    parser.add_argument('--timeout', type=float, default=25)
    parser.add_argument('--wheelhouse', type=Path)
    parser.add_argument('--data-from', type=Path)
    args = parser.parse_args()
    if args.home is None:
        try:
            args.home = Path(json.loads((ROOT / 'managed-link.json').read_text(encoding='utf-8'))['home'])
        except (OSError, ValueError, KeyError):
            args.home = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'FeibiFeibaPetSwitch'
    home = args.home.resolve()
    if not (home / 'current.json').exists() and args.action in ('stop','control','diagnose','settings'):
        script, extra = {'stop':('bridge_control.py',['quit']), 'control':('bridge_control.py',[args.command, '--timeout', str(args.timeout)]),
                         'diagnose':('diagnostics.py',['--gui']), 'settings':('settings.py',['--gui'])}[args.action]
        raise SystemExit(relay_client([sys.executable, str(ROOT / 'app' / script), *extra]))
    if args.action == 'enable':
        print(json.dumps(enable(home, args.wheelhouse, args.data_from), ensure_ascii=False))
    elif args.action == 'update':
        try:
            print(json.dumps(update(home), ensure_ascii=False))
        except Exception as error:
            status(home, phase='error', error=str(error))
            raise
    elif args.action == 'disable-updates':
        disable_updater()
    elif args.action == 'stop':
        stop(home)
    else:
        current = pointer(home)
        app = home / current['release'] / 'app'
        script, extra = {'run':('native_bridge.py', []), 'control':('bridge_control.py', [args.command,'--timeout',str(args.timeout)]),
                         'diagnose':('diagnostics.py', ['--gui']), 'settings':('settings.py', ['--gui'])}[args.action]
        command = [str(home / current['python']), str(app / script), *extra]
        if args.action == 'control':
            raise SystemExit(relay_client(command, env=environment(home)))
        child = subprocess.run(command, env=environment(home), creationflags=NO_WINDOW)
        raise SystemExit(child.returncode)


if __name__ == '__main__':
    main()

"""Versioned local installation and manual upgrades from a release folder."""
import argparse
import csv
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import venv
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
LATEST_RELEASE = 'https://github.com/kekekecola/feibi-feiba-pet-switch/releases/latest'
TASK = 'FeibiFeibaPetSwitch'
UPDATE_TASK = TASK + 'Update'
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
TASK_NS = {'t': 'http://schemas.microsoft.com/windows/2004/02/mit/task'}


class UpdateLock:
    def __init__(self, home, wait=0):
        import ctypes
        from ctypes import wintypes
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        name = 'Local\\FeibiUpdate_' + hashlib.sha256(str(home.resolve()).casefold().encode()).hexdigest()[:24]
        deadline = time.monotonic() + wait
        while True:
            self.handle = self.kernel.CreateMutexW(None, False, name)
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())
            if ctypes.get_last_error() != 183:
                break
            self.kernel.CloseHandle(self.handle)
            if time.monotonic() >= deadline:
                raise RuntimeError('An update is already running')
            time.sleep(.25)

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


def default_home():
    return (Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'FeibiFeibaPetSwitch').resolve()


def user_sid():
    result = run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, text=True)
    return next(csv.reader(result.stdout.splitlines()))[1]


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
    if action != 'run':
        raise ValueError('Only the switcher logon task is supported.')
    namespace = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    tag = lambda name: '{' + namespace + '}' + name
    tree = ET.fromstring(task_xml(python.with_name('pythonw.exe'), user_sid))
    tree.find('.//' + tag('Arguments')).text = '"' + str(home / 'launcher.py') + '" ' + action
    tree.find('.//' + tag('WorkingDirectory')).text = str(home)
    ET.register_namespace('', namespace)
    return ET.tostring(tree, encoding='unicode')


def register(task, xml):
    with tempfile.TemporaryDirectory(prefix='feibi-managed-') as folder:
        file = Path(folder) / 'task.xml'
        file.write_text(xml, encoding='utf-16')
        run(['schtasks', '/Create', '/TN', task, '/XML', file, '/F'], capture_output=True)


def retire_update_task(home, wait=0):
    """Remove the old project-owned scheduler entry once, without ending active work."""
    from install_transaction import task_snapshot

    with UpdateLock(home / 'retire-updater', wait=10):
        marker = home / 'manual-updates-v1'
        if marker.is_file():
            return True
        deadline = time.monotonic() + wait
        while True:
            snapshot = task_snapshot(UPDATE_TASK)
            if not snapshot.get('exists'):
                break
            tree = ET.fromstring(snapshot['xml'])
            work = tree.findtext('.//t:Exec/t:WorkingDirectory', namespaces=TASK_NS)
            args = tree.findtext('.//t:Exec/t:Arguments', namespaces=TASK_NS, default='')
            entry = re.fullmatch(r'"([^"]+)"\s+update', args.strip(), re.IGNORECASE)
            if (not work or Path(work).resolve() != home.resolve() or not entry
                    or Path(entry[1]).resolve() != (home / 'launcher.py').resolve()):
                raise RuntimeError('The old update task belongs to a different installation.')
            if not snapshot.get('running'):
                run(['schtasks', '/Delete', '/TN', UPDATE_TASK, '/F'], capture_output=True)
                break
            if time.monotonic() >= deadline:
                return False
            time.sleep(.5)
        status(home, automatic_updates=False, mode='manual', updater_cleanup_error=None)
        (home / 'updates-disabled').touch()  # Also block re-enrollment after a downgrade.
        marker.touch()
        return True


def retire_update_task_safely(home):
    try:
        if not retire_update_task(home, wait=30):
            status(home, automatic_updates=False, mode='manual',
                   updater_cleanup_error='Old task is still finishing. Its removal will be retried next time the switcher starts.')
    except Exception as error:
        status(home, automatic_updates=False, mode='manual', updater_cleanup_error=str(error))


def register_main_task(home):
    current = pointer(home)
    register(TASK, scheduler_xml(home, home / current['python'], user_sid(), 'run'))


def stop(home, keep_helper=False):
    current = pointer(home)
    python = home / current['python']
    source = home / current['release']
    try:
        action = 'quit-for-update' if keep_helper and version(current['version']) >= (1, 6, 9) else 'quit'
        run([python, source / 'app' / 'bridge_control.py', action, '--timeout', '40'], env=environment(home),
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
                    and state.get('version') == expected and state.get('tray_registered')
                    and (state.get('hotkey_registered') or state.get('hotkey_deferred'))):
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


def activate(home, source, python, previous, start=True):
    new = dict(version=(source / 'VERSION').read_text().strip(), release=str(source.relative_to(home)),
               python=str(python.relative_to(home)))
    atomic(home / 'pending-update.json', dict(previous=previous, new=new, accepts_deferred_hotkey=True))
    if previous:
        stop(home, keep_helper=True)
    atomic(home / 'current.json', new)
    try:
        if start:
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


def update_from_folder(home, source, start=True):
    """Stage a downloaded release without touching the client's pet library."""
    verify_source(source)
    incoming = (source / 'VERSION').read_text(encoding='utf-8').strip()
    with UpdateLock(home):
        recover_pending(home)
        current = pointer(home)
        if version(incoming) <= version(current['version']):
            return current
        target = home / 'versions' / ('v' + incoming + '-' + uuid.uuid4().hex[:8])
        if target.resolve().is_relative_to(source.resolve()):
            raise ValueError('Release source cannot contain the managed versions directory')
        shutil.copytree(source, target, ignore=shutil.ignore_patterns(
            '.git', '.venv', 'runtime', '__pycache__', 'dist', 'managed-link.json'))
        verify_source(target)
        status(home, phase='preparing', latest_version=incoming, error=None)
        try:
            python = prepare_python(home, target, current)
            if start:
                result = activate(home, target, python, current)
            else:
                result = activate(home, target, python, current, start=False)
        except Exception as error:
            status(home, phase='error', error=str(error))
            raise
        status(home, phase='current', installed_version=result['version'], error=None)
        return result


def enable(home, wheelhouse=None, data_from=None, *, prepare_runtime=None, start=True):
    from install import installed_location, stop_service
    from install_transaction import Transaction, task_snapshot, restore_task

    verify_source(ROOT)
    with UpdateLock(home):
        snapshot = task_snapshot(TASK)
        mode, old_source = installed_location(snapshot)
        if mode == 'managed' or (home / 'current.json').exists():
            raise RuntimeError('Managed install already exists. Run the program update script.')
        incoming = (ROOT / 'VERSION').read_text(encoding='utf-8').strip()
        if old_source and (old_source / 'VERSION').is_file():
            old_version = (old_source / 'VERSION').read_text(encoding='utf-8').strip()
            if version(incoming) < version(old_version):
                raise RuntimeError('The downloaded installer is older than the installed program.')
        target = home / 'versions' / ('v' + incoming + '-' + uuid.uuid4().hex[:8])
        if target.resolve().is_relative_to(ROOT.resolve()):
            raise ValueError('Installation directory must be outside the source folder.')
        shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(
            '.git', '.venv', 'runtime', '__pycache__', 'dist', 'managed-link.json'))
        python = prepare_python(home, target, wheelhouse=wheelhouse)
        sid = user_sid()
        transaction = Transaction(snapshot)
        task_touched = False
        runtime = home / 'runtime'
        old_data = Path(data_from) if data_from else (old_source or ROOT) / 'app' / 'runtime'
        try:
            if snapshot.get('running'):
                task_touched = True
                stop_service(old_source / '.venv' / 'Scripts' / 'python.exe', old_source)
            runtime.mkdir(parents=True, exist_ok=True)
            for name in ('pet-roles.json', 'settings.json', 'hotkey-last-good.json'):
                if (old_data / name).is_file() and (old_data / name).resolve() != (runtime / name).resolve():
                    transaction.remember(runtime / name)
                    shutil.copy2(old_data / name, runtime / name)
            if prepare_runtime:
                prepare_runtime(runtime, transaction)
            for path in (home / 'launcher.py', home / 'current.json', home / 'pending-update.json', ROOT / 'managed-link.json'):
                transaction.remember(path)
            shutil.copy2(ROOT / 'launcher.py', home / 'launcher.py')
            task_touched = True
            register(TASK, scheduler_xml(home, python, sid, 'run'))
            result = activate(home, target, python, None, start=start)
            atomic(ROOT / 'managed-link.json', {'home': str(home)})
        except Exception:
            try:
                if (home / 'current.json').is_file():
                    stop(home)
            finally:
                try:
                    transaction.rollback()
                finally:
                    if task_touched:
                        restore_task(snapshot, TASK)
            raise
        status(home, phase='current', installed_version=result['version'], automatic_updates=False, mode='manual', error=None)
        return result


def disable_updater(home=None):
    from install_transaction import task_snapshot
    if task_snapshot(UPDATE_TASK).get('running'):
        run(['schtasks', '/End', '/TN', UPDATE_TASK], capture_output=True)
    if task_snapshot(UPDATE_TASK)['exists']:
        run(['schtasks', '/Delete', '/TN', UPDATE_TASK, '/F'], capture_output=True)
    if home and (home / 'current.json').is_file():
        (home / 'updates-disabled').touch()
        status(home, automatic_updates=False)


def relay_client(command, env=None):
    result = subprocess.run([str(x) for x in command], env=env, creationflags=NO_WINDOW, capture_output=True)
    if sys.stdout:
        sys.stdout.buffer.write(result.stdout)
    if sys.stderr:
        sys.stderr.buffer.write(result.stderr)
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('enable','update','update-on-client-launch','run','control','diagnose','settings','stop','disable-updates'))
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
            args.home = default_home()
    home = args.home.resolve()
    if not (home / 'current.json').exists() and args.action in ('stop','control','diagnose','settings'):
        script, extra = {'stop':('bridge_control.py',['quit']), 'control':('bridge_control.py',[args.command, '--timeout', str(args.timeout)]),
                         'diagnose':('diagnostics.py',['--gui']), 'settings':('settings.py',['--gui'])}[args.action]
        raise SystemExit(relay_client([sys.executable, str(ROOT / 'app' / script), *extra]))
    if args.action in ('update', 'update-on-client-launch'):
        # Compatibility with the obsolete scheduler: it never contacts GitHub.
        if args.action == 'update':
            print('Manual updates: download the latest ZIP and run the update script. ' + LATEST_RELEASE)
        return
    if args.action == 'enable':
        if (home / 'current.json').is_file():
            retire_update_task_safely(home)
            print(json.dumps(pointer(home), ensure_ascii=False))
        else:
            print(json.dumps(enable(home, args.wheelhouse, args.data_from), ensure_ascii=False))
    elif args.action == 'disable-updates':
        retire_update_task_safely(home)
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
        if args.action == 'run' and not (home / 'manual-updates-v1').is_file():
            threading.Thread(target=retire_update_task_safely, args=(home,), daemon=True).start()
        child = subprocess.run(command, env=environment(home), creationflags=NO_WINDOW)
        raise SystemExit(child.returncode)


if __name__ == '__main__':
    main()

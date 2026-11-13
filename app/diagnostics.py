"""Generate a local, allowlisted report; no account configuration is exported."""
import argparse
import importlib.metadata
import json
import platform
import time
from pathlib import Path
import control
import settings
from paths import RUNTIME, VERSION


def collect():
    state = control.read_json(RUNTIME / 'bridge-status.json')
    keys = ('version', 'phase', 'connection_state', 'shutdown_source', 'hotkey', 'hotkey_registered', 'hotkey_error',
            'tray_registered', 'tray_icon_custom', 'tray_error', 'settings_error', 'update_error', 'form', 'native_visible', 'cg_visible', 'native_switch_confirmed', 'error', 'control_details', 'timings_ms')
    report = dict(version=VERSION, python=platform.python_version(), platform=platform.system(),
                  status={key:state.get(key) for key in keys},
                  heartbeat_fresh=time.time() - state.get('updated_at', 0) < 6,
                  process_alive=control.process_alive(state.get('pid')),
                  recovery_pending=(RUNTIME / 'native-recovery.json').exists(),
                  updater=control.read_json(RUNTIME / 'update-status.json'))
    # Updater status is intentionally small; never include a raw config.toml or global state.
    report['updater'] = {key:report['updater'].get(key) for key in
                         ('phase', 'installed_version', 'latest_version', 'last_checked', 'error')}
    try:
        report['requested_hotkey'] = settings.load()['hotkey']
    except (OSError, ValueError) as error:
        report['settings_error'] = str(error)
    report['dependencies'] = {}
    for package in ('Pillow', 'comtypes', 'uiautomation'):
        try:
            report['dependencies'][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            report['dependencies'][package] = 'missing'
    return report


def write_report():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    path = RUNTIME / 'diagnostics.json'
    path.write_text(json.dumps(collect(), ensure_ascii=False, indent=2), encoding='utf-8')
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    path = write_report()
    if args.gui:
        from control_room import run
        run('diagnostics')
    else:
        print(path)

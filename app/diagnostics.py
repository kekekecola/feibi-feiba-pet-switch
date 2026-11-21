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


STAGES = {
    'background_page': '打开后台宠物设置页',
    'cg_player': '启动动画播放器',
    'native_window': '寻找原生宠物窗口',
    'pet_cards': '检查两张宠物卡片',
    'first_frame': '显示 CG 起始帧',
    'native_selection': '确认原生宠物选择',
    'animation': '播放动画',
    'native_display': '恢复原生宠物显示',
    'preparing': '准备切换',
    'selecting_native': '确认原生宠物选择',
    'transition': '播放动画',
    'restoring': '恢复原生宠物显示',
}


def summary(state, heartbeat_fresh, running):
    from tray import status_label
    if not heartbeat_fresh or not running:
        return '切换后台未运行或状态已过期。'
    failure = state.get('last_switch_failure') or {}
    if state.get('error'):
        stage = STAGES.get(state.get('stage'), STAGES.get(failure.get('stage'), '后台连接'))
        return stage + '失败。' + state['error']
    if failure:
        return '上次切换在「' + STAGES.get(failure.get('stage'), '准备切换') + '」阶段未完成。'
    if state.get('phase') == 'sleeping':
        return '省内存待机。按快捷键后恢复预热进程。'
    return status_label(state) + '。'


def collect():
    state = control.read_json(RUNTIME / 'bridge-status.json')
    keys = ('version', 'phase', 'connection_state', 'shutdown_source', 'hotkey', 'hotkey_registered', 'hotkey_error',
            'tray_registered', 'tray_icon_custom', 'tray_error', 'settings_error', 'update_error', 'form', 'native_visible', 'cg_visible', 'native_switch_confirmed', 'error', 'control_details', 'timings_ms',
            'switch_ready', 'hotkey_deferred', 'pending_switch', 'pending_switch_notice', 'stage', 'memory_saving', 'memory_saving_error',
            'helper_setup_pending', 'helper_setup_error')
    report = dict(version=VERSION, python=platform.python_version(), platform=platform.system(),
                  status={key:state.get(key) for key in keys},
                  heartbeat_fresh=time.time() - state.get('updated_at', 0) < 6,
                  process_alive=control.process_alive(state.get('pid')),
                  recovery_pending=(RUNTIME / 'native-recovery.json').exists(),
                  updater=control.read_json(RUNTIME / 'update-status.json'))
    report = {'summary': summary(state, report['heartbeat_fresh'], report['process_alive']), **report}
    report['status']['last_switch_failure'] = state.get('last_switch_failure')
    report['status']['native_overlay_form'] = state.get('native_overlay_form')
    # Updater status is intentionally small; never include a raw config.toml or global state.
    report['updater'] = {key:report['updater'].get(key) for key in
                         ('phase', 'installed_version', 'error', 'updater_cleanup_error')}
    report['updater']['automatic_updates'] = False
    report['updater']['mode'] = 'manual'
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

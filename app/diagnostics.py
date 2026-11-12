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
            'tray_registered', 'tray_icon_custom', 'tray_error', 'settings_error', 'update_error', 'form', 'native_visible', 'cg_visible', 'native_switch_confirmed', 'error', 'timings_ms')
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
        import tkinter as tk
        from tkinter import ttk
        from tkinter.scrolledtext import ScrolledText
        root = tk.Tk()
        try:
            root.iconbitmap(str(Path(__file__).parent / 'assets' / 'feibi-tray.ico'))
        except tk.TclError:
            pass
        root.title('菲比 / 菲八 · 状态与诊断')
        root.geometry('700x570')
        tabs = ttk.Notebook(root)
        tabs.pack(fill='both', expand=True, padx=12, pady=12)
        overview, detail = ttk.Frame(tabs), ttk.Frame(tabs)
        tabs.add(overview,text='运行状态')
        tabs.add(detail,text='诊断详情')
        summary = tk.StringVar()
        ttk.Label(overview,textvariable=summary,justify='left',wraplength=640).pack(anchor='w',padx=14,pady=16)
        ttk.Label(overview,text='诊断仅保存在本机，不会自动上传。窗口可见不等于图像已正常绘制。',wraplength=640).pack(anchor='w',padx=14)
        box = ScrolledText(detail, wrap='word')
        box.pack(fill='both', expand=True)
        def refresh():
            from tray import status_label
            write_report()
            report = collect()
            state, updater = report['status'], report['updater']
            names = {'feibi':'菲比啾比','feiba':'菲八啾比'}
            error = state.get('hotkey_error') or state.get('settings_error') or state.get('error') or state.get('update_error') or updater.get('error')
            text = [f"程序版本：{VERSION}", f"运行状态：{status_label(state) if report['process_alive'] and report['heartbeat_fresh'] else '服务未运行或心跳过期'}",
                    '当前形态：' + names.get(state.get('form'),'尚未识别'),
                    '切换快捷键：' + str(state.get('hotkey') or '未知') + ('（已注册）' if state.get('hotkey_registered') else '（未注册）'),
                    '托盘图标：' + ('菲比啾比头像' if state.get('tray_icon_custom') else '系统备用图标'),
                    '自动更新：' + {'current':'已是已知最新版本','checking':'正在检查','downloading':'正在下载','preparing':'准备新版本','applying':'正在应用','error':'检查或更新失败'}.get(updater.get('phase'),'尚未开启或未检查'),
                    '已安装 / 最近查询到的版本：' + str(updater.get('installed_version') or VERSION) + ' / ' + str(updater.get('latest_version') or '未知')]
            if error:
                text += ['\n需要处理：' + str(error)]
            summary.set('\n\n'.join(text))
            box.configure(state='normal')
            box.delete('1.0','end')
            box.insert('1.0',path.read_text(encoding='utf-8'))
            box.configure(state='disabled')
        ttk.Button(root,text='刷新状态',command=refresh).pack(pady=(0,10))
        refresh()
        root.mainloop()
    else:
        print(path)

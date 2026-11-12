"""Validated live settings. Windows-reserved shortcuts are not accepted."""
import argparse
import json
import os
import re
import uuid
from paths import RUNTIME

SETTINGS = RUNTIME / 'settings.json'
LAST_GOOD = RUNTIME / 'hotkey-last-good.json'


def last_good():
    try:
        return parse_hotkey(json.loads(LAST_GOOD.read_text(encoding='utf-8'))['hotkey'])[0]
    except (OSError, ValueError, KeyError, TypeError):
        return 'F8'


def remember_good(value):
    value = parse_hotkey(value)[0]
    if LAST_GOOD.exists() and last_good() == value:
        return
    LAST_GOOD.parent.mkdir(parents=True, exist_ok=True)
    temporary = LAST_GOOD.with_name('hotkey-good.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps({'hotkey':value}), encoding='utf-8')
    os.replace(temporary, LAST_GOOD)


def parse_hotkey(value):
    if not isinstance(value, str):
        raise ValueError('快捷键必须是字符串')
    tokens = [part.strip().upper() for part in value.split('+')]
    if not tokens or not tokens[-1] or len(set(tokens)) != len(tokens):
        raise ValueError('快捷键格式无效，例如 F8、Ctrl+Alt+H')
    modifiers = {'CTRL': 2, 'ALT': 1, 'SHIFT': 4}
    if any(token not in modifiers for token in tokens[:-1]):
        raise ValueError('仅支持 Ctrl、Alt、Shift 修饰键')
    key = tokens[-1]
    if re.fullmatch(r'F(?:[1-9]|1\d|2[0-4])', key):
        if key == 'F12':
            raise ValueError('F12 为 Windows 调试器保留，请选择其他按键')
        vk = 0x70 + int(key[1:]) - 1
    elif re.fullmatch('[A-Z0-9]', key) and len(tokens) > 1:
        vk = ord(key)
    else:
        raise ValueError('使用 F1–F24（除F12），或带修饰键的字母/数字')
    canonical = '+'.join([name.title() for name in modifiers if name in tokens[:-1]] + [key])
    return canonical, sum(modifiers[token] for token in tokens[:-1]) | 0x4000, vk


def load():
    try:
        data = json.loads(SETTINGS.read_text(encoding='utf-8'))
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        raise ValueError('settings.json 必须为对象')
    data['hotkey'] = parse_hotkey(data.get('hotkey', 'F8'))[0]
    return data


def save_hotkey(value):
    canonical = parse_hotkey(value)[0]
    try:
        data = load()
    except (ValueError, OSError):
        if SETTINGS.exists():
            import shutil
            shutil.copy2(SETTINGS, SETTINGS.with_name('settings.broken.' + uuid.uuid4().hex + '.json'))
        data = {}
    data['hotkey'] = canonical
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS.with_name('settings.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, SETTINGS)
    return canonical


def gui():
    import tkinter as tk
    from tkinter import ttk
    import control
    root = tk.Tk()
    root.title('菲比 / 菲八 · 快捷键')
    root.geometry('440x170')
    try:
        initial = load()['hotkey']
    except (ValueError, OSError):
        initial = 'F8'
    text = tk.StringVar(value=initial)
    ttk.Label(root, text='切换快捷键（默认 F8），例如 Ctrl+Alt+H').pack(pady=12)
    ttk.Entry(root, textvariable=text, width=32).pack()
    feedback = tk.StringVar(value='保存后约1秒生效；冲突时保留原快捷键。')
    def apply():
        try:
            key = save_hotkey(text.get())
            feedback.set('已请求改绑 ' + key + '，正在等待服务确认…')
            root.after(1700, confirm)
        except (ValueError, OSError) as error:
            feedback.set(str(error))
    def confirm():
        state = control.read_json(RUNTIME / 'bridge-status.json')
        feedback.set(state.get('hotkey_error') or ('当前生效：' + str(state.get('hotkey', '服务未运行'))))
    ttk.Button(root, text='保存快捷键', command=apply).pack(pady=8)
    ttk.Label(root, textvariable=feedback, wraplength=420).pack()
    root.mainloop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--hotkey')
    parser.add_argument('--gui', action='store_true')
    args = parser.parse_args()
    if args.gui:
        gui()
    elif args.hotkey:
        print(save_hotkey(args.hotkey))
    else:
        print(json.dumps(load(), ensure_ascii=False))

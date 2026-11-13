"""Validated live settings. Windows-reserved shortcuts are not accepted."""
import argparse
import json
import os
import re
import uuid
from pathlib import Path
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
    from control_room import run
    run('hotkeys')


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

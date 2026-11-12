"""Durable ownership-checked native-window recovery after a bridge crash."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import uuid

import control
import native_uia as native

from paths import RUNTIME
JOURNAL = RUNTIME / 'native-recovery.json'
PROPERTY = 'FeibiNativeRecovery'


def api():
    user = native.Windows().user32
    user.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
    user.SetPropW.restype = wintypes.BOOL
    user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.GetPropW.restype = wintypes.HANDLE
    user.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user.RemovePropW.restype = wintypes.HANDLE
    return user


def begin(window):
    if control.read_json(JOURNAL):
        raise RuntimeError('上一次原生窗口恢复尚未完成。')
    nonce = uuid.uuid4().int & 0x7fffffff or 1
    user = api()
    if not user.SetPropW(window.hwnd, PROPERTY, nonce):
        raise RuntimeError('无法登记原生窗口恢复身份。')
    data = dict(hwnd=window.hwnd, pid=window.pid, nonce=nonce, owner_pid=os.getpid())
    JOURNAL.parent.mkdir(exist_ok=True)
    temporary = JOURNAL.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(data, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, JOURNAL)


def recover(cg_visible):
    data = control.read_json(JOURNAL)
    if not data:
        return True
    if cg_visible:
        return False
    user = api()
    hwnd = data.get('hwnd')
    if user.IsWindow(hwnd):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == data.get('pid') and user.GetPropW(hwnd, PROPERTY) == data.get('nonce'):
            user.ShowWindow(hwnd, 4)
            if not user.IsWindowVisible(hwnd):
                return False
            user.RemovePropW(hwnd, PROPERTY)
    JOURNAL.unlink(missing_ok=True)
    return True


def owner_dead():
    data = control.read_json(JOURNAL)
    return bool(data and not control.process_alive(data.get('owner_pid')))

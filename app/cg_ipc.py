"""Direct file IPC to the already-running player, without command processes."""
import json
import os
import time
import uuid
from paths import RUNTIME
import control
from request_lifecycle import decide


class Ticket:
    def __init__(self, action, **parameters):
        self.command_id = uuid.uuid4().hex
        self.path = RUNTIME / 'acks' / (self.command_id+'.json')
        self.returncode = None
        self.output = None
        folder = RUNTIME / 'commands'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (self.command_id+'.json')
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(command_id=self.command_id,action=action,issued_at=time.time(),**parameters)),encoding='utf-8')
        os.replace(temporary,target)

    def poll(self):
        ack = control.read_json(self.path)
        if ack.get('command_id') == self.command_id:
            self.returncode = 0 if ack.get('result')=='completed' else 1
            self.output = json.dumps(ack,ensure_ascii=False).encode('utf-8')
        return self.returncode

    def communicate(self, timeout):
        deadline=time.monotonic()+timeout
        while self.poll() is None:
            if time.monotonic()>deadline:
                raise TimeoutError('等待CG回执超时')
            time.sleep(.01)
        return self.output,b''

    def terminate(self):
        decide(RUNTIME / 'cg-decisions',self.command_id,'cancelled')
        Ticket('hide')


def hide_now():
    """Withdraw only the marked player window; never touch a reused HWND."""
    import ctypes
    from ctypes import wintypes as W
    state=control.status()
    hwnd=state.get('hwnd')
    if not hwnd:
        return False
    user=ctypes.WinDLL('user32',use_last_error=True)
    user.IsWindow.argtypes=[W.HWND]
    user.IsWindowVisible.argtypes=[W.HWND]
    user.GetWindowThreadProcessId.argtypes=[W.HWND,ctypes.POINTER(W.DWORD)]
    user.GetPropW.argtypes=[W.HWND,W.LPCWSTR];user.GetPropW.restype=W.HANDLE
    user.ShowWindow.argtypes=[W.HWND,ctypes.c_int]
    pid=W.DWORD()
    user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
    if not user.IsWindow(hwnd) or pid.value!=state.get('pid') or user.GetPropW(hwnd,'FeibiHatCGOverlay')!=0x46484347:
        return False
    user.ShowWindow(hwnd,0)
    return not user.IsWindowVisible(hwnd)

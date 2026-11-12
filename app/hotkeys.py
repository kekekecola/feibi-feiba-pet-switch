import ctypes
from ctypes import wintypes
import queue
import threading
from settings import parse_hotkey


class GlobalF8:
    def __init__(self, events, hotkey='F8', fallback=None):
        self.events = events
        self.notifications = queue.Queue()
        self.requests = queue.Queue()
        self.ready = threading.Event()
        self.thread_id = None
        self.error = None
        self.active = parse_hotkey(hotkey)[0]
        self.fallback = parse_hotkey(fallback)[0] if fallback else None
        self.registered = False
        self.thread = threading.Thread(target=self.listen, daemon=True)

    def install(self):
        self.thread.start()
        if not self.ready.wait(3):
            raise RuntimeError('快捷键消息线程未就绪')

    def apply_binding(self, user, desired):
        canonical, modifiers, vk = parse_hotkey(desired)
        if self.registered and canonical == self.active:
            self.error = None
        else:
            new_id = 0x46B9 if getattr(self, 'active_id', None) == 0x46B8 else 0x46B8
            if user.RegisterHotKey(None, new_id, modifiers, vk):
                if self.registered:
                    user.UnregisterHotKey(None, self.active_id)
                self.active_id = new_id
                self.active = canonical
                self.registered = True
                self.error = None
            else:
                self.error = f'{canonical} 被其他程序占用或无法注册；当前生效：' + (self.active if self.registered else '无')
        self.notifications.put(dict(hotkey=self.active, hotkey_registered=self.registered, hotkey_error=self.error))

    def listen(self):
        user = ctypes.WinDLL('user32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        user.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        user.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        user.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        user.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
        message = wintypes.MSG()
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        self.thread_id = kernel.GetCurrentThreadId()
        self.apply_binding(user, self.active)
        if not self.registered and self.fallback and self.fallback != self.active:
            requested_error = self.error
            self.apply_binding(user, self.fallback)
            if self.registered:
                self.error = requested_error + '；已恢复上次可用绑定：' + self.active
                self.notifications.put(dict(hotkey=self.active, hotkey_registered=True, hotkey_error=self.error))
        self.ready.set()
        try:
            while user.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == 0x0312 and message.wParam == getattr(self, 'active_id', None):
                    self.events.put('toggle')
                elif message.message == 0x8001:
                    while not self.requests.empty():
                        desired = self.requests.get_nowait()
                        if self.requests.empty():
                            self.apply_binding(user, desired)
        finally:
            if self.registered:
                user.UnregisterHotKey(None, self.active_id)

    def rebind(self, hotkey):
        self.requests.put(parse_hotkey(hotkey)[0])
        ctypes.WinDLL('user32').PostThreadMessageW(self.thread_id, 0x8001, 0, 0)

    def close(self):
        if self.thread_id:
            ctypes.WinDLL('user32').PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=2)

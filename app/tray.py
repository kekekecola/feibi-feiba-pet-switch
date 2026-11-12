"""A hidden native message window and notification icon; no foreground activation."""
import ctypes
from ctypes import wintypes as W
import threading


def status_label(state):
    if state.get('hotkey_error') or state.get('settings_error'):
        return '快捷键异常'
    phase = state.get('phase')
    return {'idle':'就绪', 'preparing':'准备切换', 'transition':'正在切换', 'restoring':'恢复原生',
            'reconnecting':'重新连接', 'waiting_gpt':'等待客户端', 'error':'切换失败', 'stopped':'已退出'}.get(phase, '准备中')


class Tray:
    def __init__(self, events):
        self.events = events
        self.state = {}
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.error = None
        self.hwnd = None
        self.registered = False
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()
        self.ready.wait(3)

    def update(self, state):
        with self.lock:
            self.state = state.copy()
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, 0x8002, 0, 0)

    def close(self):
        if self.hwnd:
            self.user.PostMessageW(self.hwnd, 0x0010, 0, 0)
            self.thread.join(timeout=2)

    def run(self):
        try:
            self.loop()
        except Exception as error:
            self.error = str(error)
        finally:
            self.ready.set()

    def loop(self):
        self.user = u = ctypes.WinDLL('user32', use_last_error=True)
        shell = ctypes.WinDLL('shell32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        class WC(ctypes.Structure):
            _fields_ = [('style', W.UINT), ('proc', proc_type), ('extra', ctypes.c_int), ('window_extra', ctypes.c_int),
                        ('instance', W.HINSTANCE), ('icon', W.HICON), ('cursor', W.HANDLE), ('background', W.HBRUSH),
                        ('menu', W.LPCWSTR), ('name', W.LPCWSTR)]
        class NI(ctypes.Structure):
            _fields_ = [('size', W.DWORD), ('hwnd', W.HWND), ('uid', W.UINT), ('flags', W.UINT),
                        ('callback', W.UINT), ('icon', W.HICON), ('tip', W.WCHAR*128), ('state', W.DWORD),
                        ('mask', W.DWORD), ('info', W.WCHAR*256), ('version', W.UINT), ('title', W.WCHAR*64),
                        ('infoflags', W.DWORD), ('guid', ctypes.c_byte*16), ('balloon', W.HICON)]
        kernel.GetModuleHandleW.restype = W.HMODULE
        u.RegisterClassW.argtypes = [ctypes.POINTER(WC)]
        u.UnregisterClassW.argtypes = [W.LPCWSTR, W.HINSTANCE]
        u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID]
        u.CreateWindowExW.restype = W.HWND
        u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u.DefWindowProcW.restype = ctypes.c_ssize_t
        u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u.DestroyWindow.argtypes = [W.HWND]
        u.SetForegroundWindow.argtypes = [W.HWND]
        u.LoadIconW.argtypes = [W.HINSTANCE, ctypes.c_void_p]
        u.LoadIconW.restype = W.HICON
        u.CreatePopupMenu.restype = W.HMENU
        u.AppendMenuW.argtypes = [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR]
        u.DestroyMenu.argtypes = [W.HMENU]
        u.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, ctypes.c_void_p]
        shell.Shell_NotifyIconW.argtypes = [W.DWORD, ctypes.POINTER(NI)]
        u.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
        taskbar_created = u.RegisterWindowMessageW('TaskbarCreated')
        self.data = NI(size=ctypes.sizeof(NI), uid=1, flags=7, callback=0x8001)
        def notify(add=False):
            with self.lock:
                state = self.state.copy()
            self.data.tip = (f"菲比/菲八 {state.get('version', '')} · {state.get('hotkey', 'F8')} · " + status_label(state))[:127]
            self.data.icon = u.LoadIconW(None, 32513 if state.get('error') or state.get('hotkey_error') else 32516)
            operation = 0 if add or not self.registered else 1
            self.registered = bool(shell.Shell_NotifyIconW(operation, ctypes.byref(self.data)))
            if operation == 0 and self.registered:
                self.data.version = 3
                shell.Shell_NotifyIconW(4, ctypes.byref(self.data))
        def menu():
            handle = u.CreatePopupMenu()
            try:
                with self.lock:
                    state = self.state.copy()
                u.AppendMenuW(handle, 1, 0, status_label(state) + ' · ' + state.get('hotkey', 'F8'))
                actions = [(1,'切换戴帽 / 摘帽','toggle'), (2,'一键诊断','diagnose'), (3,'设置快捷键','settings'),
                           (4,'检查并应用更新','update'), (5,'退出切换服务','quit')]
                for key, label, _ in actions:
                    u.AppendMenuW(handle, 0, key, label)
                point = W.POINT()
                u.GetCursorPos(ctypes.byref(point))
                # Only an explicit user-opened menu activates this hidden owner.
                u.SetForegroundWindow(self.hwnd)
                chosen = u.TrackPopupMenu(handle, 0x0100 | 0x0002, point.x, point.y, 0, self.hwnd, None)
                u.PostMessageW(self.hwnd, 0, 0, 0)
                for key, _, action in actions:
                    if key == chosen:
                        self.events.put(action)
            finally:
                u.DestroyMenu(handle)
        def callback(hwnd, message, wp, lp):
            if message == 0x8001:
                if lp in (0x0205, 0x007B):
                    menu()
                elif lp == 0x0203:
                    self.events.put('diagnose')
                return 0
            if message == 0x8002 or message == taskbar_created:
                notify(add=message == taskbar_created)
                return 0
            if message == 0x0010:
                shell.Shell_NotifyIconW(2, ctypes.byref(self.data))
                self.registered = False
                u.DestroyWindow(hwnd)
                u.PostQuitMessage(0)
                return 0
            return u.DefWindowProcW(hwnd, message, wp, lp)
        self.callback = proc_type(callback)
        instance = kernel.GetModuleHandleW(None)
        name = 'FeibiPetSwitchTray'
        wc = WC(proc=self.callback, instance=instance, name=name)
        if not u.RegisterClassW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.hwnd = u.CreateWindowExW(0x08000080, name, name, 0, 0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self.data.hwnd = self.hwnd
            notify(add=True)
            self.ready.set()
            message = W.MSG()
            u.GetMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT]
            u.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]
            while u.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                u.TranslateMessage(ctypes.byref(message))
                u.DispatchMessageW(ctypes.byref(message))
        finally:
            shell.Shell_NotifyIconW(2, ctypes.byref(self.data))
            u.UnregisterClassW(name, instance)

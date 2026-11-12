"""A hidden native message window and notification icon; no foreground activation."""
import ctypes
from ctypes import wintypes as W
import threading
from pathlib import Path


def menu_entries(state, updater=None, managed=False):
    updater = updater or {}
    names = {'feibi':'菲比啾比', 'feiba':'菲八啾比'}
    form = state.get('form')
    ready = state.get('phase') == 'idle' and state.get('connection_state') == 'ready' and form in names
    label, action = ('戴帽 → 菲比啾比', 'put-on') if form == 'feiba' else ('摘帽 → 菲八啾比', 'take-off')
    if form not in names:
        label, action = '切换宠物形态', 'toggle'
    entries = [dict(label='菲比啾比 / 菲八啾比  v' + state.get('version',''), enabled=False),
               dict(label=status_label(state) + ' · 当前：' + names.get(form,'尚未识别'), enabled=False),
               dict(label='切换快捷键：' + state.get('hotkey','F8'), enabled=False), None,
               dict(id=1, label=label, action=action, enabled=ready), None,
               dict(id=3, label='快捷键设置…', action='settings'),
               dict(id=2, label='状态与诊断…', action='diagnose'),
               dict(id=8, label='打开日志文件夹', action='logs'), None]
    updating = updater.get('phase') in ('checking','downloading','preparing','applying')
    entries += [dict(id=4, label='正在检查或安装更新…' if updating else '检查并安装更新',
                     action='update', enabled=managed and not updating)]
    if not managed:
        entries.append(dict(label='自动更新尚未开启，详见项目说明', enabled=False))
    elif updater.get('phase') == 'error':
        entries.append(dict(label='上次更新失败，请查看状态与诊断', enabled=False))
    elif updater.get('installed_version'):
        entries.append(dict(label='已安装：v' + updater['installed_version'], enabled=False))
    entries += [dict(id=9,label='使用说明与项目主页',action='help'), None,
                dict(id=5,label='退出并停用 ' + state.get('hotkey','快捷键'),action='quit')]
    return entries


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
        self.icon_loaded = False
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
        u.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int, W.UINT]
        u.LoadImageW.restype = W.HANDLE
        u.DestroyIcon.argtypes = [W.HICON]
        u.CreatePopupMenu.restype = W.HMENU
        u.AppendMenuW.argtypes = [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR]
        u.DestroyMenu.argtypes = [W.HMENU]
        u.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, ctypes.c_void_p]
        shell.Shell_NotifyIconW.argtypes = [W.DWORD, ctypes.POINTER(NI)]
        u.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
        taskbar_created = u.RegisterWindowMessageW('TaskbarCreated')
        self.data = NI(size=ctypes.sizeof(NI), uid=1, flags=7, callback=0x8001)
        icon = u.LoadImageW(None, str(Path(__file__).parent / 'assets' / 'feibi-tray.ico'), 1,
                            u.GetSystemMetrics(49), u.GetSystemMetrics(50), 0x0010)
        self.icon_loaded = bool(icon)
        def notify(add=False):
            with self.lock:
                state = self.state.copy()
            self.data.tip = (f"菲比/菲八 {state.get('version', '')} · {state.get('hotkey', 'F8')} · " + status_label(state))[:127]
            self.data.icon = icon or u.LoadIconW(None, 32516)
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
                import os
                import control
                from paths import RUNTIME
                entries = menu_entries(state, control.read_json(RUNTIME / 'update-status.json'), bool(os.environ.get('FEIBI_SWITCH_HOME')))
                for entry in entries:
                    if entry is None:
                        u.AppendMenuW(handle, 0x800, 0, None)
                    else:
                        u.AppendMenuW(handle, 0 if entry.get('enabled',True) else 1, entry.get('id',0), entry['label'])
                point = W.POINT()
                u.GetCursorPos(ctypes.byref(point))
                # Only an explicit user-opened menu activates this hidden owner.
                u.SetForegroundWindow(self.hwnd)
                chosen = u.TrackPopupMenu(handle, 0x0100 | 0x0002, point.x, point.y, 0, self.hwnd, None)
                u.PostMessageW(self.hwnd, 0, 0, 0)
                for entry in entries:
                    if entry and entry.get('id') == chosen and entry.get('enabled',True) and entry.get('action'):
                        self.events.put(entry['action'])
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
            if icon:
                u.DestroyIcon(icon)
            u.UnregisterClassW(name, instance)

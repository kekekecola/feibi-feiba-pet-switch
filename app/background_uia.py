"""An owned, invisible native settings window for focus-free pet selection."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import threading
import time

import native_uia as native
import msaa_controls
import ui_labels

from paths import RUNTIME
STATE = RUNTIME / "background-settings.json"
MARKER = "FeibiNativeBackgroundSettings"
MARKER_VALUE = 0x46484942


def api():
    windows = native.Windows()
    u = windows.user32
    u.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    u.GetPropW.restype = wintypes.HANDLE
    u.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
    u.SetPropW.restype = wintypes.BOOL
    u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_ubyte, wintypes.DWORD]
    u.SetLayeredWindowAttributes.restype = wintypes.BOOL
    u.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    setter = u.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else u.SetWindowLongW
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    setter.restype = ctypes.c_ssize_t
    return windows, setter


def invoke_background(control):
    auto = native.automation()
    for kind, method in ((auto.PatternId.InvokePattern, "Invoke"),
                         (auto.PatternId.TogglePattern, "Toggle"),
                         (auto.PatternId.SelectionItemPattern, "Select"),
                         (auto.PatternId.LegacyIAccessiblePattern, "DoDefaultAction")):
        pattern = control.GetPattern(kind)
        if pattern:
            getattr(pattern, method)(waitTime=0)
            return
    raise RuntimeError("后台控件不支持直接调用，已取消；不会移动鼠标或激活前台。")


def owned_window():
    windows, _ = api()
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    all_windows = windows.enumerate()
    for window in all_windows:
        if (window.hwnd == state.get("hwnd") and window.pid == state.get("pid")
                and windows.user32.GetPropW(window.hwnd, MARKER) == MARKER_VALUE):
            return window
    # A setup provider may hang after creating the window but before returning.
    # Recover only our uniquely marked helper, never an arbitrary GPT window.
    marked = [w for w in all_windows if windows.user32.GetPropW(w.hwnd, MARKER) == MARKER_VALUE]
    if len(marked) == 1:
        helper = marked[0]
        mains = [w for w in all_windows if w.hwnd != helper.hwnd and w.visible
                 and w.class_name == 'Chrome_WidgetWin_1' and w.title == 'ChatGPT'
                 and not w.exstyle & (0x80 | 0x8)]
        save_state(helper, mains[0].hwnd if mains else None)
        invisible(helper)
        return helper
    return None


def save_state(window, main_hwnd):
    STATE.parent.mkdir(exist_ok=True)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps({'hwnd': window.hwnd, 'pid': window.pid,
                                    'original_main_hwnd': main_hwnd}), encoding='utf-8')
    os.replace(temporary, STATE)


def click_background(control, window):
    """Deliver pointer messages to the matched renderer without cursor/focus input."""
    windows, _ = api()
    u = windows.user32
    bounds = control.BoundingRectangle
    x, y = (bounds.left + bounds.right) // 2, (bounds.top + bounds.bottom) // 2
    candidates = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        name = ctypes.create_unicode_buffer(256)
        u.GetClassNameW(hwnd, name, len(name))
        if name.value == "Chrome_RenderWidgetHostHWND":
            rect = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(rect))
            if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
                candidates.append(hwnd)
        return True
    u.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    u.EnumChildWindows(window.hwnd, callback_type(visit), 0)
    # Current Electron/Aura builds host input on Chrome_WidgetWin_1 itself
    # and expose only an Intermediate D3D Window as a child.
    if not candidates:
        candidates.append(window.hwnd)
    if len(candidates) != 1:
        raise RuntimeError("无法唯一定位后台按钮所在的渲染窗口。")
    point = wintypes.POINT(x, y)
    u.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    u.ScreenToClient(candidates[0], ctypes.byref(point))
    position = (point.y & 0xffff) << 16 | (point.x & 0xffff)
    u.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
        wintypes.LPARAM, wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t)]
    u.SendMessageTimeoutW.restype = wintypes.LPARAM
    result = ctypes.c_size_t()
    for message, flags in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
        if not u.SendMessageTimeoutW(candidates[0], message, flags, position, 2, 1000, ctypes.byref(result)):
            raise RuntimeError("后台控件没有响应指针消息。")


def invisible(window):
    windows, setter = api()
    style = windows.get_long(window.hwnd, -20)
    # Keep WS_VISIBLE for Chromium accessibility, while preventing painting/focus/taskbar.
    setter(window.hwnd, -20, (style | 0x00080000 | 0x08000000 | 0x80) & ~0x40000)
    if not windows.user32.SetLayeredWindowAttributes(window.hwnd, 0, 0, 2):
        raise ctypes.WinError(ctypes.get_last_error())


def restore_setup_focus(target, helper, original_main=None):
    windows, _ = api()
    if not target or not windows.user32.IsWindow(target) or not windows.user32.IsWindowVisible(target):
        target = original_main
    if not target or not windows.user32.IsWindow(target):
        return
    # Chromium can finish focusing its new page after the shortcut returns.
    for _ in range(5):
        current = windows.user32.GetForegroundWindow()
        if current not in (helper, target, original_main):
            return  # Respect a user who deliberately switched to another application.
        if current != target:
            windows.user32.SetForegroundWindow(target)
            if windows.user32.GetForegroundWindow() != target:
                # Reconnect preparation temporarily activates an invisible native
                # window. Join its input queue only while restoring the real view.
                user = windows.user32
                user.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
                user.BringWindowToTop.argtypes = [wintypes.HWND]
                kernel = windows.kernel32
                kernel.GetCurrentThreadId.restype = wintypes.DWORD
                thread = kernel.GetCurrentThreadId()
                foreground_thread = user.GetWindowThreadProcessId(current, None)
                attached = thread != foreground_thread and user.AttachThreadInput(thread, foreground_thread, True)
                try:
                    user.BringWindowToTop(target)
                    user.SetForegroundWindow(target)
                finally:
                    if attached:
                        user.AttachThreadInput(thread, foreground_thread, False)
        time.sleep(0.06)


def prepare():
    existing = owned_window()
    if existing:
        try:
            msaa_controls.pet_actions(existing.hwnd, native.PET_NAMES)
            ready = True
        except RuntimeError as error:
            if isinstance(error, msaa_controls.PetControlsError) and error.details['duplicates']:
                raise
            ready = False
        if ready:
            windows, _ = api()
            windows.user32.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
            windows.user32.EnableWindow(existing.hwnd, False)
            return {"prepared": True, "hwnd": existing.hwnd, "reused": True}
        return finish_prepare(existing)
    windows, _ = api()
    main = windows.main()
    foreground = windows.user32.GetForegroundWindow()
    original = {w.hwnd for w in windows.enumerate()}
    root = native.automation().ControlFromHandle(main.hwnd)
    menu = root.MenuItemControl(searchDepth=26, RegexName=ui_labels.pattern("new_window"))
    if not native.exists(menu, 0.3):
        file_menu = root.MenuItemControl(searchDepth=24, AutomationId="application-menu-trigger-file-menu")
        if not native.exists(file_menu, 1):
            raise RuntimeError("找不到 GPT 新窗口菜单入口。")
        invoke_background(file_menu)
        if not native.exists(menu, 1):
            # Only startup preparation may use the foreground native menu.
            # Ordinary F8 operations never enter this provisioning path.
            click_background(file_menu, main)
        if not native.exists(menu, 2):
            raise RuntimeError("找不到 GPT 新建窗口菜单。")
    found = []
    stop = threading.Event()

    def catch_new_window():
        deadline = time.monotonic() + 12
        while not stop.is_set() and time.monotonic() < deadline:
            for window in windows.enumerate():
                if (window.pid == main.pid and window.hwnd not in original
                        and window.class_name == "Chrome_WidgetWin_1" and window.title == "ChatGPT"):
                    windows.user32.SetPropW(window.hwnd, MARKER, MARKER_VALUE)
                    save_state(window, main.hwnd)
                    invisible(window)
                    if not found:
                        found.append(window)
                    return
            stop.wait(0.025)

    watcher = threading.Thread(target=catch_new_window, daemon=True)
    watcher.start()
    try:
        invoke_background(menu)
        deadline = time.monotonic() + 10
        while not found and time.monotonic() < deadline:
            time.sleep(0.05)
        if not found:
            raise RuntimeError("未找到新创建的 GPT 辅助窗口。")
        helper = found[0]
        save_state(helper, main.hwnd)
        return finish_prepare(helper)
    finally:
        stop.set()
        watcher.join(timeout=1)
        if found:
            restore_setup_focus(foreground, found[0].hwnd, main.hwnd)
        elif foreground and windows.user32.IsWindow(foreground):
            windows.user32.SetForegroundWindow(foreground)


def finish_prepare(helper):
    windows, _ = api()
    user = windows.user32
    user.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user.EnableWindow(helper.hwnd, True)
    r = native.automation().ControlFromHandle(helper.hwnd)
    section = r.ButtonControl(searchDepth=27, RegexName=ui_labels.pattern("pets"))
    if not native.exists(section, 0.5):
        profile = r.ButtonControl(searchDepth=27, RegexName=ui_labels.pattern("profile"))
        if not native.exists(profile, 1):
            # A brand-new, fully transparent Chromium window may never publish
            # its first accessibility tree. Briefly allow a nearly-transparent
            # frame, without activation; hide it completely after initialization.
            user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
            user.SetLayeredWindowAttributes(helper.hwnd, 0, 1, 2)
            user.SetWindowPos(helper.hwnd, wintypes.HWND(-1), 0, 0, 0, 0, 0x0013 | 0x0040)
            try:
                if not native.exists(profile, 8):
                    raise RuntimeError("辅助 GPT 窗口尚未加载完成。")
            finally:
                user.SetLayeredWindowAttributes(helper.hwnd, 0, 0, 2)
                user.SetWindowPos(helper.hwnd, wintypes.HWND(-2), 0, 0, 0, 0, 0x0013)
        settings = r.MenuItemControl(searchDepth=28, RegexName=ui_labels.pattern("settings"))
        if not native.exists(settings, 0.2):
            click_background(profile, helper)
        if not native.exists(settings, 3):
            raise RuntimeError("后台设置菜单未打开。")
        invoke_background(settings)
        if not native.exists(section, 1):
            click_background(settings, helper)
        if not native.exists(section, 3):
            raise RuntimeError("后台设置页面未就绪。")
    invoke_background(section)
    try:
        msaa_controls.pet_actions(helper.hwnd, native.PET_NAMES)
    except RuntimeError:
        click_background(section, helper)
        deadline = time.monotonic() + 12
        while True:
            try:
                msaa_controls.pet_actions(helper.hwnd, native.PET_NAMES)
                break
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.4)
    invisible(helper)
    user.EnableWindow(helper.hwnd, False)
    return {"prepared": True, "hwnd": helper.hwnd, "reused": False}


class BackgroundSelection:
    def __init__(self, windows):
        self.windows = windows
        self.window = owned_window()
        if not self.window:
            raise RuntimeError("后台设置窗口未就绪。请先重新启动 F8 切换器完成准备。")
        self.original_main = json.loads(STATE.read_text(encoding="utf-8")).get("original_main_hwnd")
        if windows.user32.GetForegroundWindow() == self.window.hwnd:
            restore_setup_focus(self.original_main, self.window.hwnd, self.original_main)
        self.root = native.automation().ControlFromHandle(self.window.hwnd)
        self.foreground_before = windows.user32.GetForegroundWindow()

    def prepare(self, target):
        self.repaired = False
        try:
            return msaa_controls.pet_actions(self.window.hwnd, native.PET_NAMES)[target]
        except RuntimeError as error:
            if isinstance(error, msaa_controls.PetControlsError) and error.details['duplicates']:
                raise
            # No selection or CG has happened yet: rebuilding here cannot
            # repeat a toggle which may already have taken effect.
            logging.warning('Pet controls stale; repairing helper before selection')
            prepare()
            window = owned_window()
            if window is None:
                raise RuntimeError('后台设置窗口恢复失败，未执行原生切换。')
            self.window = window
            self.root = native.automation().ControlFromHandle(window.hwnd)
            self.repaired = True
            return msaa_controls.pet_actions(window.hwnd, native.PET_NAMES)[target]

    def select(self, target, card):
        user = self.windows.user32
        user.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        user.EnableWindow(self.window.hwnd, False)
        try:
            previous_id = native.preferences().get('selected-avatar-id')
            msaa_controls.execute(card)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if native.selected_form() == target:
                    return
                if native.preferences().get('selected-avatar-id') != previous_id:
                    if native.reconcile_selected(target) == target:
                        return
                time.sleep(0.08)
            raise RuntimeError("后台原生选择没有得到确认。")
        finally:
            user.EnableWindow(self.window.hwnd, False)

    def restore(self):
        # Re-created Chromium windows can request focus after loading completes.
        if self.windows.user32.GetForegroundWindow() == self.window.hwnd:
            target = self.foreground_before if self.foreground_before != self.window.hwnd else self.original_main
            restore_setup_focus(target, self.window.hwnd, self.original_main)
        return


def release_helper_focus():
    windows, _ = api()
    current = windows.user32.GetForegroundWindow()
    if windows.user32.GetPropW(current, MARKER) != MARKER_VALUE:
        return
    window = owned_window()
    if window and window.hwnd == current:
        original = json.loads(STATE.read_text(encoding="utf-8")).get("original_main_hwnd")
        restore_setup_focus(original, window.hwnd, original)


def close():
    window = owned_window()
    if window:
        windows, _ = api()
        windows.user32.PostMessageW(window.hwnd, 0x0010, 0, 0)
        deadline = time.monotonic() + 2
        while windows.user32.IsWindow(window.hwnd) and time.monotonic() < deadline:
            time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "status", "close"))
    action = parser.parse_args().action
    native.dpi_awareness()
    if action == "prepare":
        print(json.dumps(prepare(), ensure_ascii=True))
    elif action == "status":
        window = owned_window()
        print(json.dumps({"prepared": window is not None, "hwnd": window.hwnd if window else None}))
    else:
        close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

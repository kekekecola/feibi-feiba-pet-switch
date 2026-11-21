"""An owned, invisible native settings window for focus-free pet selection."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import time

import native_uia as native
import msaa_controls

from paths import RUNTIME
STATE = RUNTIME / "background-settings.json"
MARKER = "FeibiNativeBackgroundSettings"
MARKER_VALUE = 0x46484942
_next_recovery_scan = 0


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


def owned_window():
    global _next_recovery_scan
    windows, _ = api()
    try:
        state=json.loads(STATE.read_text(encoding='utf-8'))
        if not isinstance(state,dict):state={}
    except (OSError,ValueError):
        state={}
    existing=window_from_state(windows,state)
    if existing:
        return existing
    if time.monotonic()<_next_recovery_scan:
        return None
    _next_recovery_scan=time.monotonic()+3
    all_windows = windows.enumerate()
    # A setup provider may hang after creating the window but before returning.
    # Recover only our uniquely marked helper, never an arbitrary GPT window.
    marked = [w for w in all_windows if w.exstyle & 0x80
              and w.hwnd != state.get('original_main_hwnd')
              and windows.user32.GetPropW(w.hwnd, MARKER) == MARKER_VALUE]
    if len(marked) == 1:
        helper = marked[0]
        mains = [w for w in all_windows if w.hwnd != helper.hwnd and w.visible
                 and w.class_name == 'Chrome_WidgetWin_1' and w.title == 'ChatGPT'
                 and not w.exstyle & (0x80 | 0x8)]
        save_state(helper, mains[0].hwnd if mains else None)
        invisible(helper)
        return helper
    return None


def window_from_state(windows,state):
    hwnd,pid=state.get('hwnd'),state.get('pid')
    user=windows.user32
    if (type(hwnd) is not int or type(pid) is not int
            or hwnd == state.get('original_main_hwnd') or not user.IsWindow(hwnd)):
        return None
    actual=wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd,ctypes.byref(actual))
    if actual.value!=pid or user.GetPropW(hwnd,MARKER)!=MARKER_VALUE:
        return None
    rect=wintypes.RECT()
    if not user.GetWindowRect(hwnd,ctypes.byref(rect)):
        return None
    return native.NativeWindow(hwnd,pid,'ChatGPT','Chrome_WidgetWin_1',bool(user.IsWindowVisible(hwnd)),
        windows.get_long(hwnd,-16),windows.get_long(hwnd,-20),
        (rect.left,rect.top,rect.right,rect.bottom),int(user.GetDpiForWindow(hwnd) or 96))


def save_state(window, main_hwnd):
    STATE.parent.mkdir(exist_ok=True)
    temporary = STATE.with_suffix('.tmp')
    temporary.write_text(json.dumps({'hwnd': window.hwnd, 'pid': window.pid,
                                    'original_main_hwnd': main_hwnd}), encoding='utf-8')
    os.replace(temporary, STATE)


def parked_position(window, user):
    return user.GetSystemMetrics(76)-max(1,window.rect[2]-window.rect[0])-128, user.GetSystemMetrics(77)


def invisible(window, alpha=0):
    windows, setter = api()
    style = windows.get_long(window.hwnd, -20)
    # Keep WS_VISIBLE for Chromium accessibility, while preventing painting/focus/taskbar.
    setter(window.hwnd, -20, (style | 0x00080000 | 0x08000000 | 0x80 | 0x20) & ~0x40000)
    if not windows.user32.SetLayeredWindowAttributes(window.hwnd, 0, alpha, 2):
        raise ctypes.WinError(ctypes.get_last_error())
    user=windows.user32
    user.SetWindowPos.argtypes=[wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,wintypes.UINT]
    x,y=parked_position(window,user)
    if not user.SetWindowPos(window.hwnd,wintypes.HWND(1),x,y,0,0,0x0001 | 0x0010 | 0x0040):
        raise ctypes.WinError(ctypes.get_last_error())


def is_dedicated_candidate(window, pid, original):
    return (window.pid == pid and window.hwnd not in original and window.visible
            and window.class_name == 'Chrome_WidgetWin_1' and window.title == 'ChatGPT'
            and not window.exstyle & (0x80 | 0x08))


def begin_binding():
    windows, _ = api()
    main = windows.main()
    session = windows.process_session(main.pid)
    existing = [w.hwnd for w in windows.enumerate() if w.pid == main.pid]
    if not session or main.hwnd not in existing:
        raise RuntimeError('无法确认当前 Codex 窗口，请打开 Codex 后再试。')
    return {'pid': main.pid, 'main': main.hwnd, 'session': session,
            'original': existing, 'expires': time.time() + 180}


def binding_candidate(windows, binding):
    if (time.time() >= binding['expires'] or
            windows.process_session(binding['pid']) != binding['session'] or
            not windows.user32.IsWindow(binding['main'])):
        raise RuntimeError('这次绑定已过期，或 Codex 已重启。请重新准备。')
    candidates = [w for w in windows.enumerate() if
                  is_dedicated_candidate(w, binding['pid'], binding['original'])
                  and not windows.user32.IsIconic(w.hwnd)]
    if len(candidates) != 1:
        raise RuntimeError('请只新建一个专用 Codex 窗口，并在新窗口打开宠物设置页。')
    return candidates[0]


def bind_new_window(binding):
    windows, setter = api()
    candidate = binding_candidate(windows, binding)
    # Only read before the dedicated window has been confirmed. A wrong page
    # stays visible for correction, and any COM timeout here has no side effect.
    wait_for_pet_actions(candidate.hwnd, 3)
    if binding_candidate(windows, binding).hwnd != candidate.hwnd:
        raise RuntimeError('新窗口已变更，未完成绑定。')
    old = owned_window()
    user = windows.user32
    if user.GetForegroundWindow() == candidate.hwnd:
        raise RuntimeError('请在控制室里确认绑定，让专用窗口处于后台。')
    if not user.SetPropW(candidate.hwnd, MARKER, MARKER_VALUE):
        raise RuntimeError('无法登记新窗口的辅助用途。')
    try:
        invisible(candidate)
        save_state(candidate, binding['main'])
    except Exception:
        user.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user.RemovePropW(candidate.hwnd, MARKER)
        user.SetLayeredWindowAttributes(candidate.hwnd, 0, 255, 2)
        setter(candidate.hwnd, -20, candidate.exstyle)
        user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user.SetWindowPos(candidate.hwnd, None, *candidate.rect[:2], 0, 0, 0x0015)
        raise
    user.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user.EnableWindow(candidate.hwnd, False)
    if old and old.hwnd != candidate.hwnd and window_from_state(windows, {'hwnd':old.hwnd,'pid':old.pid}):
        user.PostMessageW(old.hwnd, 0x0010, 0, 0)
    return {'bound': True}


def prepare():
    helper = owned_window()
    if helper is None:
        raise RuntimeError('等待绑定辅助窗口，请在控制室的伙伴状态中准备绑定。')
    try:
        msaa_controls.pet_actions(helper.hwnd, native.PET_NAMES)
    except RuntimeError as error:
        if ambiguous_cards(error):
            raise
        return finish_prepare(helper)
    return {'prepared': True, 'hwnd': helper.hwnd, 'reused': True}


def ambiguous_cards(error):
    return isinstance(error, msaa_controls.PetControlsError) and (
        error.details['duplicates'] or error.details['traversal_limited'])


def wait_for_pet_actions(hwnd, seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return msaa_controls.pet_actions(hwnd, native.PET_NAMES)
        except RuntimeError as error:
            if ambiguous_cards(error) or time.monotonic() >= deadline:
                raise
            time.sleep(0.25)


def finish_prepare(helper, target=None):
    windows, _ = api()
    user = windows.user32
    live = window_from_state(windows, {'hwnd': helper.hwnd, 'pid': helper.pid})
    if not live or user.GetForegroundWindow() == helper.hwnd:
        raise RuntimeError('辅助窗口身份或前台状态有变化，请重新绑定。')
    helper = live
    user.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    # Park before enabling or changing alpha: even a killed setup worker must
    # never leave a near-transparent, enabled window over the user's desktop.
    invisible(helper,alpha=1)
    user.EnableWindow(helper.hwnd, True)
    # Alpha-zero Chromium pages can stop publishing/updating their accessibility
    # tree on other GPUs. Allow initialization at alpha 1, without activation.
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    try:
        return _finish_prepare(helper, target)
    finally:
        try:
            invisible(helper)
        finally:
            user.EnableWindow(helper.hwnd, False)


def _finish_prepare(helper, target=None):
    actions = wait_for_pet_actions(helper.hwnd, 5)
    return actions[target] if target else {'prepared': True, 'hwnd': helper.hwnd, 'reused': True}


class BackgroundSelection:
    def __init__(self, windows):
        self.windows = windows
        self.window = owned_window()
        if not self.window:
            raise RuntimeError("后台设置窗口未就绪。请先重新启动 F8 切换器完成准备。")

    def prepare(self, target, repair=True, reuse_only=False):
        self.repaired = False
        try:
            return msaa_controls.pet_actions(self.window.hwnd, native.PET_NAMES)[target]
        except RuntimeError as error:
            if ambiguous_cards(error):
                raise
            if not repair:
                if isinstance(error, msaa_controls.PetControlsError):
                    raise
                raise RuntimeError('后台宠物页面失效，后台将重新准备。') from error
            # No native selection has happened yet: rebuilding here cannot
            # repeat a toggle which may already have taken effect.
            logging.warning('Pet controls stale; repairing helper before selection')
            if reuse_only:
                # This attempt occurs before the first CG frame and repairs
                # only the helper already owned by this request.
                existing = owned_window()
                if not existing or (existing.hwnd, existing.pid) != (self.window.hwnd, self.window.pid):
                    raise RuntimeError('辅助窗口已变更，后台将重新连接。') from error
                action = finish_prepare(existing, target)
                self.repaired = True
                return action
            prepare()
            window = owned_window()
            if window is None:
                raise RuntimeError('后台设置窗口恢复失败，未执行原生切换。')
            self.window = window
            self.repaired = True
            return msaa_controls.pet_actions(window.hwnd, native.PET_NAMES)[target]

    def select(self, target, card, before_action=None):
        user = self.windows.user32
        user.EnableWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
        # Some Chromium providers silently ignore MSAA actions when the parent
        # HWND is disabled. Enable only the verified, click-through parked helper.
        windows, _ = api()
        hwnd = self.window.hwnd
        identity = {'hwnd': hwnd, 'pid': self.window.pid}
        helper = window_from_state(windows, identity)
        required_styles = 0x00080000 | 0x08000000 | 0x20 | 0x80
        if (not helper or helper.rect[2] > user.GetSystemMetrics(76)
                or (helper.exstyle & required_styles) != required_styles or helper.exstyle & 0x8
                or user.GetForegroundWindow() == hwnd):
            raise RuntimeError('辅助窗口未安全停放，已取消原生选择。')
        previous_id = native.preferences().get('selected-avatar-id')
        try:
            user.EnableWindow(hwnd, True)
            if before_action:
                before_action()
            msaa_controls.execute(card)
            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                if native.selected_form(refresh=False) == target:
                    return
                if native.preferences().get('selected-avatar-id') != previous_id:
                    if native.reconcile_selected(target) == target:
                        return
                time.sleep(0.08)
            raise RuntimeError("原生宠物选择没有得到确认，请在诊断中查看本次切换结果。")
        finally:
            if window_from_state(windows, identity):
                user.EnableWindow(hwnd, False)

    def restore(self):
        release_helper_focus()


def release_helper_focus():
    """Keep only the owned helper off screen; never activate a user window."""
    windows, _ = api()
    helper = owned_window()
    if helper and (helper.rect[2] >= windows.user32.GetSystemMetrics(76)
                   or helper.exstyle & 0x8 or not helper.exstyle & 0x20):
        invisible(helper)


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
    parser.add_argument("action", choices=("prepare", "status", "close", "bind"))
    parser.add_argument("--binding")
    args = parser.parse_args()
    action = args.action
    native.dpi_awareness()
    if action == "bind":
        import sys
        sys.coinit_flags = 0
        sys.stdout.reconfigure(encoding="utf-8")
        auto = native.automation()
        with auto.UIAutomationInitializerInThread():
            try:
                print(json.dumps(bind_new_window(json.loads(args.binding)), ensure_ascii=False))
            except Exception as error:
                print(json.dumps({"error": str(error)}, ensure_ascii=False))
                return 1
    elif action == "prepare":
        print(json.dumps(prepare(), ensure_ascii=True))
    elif action == "status":
        window = owned_window()
        print(json.dumps({"prepared": window is not None, "hwnd": window.hwnd if window else None}))
    else:
        close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

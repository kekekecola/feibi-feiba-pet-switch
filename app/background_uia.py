"""An owned, invisible native settings window for focus-free pet selection."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import logging
import os
import re
from pathlib import Path
import time
import threading

import native_uia as native
import msaa_controls
import ui_labels

from paths import RUNTIME
STATE = RUNTIME / "background-settings.json"
MENU_LEASE = RUNTIME / "connect-menu.json"
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


def click_background(control, window):
    """Deliver pointer messages to the matched renderer without cursor/focus input."""
    windows, _ = api()
    u = windows.user32
    live = window_from_state(windows, {'hwnd':window.hwnd, 'pid':window.pid})
    if not live or live.rect[2] >= u.GetSystemMetrics(76) or not live.exstyle & 0x08000000:
        raise RuntimeError('只允许操作已停放的后台设置控件。')
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
    try:
        for message, flags in ((0x0200, 0), (0x0201, 1), (0x0202, 0)):
            if not u.SendMessageTimeoutW(candidates[0], message, flags, position, 2, 1000, ctypes.byref(result)):
                raise RuntimeError("后台控件没有响应指针消息。")
    except Exception:
        # Never leave a provider with a synthetic button-down/capture after a
        # timeout. These messages target only this operation's renderer.
        u.PostMessageW(candidates[0], 0x0202, 0, position)
        u.PostMessageW(candidates[0], 0x001f, 0, 0)
        raise


class LastInput(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('tick', wintypes.DWORD)]


def input_tick(user):
    state = LastInput(ctypes.sizeof(LastInput), 0)
    user.GetLastInputInfo.argtypes = [ctypes.POINTER(LastInput)]
    return state.tick if user.GetLastInputInfo(ctypes.byref(state)) else None


def capture_setup_intent():
    windows, _ = api()
    user = windows.user32
    foreground = user.GetForegroundWindow()
    main = next((w for w in windows.enumerate() if w.hwnd == foreground and w.visible
                 and w.class_name == 'Chrome_WidgetWin_1' and w.title == 'ChatGPT'
                 and not w.exstyle & (0x80 | 0x8)), None)
    if not main or user.IsIconic(main.hwnd):
        return None
    tick = input_tick(user)
    session = windows.process_session(main.pid)
    if tick is None or not session:
        return None
    return {'hwnd': main.hwnd, 'pid': main.pid, 'session': session, 'input': tick,
            'captured': time.monotonic()}


def same_foreground(windows, intent):
    user = windows.user32
    if (not intent or user.GetForegroundWindow() != intent['hwnd']
            or windows.process_session(intent['pid']) != intent['session']):
        return False
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(intent['hwnd'], ctypes.byref(pid))
    return pid.value == intent['pid']


def still_user_request(windows, intent):
    return same_foreground(windows, intent) and input_tick(windows.user32) == intent['input']


def file_pattern(root):
    from comtypes import COMError
    from uiautomation.uiautomation import _AutomationClient
    auto = native.automation()
    uia = _AutomationClient.instance().IUIAutomation
    condition = uia.CreatePropertyCondition(auto.PropertyId.AutomationIdProperty, 'application-menu-trigger-file-menu')
    try:
        control = auto.Control.CreateControlFromElement(root.Element.FindFirst(4, condition))
        return control.GetPattern(auto.PatternId.ExpandCollapsePattern) if control else None
    except COMError:
        return None  # The menu provider may be replaced during startup.


def return_original_focus(windows, intent, helper_hwnd):
    user = windows.user32
    if not helper_hwnd or user.GetForegroundWindow() != helper_hwnd:
        return
    if not window_from_state(windows, {'hwnd':helper_hwnd, 'pid':intent['pid']}):
        return
    if not user.IsWindow(intent['hwnd']) or windows.process_session(intent['pid']) != intent['session']:
        return
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(intent['hwnd'], ctypes.byref(pid))
    if pid.value == intent['pid']:
        user.SetForegroundWindow(intent['hwnd'])


def cleanup_connection_menu():
    """Collapse only a menu opened for one F8, while input still belongs to it."""
    if not MENU_LEASE.is_file():
        return
    try:
        lease = json.loads(MENU_LEASE.read_text(encoding='utf-8'))
        windows, _ = api()
        if time.time() > lease['expires']:
            return
        return_original_focus(windows, lease, lease.get('helper'))
        if not still_user_request(windows, lease):
            return
        root = native.automation().ControlFromHandle(lease['hwnd'])
        pattern = file_pattern(root)
        if pattern and pattern.ExpandCollapseState == 1 and still_user_request(windows, lease):
            pattern.Collapse(waitTime=0)
    finally:
        # If the process is killed during Collapse, the supervisor sees and
        # attempts this lease once more, with the same ownership checks.
        MENU_LEASE.unlink(missing_ok=True)


def provision(intent):
    windows, _ = api()
    user = windows.user32
    if not same_foreground(windows, intent) or time.monotonic() - intent['captured'] > 10:
        raise RuntimeError('首次连接时请在 Codex 前台按 F8。')
    original = {w.hwnd for w in windows.enumerate()}
    root = native.automation().ControlFromHandle(intent['hwnd'])
    pattern = file_pattern(root)
    if pattern and pattern.ExpandCollapseState != 0:
        raise RuntimeError('文件菜单正在使用或不可安全展开。请收起菜单后在 Codex 按 F8。')
    # WM_HOTKEY is sent on key-down. Let the triggering key-up settle
    # before leasing the menu, then require a quiet interval.
    time.sleep(.16)
    tick = input_tick(user)
    if tick is None:
        raise RuntimeError('无法确认当前输入状态。')
    intent = intent | {'input': tick}
    time.sleep(.12)
    # A newly opened main window can exist before its file menu provider does.
    # Observe readiness under the original foreground/input lease. Expand once.
    deadline = time.monotonic() + 6
    while not pattern and time.monotonic() < deadline and still_user_request(windows, intent):
        time.sleep(.12)
        pattern = file_pattern(root)
    if not still_user_request(windows, intent):
        raise RuntimeError('检测到后续输入，已取消本次连接。')
    if not pattern or pattern.ExpandCollapseState != 0:
        raise RuntimeError('文件菜单尚未就绪或正在使用，已取消连接。')
    lease = intent | {'expires':time.time()+65}
    MENU_LEASE.write_text(json.dumps(lease), encoding='utf-8')
    found = []
    stop = threading.Event()

    def catch_window():
        while not stop.is_set():
            candidates = [window for window in windows.enumerate()
                          if window.pid == intent['pid'] and window.hwnd not in original and window.visible
                          and window.class_name == 'Chrome_WidgetWin_1' and window.title == 'ChatGPT'
                          and not window.exstyle & 0x88]
            if candidates:
                if len(candidates) == 1 and input_tick(user) == intent['input']:
                    window = candidates[0]
                    if not stop.is_set() and user.SetPropW(window.hwnd, MARKER, MARKER_VALUE):
                        temporary = MENU_LEASE.with_suffix('.tmp')
                        temporary.write_text(json.dumps(lease | {'helper':window.hwnd}), encoding='utf-8')
                        os.replace(temporary, MENU_LEASE)
                        save_state(window, intent['hwnd'])
                        invisible(window)
                        found.append(window)
                stop.set()
                return
            stop.wait(.025)

    watcher = None
    try:
        pattern.Expand(waitTime=0)
        menu = setup_control(root, 'new_window', 1.25)
        if menu is None:
            raise RuntimeError('本次未找到新窗口入口。')
        if not still_user_request(windows, intent):
            raise RuntimeError('检测到后续输入，已取消本次连接。')
        watcher = threading.Thread(target=catch_window, daemon=True)
        watcher.start()
        invoke_background(menu)
        stop.wait(4)
        if not found:
            raise RuntimeError('Codex 未创建可确认的新窗口。可在需要时再按 F8。')
    finally:
        stop.set()
        if watcher:
            watcher.join(timeout=.4)
        if not found:
            cleanup_connection_menu()
    return found[0]


def prepare(intent=None):
    helper = owned_window()
    if helper is not None and intent and intent.get('replace_helper'):
        windows, _ = api()
        if not still_user_request(windows, intent):
            raise RuntimeError('当前输入或焦点已变化，已取消重连。')
        close()
        helper = owned_window()
        if helper is not None:
            raise RuntimeError('旧后台连接尚未退出，请稍后再按 F8。')
    if helper is None:
        if intent is None:
            raise RuntimeError('尚未连接。在 Codex 前台按 F8 可尝试一次。')
        helper = provision(intent)
        windows, _ = api()
        user = windows.user32
        stop = threading.Event()
        original_input = input_tick(user)

        def return_focus():
            # Never focus the helper. If Codex focused its newly created
            # window, permit only the remainder of this explicit request.
            return_original_focus(windows, intent, helper.hwnd)

        def watch_user():
            while not stop.wait(.025):
                if input_tick(user) != original_input:
                    return_focus()
                    break

        watcher = threading.Thread(target=watch_user, daemon=True)
        watcher.start()
        try:
            return_focus()
            return finish_prepare(helper, allow_created_foreground=True)
        finally:
            stop.set()
            return_focus()
            watcher.join(timeout=.2)
            cleanup_connection_menu()
    try:
        msaa_controls.pet_actions(helper.hwnd, native.PET_NAMES)
    except RuntimeError as error:
        if ambiguous_cards(error):
            raise
        return finish_prepare(helper)
    return {'prepared': True, 'hwnd': helper.hwnd, 'reused': True}


def setup_control(root, key, timeout=.2):
    from comtypes import COMError
    from uiautomation.uiautomation import _AutomationClient
    auto = native.automation()
    types = auto.ControlType
    kinds = (types.ButtonControl, types.TabItemControl, types.HyperlinkControl) if key == 'pets' else (types.MenuItemControl, types.ButtonControl)
    uia = _AutomationClient.instance().IUIAutomation
    condition = uia.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, kinds[0])
    for kind in kinds[1:]:
        condition = uia.CreateOrCondition(condition, uia.CreatePropertyCondition(auto.PropertyId.ControlTypeProperty, kind))
    cache = uia.CreateCacheRequest()
    cache.AddProperty(auto.PropertyId.NameProperty)
    match = re.compile(ui_labels.pattern(key))
    deadline = time.monotonic() + timeout
    while True:
        try:
            nodes = root.Element.FindAllBuildCache(4, condition, cache)
            for index in range(nodes.Length):
                node = nodes.GetElement(index)
                if match.search(node.CachedName or ''):
                    return auto.Control.CreateControlFromElement(node)
        except COMError:
            pass  # Chromium can replace a node while the new window loads.
        if time.monotonic() >= deadline:
            return None
        time.sleep(.08)


def navigate_setup(root, helper, key, destination):
    """One semantic action, observed result, then one setup-only message fallback."""
    control = setup_control(root, key)
    if control is None:
        raise RuntimeError('后台导航入口未就绪：' + key)
    if key == 'profile':
        expand = control.GetPattern(native.automation().PatternId.ExpandCollapsePattern)
        if expand:
            try:
                if expand.ExpandCollapseState == 0:
                    expand.Expand(waitTime=0)
                if destination():
                    return
                if expand.ExpandCollapseState == 1:
                    raise RuntimeError('后台个人资料菜单已打开，设置入口尚未出现。')
            except RuntimeError:
                raise
            except Exception:
                logging.debug('Profile expansion unavailable on owned helper', exc_info=True)
    try:
        msaa_controls.execute(msaa_controls.semantic_action(helper.hwnd, ui_labels.pattern(key)))
    except Exception:
        try:
            invoke_background(control)
        except Exception:
            logging.warning('Setup navigation pattern unavailable: %s', key)
            click_background(control, helper)
            if not destination():
                raise RuntimeError('后台导航没有响应：' + key)
            return
    if destination():
        return
    # Some providers advertise an action which is a no-op. Do not double-toggle
    # an already-open menu; re-observe the destination before sending messages.
    click_background(control, helper)
    if not destination():
        raise RuntimeError('后台导航没有响应：' + key)


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


def finish_prepare(helper, target=None, allow_created_foreground=False):
    windows, _ = api()
    user = windows.user32
    live = window_from_state(windows, {'hwnd': helper.hwnd, 'pid': helper.pid})
    if not live or (not allow_created_foreground and user.GetForegroundWindow() == helper.hwnd):
        raise RuntimeError('后台窗口身份或前台状态有变化，请在 Codex 重新按 F8。')
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
    r = native.automation().ControlFromHandle(helper.hwnd)
    # New windows may asynchronously restore the settings route. Wait for a
    # usable route rather than treating its temporary blank tree as menu failure.
    deadline = time.monotonic() + 10
    while True:
        try:
            actions = msaa_controls.pet_actions(helper.hwnd, native.PET_NAMES)
            if target:
                return actions[target]
            return {'prepared':True, 'hwnd':helper.hwnd, 'reused':True}
        except RuntimeError as error:
            if ambiguous_cards(error):
                raise
        section = setup_control(r, 'pets', 0)
        if section is not None:
            break
        settings_visible = setup_control(r, 'settings', 0) is not None
        if settings_visible or setup_control(r, 'profile', 0) is not None:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('辅助 GPT 窗口尚未加载完成。')
        time.sleep(.15)
    if section is None:
        # A pre-existing profile menu must be used directly, not toggled closed.
        if not settings_visible:
            navigate_setup(r, helper, 'profile', lambda: setup_control(r, 'settings', 3) is not None)
        navigate_setup(r, helper, 'settings', lambda: setup_control(r, 'pets', 3) is not None)
        section = setup_control(r, 'pets')
    if section is None:
        raise RuntimeError('后台宠物设置入口在加载期间消失。')
    try:
        msaa_controls.execute(msaa_controls.semantic_action(helper.hwnd, ui_labels.pattern('pets')))
    except Exception:
        invoke_background(section)
    try:
        # Opening the section and receiving the pet list are asynchronous.
        # Give it time to fill before retrying the navigation.
        actions = wait_for_pet_actions(helper.hwnd, 4)
    except RuntimeError as error:
        if ambiguous_cards(error):
            raise
        section = setup_control(r, 'pets')
        if section is None:
            raise
        click_background(section, helper)
        actions = wait_for_pet_actions(helper.hwnd, 5)
    if target:
        return actions[target]
    return {"prepared": True, "hwnd": helper.hwnd, "reused": False}


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
        def parked(window):
            return (window.rect[2] <= user.GetSystemMetrics(76)
                    and window.exstyle & required_styles == required_styles and not window.exstyle & 0x8)

        if helper and user.GetForegroundWindow() != hwnd and not parked(helper):
            invisible(helper)
            helper = window_from_state(windows, identity)
        if not helper or user.GetForegroundWindow() == hwnd or not parked(helper):
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
    parser.add_argument("action", choices=("prepare", "status", "close", "cleanup-menu"))
    args = parser.parse_args()
    action = args.action
    native.dpi_awareness()
    if action == "cleanup-menu":
        import sys
        sys.coinit_flags = 0
        sys.stdout.reconfigure(encoding="utf-8")
        auto = native.automation()
        with auto.UIAutomationInitializerInThread():
            try:
                cleanup_connection_menu()
                print(json.dumps({"cleaned": True}))
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

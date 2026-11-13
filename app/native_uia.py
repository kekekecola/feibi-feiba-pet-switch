"""Select the existing ChatGPT pet through its actual Windows settings controls."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, asdict
import json
import os
import re
from pathlib import Path
import sys
import time
import tomllib

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR / "vendor"))

import pet_registry
PET_IDS = {form: role['id'] for form, role in pet_registry.load().items()}
PET_NAMES = {form: role['name'] for form, role in pet_registry.load().items()}


def preferences() -> dict:
    directory = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return tomllib.loads((directory / "config.toml").read_text(encoding="utf-8")).get("desktop", {})


def selected_form() -> str | None:
    selected = preferences().get("selected-avatar-id")
    roles = pet_registry.refresh(selected)
    PET_IDS.update({form: role['id'] for form, role in roles.items()})
    PET_NAMES.update({form: role['name'] for form, role in roles.items()})
    return next((form for form, pet_id in PET_IDS.items() if selected == pet_id), None)


def dpi_awareness() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError:
        user32.SetProcessDPIAware()


def reconcile_selected(expected=None):
    """Bind a recreated ID only after observing its native overlay identity."""
    selected = preferences().get('selected-avatar-id')
    roles = pet_registry.load()
    candidates = set()
    for window in Windows().enumerate():
        if not window.exstyle & 0x8:
            continue
        controls = list(walk_controls(automation().ControlFromHandle(window.hwnd), 24))
        if not any(c.ControlTypeName == 'DocumentControl' and 'avatar-overlay' in control_value(c)
                   for c, _ in controls):
            continue
        for image, _ in controls:
            if image.ControlTypeName != 'ImageControl':
                continue
            name = image.Name
            for form, role in roles.items():
                if name == role['name'] or name.startswith(role['name'] + ' '):
                    candidates.add(form)
    if len(candidates) != 1:
        return None
    form = candidates.pop()
    if expected and form != expected:
        return None
    if preferences().get('selected-avatar-id') != selected:
        return None
    # An ID already assigned to the opposite form indicates disagreement, not a rename.
    if any(role['id'] == selected and key != form for key, role in roles.items()):
        return None
    pet_registry.bind(form, selected)
    return selected_form()


@dataclass
class NativeWindow:
    hwnd: int
    pid: int
    title: str
    class_name: str
    visible: bool
    style: int
    exstyle: int
    rect: tuple[int, int, int, int]
    dpi: int


class Windows:
    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        self.user32.GetDpiForWindow.restype = wintypes.UINT
        self.get_long = self.user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else self.user32.GetWindowLongW
        self.get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_long.restype = ctypes.c_ssize_t
        self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    def executable(self, pid: int) -> str:
        handle = self.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return buffer.value
            return ""
        finally:
            self.kernel32.CloseHandle(handle)

    def enumerate(self) -> list[NativeWindow]:
        result = []
        paths = {}
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _):
            pid = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value not in paths:
                paths[pid.value] = self.executable(pid.value)
            executable = paths[pid.value].lower()
            if not executable.endswith("\\chatgpt.exe") or "openai.codex" not in executable:
                return True
            title = ctypes.create_unicode_buffer(1024)
            class_name = ctypes.create_unicode_buffer(256)
            rect = wintypes.RECT()
            self.user32.GetWindowTextW(hwnd, title, len(title))
            self.user32.GetClassNameW(hwnd, class_name, len(class_name))
            self.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            if not class_name.value.startswith("Chrome_WidgetWin"):
                return True
            result.append(NativeWindow(int(hwnd), pid.value, title.value, class_name.value,
                bool(self.user32.IsWindowVisible(hwnd)), self.get_long(hwnd, -16), self.get_long(hwnd, -20),
                (rect.left, rect.top, rect.right, rect.bottom), int(self.user32.GetDpiForWindow(hwnd) or 96)))
            return True

        self.user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        self.user32.EnumWindows(callback_type(callback), 0)
        return result

    def main(self) -> NativeWindow:
        # The Electron main window uses a custom titlebar, without WS_CAPTION.
        candidates = [w for w in self.enumerate()
                      if w.class_name == "Chrome_WidgetWin_1" and w.title == "ChatGPT"
                      and not w.exstyle & 0x80]
        if not candidates:
            raise RuntimeError("没有找到 GPT 主窗口，请先打开 GPT。")
        visible = [w for w in candidates if w.visible]
        if len(visible) == 1:
            return visible[0]
        if len(candidates) == 1:
            return candidates[0]
        foreground = self.user32.GetForegroundWindow()
        active = next((w for w in candidates if w.hwnd == foreground), None)
        return active or (visible or candidates)[0]

    def foreground(self, window: NativeWindow) -> None:
        if self.user32.IsIconic(window.hwnd):
            self.user32.ShowWindow(window.hwnd, 9)
        self.user32.ShowWindow(window.hwnd, 5)
        self.user32.SetForegroundWindow(window.hwnd)


def automation():
    import uiautomation as auto
    auto.SetGlobalSearchTimeout(1)
    return auto


def walk_controls(root, depth: int = 22):
    auto = automation()
    yield from auto.WalkControl(root, includeTop=True, maxDepth=depth)


def control_value(control) -> str:
    auto = automation()
    for pattern_id in (auto.PatternId.ValuePattern, auto.PatternId.LegacyIAccessiblePattern):
        try:
            pattern = control.GetPattern(pattern_id)
            if pattern:
                return str(pattern.Value)
        except Exception:
            pass
    return ""


def relevant_controls(hwnd: int, limit: int = 90) -> list[dict]:
    auto = automation()
    root = auto.ControlFromHandle(hwnd)
    result = []
    for control, depth in walk_controls(root):
        name = control.Name or ""
        kind = control.ControlTypeName
        value = control_value(control) if kind == "DocumentControl" else ""
        if depth <= 2 or kind == "DocumentControl" or any(k in name for k in ("菲比", "菲八", "Mini", "迷你", "智能伙伴")):
            bounds = control.BoundingRectangle
            result.append({"name": name[:240], "type": kind, "depth": depth,
                "automation_id": control.AutomationId, "value": value[:300],
                "rect": [bounds.left, bounds.top, bounds.right, bounds.bottom]})
            if len(result) >= limit:
                break
    return result


def diagnose() -> dict:
    windows = Windows().enumerate()
    data = []
    for window in windows:
        item = asdict(window)
        if window.visible:
            try:
                item["controls"] = relevant_controls(window.hwnd)
            except Exception as error:
                item["uia_error"] = str(error)
        data.append(item)
    return {"selected_form": selected_form(), "native_size": preferences().get("avatar-overlay-mascot-width-px", 112), "windows": data}


def invoke(control) -> None:
    """Use the matched native control's accessibility action before mouse input."""
    auto = automation()
    for kind, method in ((auto.PatternId.InvokePattern, "Invoke"),
                         (auto.PatternId.TogglePattern, "Toggle"),
                         (auto.PatternId.SelectionItemPattern, "Select")):
        pattern = control.GetPattern(kind)
        if pattern:
            getattr(pattern, method)(waitTime=0)
            return
    control.Click(waitTime=0.1)


def exists(control, seconds=1):
    # Chromium can replace its accessibility nodes midway through SPA navigation.
    from comtypes import COMError
    deadline = time.monotonic() + seconds
    while True:
        try:
            if control.Exists(0, 0):
                return True
        except COMError:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _find_overlay_once(windows: Windows):
    auto = automation()
    found = []
    for window in windows.enumerate():
        if not window.visible or not window.exstyle & 0x8:
            continue
        try:
            from uiautomation.uiautomation import _AutomationClient
            uia = _AutomationClient.instance().IUIAutomation
            condition = uia.CreateOrCondition(uia.CreatePropertyCondition(30003,50006),uia.CreatePropertyCondition(30003,50030))
            cache = uia.CreateCacheRequest()
            for prop in (30001,30003,30045):
                cache.AddProperty(prop)
            nodes = uia.ElementFromHandle(window.hwnd).FindAllBuildCache(4,condition,cache)
            images = []
            overlay_document = False
            for i in range(nodes.Length):
                node = nodes.GetElement(i)
                if node.CachedControlType == 50030:
                    value = node.GetCachedPropertyValue(30045)
                    overlay_document |= isinstance(value,str) and 'avatar-overlay' in value
                elif node.CachedControlType == 50006:
                    rect = node.CachedBoundingRectangle
                    images.append((rect.left,rect.top,rect.right,rect.bottom))
            if overlay_document:
                expected = preferences().get('avatar-overlay-mascot-width-px',112)*window.dpi/96
                for rect in images:
                    width,height=rect[2]-rect[0],rect[3]-rect[1]
                    if width>0 and abs(width-expected)<=4 and abs(height/width-208/192)<.04:
                        found.append((window,rect))
                continue
        except Exception:
            pass  # Preserve the compatibility walk for older providers.
        root = auto.ControlFromHandle(window.hwnd)
        controls = list(walk_controls(root, 24))
        documents = [c for c, depth in controls
                     if c.ControlTypeName == "DocumentControl"]
        if not any("avatar-overlay" in control_value(c) for c in documents):
            continue
        expected = preferences().get('avatar-overlay-mascot-width-px', 112) * window.dpi / 96
        for image, _ in controls:
            if image.ControlTypeName != 'ImageControl':
                continue
            rect = image.BoundingRectangle
            # Match the sprite geometry inside the proven overlay document,
            # independently of pet names and the localized image suffix.
            if (abs(rect.width() - expected) <= 4 and rect.width() > 0
                    and abs(rect.height() / rect.width() - 208 / 192) < .04):
                found.append((window, (rect.left, rect.top, rect.right, rect.bottom)))
    if len(found) != 1:
        raise RuntimeError("未唯一找到正在显示的 GPT 菲比/菲八，请先显示这只原生智能伙伴。")
    return found[0]


def find_overlay(windows: Windows):
    from comtypes import COMError
    deadline = time.monotonic() + 6
    while True:
        try:
            return _find_overlay_once(windows)
        except (RuntimeError, COMError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.12)


class NativeSelection:
    def __init__(self, windows: Windows):
        self.windows = windows
        self.window = windows.main()
        self.root = automation().ControlFromHandle(self.window.hwnd)
        self.old_foreground = windows.user32.GetForegroundWindow()
        self.was_minimized = bool(windows.user32.IsIconic(self.window.hwnd))
        self.was_visible = self.window.visible
        self.opened_settings = False

    def button(self, name):
        return self.root.ButtonControl(searchDepth=26, Name=name)

    def pet_button(self, form):
        return self.root.ButtonControl(searchDepth=27,
            RegexName="^" + re.escape(PET_NAMES[form]) + r"(?:\s|$)")

    def prepare(self, target):
        self.windows.foreground(self.window)
        card = self.pet_button(target)
        if exists(card, 0.4):
            return card
        section = self.button("迷你界面和智能伙伴")
        if not exists(section, 0.4):
            self.opened_settings = True
            if self.windows.user32.GetForegroundWindow() != self.window.hwnd:
                raise RuntimeError("GPT 主窗口无法取得焦点，已取消以避免向其他应用发送按键。")
            automation().SendKeys("{Ctrl},", waitTime=0.15)
            if not exists(section, 1.5):
                profile = self.button("打开个人资料菜单")
                if not exists(profile, 1):
                    raise RuntimeError("找不到 GPT 设置入口。")
                invoke(profile)
                settings = self.root.MenuItemControl(searchDepth=26, RegexName=r"^设置(?:\s|$)")
                if not exists(settings, 2):
                    raise RuntimeError("找不到 GPT 原生设置菜单。")
                invoke(settings)
            if not exists(section, 3):
                raise RuntimeError("GPT 设置页面未就绪。")
        invoke(section)
        if not exists(card, 3):
            raise RuntimeError("GPT 设置中未找到目标智能伙伴。")
        return card

    def select(self, form, card):
        if selected_form() != form:
            invoke(card)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if selected_form() == form:
                return
            time.sleep(0.08)
        raise RuntimeError("原生宠物选择未得到配置读回确认。")

    def restore(self):
        if self.opened_settings:
            for _ in range(2):
                heading = self.root.DocumentControl(searchDepth=13)
                if not exists(heading, 0.3) or heading.Name != "设置":
                    break
                back = self.button("返回")
                if not exists(back, 0.3):
                    break
                invoke(back)
                time.sleep(0.2)
        if self.was_minimized:
            self.windows.user32.ShowWindow(self.window.hwnd, 6)
        elif not self.was_visible:
            self.windows.user32.ShowWindow(self.window.hwnd, 0)
        if self.old_foreground and self.windows.user32.IsWindow(self.old_foreground):
            self.windows.user32.SetForegroundWindow(self.old_foreground)


def main() -> int:
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["diagnose"])
    parser.parse_args()
    dpi_awareness()
    print(json.dumps(diagnose(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

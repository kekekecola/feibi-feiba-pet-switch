"""A local transparent companion with an F8 hat transition. Requires Pillow."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import sys
import time
import tomllib
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageChops
import recovery_journal


APP_DIR = Path(__file__).resolve().parent
ASSETS = APP_DIR / "assets"
RUNTIME = APP_DIR / "runtime"
TITLE = "菲比 · F8 戴帽/摘帽"
HOTKEY_ID = 0x46B8
WM_HOTKEY = 0x0312
VK_F8 = 0x77
MOD_NOREPEAT = 0x4000
GWLP_WNDPROC = -4
FORM_NAMES = {"feibi": "菲比啾比", "feiba": "菲八啾比"}
COMMANDS = {"take-off", "put-on", "toggle", "play-take-off", "play-put-on", "hide", "quit"}


def enable_dpi_awareness() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        set_context = user32.SetProcessDpiAwarenessContext
        set_context.argtypes = [ctypes.c_void_p]
        set_context.restype = wintypes.BOOL
        set_context(ctypes.c_void_p(-4))  # per-monitor DPI aware v2, before Tk creates windows
    except AttributeError:
        user32.SetProcessDPIAware()


def window_dpi(root: tk.Tk) -> int:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetParent.argtypes = [wintypes.HWND]
    user32.GetParent.restype = wintypes.HWND
    root.update_idletasks()
    hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
    try:
        user32.GetDpiForWindow.argtypes = [wintypes.HWND]
        user32.GetDpiForWindow.restype = wintypes.UINT
        dpi = user32.GetDpiForWindow(hwnd)
        if dpi:
            return int(dpi)
        user32.GetDpiForSystem.restype = wintypes.UINT
        return int(user32.GetDpiForSystem() or 96)
    except AttributeError:
        return 96


def native_pet_size() -> int:
    """Read the desktop preference without writing to the application's config."""
    codex_dir = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        config = tomllib.loads((codex_dir / "config.toml").read_text(encoding="utf-8"))
        value = config.get("desktop", {}).get("avatar-overlay-mascot-width-px", 112)
        return max(80, min(224, int(value)))
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError):
        return 112


def premultiplied_bgra(image: Image.Image) -> bytes:
    """UpdateLayeredWindow requires premultiplied BGRA, not a color-key matte."""
    red, green, blue, alpha = image.convert("RGBA").split()
    return Image.merge("RGBA", (
        ImageChops.multiply(blue, alpha),
        ImageChops.multiply(green, alpha),
        ImageChops.multiply(red, alpha),
        alpha,
    )).tobytes()


class BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG),
                ("height", wintypes.LONG), ("planes", wintypes.WORD),
                ("bit_count", wintypes.WORD), ("compression", wintypes.DWORD),
                ("size_image", wintypes.DWORD), ("x_pels", wintypes.LONG),
                ("y_pels", wintypes.LONG), ("colors_used", wintypes.DWORD),
                ("colors_important", wintypes.DWORD)]


class BitmapInfo(ctypes.Structure):
    _fields_ = [("header", BitmapInfoHeader), ("colors", wintypes.DWORD * 1)]


class BlendFunction(ctypes.Structure):
    _fields_ = [("operation", ctypes.c_ubyte), ("flags", ctypes.c_ubyte),
                ("constant_alpha", ctypes.c_ubyte), ("alpha_format", ctypes.c_ubyte)]


class LayeredRenderer:
    """Per-pixel alpha for the player's own Tk wrapper HWND. No colored canvas."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user32.GetParent.argtypes = [wintypes.HWND]
        self.user32.GetParent.restype = wintypes.HWND
        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.GetDC.restype = wintypes.HDC
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = ctypes.c_int
        self.user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC,
            ctypes.POINTER(wintypes.POINT), ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
            ctypes.POINTER(wintypes.POINT), wintypes.DWORD, ctypes.POINTER(BlendFunction), wintypes.DWORD]
        self.user32.UpdateLayeredWindow.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        suffix = "PtrW" if ctypes.sizeof(ctypes.c_void_p) == 8 else "W"
        self.get_long = getattr(self.user32, "GetWindowLong" + suffix)
        self.get_long.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_long.restype = ctypes.c_ssize_t
        self.set_long = getattr(self.user32, "SetWindowLong" + suffix)
        self.set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_long.restype = ctypes.c_ssize_t
        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BitmapInfo),
            wintypes.UINT, ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
        self.gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self.gdi32.SelectObject.restype = wintypes.HANDLE
        self.gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL
        root.update_idletasks()
        self.hwnd = self.user32.GetParent(root.winfo_id()) or root.winfo_id()
        self.user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
        self.user32.SetPropW(self.hwnd, "FeibiHatCGOverlay", 0x46484347)
        self.screen_dc = self.user32.GetDC(None)
        self.memory_dc = self.gdi32.CreateCompatibleDC(self.screen_dc)
        if not self.screen_dc or not self.memory_dc:
            raise ctypes.WinError(ctypes.get_last_error())
        self.bitmap = None
        self.previous_object = None
        self.bits = ctypes.c_void_p()
        self.size = None
        # Never call SetLayeredWindowAttributes; it would conflict with this alpha path.
        self.set_long(self.hwnd, -20, self.get_long(self.hwnd, -20)
                      | 0x00080000 | 0x08000000 | 0x80 | 0x20)

    def show(self):
        self.user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE

    def hide(self):
        self.user32.ShowWindow(self.hwnd, 0)

    def visible(self):
        return bool(self.user32.IsWindowVisible(self.hwnd))

    def _allocate(self, width: int, height: int) -> None:
        if self.size == (width, height):
            return
        if self.bitmap:
            self.gdi32.SelectObject(self.memory_dc, self.previous_object)
            self.gdi32.DeleteObject(self.bitmap)
        info = BitmapInfo()
        info.header.size = ctypes.sizeof(BitmapInfoHeader)
        info.header.width = width
        info.header.height = -height  # top-down rows
        info.header.planes = 1
        info.header.bit_count = 32
        self.bitmap = self.gdi32.CreateDIBSection(self.memory_dc, ctypes.byref(info), 0, ctypes.byref(self.bits), None, 0)
        if not self.bitmap:
            raise ctypes.WinError(ctypes.get_last_error())
        self.previous_object = self.gdi32.SelectObject(self.memory_dc, self.bitmap)
        self.size = (width, height)

    def draw(self, image: Image.Image) -> None:
        style = self.get_long(self.hwnd, -20)
        if not style & 0x00080000:
            self.set_long(self.hwnd, -20, style | 0x00080000)
        width, height = image.size
        self._allocate(width, height)
        pixels = premultiplied_bgra(image)
        ctypes.memmove(self.bits, pixels, len(pixels))
        destination = wintypes.POINT(*self.position())
        source = wintypes.POINT(0, 0)
        size = wintypes.SIZE(width, height)
        blend = BlendFunction(0, 0, 255, 1)
        if not self.user32.UpdateLayeredWindow(self.hwnd, self.screen_dc, ctypes.byref(destination),
                ctypes.byref(size), self.memory_dc, ctypes.byref(source), 0, ctypes.byref(blend), 2):
            raise ctypes.WinError(ctypes.get_last_error())

    def position(self) -> tuple[int, int]:
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(self.hwnd, ctypes.byref(rect)):
            raise ctypes.WinError(ctypes.get_last_error())
        return rect.left, rect.top

    def move(self, x: int, y: int, width: int, height: int) -> None:
        # Physical coordinates, including monitors to the left/top of the primary.
        if not self.user32.SetWindowPos(self.hwnd, wintypes.HWND(-1), x, y, width, height, 0x0010):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self.bitmap:
            self.gdi32.SelectObject(self.memory_dc, self.previous_object)
            self.gdi32.DeleteObject(self.bitmap)
            self.bitmap = None
        if self.memory_dc:
            self.gdi32.DeleteDC(self.memory_dc)
            self.memory_dc = None
        if self.screen_dc:
            self.user32.ReleaseDC(None, self.screen_dc)
            self.screen_dc = None


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(12):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            # Readers/scanners can briefly omit FILE_SHARE_DELETE on Windows.
            time.sleep(0.01 * (attempt + 1))


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@dataclass
class FormState:
    """Commit a form only after its transition has finished."""

    form: str = "feibi"
    target: str | None = None

    def begin(self, action: str) -> str:
        if self.target is not None:
            return "busy"
        target = {"take-off": "feiba", "put-on": "feibi"}.get(action)
        if action == "toggle":
            target = "feiba" if self.form == "feibi" else "feibi"
        if target is None:
            raise ValueError(f"Unsupported action: {action}")
        if target == self.form:
            return "noop"
        self.target = target
        return "started"

    def complete(self) -> None:
        if self.target is None:
            raise RuntimeError("No transition is active")
        self.form = self.target
        self.target = None

    def cancel(self) -> None:
        self.target = None


class Win32Hotkey:
    """Receive WM_HOTKEY in this Tk window's normal Windows message loop."""

    def __init__(self, root: tk.Tk, callback) -> None:
        self.root = root
        self.callback = callback
        self.hwnd = None
        self.old_proc = None
        self.registered = False
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetParent.argtypes = [wintypes.HWND]
        self.user32.GetParent.restype = wintypes.HWND
        self.user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.user32.RegisterHotKey.restype = wintypes.BOOL
        self.user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.UnregisterHotKey.restype = wintypes.BOOL
        self.set_proc = self.user32.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else self.user32.SetWindowLongW
        self.set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_proc.restype = ctypes.c_ssize_t
        self.user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.CallWindowProcW.restype = ctypes.c_ssize_t
        self.proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        # The callback must remain referenced until the original WndProc is restored.
        self.window_proc = self.proc_type(self._window_proc)

    def install(self) -> None:
        self.root.update_idletasks()
        child = self.root.winfo_id()
        self.hwnd = self.user32.GetParent(child) or child
        ctypes.set_last_error(0)
        self.old_proc = self.set_proc(self.hwnd, GWLP_WNDPROC, ctypes.cast(self.window_proc, ctypes.c_void_p).value)
        if not self.old_proc and ctypes.get_last_error():
            raise ctypes.WinError(ctypes.get_last_error())
        if not self.user32.RegisterHotKey(self.hwnd, HOTKEY_ID, MOD_NOREPEAT, VK_F8):
            error = ctypes.get_last_error()
            self.close()
            raise RuntimeError(f"F8 无法注册，可能已被其他程序占用。Windows error {error}")
        self.registered = True

    def _window_proc(self, hwnd, message, wparam, lparam):
        if message == WM_HOTKEY and wparam == HOTKEY_ID:
            try:
                self.root.after_idle(self.callback)
            except tk.TclError:
                pass
            return 0
        return self.user32.CallWindowProcW(self.old_proc, hwnd, message, wparam, lparam)

    def close(self) -> None:
        if self.registered:
            self.user32.UnregisterHotKey(self.hwnd, HOTKEY_ID)
            self.registered = False
        if self.old_proc:
            self.set_proc(self.hwnd, GWLP_WNDPROC, self.old_proc)
            self.old_proc = None


class SingleInstance:
    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel32.CreateMutexW(None, False, "Local\\FeibiHatPlayer_F8_v1")
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.already_running = ctypes.get_last_error() == 183

    def close(self) -> None:
        if self.handle:
            self.kernel32.CloseHandle(self.handle)
            self.handle = None


class Player:
    def __init__(self, root: tk.Tk, *, preview: bool = False) -> None:
        self.root = root
        self.preview = preview
        self.closed = False
        self.started_at = time.time()
        self.phase = "idle"
        self.current_command = None
        self.last_completed_command = None
        self.hotkey = None
        self.transition_after = None
        self.transition_index = 0
        self.hold_last = False
        self.hold_timeout = None
        self.dpi_override = None
        self.native_width_override = None
        self.width_sample_size = None
        self.width_sample_dpi = None
        self.drag_origin = None
        self.drag_direction = "running-right"
        self.idle_index = 0
        saved = read_json(RUNTIME / "state.json")
        self.state = FormState(saved.get("form") if saved.get("form") in FORM_NAMES else "feibi")
        metadata = read_json(ASSETS / "take-off.json")
        self.source_width = int(metadata.get("cell_width", 192))
        self.source_height = int(metadata.get("cell_height", 208))
        self.transition_delay = int(metadata.get("frame_duration_ms", 120))
        if not 192 <= self.source_width <= 1024 or not 208 <= self.source_height <= 1024 or not 20 <= self.transition_delay <= 2000:
            raise ValueError("Invalid transition geometry or frame duration")
        self.idle_offset = ((self.source_width - 192) // 2, self.source_height - 208)
        self.native_pet_size_px = native_pet_size()
        self.dpi = window_dpi(root)
        self.scale = self.native_pet_size_px / 192 * self.dpi / 96
        self.width = round(self.source_width * self.scale)
        self.height = round(self.source_height * self.scale)
        self.render_cache = {}
        self.frames = self._load_atlases()
        self.transition = self._load_transition()
        root.title(TITLE)
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        x = int(saved.get("x", root.winfo_screenwidth() - self.width - 80))
        y = int(saved.get("y", root.winfo_screenheight() - self.height - 80))
        self._place_on_visible_screen(x, y)
        self.renderer = LayeredRenderer(root)
        self.renderer.move(*self.initial_position, self.width, self.height)
        root.report_callback_exception = self._callback_error
        self.menu = tk.Menu(root, tearoff=False)
        self.menu.add_command(label="摘帽子 · 菲八啾比", command=lambda: self.request("take-off"))
        self.menu.add_command(label="戴帽子 · 菲比啾比", command=lambda: self.request("put-on"))
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.close)
        root.bind("<Button-1>", self._drag_start)
        root.bind("<B1-Motion>", self._drag_move)
        root.bind("<ButtonRelease-1>", self._drag_end)
        root.bind("<Button-3>", self._context_menu)
        root.bind("<Escape>", self.cancel_transition)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._show_standard("idle", 0)
        root.update_idletasks()
        if self.preview:
            self.hotkey = Win32Hotkey(root, lambda: self.request("toggle"))
            self.hotkey.install()
        self._write_status()
        self._save_state()
        self._animation_tick()
        self._poll_commands()
        self._heartbeat()
        self._refresh_size()
        logging.info("Ready: title=%s pid=%s preview=%s renderer=UpdateLayeredWindow", TITLE, os.getpid(), preview)

    def _load_atlases(self) -> dict:
        result = {}
        for form in FORM_NAMES:
            with Image.open(ASSETS / f"{form}.webp") as image:
                image = image.convert("RGBA")
                if image.size != (1536, 1872):
                    raise ValueError(f"Unexpected {form} atlas size: {image.size}")
                result[form] = {}
                for action, row, count in (("idle", 0, 6), ("running-right", 1, 8), ("running-left", 2, 8)):
                    frames = []
                    for column in range(count):
                        frame = image.crop((column * 192, row * 208, (column + 1) * 192, (row + 1) * 208))
                        if not frame.getchannel("A").getbbox():
                            raise ValueError(f"Empty sprite: {form} {action} {column}")
                        canvas = Image.new("RGBA", (self.source_width, self.source_height))
                        canvas.alpha_composite(frame, self.idle_offset)
                        frames.append(canvas)
                    result[form][action] = frames
        return result

    def _load_transition(self) -> list:
        with Image.open(ASSETS / "take-off.png") as image:
            image = image.convert("RGBA")
            if image.height != self.source_height or image.width % self.source_width or image.width // self.source_width < 2:
                raise ValueError(f"Transition strip does not match {self.source_width}x{self.source_height} cells")
            frames = []
            for index in range(image.width // self.source_width):
                frame = image.crop((index * self.source_width, 0, (index + 1) * self.source_width, self.source_height))
                if not frame.getchannel("A").getbbox():
                    raise ValueError(f"Empty transition frame {index}")
                frames.append(frame)
            return frames

    def _place_on_visible_screen(self, x: int, y: int) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
        x = max(left, min(x, left + width - self.width))
        y = max(top, min(y, top + height - self.height))
        self.initial_position = (x, y)
        self.root.geometry(f"{self.width}x{self.height}{x:+d}{y:+d}")

    def _show_standard(self, action: str, frame: int) -> None:
        items = self.frames[self.state.form][action]
        self._draw(items[frame % len(items)])

    def _draw(self, image: Image.Image) -> None:
        key = (id(image), self.width, self.height)
        if key not in self.render_cache:
            self.render_cache[key] = image.resize((self.width, self.height), Image.Resampling.LANCZOS)
        self.renderer.draw(self.render_cache[key])

    def _refresh_size(self) -> None:
        if self.closed:
            return
        self._refresh_size_once()
        self.root.after(150 if self.phase in ('transition', 'holding') else 1000, self._refresh_size)

    def _refresh_size_once(self) -> None:
        size = native_pet_size()
        dpi = window_dpi(self.root) if self.phase in ('transition', 'holding') else self.dpi_override or window_dpi(self.root)
        if self.native_width_override:
            scale = self.native_width_override / 192 * size / self.width_sample_size * dpi / self.width_sample_dpi
        else:
            scale = size / 192 * dpi / 96
        if size != self.native_pet_size_px or dpi != self.dpi or scale != self.scale:
            self.native_pet_size_px = size
            self.dpi = dpi
            old_scale = self.scale
            self.scale = scale
            self.width = round(self.source_width * self.scale)
            self.height = round(self.source_height * self.scale)
            self.render_cache.clear()
            position = self.renderer.position()
            anchor = (self.idle_offset[0] + 91, self.idle_offset[1] + 200)
            position = tuple(round(p + a * (old_scale - scale)) for p, a in zip(position, anchor))
            self.root.geometry(f"{self.width}x{self.height}")
            self.root.update_idletasks()
            self.renderer.move(*position, self.width, self.height)
            if self.phase == "transition":
                self._draw(self.active_transition[max(0, self.transition_index - 1)])
            elif self.phase == "holding":
                self._draw(self.active_transition[-1])
            elif self.preview:
                self._show_standard("idle", 0)
            self._write_status()

    def _animation_tick(self) -> None:
        if self.closed:
            return
        if self.phase == "idle" and self.preview:
            action = self.drag_direction if self.drag_origin else "idle"
            self._show_standard(action, self.idle_index)
            self.idle_index += 1
        self.root.after(120 if self.drag_origin else 180, self._animation_tick)

    def request(self, action: str, command_id: str | None = None, parameters: dict | None = None) -> None:
        parameters = parameters or {}
        if action == "quit":
            self._ack(command_id, "completed", "退出播放器")
            self.close()
            return
        if action == "hide":
            self.cancel_transition()
            if self.hold_timeout:
                self.root.after_cancel(self.hold_timeout)
                self.hold_timeout = None
            self.phase = "idle"
            self.renderer.hide()
            self._ack(command_id, "completed", "CG 覆盖层已撤回")
            self._write_status()
            return
        cg_only = action in ("play-take-off", "play-put-on")
        if not self.preview and not cg_only:
            self._ack(command_id, "native_bridge_not_configured", "尚未配置 GPT 原生宠物切换桥。")
            self._write_status()
            return
        if cg_only:
            if self.state.target is not None or self.phase == "holding":
                self._ack(command_id, "ignored_busy", "过渡动画正在播放")
                return
            if any(parameters.get(key) is not None and type(parameters[key]) is not int for key in ("dpi", "x", "y")):
                self._ack(command_id, "failed", "坐标和 dpi 必须为整数")
                return
            if (parameters.get("x") is None) != (parameters.get("y") is None):
                self._ack(command_id, "failed", "x 与 y 必须一起指定")
                return
            requested_dpi = parameters.get("dpi")
            if requested_dpi is not None and not 48 <= requested_dpi <= 480:
                self._ack(command_id, "failed", "dpi 必须在 48 到 480 之间")
                return
            self.dpi_override = requested_dpi
            requested_width = parameters.get('native_width')
            if requested_width is not None and (type(requested_width) is not int or not 20 <= requested_width <= 1500):
                self._ack(command_id, 'failed', '无效的原生像素宽度')
                return
            self.native_width_override = requested_width
            self.width_sample_size = native_pet_size()
            self.width_sample_dpi = requested_dpi or window_dpi(self.root)
            self._refresh_size_once()
            if parameters.get("x") is not None and parameters.get("y") is not None:
                self.renderer.move(int(parameters["x"]), int(parameters["y"]), self.width, self.height)
            self.hold_last = parameters.get("hold") is True
            self.state.form = "feibi" if action == "play-take-off" else "feiba"
            action = "take-off" if action == "play-take-off" else "put-on"
        result = self.state.begin(action)
        if result == "busy":
            self._ack(command_id, "ignored_busy", "过渡动画正在播放")
            return
        if result == "noop":
            self._ack(command_id, "completed", "已经是目标形态")
            self._write_status()
            return
        self.current_command = command_id
        self.phase = "transition"
        self.renderer.show()
        self.root.update_idletasks()
        if parameters.get("x") is not None and parameters.get("y") is not None:
            self.renderer.move(parameters["x"], parameters["y"], self.width, self.height)
        self.transition_index = 0
        self.active_transition = self.transition if self.state.target == "feiba" else list(reversed(self.transition))
        self._write_status()
        logging.info("Transition started: %s -> %s command=%s", self.state.form, self.state.target, command_id)
        self._advance_transition()

    def _advance_transition(self) -> None:
        self.transition_after = None
        if self.closed or self.phase != "transition":
            return
        self._refresh_size_once()
        if self.transition_index >= len(self.active_transition):
            self.state.complete()
            self.phase = "holding" if self.hold_last and not self.preview else "idle"
            self.idle_index = 0
            self._draw(self.active_transition[-1])
            self._save_state()
            self._ack(self.current_command, "completed", FORM_NAMES[self.state.form])
            self.current_command = None
            if self.phase == "holding":
                self.hold_timeout = self.root.after(15000, lambda: self.request("hide"))
            elif not self.preview:
                self.renderer.hide()
            self._write_status()
            logging.info("Transition completed: form=%s", self.state.form)
            return
        self._draw(self.active_transition[self.transition_index])
        self.transition_index += 1
        self.transition_after = self.root.after(self.transition_delay, self._advance_transition)

    def cancel_transition(self, _event=None) -> None:
        if self.phase != "transition":
            return
        if self.transition_after:
            self.root.after_cancel(self.transition_after)
            self.transition_after = None
        self.state.cancel()
        self.phase = "idle"
        self._show_standard("idle", 0)
        if not self.preview:
            self.renderer.hide()
        self._ack(self.current_command, "cancelled", "过渡已取消，保留原形态")
        self.current_command = None
        self._write_status()

    def _drag_start(self, event) -> None:
        self.drag_origin = (event.x_root - self.root.winfo_x(), event.y_root - self.root.winfo_y())
        self.root.focus_force()

    def _drag_move(self, event) -> None:
        if self.drag_origin:
            x, y = event.x_root - self.drag_origin[0], event.y_root - self.drag_origin[1]
            self.drag_direction = "running-right" if x >= self.root.winfo_x() else "running-left"
            self.root.geometry(f"{x:+d}{y:+d}")

    def _drag_end(self, _event) -> None:
        self.drag_origin = None
        self._save_state()
        self._write_status()

    def _context_menu(self, event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _save_state(self) -> None:
        x, y = self.renderer.position()
        write_json(RUNTIME / "state.json", {"form": self.state.form, "x": x, "y": y})

    def _write_status(self) -> None:
        write_json(RUNTIME / "status.json", {
            "running": not self.closed,
            "pid": os.getpid(),
            "hwnd": self.renderer.hwnd,
            "phase": self.phase,
            "form": self.state.form,
            "target_form": self.state.target,
            "hotkey": "F8",
            "hotkey_registered": bool(self.hotkey and self.hotkey.registered),
            "preview": self.preview,
            "native_bridge_configured": False,
            "native_switch_confirmed": False,
            "hotkey_error": None if self.preview else "native_bridge_not_configured",
            "renderer": "UpdateLayeredWindow-premultiplied-BGRA",
            "native_pet_size_px": self.native_pet_size_px,
            "native_cell_width_px": self.native_width_override,
            "dpi": self.dpi,
            "scale": self.scale,
            "pixel_width": self.width,
            "pixel_height": self.height,
            "visible": self.renderer.visible(),
            "last_completed_command": self.last_completed_command,
            "window": {"x": self.renderer.position()[0], "y": self.renderer.position()[1], "width": self.width, "height": self.height},
            "updated_at": time.time(),
        })

    def _ack(self, command_id: str | None, result: str, message: str) -> None:
        if command_id is None:
            return
        if result == "completed":
            self.last_completed_command = command_id
        write_json(RUNTIME / "acks" / f"{command_id}.json", {
            "command_id": command_id, "result": result, "message": message,
            "form": self.state.form, "phase": self.phase, "completed_at": time.time(),
        })

    def _poll_commands(self) -> None:
        if self.closed:
            return
        commands_dir = RUNTIME / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        for path in sorted(commands_dir.glob("*.json")):
            command_id = path.stem
            if not re.fullmatch(r"[a-f0-9]{32}", command_id):
                continue
            existing = RUNTIME / "acks" / path.name
            if existing.exists():
                path.unlink(missing_ok=True)
                continue
            command = read_json(path)
            if command.get("command_id") != command_id or command.get("action") not in COMMANDS:
                self._ack(command_id, "failed", "无效命令")
            elif not isinstance(command.get("issued_at"), (float, int)) or command["issued_at"] < self.started_at - 1 or time.time() - command["issued_at"] > 60:
                self._ack(command_id, "failed", "命令已过期，请重新调用")
            else:
                self.request(command["action"], command_id, command)
            path.unlink(missing_ok=True)
            if self.closed:
                return
        self.root.after(80, self._poll_commands)

    def _heartbeat(self) -> None:
        if self.closed:
            return
        if not self.preview and recovery_journal.owner_dead():
            self.request('hide')
            recovery_journal.recover(self.renderer.visible())
        self._write_status()
        self.root.after(1000, self._heartbeat)

    def _callback_error(self, error_type, error, traceback) -> None:
        logging.error("Player callback failed", exc_info=(error_type, error, traceback))
        self._ack(self.current_command, "failed", str(error))
        self.current_command = None
        self.close()
        write_json(RUNTIME / "status.json", {
            "running": False, "pid": os.getpid(), "phase": "error",
            "hotkey_registered": False, "renderer": "UpdateLayeredWindow-premultiplied-BGRA",
            "error": str(error), "updated_at": time.time(),
        })

    def close(self) -> None:
        if self.closed:
            return
        self.cancel_transition()
        self.closed = True
        self.phase = "stopped"
        self._save_state()
        if self.hotkey:
            self.hotkey.close()
        self._write_status()
        self.renderer.close()
        self.root.destroy()
        logging.info("Player closed")


def main() -> int:
    if os.name != "nt":
        raise SystemExit("The F8 player requires Windows.")
    parser = argparse.ArgumentParser(description="Temporary hat CG overlay")
    parser.add_argument("--preview", action="store_true", help="Debug mode: keep a companion visible and register F8")
    arguments = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(RUNTIME / "player.log", maxBytes=512_000, backupCount=1, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(message)s")
    instance = SingleInstance()
    if instance.already_running:
        instance.close()
        return 0
    root = None
    player = None
    try:
        enable_dpi_awareness()
        root = tk.Tk()
        root.withdraw()
        player = Player(root, preview=arguments.preview)
        if arguments.preview:
            root.deiconify()
        root.mainloop()
        return 0
    except Exception as error:
        logging.exception("Startup or runtime failed; hotkey_error=%s", error)
        write_json(RUNTIME / "status.json", {"running": False, "pid": os.getpid(), "phase": "error", "form": None, "target_form": None, "hotkey_registered": False, "hotkey_error": str(error), "updated_at": time.time()})
        if root:
            messagebox.showerror(TITLE, str(error), parent=root)
        return 1
    finally:
        if player and not player.closed:
            player.close()
        elif root:
            try:
                root.destroy()
            except tk.TclError:
                pass
        instance.close()


if __name__ == "__main__":
    raise SystemExit(main())

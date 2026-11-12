"""F8 operates the existing GPT pet, with a temporary alpha CG overlay."""
from __future__ import annotations

import argparse
import heapq
import ctypes
import json
import logging
import os
import queue
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import control
import native_uia as native
import background_uia as background
from request_lifecycle import valid_command, claim
import recovery_journal
import isolated_calls
import maintenance
import tray

APP_DIR = Path(__file__).resolve().parent
from paths import RUNTIME, VERSION
STATUS = RUNTIME / "bridge-status.json"
ACTIONS = {"toggle", "take-off", "put-on", "quit"}
PENDING_RESTORE = []


def trace_focus(stage):
    logging.info("FOCUS %s %s", stage, native.Windows().user32.GetForegroundWindow())


class ServiceLoop:
    """Timer loop without a GUI window or a keyboard-focus target."""
    def __init__(self):
        self.callbacks = []
        self.serial = 0
        self.running = True

    def after(self, milliseconds, callback):
        self.serial += 1
        heapq.heappush(self.callbacks, (time.monotonic() + milliseconds / 1000, self.serial, callback))

    def destroy(self):
        self.running = False

    def mainloop(self):
        while self.running:
            if self.callbacks and self.callbacks[0][0] <= time.monotonic():
                _, _, callback = heapq.heappop(self.callbacks)
                try:
                    callback()
                except Exception:
                    logging.exception("Service callback failed; keeping hotkey service alive")
            else:
                time.sleep(0.02)


from hotkeys import GlobalF8
import settings

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(12):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(0.01 * (attempt + 1))


def cg_command(action, *arguments):
    return [str(Path(sys.executable).with_name("python.exe")),
            str(APP_DIR / "control.py"), action, *map(str, arguments)]


def ensure_cg(allow_start=True):
    state = control.status()
    if not state.get("running"):
        if not allow_start:
            raise RuntimeError("CG 辅助层未就绪，请重新启动 F8 切换器。当前画面保持不变。")
        subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")),
                          str(APP_DIR / "player.py")], cwd=APP_DIR,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            state = control.status()
            if state.get("running"):
                break
            time.sleep(0.1)
    if not state.get("running") or state.get("preview"):
        raise RuntimeError("CG 辅助层未就绪，或旧的独立预览仍在运行。")


def cg_visible():
    state = control.status()
    if state.get("hwnd"):
        user = native.Windows().user32
        return bool(user.IsWindow(state['hwnd']) and user.IsWindowVisible(state['hwnd']))
    return bool(state.get('visible'))


def hide_cg():
    state = control.status()
    try:
        if state.get("running"):
            subprocess.run(cg_command("hide"), capture_output=True, timeout=3,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:
        logging.exception("CG did not accept normal hide")
    windows = native.Windows()
    hwnd = state.get("hwnd")
    if hwnd and windows.user32.IsWindow(hwnd) and windows.user32.IsWindowVisible(hwnd):
        from ctypes import wintypes
        user = windows.user32
        user.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
        user.GetPropW.restype = wintypes.HANDLE
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == state.get("pid") and user.GetPropW(hwnd, "FeibiHatCGOverlay") == 0x46484347:
            kernel = windows.kernel32
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
            handle = kernel.OpenProcess(0x1001, False, pid.value)
            if handle:
                try:
                    kernel.TerminateProcess(handle, 1)
                finally:
                    kernel.CloseHandle(handle)
                deadline = time.monotonic() + 2
                while user.IsWindow(hwnd) and time.monotonic() < deadline:
                    time.sleep(0.04)
        if user.IsWindow(hwnd) and user.IsWindowVisible(hwnd):
            raise RuntimeError("CG 未撤回；保持原生 pet 隐藏以避免重复显示。")
    elif not hwnd and state.get("visible"):
        raise RuntimeError("无法确认 CG 已撤回。")


def recover_native_visibility():
    for recovery in PENDING_RESTORE[:]:
        state = control.status()
        windows, overlay, stop = recovery
        hwnd = state.get("hwnd")
        if hwnd and windows.user32.IsWindow(hwnd) and windows.user32.IsWindowVisible(hwnd):
            continue
        if not hwnd and state.get("visible"):
            continue
        stop.set()
        if windows.user32.IsWindow(overlay.hwnd):
            from ctypes import wintypes
            pid = wintypes.DWORD()
            windows.user32.GetWindowThreadProcessId(overlay.hwnd, ctypes.byref(pid))
            if pid.value == overlay.pid:
                windows.user32.ShowWindow(overlay.hwnd, 4)
        PENDING_RESTORE.remove(recovery)


def transition(action, report):
    started = time.perf_counter()
    source = native.selected_form()
    if source is None:
        source = native.reconcile_selected()
    if source is None:
        raise RuntimeError("无法唯一识别当前宠物，请检查 pet-roles.json 的名称和描述。")
    target = {"take-off": "feiba", "put-on": "feibi"}.get(action)
    if action == "toggle":
        target = "feiba" if source == "feibi" else "feibi"
    windows = native.Windows()
    overlay, bounds = native.find_overlay(windows)
    overlay_ms = round((time.perf_counter() - started) * 1000, 1)
    trace_focus("overlay-found")
    if source == target:
        return {"form": source, "selected_id": native.PET_IDS[source],
                "native_switch_confirmed": True, "native_visible": True, "cg_visible": False,
                "background_selection": True}
    selector = background.BackgroundSelection(windows)
    original_foreground = windows.user32.GetForegroundWindow()
    stop_guard = threading.Event()
    guard = None
    playback = None
    hidden = False
    journal_started = False
    original_size = native.preferences().get("avatar-overlay-mascot-width-px", 112)
    try:
        report(phase="preparing", form=source, target_form=target)
        ensure_cg(allow_start=False)
        card = selector.prepare(target)
        if selector.repaired:
            if native.selected_form() != source:
                raise RuntimeError('恢复后台窗口期间原生形态发生变化，已取消此次切换。')
            overlay, bounds = native.find_overlay(windows)
        card_ms = round((time.perf_counter() - started) * 1000, 1)
        trace_focus("card-ready")
        # The ImageControl bounds are the actual native sprite cell in physical pixels.
        dpi = overlay.dpi
        native_width = bounds[2] - bounds[0]
        scale = native_width / 192
        x = round(bounds[0] - 48 * scale)
        y = round(bounds[1] - 40 * scale)
        recovery_journal.begin(overlay)
        journal_started = True
        windows.user32.ShowWindow(overlay.hwnd, 0)
        hidden = True

        def keep_native_hidden():
            while not stop_guard.wait(0.025):
                if windows.user32.IsWindow(overlay.hwnd):
                    windows.user32.ShowWindow(overlay.hwnd, 0)

        guard = threading.Thread(target=keep_native_hidden, daemon=True)
        guard.start()
        report(phase="transition", cg_position=[x, y], native_image_rect=list(bounds),
               dpi=dpi, native_pet_size_px=original_size, native_visible=False)
        cg_action = "play-take-off" if target == "feiba" else "play-put-on"
        playback = subprocess.Popen(cg_command(cg_action, "--hold", "--x", x, "--y", y,
                                               "--dpi", dpi, "--native-width", native_width), stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 5
        while not cg_visible():
            if playback.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("CG 未开始显示，原生选择已取消。")
            time.sleep(0.04)
        first_cg_ms = round((time.perf_counter() - started) * 1000, 1)
        trace_focus("cg-visible")
        selector.select(target, card)
        trace_focus("native-selected")
        output, error = playback.communicate(timeout=10)
        if playback.returncode != 0:
            raise RuntimeError("CG 播放失败：" + (error or output).decode("utf-8", "replace")[-300:])
        acknowledgement = json.loads(output.decode("utf-8"))
        trace_focus("cg-completed")
        if acknowledgement.get("result") != "completed":
            raise RuntimeError("CG 未正常完成。")
        if native.selected_form() != target:
            raise RuntimeError("播放后原生宠物选择不匹配。")
        report(phase="restoring", form=target)
    finally:
        # Cleanup order prevents a second visible pet, including on failed selection.
        try:
            trace_focus("before-restore")
            selector.restore()
            trace_focus("after-restore")
        except Exception:
            logging.exception("Could not restore the previous main view/focus")
        cg_hidden = False
        try:
            hide_cg()
            cg_hidden = True
        finally:
            if cg_hidden:
                stop_guard.set()
                if guard:
                    guard.join(timeout=1)
            elif hidden:
                PENDING_RESTORE.append((windows, overlay, stop_guard))
            if hidden and cg_hidden and windows.user32.IsWindow(overlay.hwnd):
                windows.user32.ShowWindow(overlay.hwnd, 4)  # show without activation
            if journal_started and cg_hidden:
                recovery_journal.recover(False)
            report(cg_visible=cg_visible(),
                   native_visible=bool(windows.user32.IsWindowVisible(overlay.hwnd)))
            if playback and playback.poll() is None:
                try:
                    playback.communicate(timeout=3)
                except subprocess.TimeoutExpired:
                    playback.terminate()
    visible = bool(windows.user32.IsWindowVisible(overlay.hwnd))
    if not visible or cg_visible():
        raise RuntimeError("过渡后的原生显示恢复检查失败。")
    return {"form": target, "selected_id": native.PET_IDS[target],
            "native_switch_confirmed": True, "native_visible": visible,
            "cg_visible": False, "native_pet_size_px": original_size,
            "native_image_rect": list(bounds), "dpi": dpi,
            "timings_ms": dict(overlay=overlay_ms, prepared=card_ms, first_cg=first_cg_ms, total=round((time.perf_counter()-started)*1000, 1)),
            "foreground_before": original_foreground,
            "foreground_after": windows.user32.GetForegroundWindow(),
            "background_selection": True}


class Bridge:
    def __init__(self, root):
        self.root = root
        self.lock = threading.RLock()
        self.busy = False
        self.closing = False
        self.pending_request = None
        self.request_meta = {}
        self.next_reconnect = 0
        self.next_maintenance = 0
        self.state = {"running": True, "pid": os.getpid(), "phase": "idle",
                      "native_bridge_configured": True, "native_switch_confirmed": False,
                      "background_selection": True,
                      "hotkey": "F8", "hotkey_registered": False, "cg_visible": False}
        self.hotkey_events = queue.Queue()
        try:
            desired_hotkey = settings.load()['hotkey']
            settings_error = None
        except (OSError, ValueError) as error:
            desired_hotkey, settings_error = 'F8', str(error)
        self.hotkey = GlobalF8(self.hotkey_events, desired_hotkey, settings.last_good())
        self.settings_stamp = None
        self.progress_path = None
        self.tray = tray.Tray(self.hotkey_events)
        self.tray.start()
        self.hotkey.install()
        self.update(hotkey_registered=self.hotkey.registered, hotkey=self.hotkey.active, hotkey_error=self.hotkey.error, settings_error=settings_error, version=VERSION)
        root.after(80, self.poll)
        root.after(1000, self.heartbeat)
        root.after(50, self.reconnect_if_needed)

    def update(self, **changes):
        with self.lock:
            self.state.update(changes)
            if getattr(self, "tray", None):
                self.state["tray_registered"] = self.tray.registered
                self.state["tray_error"] = self.tray.error
                self.tray.update(self.state)
            self.state["updated_at"] = time.time()
            write_json(STATUS, self.state)

    def utility(self, action):
        if action == 'update':
            if not os.environ.get('FEIBI_SWITCH_HOME'):
                self.update(update_error='尚未开启自动更新，请运行项目的“开启自动更新.cmd”。')
                action = 'diagnose'
            else:
                result = subprocess.run(['schtasks', '/Run', '/TN', 'FeibiFeibaPetSwitchUpdate'],
                                        capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                self.update(update_error=None if result.returncode == 0 else '无法启动更新任务')
                return
        script = 'diagnostics.py' if action == 'diagnose' else 'settings.py'
        subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')), str(APP_DIR / script), '--gui'],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)

    def request(self, action, command_id=None):
        if action == "quit":
            source = 'control_quit' if command_id else 'tray_exit'
            logging.info('Shutdown requested: source=%s command_id=%s', source, command_id)
            self.update(shutdown_source=source)
            self.closing = True
            self.quit_command = command_id
            if not self.busy:
                self.close()
            return
        with self.lock:
            if self.busy or self.closing:
                if not self.closing and self.state.get("phase") == "reconnecting" and self.pending_request is None:
                    self.pending_request = (action, command_id)
                    return
                self.ack(command_id, "ignored_busy", "当前过渡尚未结束。")
                return
            self.busy = True
            self.update(phase="preparing", native_switch_confirmed=False)
        threading.Thread(target=self.worker, args=(action, command_id), daemon=True).start()

    def worker(self, action, command_id):
        try:
            # COM calls cannot reliably be cancelled in a Python thread. Keep
            # both discovery and invocation outside the long-lived hotkey host.
            if action and command_id and not claim(RUNTIME / "bridge-decisions", self.request_meta[command_id]):
                self.update(phase="idle", target_form=None)
                self.ack(command_id, "cancelled", "请求已过期或取消，未执行切换。")
                return
            command = [str(Path(sys.executable).with_name('python.exe')), str(APP_DIR / 'native_bridge.py')]
            command += ['--once', action] if action else ['--prepare-only']
            token = uuid.uuid4().hex
            progress_path = RUNTIME / ('progress-' + token + '.json')
            self.progress_path = progress_path
            command += ['--progress-token', token]
            worker_started = time.perf_counter()
            result = json.loads(isolated_calls.run(command, timeout=35 if not action else 22))
            if action:
                result['worker_total_ms'] = round((time.perf_counter()-worker_started)*1000, 1)
            self.progress_path = None
            self.update(phase="idle", connection_state="ready", target_form=None, error=None, **result)
            self.ack(command_id, "completed", "原生智能伙伴已切换。")
            logging.info("Native transition completed: %s", result)
        except Exception as error:
            logging.exception("Native transition failed")
            # A killed COM worker cannot run its finally block. Its durable
            # journal belongs to that worker; withdraw CG before restoring native.
            try:
                hide_cg()
                recovery_journal.recover(cg_visible())
                background.release_helper_focus()
            except Exception:
                logging.exception('Isolated worker recovery failed; heartbeat will retry')
            self.next_reconnect = time.monotonic() + 8
            self.update(phase="error", connection_state="disconnected", target_form=None, error=str(error),
                        native_switch_confirmed=False,
                        form=native.selected_form(), cg_visible=control.status().get("visible", False))
            self.ack(command_id, "failed", str(error))
        finally:
            path = self.progress_path
            self.progress_path = None
            try:
                if path:
                    path.unlink(missing_ok=True)
                if 'progress_path' in locals():
                    progress_path.unlink(missing_ok=True)
            except OSError:
                logging.exception('Progress file cleanup deferred')
            with self.lock:
                self.busy = False
                pending = self.pending_request
                self.pending_request = None
            if pending:
                self.hotkey_events.put(pending)

    def reconnect_if_needed(self):
        if self.closing or self.busy or time.monotonic() < self.next_reconnect:
            return
        try:
            if recovery_journal.JOURNAL.exists():
                hide_cg()
                if not recovery_journal.recover(cg_visible()):
                    self.update(phase='recovering_native')
                    return
            if (self.state.get("connection_state") == "ready"
                    and background.owned_window() and control.status().get("running")):
                return
            self.update(connection_state="disconnected", native_switch_confirmed=False)
            # Keep F8 alive while GPT is closed; initialize only after a primary window exists.
            try:
                native.Windows().main()
            except RuntimeError:
                self.update(phase="waiting_gpt", connection_state="waiting_gpt")
                return
            with self.lock:
                self.busy = True
                self.update(phase="reconnecting", connection_state="reconnecting")
            threading.Thread(target=self.worker, args=(None, None), daemon=True).start()
        except Exception as error:
            logging.exception("Connection check failed")
            self.next_reconnect = time.monotonic() + 8
            self.update(connection_state="disconnected", error=str(error))

    def ack(self, command_id, result, message):
        if command_id:
            write_json(RUNTIME / "bridge-acks" / (command_id + ".json"),
                       {"command_id": command_id, "result": result, "message": message,
                        "status": self.state.copy()})
            self.request_meta.pop(command_id, None)

    def poll(self):
        try:
            self._poll_once()
        except Exception:
            logging.exception("Command polling failed; will retry")
        finally:
            if self.state.get("running", True):
                self.root.after(80, self.poll)

    def _poll_once(self):
        if self.closing and not self.busy:
            self.close()
            return
        while not self.hotkey_events.empty():
            item = self.hotkey_events.get_nowait()
            if isinstance(item, str) and item in ("diagnose", "settings", "update"):
                self.utility(item)
                continue
            if isinstance(item, tuple):
                self.request(*item)
            else:
                self.request(item)
        while not self.hotkey.notifications.empty():
            notification = self.hotkey.notifications.get_nowait()
            self.update(**notification)
            if notification.get('hotkey_registered') and not notification.get('hotkey_error'):
                settings.remember_good(notification['hotkey'])
        folder = RUNTIME / "bridge-commands"
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(folder.glob("*.json"), key=lambda p: p.stat().st_mtime):
            command = control.read_json(path)
            if (RUNTIME / "bridge-acks" / path.name).exists():
                path.unlink(missing_ok=True)
                continue
            if valid_command(command, path.stem, ACTIONS):
                self.request_meta[path.stem] = command
                if command['action'] == 'quit' and not claim(RUNTIME / 'bridge-decisions', command):
                    self.ack(path.stem, 'cancelled', '退出请求已取消或过期。')
                else:
                    self.request(command["action"], path.stem)
            elif len(path.stem) == 32 and all(c in '0123456789abcdef' for c in path.stem):
                self.ack(path.stem, 'failed', '无效命令数据，未执行。')
            path.unlink(missing_ok=True)

    def heartbeat(self):
        if not self.closing:
            try:
                stamp = settings.SETTINGS.stat().st_mtime_ns if settings.SETTINGS.exists() else 0
                if stamp != self.settings_stamp:
                    try:
                        self.hotkey.rebind(settings.load()['hotkey'])
                        self.update(settings_error=None)
                        self.settings_stamp = stamp
                    except (OSError, ValueError) as error:
                        self.update(settings_error=str(error))
                if not self.busy:
                    if time.monotonic() >= self.next_maintenance:
                        active = set(self.request_meta)
                        if self.pending_request and self.pending_request[1]:
                            active.add(self.pending_request[1])
                        maintenance.cleanup(RUNTIME, active)
                        self.next_maintenance = time.monotonic() + 3600
                    if recovery_journal.JOURNAL.exists():
                        hide_cg()
                        recovery_journal.recover(cg_visible())
                    recover_native_visibility()
                    background.release_helper_focus()
                    prefs = native.preferences()
                    self.update(form=native.selected_form(), selected_id=prefs.get("selected-avatar-id"),
                                native_pet_size_px=prefs.get("avatar-overlay-mascot-width-px", 112))
                    self.reconnect_if_needed()
                else:
                    with self.lock:
                        if self.progress_path and self.state.get('phase') != 'idle':
                            path = self.progress_path
                            progress = control.read_json(path)
                            if self.progress_path == path and progress.get('token') == path.stem.removeprefix('progress-'):
                                keys = ('phase','form','target_form','native_visible','cg_visible')
                                self.update(**{key:progress[key] for key in keys if key in progress})
                        self.update()
            except Exception:
                logging.exception("Heartbeat read failed; will retry")
            finally:
                self.root.after(1000, self.heartbeat)

    def close(self):
        try:
            for cleanup in (self.tray.close, self.hotkey.close, hide_cg, recover_native_visibility, background.close):
                try:
                    cleanup()
                except Exception:
                    logging.exception("Shutdown cleanup failed")
            if control.status().get("running"):
                try:
                    subprocess.run(cg_command("quit"), capture_output=True, timeout=5,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                except Exception:
                    logging.exception("CG quit did not complete")
            if self.pending_request:
                self.ack(self.pending_request[1], "cancelled", "联动已关闭，排队请求已取消。")
                self.pending_request = None
            self.update(running=False, phase="stopped", hotkey_registered=False)
            self.ack(getattr(self, "quit_command", None), "completed", "F8 原生切换器已退出。")
        finally:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", choices=("toggle", "take-off", "put-on"))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--progress-token")
    args = parser.parse_args()
    if args.progress_token and (len(args.progress_token) != 32 or any(c not in "0123456789abcdef" for c in args.progress_token)):
        parser.error("Invalid progress token")
    RUNTIME.mkdir(parents=True, exist_ok=True)
    native.dpi_awareness()
    if args.once or args.prepare_only:
        maintenance.configure_logging(RUNTIME, worker=True)
        if sys.stdout:
            sys.stdout.reconfigure(encoding="utf-8")
        try:
            auto = native.automation()
            with auto.UIAutomationInitializerInThread():
                if not args.once or not background.owned_window():
                    background.prepare()
                ensure_cg()
                def report(**data):
                    logging.info('phase %s', data)
                    if args.progress_token:
                        write_json(RUNTIME / ('progress-' + args.progress_token + '.json'), dict(token=args.progress_token, **data))
                result = transition(args.once, report) if args.once else {}
            print(json.dumps(result, ensure_ascii=False))
            return 0
        except Exception as error:
            logging.exception("One-shot failed")
            print(json.dumps({"error": str(error)}, ensure_ascii=False))
            return 1
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateMutexW(None, False, "Local\\FeibiNativeHatBridge_F8_v1")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:
        kernel.CloseHandle(handle)
        return 0
    root = None
    maintenance.configure_logging(RUNTIME)
    try:
        root = ServiceLoop()
        bridge = Bridge(root)
        root.mainloop()
    except Exception as error:
        logging.exception("Bridge startup failed")
        background.close()
        write_json(STATUS, {"running": False, "pid": os.getpid(), "phase": "error",
                           "hotkey_registered": False, "error": str(error), "updated_at": time.time()})
        return 1
    finally:
        kernel.CloseHandle(handle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

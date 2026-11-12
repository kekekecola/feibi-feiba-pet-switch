"""Send a local action to the F8 companion, or inspect its status."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid


RUNTIME = Path(__file__).resolve().parent / "runtime"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def process_alive(pid: int) -> bool:
    if os.name != "nt" or not isinstance(pid, int) or pid <= 0:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) and code.value == 259)
    finally:
        kernel32.CloseHandle(handle)


def status() -> dict:
    result = read_json(RUNTIME / "status.json")
    age = time.time() - result.get("updated_at", 0)
    result["running"] = bool(result.get("running") and age < 6 and process_alive(result.get("pid")))
    if not result["running"]:
        result["hotkey_registered"] = False
    return result


def main() -> int:
    if sys.stdout:
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F8 companion control")
    parser.add_argument("action", choices=("status", "take-off", "put-on", "toggle", "play-take-off", "play-put-on", "hide", "quit"))
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--hold", action="store_true", help="Keep the CG's last frame visible until hide, up to 15 seconds")
    parser.add_argument("--x", type=int, help="CG window physical left coordinate")
    parser.add_argument("--y", type=int, help="CG window physical top coordinate")
    parser.add_argument("--dpi", type=int, help="Native pet monitor DPI, for example 144")
    parser.add_argument("--native-width", type=int, help="Measured native sprite cell width in physical pixels")
    arguments = parser.parse_args()
    if not math.isfinite(arguments.timeout) or not 0 < arguments.timeout <= 120:
        parser.error('--timeout must be finite and between 0 and 120 seconds')
    if (arguments.x is None) != (arguments.y is None):
        parser.error("--x and --y must be provided together")
    if arguments.dpi is not None and not 48 <= arguments.dpi <= 480:
        parser.error("--dpi must be between 48 and 480")
    if arguments.native_width is not None and not 20 <= arguments.native_width <= 1500:
        parser.error('--native-width must be between 20 and 1500')
    if (arguments.hold or arguments.x is not None or arguments.dpi is not None or arguments.native_width is not None) and arguments.action not in ("play-take-off", "play-put-on"):
        parser.error("--hold, --x, --y and --dpi apply to play-* commands")
    current = status()
    if arguments.action == "status":
        print(json.dumps(current, ensure_ascii=False))
        return 0
    if not current.get("running"):
        print(json.dumps({"result": "failed", "message": "播放器未运行，请先双击启动播放器。", "status": current}, ensure_ascii=False))
        return 2
    command_id = uuid.uuid4().hex
    command = {"command_id": command_id, "action": arguments.action, "issued_at": time.time()}
    if arguments.action in ("play-take-off", "play-put-on"):
        command.update(hold=arguments.hold, x=arguments.x, y=arguments.y, dpi=arguments.dpi,
                       native_width=arguments.native_width)
    directory = RUNTIME / "commands"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{command_id}.json"
    temporary = directory / f"{command_id}.tmp"
    temporary.write_text(json.dumps(command), encoding="utf-8")
    os.replace(temporary, path)
    deadline = time.monotonic() + max(0.1, arguments.timeout)
    ack_path = RUNTIME / "acks" / path.name
    while time.monotonic() < deadline:
        ack = read_json(ack_path)
        if ack.get("command_id") == command_id:
            # The player writes completion after committing its stable form.
            print(json.dumps(ack, ensure_ascii=False))
            return 0 if ack.get("result") == "completed" else 3
        if not status().get("running"):
            # A quit acknowledgement can be written immediately before process exit.
            ack = read_json(ack_path)
            if ack.get("result") == "completed":
                print(json.dumps(ack, ensure_ascii=False))
                return 0
            print(json.dumps({"result": "failed", "command_id": command_id, "message": "播放器已停止。"}, ensure_ascii=False))
            return 2
        time.sleep(0.08)
    print(json.dumps({"result": "timeout", "command_id": command_id, "message": "等待动作完成超时。请先查询 status，避免重复切换。"}, ensure_ascii=False))
    return 4


if __name__ == "__main__":
    raise SystemExit(main())

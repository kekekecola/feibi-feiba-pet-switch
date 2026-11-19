"""Wake the local CG player while retaining file IPC as the source of truth."""
import ctypes
from ctypes import wintypes
import hashlib
import os
import threading

from paths import RUNTIME


def event_name():
    key = hashlib.sha256(str(RUNTIME.resolve()).casefold().encode('utf-8')).hexdigest()[:24]
    return 'Local\\FeibiCgWake_' + key


def kernel_api():
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateEventW.restype = wintypes.HANDLE
    kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.OpenEventW.restype = wintypes.HANDLE
    kernel.SetEvent.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    return kernel


def notify():
    if os.name != 'nt':
        return
    try:
        kernel = kernel_api()
        handle = kernel.OpenEventW(0x0002, False, event_name())
        if handle:
            try:
                kernel.SetEvent(handle)
            finally:
                kernel.CloseHandle(handle)
    except OSError:
        pass  # The periodic file poll remains available.


def listen(player):
    if os.name != 'nt':
        return
    from tkinter import TclError
    kernel = kernel_api()
    handle = kernel.CreateEventW(None, False, False, event_name())
    if not handle:
        return

    def wait():
        try:
            while not player.closed:
                result = kernel.WaitForSingleObject(handle, 1000)
                if result == 0 and not player.closed:
                    try:
                        player.root.after(0, player._drain_commands)
                    except (RuntimeError, TclError):
                        break
                elif result not in (0, 258):
                    break
        finally:
            kernel.CloseHandle(handle)

    threading.Thread(target=wait, daemon=True).start()

"""Call native MSAA actions directly, without UIA's activation wrapper."""
import ctypes
from ctypes import wintypes
import pet_registry


def pet_actions(hwnd, names):
    import comtypes
    from comtypes.automation import VARIANT
    from comtypes.gen.UIAutomationClient import IAccessible
    oleacc = ctypes.WinDLL('oleacc')
    oleacc.AccessibleObjectFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD,
        ctypes.POINTER(comtypes.GUID), ctypes.POINTER(ctypes.POINTER(IAccessible))]
    oleacc.AccessibleChildren.argtypes = [ctypes.POINTER(IAccessible), ctypes.c_long,
        ctypes.c_long, ctypes.POINTER(VARIANT), ctypes.POINTER(ctypes.c_long)]
    root = ctypes.POINTER(IAccessible)()
    result = oleacc.AccessibleObjectFromWindow(hwnd, 0xfffffffc,
        ctypes.byref(IAccessible._iid_), ctypes.byref(root))
    if result != 0 or not root:
        raise RuntimeError('无法读取后台原生 MSAA 控件。')
    found = {}
    observed = {}
    ambiguous = set()
    visited = 0

    def inspect(obj, child):
        name = str(obj.accName(child) or '')
        for form, label in names.items():
            # Chromium exposes clickable text descendants too. Only the actual
            # pushbutton is a candidate; duplicate cards still fail closed.
            if pet_registry.matches(form, name) and obj.accRole(child) == 43 and obj.accDefaultAction(child):
                if form in found:
                    ambiguous.add(form)
                found[form] = (obj, child)
                observed[form] = name

    def walk(obj, depth=0):
        nonlocal visited
        visited += 1
        if depth > 30 or visited > 4000:
            return
        try:
            inspect(obj, 0)
            count = min(int(obj.accChildCount), 200)
            if count <= 0:
                return
            children = (VARIANT * count)()
            retrieved = ctypes.c_long()
            oleacc.AccessibleChildren(obj, 0, count, children, ctypes.byref(retrieved))
            for child in children[:retrieved.value]:
                if child.vt in (9, 13):
                    walk(child.value.QueryInterface(IAccessible), depth + 1)
                elif child.vt == 3:
                    inspect(obj, child.value)
        except comtypes.COMError:
            return

    walk(root)
    if ambiguous or set(found) != set(names):
        raise RuntimeError('后台 MSAA 宠物动作未就绪；已取消，不采用抢焦点的备用操作。')
    pet_registry.observe_names(observed)
    return found


def execute(action):
    accessible, child_id = action
    accessible.accDoDefaultAction(child_id)

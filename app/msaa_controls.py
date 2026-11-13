"""Call native MSAA actions directly, without UIA's activation wrapper."""
import ctypes
from ctypes import wintypes
import pet_registry

# Standard MSAA selection controls. Text/image descendants are deliberately
# excluded even when Chromium advertises a click action on them.
ACTION_ROLES = {34, 43, 44, 45}


def cached_actions(hwnd, root, oleacc, IAccessible, VARIANT, names, roles):
    """Batch-read all selection controls, then resolve raw MSAA by geometry.

    No pointer input and no UIA default action. Any unsupported provider or
    uncertain match falls back to the complete MSAA enumeration below.
    """
    from uiautomation.uiautomation import _AutomationClient
    uia = _AutomationClient.instance().IUIAutomation
    condition = uia.CreatePropertyCondition(30003,50000)
    for kind in (50002,50007,50013):
        condition = uia.CreateOrCondition(condition,uia.CreatePropertyCondition(30003,kind))
    cache = uia.CreateCacheRequest()
    for property_id in (30005,30001):
        cache.AddProperty(property_id)
    elements = uia.ElementFromHandle(hwnd).FindAllBuildCache(4,condition,cache)
    candidates = {form:[] for form in names}
    for index in range(elements.Length):
        item = elements.GetElement(index)
        name = item.CachedName
        for form in names:
            if pet_registry.matches(form,name,roles):
                rect = item.CachedBoundingRectangle
                candidates[form].append((name,(rect.left,rect.top,rect.right,rect.bottom)))
    if any(len(items)!=1 for items in candidates.values()):
        return None
    resolved = {}
    observed = {}
    for form,items in candidates.items():
        expected,rect = items[0]
        x,y=(rect[0]+rect[2])//2,(rect[1]+rect[3])//2
        seen,anchors=set(),{}
        budget=[250]
        def find(obj, child=0):
            budget[0]-=1
            if budget[0]<0:
                return None
            unknown=obj.QueryInterface(__import__('comtypes').IUnknown)
            key=ctypes.cast(unknown,ctypes.c_void_p).value
            anchors.setdefault(key,unknown)
            identity=(key,child)
            if identity in seen:
                return None
            seen.add(identity)
            left,top,width,height=obj.accLocation(child)
            if not (left<=x<left+width and top<=y<top+height):
                return None
            name=str(obj.accName(child) or '')
            if pet_registry.normalized(name)==pet_registry.normalized(expected) and obj.accRole(child) in ACTION_ROLES and obj.accDefaultAction(child):
                return obj,child
            if child:
                return None
            count=int(obj.accChildCount)
            for offset in range(0,min(count,8000),128):
                size=min(128,count-offset);children=(VARIANT*size)();retrieved=ctypes.c_long()
                oleacc.AccessibleChildren(obj,offset,size,children,ctypes.byref(retrieved))
                for entry in children[:retrieved.value]:
                    result=None
                    if entry.vt in (9,13):
                        result=find(entry.value.QueryInterface(IAccessible))
                    elif entry.vt==3:
                        result=find(obj,entry.value)
                    if result is not None:
                        return result
                if budget[0]<0:
                    return None
            return None
        action=find(root)
        if action is None:
            return None
        resolved[form]=action
        observed[form]=expected
    pet_registry.observe_names(observed)
    return resolved


class PetControlsError(RuntimeError):
    def __init__(self, missing, duplicates, counts, limit=False):
        self.details = dict(missing=missing, duplicates=duplicates, matches=counts, traversal_limited=limit)
        parts = []
        if missing:
            parts.append('宠物选择页未找到：' + '、'.join(missing) + '；请确认两只均已导入且名称与配置一致')
        if duplicates:
            parts.append('存在同名可操作条目：' + '、'.join(duplicates) + '；请选定保留的一套或改成唯一名称')
        if limit:
            parts.append('控件树超过读取限制，不能确认唯一目标')
        super().__init__('；'.join(parts) + '。尚未选择宠物或播放CG。')


class ActionCollector:
    def __init__(self, names, roles):
        self.names, self.roles = names, roles
        self.matches = {form:[] for form in names}
        self.observed = {}
        self.seen = set()

    def add(self, identity, name, role, default_action, action):
        if identity in self.seen:
            return
        self.seen.add(identity)
        if role not in ACTION_ROLES or not default_action:
            return
        for form in self.names:
            if pet_registry.matches(form, name, self.roles):
                self.matches[form].append(action)
                self.observed[form] = name

    def resolve(self, limited=False):
        counts = {self.names[form]:len(items) for form,items in self.matches.items()}
        missing = [self.names[form] for form,items in self.matches.items() if not items]
        duplicates = [self.names[form]+'（'+str(len(items))+'项）' for form,items in self.matches.items() if len(items)>1]
        if missing or duplicates or limited:
            raise PetControlsError(missing, duplicates, counts, limited)
        pet_registry.observe_names(self.observed)
        return {form:items[0] for form,items in self.matches.items()}


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
    roles = pet_registry.load()
    try:
        fast = cached_actions(hwnd,root,oleacc,IAccessible,VARIANT,names,roles)
        if fast is not None:
            return fast
    except Exception:
        pass  # Provider compatibility is handled by the full scan, not a click.
    collector = ActionCollector(names, roles)
    walked = set()
    limited = False
    visited = 0
    anchors = {}

    def identity(obj):
        unknown = obj.QueryInterface(comtypes.IUnknown)
        key = ctypes.cast(unknown, ctypes.c_void_p).value
        anchors.setdefault(key, unknown)  # Keep identities alive for this scan.
        return key

    def inspect(obj, child):
        name = str(obj.accName(child) or '')
        role = obj.accRole(child)
        if role not in ACTION_ROLES or not any(pet_registry.matches(form,name,roles) for form in names):
            return
        collector.add((identity(obj),child), name, role, obj.accDefaultAction(child), (obj,child))

    def walk(obj, depth=0):
        nonlocal visited, limited
        key = identity(obj)
        if key in walked:
            return
        walked.add(key)
        visited += 1
        if depth > 40 or visited > 8000:
            limited = True
            return
        try:
            inspect(obj, 0)
            count = int(obj.accChildCount)
            if count <= 0:
                return
            for offset in range(0, min(count,8000),128):
                size = min(128,count-offset)
                children = (VARIANT * size)()
                retrieved = ctypes.c_long()
                oleacc.AccessibleChildren(obj, offset, size, children, ctypes.byref(retrieved))
                for child in children[:retrieved.value]:
                    if child.vt in (9, 13):
                        walk(child.value.QueryInterface(IAccessible), depth + 1)
                    elif child.vt == 3:
                        inspect(obj, child.value)
                if visited > 8000:
                    break
            if count>8000:
                limited=True
        except comtypes.COMError:
            return

    walk(root)
    return collector.resolve(limited)


def execute(action):
    accessible, child_id = action
    accessible.accDoDefaultAction(child_id)

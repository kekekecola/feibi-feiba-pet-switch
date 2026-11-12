import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from tray import menu_entries


class TrayMenuTests(unittest.TestCase):
    def state(self, **overrides):
        return dict(dict(phase='idle', connection_state='ready', form='feiba', hotkey='Ctrl+Alt+H',version='1.3.0'), **overrides)

    def test_primary_action_targets_other_form(self):
        entries = menu_entries(self.state(), managed=True)
        primary = next(e for e in entries if e and e.get('id')==1)
        self.assertEqual(primary['action'],'put-on')
        self.assertTrue(primary['enabled'])
        self.assertIn('菲比啾比', primary['label'])
        self.assertIn('Ctrl+Alt+H', entries[2]['label'])

    def test_busy_switch_is_disabled(self):
        primary = next(e for e in menu_entries(self.state(phase='transition')) if e and e.get('id')==1)
        self.assertFalse(primary['enabled'])

    def test_update_is_disabled_when_unmanaged_or_updating(self):
        for managed, updater in [(False,{}),(True,{'phase':'applying'})]:
            entry = next(e for e in menu_entries(self.state(),updater,managed) if e and e.get('id')==4)
            self.assertFalse(entry['enabled'])

    def test_unique_action_ids_and_explicit_exit(self):
        entries = [e for e in menu_entries(self.state(), managed=True) if e and e.get('action')]
        ids = [e['id'] for e in entries]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertIn('停用',next(e['label'] for e in entries if e['action']=='quit'))
        self.assertTrue(any(e['action']=='logs' for e in entries))

    def test_native_multi_resolution_icon_loads(self):
        icon = ROOT / 'app' / 'assets' / 'feibi-tray.ico'
        with Image.open(icon) as im:
            self.assertTrue({(16,16),(32,32),(64,64),(256,256)}.issubset(im.ico.sizes()))
            bounds = im.getchannel('A').getbbox()
            self.assertGreaterEqual(bounds[2]-bounds[0],240)
            tiny = im.ico.getimage((16,16)).convert('RGBA').getchannel('A').getbbox()
            self.assertGreaterEqual(tiny[2]-tiny[0],14)
        user = ctypes.WinDLL('user32')
        user.LoadImageW.argtypes = [wintypes.HINSTANCE,wintypes.LPCWSTR,wintypes.UINT,ctypes.c_int,ctypes.c_int,wintypes.UINT]
        user.LoadImageW.restype = wintypes.HANDLE
        user.DestroyIcon.argtypes = [wintypes.HICON]
        handle = user.LoadImageW(None,str(icon),1,32,32,0x10)
        self.assertTrue(handle)
        user.DestroyIcon(handle)

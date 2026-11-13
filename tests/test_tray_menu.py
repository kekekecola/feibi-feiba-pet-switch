import ctypes
from ctypes import wintypes
from pathlib import Path
import sys
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))
from tray import menu_entries,event_action


class TrayMenuTests(unittest.TestCase):
    def state(self, **overrides):
        return dict(dict(phase='idle', connection_state='ready', form='feiba', hotkey='Ctrl+Alt+H',version='1.3.0'), **overrides)

    def test_right_menu_contains_only_exit(self):
        entries=menu_entries(self.state(),managed=True)
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['action'],'quit')

    def test_exit_explains_current_shortcut(self):
        label=menu_entries(self.state())[0]['label']
        self.assertIn('停用',label)
        self.assertIn('Ctrl+Alt+H',label)

    def test_left_click_and_keyboard_activation_open_control_room(self):
        for event in (0x201,0x202,0x203,0x400,0x401,0x10202,0x10400):
            self.assertEqual(event_action(event),'controlroom')
        self.assertIsNone(event_action(0x205))

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

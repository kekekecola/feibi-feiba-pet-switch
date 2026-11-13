import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch,Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import paths
import native_bridge


class LocalUiWakeTests(unittest.TestCase):
    def test_direct_managed_script_uses_shared_runtime(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{},clear=True):
            home=Path(folder)
            app=home/'versions'/'review'/'app';app.mkdir(parents=True)
            (home/'current.json').write_text('{}');(home/'launcher.py').touch()
            self.assertEqual(paths.runtime_directory(app).resolve(),(home/'runtime').resolve())

    def test_user_closed_window_is_not_reopened_by_watchdog(self):
        bridge=native_bridge.Bridge.__new__(native_bridge.Bridge)
        bridge.closing=False;bridge.room_open_nonce='nonce';bridge.update=Mock();bridge.close_room=Mock()
        with patch.object(native_bridge.control,'read_json',return_value=dict(nonce='nonce',closed_by_user=True,visible=False,updated_at=time.time())):
            bridge.verify_room('nonce',None,False)
        bridge.close_room.assert_not_called()

    def test_unacknowledged_alive_process_is_restarted_once(self):
        bridge=native_bridge.Bridge.__new__(native_bridge.Bridge)
        bridge.closing=False;bridge.room_open_nonce='nonce';bridge.update=Mock();bridge.close_room=Mock();bridge.open_room=Mock()
        with patch.object(native_bridge.control,'read_json',return_value={}):
            bridge.verify_room('nonce',None,False)
            bridge.close_room.assert_called_once()
            bridge.open_room.assert_called_once_with(None,True)
            bridge.close_room.reset_mock();bridge.verify_room('nonce',None,True)
            bridge.close_room.assert_not_called()

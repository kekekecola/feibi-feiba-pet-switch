from pathlib import Path
import sys,tempfile,json,time
from types import SimpleNamespace
from unittest.mock import Mock,patch
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import background_uia as b
import native_bridge as bridge
import native_uia as native


class IdleEfficiencyTests(unittest.TestCase):
    def test_known_helper_does_not_enumerate_system_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            state=Path(folder)/'state.json';state.write_text(json.dumps({'hwnd':11,'pid':22}))
            windows=Mock();expected=object()
            with patch.object(b,'STATE',state),patch.object(b,'api',return_value=(windows,Mock())), \
                 patch.object(b,'window_from_state',return_value=expected):
                self.assertIs(b.owned_window(),expected)
                windows.enumerate.assert_not_called()

    def test_reused_hwnd_or_missing_marker_is_rejected(self):
        windows=Mock();windows.user32.GetWindowThreadProcessId.side_effect=lambda h,p:setattr(p._obj,'value',22)
        windows.user32.GetPropW.return_value=0
        self.assertIsNone(b.window_from_state(windows,{'hwnd':11,'pid':22}))
        windows.user32.GetPropW.return_value=b.MARKER_VALUE
        self.assertIsNone(b.window_from_state(windows,{'hwnd':11,'pid':33}))

    def test_ready_connection_never_runs_periodic_full_preparation(self):
        obj=bridge.Bridge.__new__(bridge.Bridge)
        obj.closing=False;obj.busy=False;obj.next_reconnect=0;obj.next_health_check=0
        obj.state={'connection_state':'ready'};obj.update=Mock()
        with patch.object(bridge.recovery_journal,'JOURNAL') as journal, \
             patch.object(bridge.background,'owned_window',return_value=object()), \
             patch.object(bridge.control,'status',return_value={'running':True}), \
             patch.object(bridge.native,'Windows') as windows:
            journal.exists.return_value=False
            obj.reconnect_if_needed()
            obj.update.assert_not_called();windows.assert_not_called()
            self.assertGreater(obj.next_health_check,time.monotonic())

    def test_compact_layout_failure_restores_original_dimensions(self):
        windows=Mock();windows.user32.GetSystemMetrics.return_value=0
        window=SimpleNamespace(hwnd=11,rect=(0,0,2200,1600),dpi=168)
        with patch.object(b,'api',return_value=(windows,Mock())), \
             patch.object(b.msaa_controls,'pet_actions',side_effect=RuntimeError('layout')):
            self.assertFalse(b.compact_helper(window))
        calls=windows.user32.SetWindowPos.call_args_list
        self.assertEqual(calls[-1].args[4:6],(2200,1600))

    def test_small_helper_is_not_enlarged(self):
        self.assertFalse(b.compact_helper(SimpleNamespace(rect=(0,0,800,600),dpi=96)))

    def test_known_pet_heartbeat_skips_global_client_state(self):
        with patch.object(native,'preferences',return_value={'selected-avatar-id':'pet_known'}), \
             patch.object(native.pet_registry,'load',return_value={'feibi':{'id':'pet_known','name':'Feibi'}}), \
             patch.object(native.pet_registry,'refresh') as refresh,patch.dict(native.PET_IDS),patch.dict(native.PET_NAMES):
            self.assertEqual(native.selected_form(refresh=False),'feibi')
            refresh.assert_not_called()

    def test_unknown_pet_id_still_refreshes_import_mapping(self):
        with patch.object(native,'preferences',return_value={'selected-avatar-id':'pet_new'}), \
             patch.object(native.pet_registry,'load',return_value={'feibi':{'id':'pet_old','name':'Feibi'}}), \
             patch.object(native.pet_registry,'refresh',return_value={'feibi':{'id':'pet_new','name':'Feibi'}}) as refresh, \
             patch.dict(native.PET_IDS,{},clear=True),patch.dict(native.PET_NAMES,{},clear=True):
            self.assertEqual(native.selected_form(refresh=False),'feibi')
            refresh.assert_called_once_with('pet_new')

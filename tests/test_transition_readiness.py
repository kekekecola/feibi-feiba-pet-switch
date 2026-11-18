import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import background_uia as background
import cg_ipc
import msaa_controls
import native_bridge
import player
from request_lifecycle import claim


class TransitionReadinessTests(unittest.TestCase):
    def test_helper_start_uses_expansion_without_toggling_open_menu(self):
        trigger = Mock()
        pattern = trigger.GetPattern.return_value
        with patch.object(background.native, 'exists', return_value=True), \
             patch.object(background.native, 'automation'), patch.object(background, 'click_background') as click:
            pattern.ExpandCollapseState = 0
            self.assertTrue(background.open_file_menu(trigger, Mock(), Mock(), Mock()))
            pattern.ExpandCollapseState = 1
            self.assertTrue(background.open_file_menu(trigger, Mock(), Mock(), Mock()))
        pattern.Expand.assert_called_once_with(waitTime=0)
        click.assert_not_called()

    def test_unrelated_or_withdrawn_cg_cannot_authorize_selection(self):
        ticket = cg_ipc.Ticket.__new__(cg_ipc.Ticket)
        ticket.command_id = 'a' * 32
        good = dict(running=True, visible=True, playback_command=ticket.command_id,
                    phase='holding', updated_at=time.time())
        for changes, expected in [({}, True), ({'playback_command':'b'*32}, False),
                                  ({'visible':False}, False), ({'phase':'idle'}, False),
                                  ({'updated_at':0}, False)]:
            with self.subTest(changes=changes), patch.object(cg_ipc.control, 'status', return_value=good | changes):
                self.assertEqual(ticket.is_visible(), expected)

    def test_cg_gate_runs_immediately_before_native_action(self):
        selector = background.BackgroundSelection.__new__(background.BackgroundSelection)
        selector.windows = Mock()
        user = selector.windows.user32
        user.GetForegroundWindow.return_value = 99
        user.GetSystemMetrics.return_value = 0
        selector.window = SimpleNamespace(hwnd=11, pid=22)
        helper = SimpleNamespace(rect=(-1000, 0, -128, 600), exstyle=0x080800A0)
        with patch.object(background, 'window_from_state', return_value=helper), \
             patch.object(background.native, 'preferences', return_value={}), \
             patch.object(background.msaa_controls, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'CG'):
                selector.select('feibi', object(), before_action=Mock(side_effect=RuntimeError('CG withdrawn')))
            execute.assert_not_called()
            self.assertEqual([call.args for call in user.EnableWindow.call_args_list], [(11, True), (11, False)])

    def test_conflict_or_disappearing_cg_aborts(self):
        ticket = Mock()
        conflict = threading.Event()
        ticket.is_visible.return_value = True
        with patch.object(native_bridge, 'cg_visible', return_value=True):
            native_bridge.confirm_playback(ticket, conflict)
            conflict.set()
            with self.assertRaises(RuntimeError):
                native_bridge.confirm_playback(ticket, conflict)
        conflict.clear()
        with patch.object(native_bridge, 'cg_visible', return_value=False):
            with self.assertRaises(RuntimeError):
                native_bridge.confirm_playback(ticket, conflict)

    def test_stale_page_cannot_repair_inside_animation(self):
        selector = background.BackgroundSelection.__new__(background.BackgroundSelection)
        selector.window = SimpleNamespace(hwnd=11)
        with patch.object(background.msaa_controls, 'pet_actions', side_effect=RuntimeError('stale')), \
             patch.object(background, 'prepare') as repair:
            with self.assertRaisesRegex(RuntimeError, '后台宠物页面失效'):
                selector.prepare('feibi', repair=False)
            repair.assert_not_called()

    def test_f8_recovers_loading_owned_helper_before_cg(self):
        helper = SimpleNamespace(hwnd=11, pid=22)
        selector = background.BackgroundSelection.__new__(background.BackgroundSelection)
        selector.window = helper
        missing = msaa_controls.PetControlsError(['菲比啾比', '菲八啾比'], [], {})
        card = object()
        with patch.object(background.msaa_controls, 'pet_actions', side_effect=missing), \
             patch.object(background, 'owned_window', return_value=helper), \
             patch.object(background, 'finish_prepare', return_value=card) as repair:
            self.assertIs(selector.prepare('feibi', reuse_only=True), card)
            repair.assert_called_once_with(helper, 'feibi')
            self.assertTrue(selector.repaired)

    def test_slow_list_arrival_does_not_repeat_pets_navigation(self):
        helper = SimpleNamespace(hwnd=11)
        missing = msaa_controls.PetControlsError(['菲比啾比', '菲八啾比'], [], {})
        card = object()
        with patch.object(background.native, 'automation'), \
             patch.object(background.ui_labels, 'pattern', return_value='pets'), \
             patch.object(background, 'setup_control', return_value=Mock()), \
             patch.object(background.msaa_controls, 'semantic_action', return_value=Mock()), \
             patch.object(background.msaa_controls, 'execute') as invoke, \
             patch.object(background.msaa_controls, 'pet_actions', side_effect=[missing, missing, {'feibi':card}]), \
             patch.object(background, 'click_background') as click, patch.object(background.time, 'sleep'):
            self.assertIs(background._finish_prepare(helper, 'feibi'), card)
            invoke.assert_called_once()
            click.assert_not_called()

    def test_ambiguous_list_is_not_repaired_or_clicked(self):
        selector = background.BackgroundSelection.__new__(background.BackgroundSelection)
        selector.window = SimpleNamespace(hwnd=11)
        error = msaa_controls.PetControlsError([], ['菲比啾比'], {'菲比啾比':2})
        with patch.object(background.msaa_controls, 'pet_actions', side_effect=error), \
             patch.object(background, 'finish_prepare') as repair:
            with self.assertRaises(msaa_controls.PetControlsError):
                selector.prepare('feibi', reuse_only=True)
            repair.assert_not_called()

    def test_stale_pet_controls_do_not_start_cg(self):
        selector = Mock()
        selector.prepare.side_effect = RuntimeError('stale controls')
        with patch.object(native_bridge.native, 'selected_form', return_value='feibi'), \
             patch.object(native_bridge.native, 'Windows'), \
             patch.object(native_bridge.native, 'find_overlay', return_value=(Mock(), [0, 0, 192, 208])), \
             patch.object(native_bridge.native, 'preferences', return_value={}), \
             patch.object(native_bridge.background, 'BackgroundSelection', return_value=selector), \
             patch.object(native_bridge, 'ensure_cg'), patch.object(native_bridge, 'trace_focus'), \
             patch.object(native_bridge.cg_ipc, 'Ticket') as ticket:
            with self.assertRaisesRegex(RuntimeError, 'stale controls'):
                native_bridge.transition('take-off', Mock())
            ticket.assert_not_called()

    def waiting_player(self):
        obj = player.Player.__new__(player.Player)
        obj.state = player.FormState('feibi')
        obj.phase = 'idle'
        obj.preview = False
        obj.transition = [object(), object()]
        obj.root = Mock()
        obj.renderer = Mock()
        obj._draw = Mock()
        obj._ack = Mock()
        obj._write_status = Mock()
        obj._refresh_size_once = Mock()
        obj._advance_transition = Mock()
        with patch.object(player,'native_pet_size',return_value=112), patch.object(player,'window_dpi',return_value=96):
            obj.request('play-take-off', 'a'*32, {'await_native':True})
        return obj

    def test_cg_waits_for_confirmation_of_same_playback(self):
        obj = self.waiting_player()
        self.assertEqual(obj.phase, 'awaiting_native')
        obj._draw.assert_called_once_with(obj.transition[0])
        obj._advance_transition.assert_not_called()
        obj.request('confirm-native', 'b'*32, {'playback_id':'c'*32})
        obj._advance_transition.assert_not_called()
        obj.request('confirm-native', 'b'*32, {'playback_id':'a'*32})
        obj._advance_transition.assert_called_once()
        self.assertEqual(obj.phase, 'transition')

    def test_unconfirmed_cg_cancels_without_transforming(self):
        obj = self.waiting_player()
        obj.cancel_transition()
        obj.request('confirm-native', 'b'*32, {'playback_id':'a'*32})
        obj._advance_transition.assert_not_called()
        obj.renderer.hide.assert_called_once()
        self.assertEqual(obj.state.form, 'feibi')
        self.assertEqual(obj._ack.call_args_list[0].args[:2], ('a'*32, 'cancelled'))

    def test_reported_old_overlay_is_not_counted_as_completed(self):
        native = native_bridge.native
        windows = Mock()
        windows.user32.GetWindowThreadProcessId.side_effect = lambda hwnd, pid: setattr(pid._obj, 'value', 22)
        windows.user32.IsWindowVisible.return_value = True
        with patch.object(native, 'automation'), patch.object(native, 'exists', side_effect=[True, False]), \
             patch.object(native.time, 'monotonic', side_effect=[0, 3]):
            with self.assertRaisesRegex(RuntimeError, '旧形态'):
                native.confirm_visible_overlay(windows, SimpleNamespace(hwnd=11, pid=22), 'feibi', 'feiba')

    def test_switch_accepts_confirmed_replacement_native_window(self):
        native = native_bridge.native
        windows = Mock()
        windows.user32.IsWindowVisible.return_value = False
        with patch.object(native, '_find_overlay_once', return_value=(SimpleNamespace(hwnd=99), (0, 0, 192, 208))), \
             patch.object(native, 'automation') as auto, patch.object(native, 'exists', side_effect=[False, True]):
            form = native.confirm_visible_overlay(windows, SimpleNamespace(hwnd=11, pid=22), 'feibi', 'feiba')
            self.assertEqual(form, 'feiba')
            auto.return_value.ControlFromHandle.assert_called_once_with(99)

    def test_delayed_cg_command_expires_before_it_can_start(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(cg_ipc, 'RUNTIME', Path(folder)):
            ticket = cg_ipc.Ticket('play-put-on')
            data = json.loads((Path(folder) / 'commands' / (ticket.command_id+'.json')).read_text())
            self.assertEqual(data['expires_at'] - data['issued_at'], 3)
            with patch('request_lifecycle.time.time', return_value=data['expires_at']+1):
                self.assertFalse(claim(Path(folder) / 'decisions', data))

    def test_settings_entry_can_be_button_instead_of_menu_item(self):
        root = Mock()
        with patch.object(background.ui_labels, 'pattern', return_value='settings'), \
             patch.object(background.native, 'exists', side_effect=[False, True]):
            self.assertIs(background.setup_control(root, 'settings'), root.ButtonControl.return_value)

    def test_semantic_navigation_has_no_second_click_when_opened(self):
        with patch.object(background, 'setup_control', return_value=Mock()), \
             patch.object(background.msaa_controls, 'semantic_action', return_value=object()), \
             patch.object(background.msaa_controls, 'execute') as execute, \
             patch.object(background, 'click_background') as click, \
             patch.object(background.ui_labels, 'pattern', return_value='settings'):
            background.navigate_setup(Mock(), SimpleNamespace(hwnd=11), 'settings', lambda:True)
            execute.assert_called_once()
            click.assert_not_called()

    def test_helper_is_hidden_and_disabled_even_when_preparation_fails(self):
        windows = Mock()
        with patch.object(background, 'api', return_value=(windows, Mock())), \
             patch.object(background, '_finish_prepare', side_effect=RuntimeError('menu failed')), \
             patch.object(background, 'invisible') as invisible:
            with self.assertRaises(RuntimeError):
                background.finish_prepare(SimpleNamespace(hwnd=11))
            self.assertEqual(invisible.call_count,2)
            self.assertEqual(invisible.call_args_list[0].kwargs,{'alpha':1})
            self.assertEqual(windows.user32.EnableWindow.call_args.args, (11, False))

    def test_helper_is_outside_all_monitors_and_never_topmost(self):
        windows=Mock();user=windows.user32
        user.GetSystemMetrics.side_effect=lambda index:{76:-1920,77:-300}[index]
        windows.get_long.return_value=0x40000
        setter=Mock();helper=SimpleNamespace(hwnd=11,rect=(0,0,1400,900))
        with patch.object(background,'api',return_value=(windows,setter)):
            background.invisible(helper,alpha=1)
        style=setter.call_args.args[2]
        self.assertTrue(style&0x20);self.assertTrue(style&0x08000000)
        self.assertFalse(style&0x40000)
        args=user.SetWindowPos.call_args.args
        self.assertEqual(args[1].value,1) # HWND_BOTTOM, never HWND_TOPMOST
        self.assertLess(args[2]+1400,-1920)

    def test_parking_happens_before_helper_can_receive_input(self):
        windows=Mock();events=[]
        windows.user32.EnableWindow.side_effect=lambda hwnd,enabled:events.append(('enabled',enabled))
        with patch.object(background,'api',return_value=(windows,Mock())), \
             patch.object(background,'invisible',side_effect=lambda *a,**k:events.append(('park',k.get('alpha',0)))), \
             patch.object(background,'_finish_prepare',return_value={'prepared':True}):
            background.finish_prepare(SimpleNamespace(hwnd=11))
        self.assertEqual(events[0],('park',1))
        self.assertEqual(events[1],('enabled',True))
        self.assertEqual(events[-1],('enabled',False))

    def test_semantic_navigation_rejects_duplicate_entries(self):
        def scan(hwnd, collect, include):
            collect((1,0), 'Settings', 34, 'click', object())
            collect((2,0), 'Settings', 43, 'click', object())
            return False
        with patch.object(msaa_controls, 'scan_actions', side_effect=scan):
            with self.assertRaises(RuntimeError):
                msaa_controls.semantic_action(11, '^Settings$')

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
from request_lifecycle import claim


class TransitionReadinessTests(unittest.TestCase):
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
        selector.window = SimpleNamespace(hwnd=11)
        with patch.object(background.native, 'preferences', return_value={}), \
             patch.object(background.msaa_controls, 'execute') as execute:
            with self.assertRaisesRegex(RuntimeError, 'CG'):
                selector.select('feibi', object(), before_action=Mock(side_effect=RuntimeError('CG withdrawn')))
            execute.assert_not_called()

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
            with self.assertRaisesRegex(RuntimeError, '本次切换已取消'):
                selector.prepare('feibi', repair=False)
            repair.assert_not_called()

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
            invisible.assert_called_once()
            self.assertEqual(windows.user32.EnableWindow.call_args.args, (11, False))

    def test_semantic_navigation_rejects_duplicate_entries(self):
        def scan(hwnd, collect, include):
            collect((1,0), 'Settings', 34, 'click', object())
            collect((2,0), 'Settings', 43, 'click', object())
            return False
        with patch.object(msaa_controls, 'scan_actions', side_effect=scan):
            with self.assertRaises(RuntimeError):
                msaa_controls.semantic_action(11, '^Settings$')

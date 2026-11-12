from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import background_uia as background
import native_bridge


class HelperRepairTests(unittest.TestCase):
    def selector(self):
        obj = background.BackgroundSelection.__new__(background.BackgroundSelection)
        obj.window = SimpleNamespace(hwnd=11)
        return obj

    def test_stale_controls_are_rebuilt_before_action(self):
        obj = self.selector()
        action = object()
        with patch.object(background.msaa_controls, 'pet_actions', side_effect=[RuntimeError('stale'), {'feiba':action}]) as find, \
             patch.object(background, 'prepare') as repair, \
             patch.object(background, 'owned_window', return_value=SimpleNamespace(hwnd=22)), \
             patch.object(background.native, 'automation', return_value=Mock()), \
             patch.object(background.msaa_controls, 'execute') as execute:
            self.assertIs(obj.prepare('feiba'), action)
            repair.assert_called_once()
            self.assertEqual(find.call_count, 2)
            execute.assert_not_called()
            self.assertTrue(obj.repaired)
            self.assertEqual(obj.window.hwnd, 22)

    def test_second_failure_does_not_retry_selection(self):
        obj = self.selector()
        with patch.object(background.msaa_controls, 'pet_actions', side_effect=RuntimeError('not ready')) as find, \
             patch.object(background, 'prepare') as repair, \
             patch.object(background, 'owned_window', return_value=SimpleNamespace(hwnd=22)), \
             patch.object(background.native, 'automation', return_value=Mock()), \
             patch.object(background.msaa_controls, 'execute') as execute:
            with self.assertRaises(RuntimeError):
                obj.prepare('feiba')
            self.assertEqual(find.call_count, 2)
            repair.assert_called_once()
            execute.assert_not_called()

    def test_exit_source_is_recorded(self):
        for command, expected in [(None,'tray_exit'), ('a'*32,'control_quit')]:
            bridge = native_bridge.Bridge.__new__(native_bridge.Bridge)
            bridge.busy = True
            bridge.update = Mock()
            bridge.request('quit', command)
            bridge.update.assert_called_once_with(shutdown_source=expected)

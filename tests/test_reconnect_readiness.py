from pathlib import Path
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import native_bridge as bridge
import tray


class ReconnectReadinessTests(unittest.TestCase):
    def service(self):
        service = bridge.Bridge.__new__(bridge.Bridge)
        service.lock = threading.RLock()
        service.root = Mock()
        service.busy = False
        service.closing = False
        service.pending_request = None
        service.next_reconnect = 100
        service.next_manual_reconnect = 0
        service.next_health_check = 0
        service.update_client_pid = None
        service.update_client_session = None
        service.last_activity = 0
        service.pool = Mock()
        service.state = dict(running=True, form='feiba', phase='error',
                             connection_state='disconnected', hotkey_registered=True)
        service.update = Mock(side_effect=service.state.update)
        service.ack = Mock()
        return service

    def test_registered_hotkey_while_connecting_is_not_reported_ready(self):
        state = self.service().state
        self.assertFalse(tray.pet_ready(state))
        self.assertEqual(tray.status_label(state | {'phase':'reconnecting'}), '正在连接')
        self.assertEqual(tray.status_label(state | {'phase':'idle','connection_state':'ready'}), '可以切换')

    def test_minimized_without_helper_waits_then_starts_when_restored(self):
        service = self.service()
        service.next_reconnect = 0
        windows = Mock()
        windows.main.return_value = SimpleNamespace(hwnd=11, pid=42)
        windows.user32.IsIconic.side_effect = [True, False]
        with patch.object(bridge.native, 'Windows', return_value=windows), \
             patch.object(bridge.background, 'owned_window', return_value=None), \
             patch.object(bridge.recovery_journal, 'JOURNAL') as journal, \
             patch.object(bridge.threading, 'Thread') as thread:
            journal.exists.return_value = False
            service.reconnect_if_needed()
            self.assertEqual(service.state['connection_state'], 'waiting_window')
            thread.assert_not_called()
            service.reconnect_if_needed()
            self.assertEqual(service.state['connection_state'], 'reconnecting')
            thread.return_value.start.assert_called_once()

    def test_press_during_backoff_queues_one_switch_and_wakes_reconnect(self):
        service = self.service()
        with patch.object(bridge.time, 'monotonic', return_value=1), \
             patch.object(bridge.native, 'preferences', return_value={'selected-avatar-id':'pet_original'}):
            service.request('toggle')
            request = service.pending_request
            service.request('toggle')
        self.assertIs(service.pending_request, request)
        self.assertTrue(service.state['pending_switch'])
        self.assertEqual(service.next_reconnect, 0)
        service.root.after.assert_called_once_with(0, service.reconnect_if_needed)

    def test_ready_releases_one_absolute_switch(self):
        service = self.service()
        service.pending_request = bridge.PendingSwitch('toggle', 'command', 'pet_original', 20)
        service.state.update(connection_state='ready', phase='idle')
        service.request = Mock()
        with patch.object(bridge.time, 'monotonic', return_value=5), \
             patch.object(bridge.native, 'preferences', return_value={'selected-avatar-id':'pet_original'}):
            service.dispatch_pending_switch()
            service.dispatch_pending_switch()
        service.request.assert_called_once_with('put-on', 'command')
        self.assertFalse(service.state['pending_switch'])

    def test_expiry_or_changed_selection_cancels_queued_switch(self):
        for now, selected_id in [(21,'pet_original'),(5,'pet_other')]:
            with self.subTest(now=now):
                service = self.service()
                service.state.update(connection_state='ready', phase='idle')
                service.pending_request = bridge.PendingSwitch('toggle', 'command', 'pet_original', 20)
                service.request = Mock()
                with patch.object(bridge.time, 'monotonic', return_value=now), \
                     patch.object(bridge.native, 'preferences', return_value={'selected-avatar-id':selected_id}):
                    service.dispatch_pending_switch()
                service.request.assert_not_called()
                self.assertIsNone(service.pending_request)
                self.assertEqual(service.ack.call_args.args[:2], ('command','cancelled'))

    def test_memory_saving_releases_only_when_idle_and_dormant_does_not_reconnect(self):
        service = self.service()
        service.state.update(connection_state='ready', phase='idle', memory_saving=True)
        service.next_reconnect = 0
        with patch.object(bridge.time, 'monotonic', return_value=301), \
             patch.object(bridge.recovery_journal, 'JOURNAL') as journal, \
             patch.object(bridge.threading, 'Thread') as thread, \
             patch.object(bridge.background, 'close') as close, \
             patch.object(bridge.background, 'owned_window', return_value=None), \
             patch.object(bridge.native, 'Windows') as windows:
            journal.exists.return_value = False
            service.busy = True
            self.assertFalse(service.maybe_release_idle_helper())
            service.busy = False
            self.assertTrue(service.maybe_release_idle_helper())
            thread.return_value.start.assert_called_once()
            service.release_idle_helper()
            close.assert_called_once()
            service.pool.close.assert_called_once()
            self.assertEqual(service.state['connection_state'], 'sleeping')
            service.reconnect_if_needed()
            windows.assert_not_called()

    def test_sleeping_mode_honors_one_wakeup_with_a_bounded_deadline(self):
        service = self.service()
        service.state.update(connection_state='sleeping', phase='sleeping', memory_saving=True)
        with patch.object(bridge.time, 'monotonic', return_value=1000), \
             patch.object(bridge.native, 'preferences', return_value={'selected-avatar-id':'pet_original'}):
            service.request('toggle')
        self.assertEqual(service.pending_request.deadline, 1025)
        service.root.after.assert_called_once_with(0, service.reconnect_if_needed)

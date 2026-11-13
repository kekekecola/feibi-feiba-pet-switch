import sys
from pathlib import Path
from unittest.mock import Mock
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import visibility_guard
import player
from unittest.mock import patch


class VisibilityGuardTests(unittest.TestCase):
    def test_first_frame_is_drawn_before_window_is_shown(self):
        obj=player.Player.__new__(player.Player)
        obj.closed=False;obj.preview=False;obj.phase='idle';obj.hold_timeout=None
        obj.state=player.FormState('feibi');obj.root=Mock();obj.renderer=Mock()
        obj.transition=[object(),object()]
        obj._refresh_size_once=Mock();obj._write_status=Mock();obj._advance_transition=Mock()
        events=Mock();obj._draw=events.draw;obj.renderer.show=events.show
        with patch.object(player,'native_pet_size',return_value=112),patch.object(player,'window_dpi',return_value=96):
            obj.request('play-take-off',parameters={})
        self.assertEqual([call[0] for call in events.mock_calls],['draw','show'])
        events.draw.assert_called_once_with(obj.transition[0])

    def test_bad_handoff_identity_never_shows_cg(self):
        obj=player.Player.__new__(player.Player)
        obj.closed=False;obj.preview=False;obj.phase='idle';obj.hold_timeout=None
        obj.state=player.FormState('feibi');obj.root=Mock();obj.renderer=Mock()
        obj.transition=[object(),object()];obj._draw=Mock();obj._ack=Mock()
        obj._refresh_size_once=Mock();obj._write_status=Mock()
        with patch.object(player,'native_pet_size',return_value=112),patch.object(player,'window_dpi',return_value=96), \
             patch.object(player.recovery_journal,'hide_for_cg',return_value=False):
            obj.request('play-take-off',parameters={'native_handoff':{'hwnd':123}})
        obj.renderer.show.assert_not_called()
        self.assertEqual(obj.phase,'idle')
        self.assertIsNone(obj.state.target)
    def test_does_not_repeatedly_hide_already_hidden_window(self):
        user=Mock();user.IsWindow.return_value=True;user.IsWindowVisible.return_value=False
        stop=Mock();stop.wait.side_effect=[False]*10+[True]
        withdraw=Mock();conflict=threading.Event()
        visibility_guard.run(user,123,stop,conflict,withdraw)
        user.ShowWindow.assert_not_called();withdraw.assert_not_called()

    def test_conflicting_client_aborts_instead_of_flashing_forever(self):
        user=Mock();user.IsWindow.return_value=True;user.IsWindowVisible.return_value=True
        stop=Mock();stop.wait.return_value=False
        withdraw=Mock();conflict=threading.Event()
        visibility_guard.run(user,123,stop,conflict,withdraw)
        self.assertTrue(conflict.is_set());self.assertEqual(user.ShowWindow.call_count,2)
        withdraw.assert_called_once()

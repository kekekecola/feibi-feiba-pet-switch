import sys
from pathlib import Path
from unittest.mock import Mock
import threading
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import visibility_guard


class VisibilityGuardTests(unittest.TestCase):
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

"""取数后只关闭本任务新建且再次核对过的直播间，不影响用户入口。"""
import unittest
from unittest.mock import Mock, patch

from glm_desktop import GLMDesktop
from core import RecapError


class RoomCleanupTests(unittest.TestCase):
    def driver(self):
        driver = object.__new__(GLMDesktop)
        driver._verified_room_id = 'room-A'
        driver.guard = Mock(return_value={'handle': 2})
        driver.key = Mock()
        driver.probe = Mock()
        driver.probe.windows.return_value = [{'handle': 1}]
        driver.mcp = Mock()
        binding = {'room_id': 'room-A', 'new_window_created': True,
                   'window': {'handle': 2, 'pid': 10, 'process_created': 20}}
        return driver, binding

    def test_success_closes_only_the_created_room(self):
        driver, binding = self.driver()
        with patch('glm_desktop.Desktop.capture', return_value={'metrics': {'累计观众': 9}}):
            result = driver.capture(binding)
        self.assertEqual(result['metrics']['累计观众'], 9)
        self.assertEqual(result['window_cleanup'], 'closed_created_room')
        driver.guard.assert_called_with(binding['window'])
        driver.key.assert_called_once_with({'handle': 2}, 'alt+f4')

    def test_existing_user_window_is_preserved(self):
        driver, binding = self.driver()
        binding['new_window_created'] = False
        with patch('glm_desktop.Desktop.capture', return_value={'metrics': {'累计观众': 9}}):
            driver.capture(binding)
        driver.key.assert_not_called()

    def test_failed_identity_does_not_close_any_window(self):
        driver, binding = self.driver()
        with patch('glm_desktop.Desktop.capture', side_effect=RecapError('room_changed')):
            with self.assertRaisesRegex(RecapError, 'room_changed'):
                driver.capture(binding)
        driver.key.assert_not_called()

    def test_takeover_during_cleanup_keeps_captured_values(self):
        driver, binding = self.driver()
        driver.guard.side_effect = RecapError('USER_CONTROL')
        with patch('glm_desktop.Desktop.capture', return_value={'metrics': {'累计观众': 9}}):
            result = driver.capture(binding)
        self.assertEqual(result['metrics']['累计观众'], 9)
        self.assertEqual(result['window_cleanup'], 'unconfirmed')
        driver.key.assert_not_called()

"""复用PR3的裁切恢复回归，按当前结构化选窗和身份约束验证；不连接桌面。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from glm_desktop import GLMDesktop
from core import RecapError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        identity = dict(handle=1, pid=2, process_created=3, window_class='Chrome_WidgetWin_1',
                        owner=0, root_owner=1, visible=True, enabled=True, minimized=False,
                        title='直播服务平台 - Edge')
        self.before = dict(identity, rect=[0,1000,1034,1079],
                           raw_rect=[0,1000,1034,1800], maximized=False)
        self.after = dict(identity, rect=[0,0,1920,1040],
                          raw_rect=[-8,-8,1928,1048], maximized=True)
        self.desktop = object.__new__(GLMDesktop)
        self.desktop.guard = Mock(side_effect=[self.before, self.after])
        self.desktop.mcp = Mock()
        self.desktop.key = Mock()
        self.desktop.probe = Mock()
        self.desktop.probe.windows.return_value = [self.before]
        self.desktop.probe.last_window_inventory = {'enum_ok': True,
            'process_lookup_failed_count': 0, 'rect_failed_count': 0,
            'class_lookup_failed_count': 0}

    def test_clipped_window_maximized_then_rechecked(self):
        self.assertEqual(self.desktop.recover_clipped_entry(self.before), self.after)
        self.desktop.key.assert_called_once_with(self.before, 'win+up')

    def test_normal_window_no_shortcut(self):
        self.before.update(raw_rect=[0,0,1200,800], rect=[0,0,1200,800])
        self.assertEqual(self.desktop.recover_clipped_entry(self.before), self.before)
        self.desktop.key.assert_not_called()

    def test_ambiguous_disabled_owned_or_changed_identity_stop(self):
        for condition in ('ambiguous', 'disabled', 'owned', 'changed', 'inventory'):
            with self.subTest(condition=condition):
                self.setUp()
                if condition == 'ambiguous':
                    self.desktop.probe.windows.return_value.append(dict(self.before, handle=4, root_owner=4))
                if condition == 'disabled': self.before['enabled'] = False
                if condition == 'owned': self.before['owner'] = 9
                if condition == 'changed':
                    self.desktop.probe.windows.return_value = [dict(self.before, process_created=4)]
                if condition == 'inventory': self.desktop.probe.last_window_inventory['enum_ok'] = False
                with self.assertRaises(RecapError): self.desktop.recover_clipped_entry(self.before)
                self.desktop.key.assert_not_called()

    def test_maximize_unconfirmed_stops(self):
        self.after['maximized'] = False
        with self.assertRaisesRegex(RecapError, 'window_maximize_unconfirmed'):
            self.desktop.recover_clipped_entry(self.before)

    def test_still_clipped_or_focus_lost_stops(self):
        self.after['rect'] = [0,1000,1034,1079]
        with self.assertRaisesRegex(RecapError, 'window_still_clipped'):
            self.desktop.recover_clipped_entry(self.before)
        self.setUp()
        self.desktop.guard.side_effect = [self.before, RecapError('foreground_changed')]
        with self.assertRaisesRegex(RecapError, 'foreground_changed'):
            self.desktop.recover_clipped_entry(self.before)

    def test_user_control_stops(self):
        self.desktop.guard.side_effect = RecapError('USER_CONTROL')
        with self.assertRaisesRegex(RecapError, 'USER_CONTROL'):
            self.desktop.recover_clipped_entry(self.before)
        self.desktop.key.assert_not_called()

    def test_missing_or_invalid_geometry_stops(self):
        for raw in (None, [0,0,0,0]):
            with self.subTest(raw=raw):
                self.setUp()
                self.before['raw_rect'] = raw
                with self.assertRaises(RecapError): self.desktop.recover_clipped_entry(self.before)
                self.desktop.key.assert_not_called()

    def test_open_entry_recovers_before_navigation(self):
        self.desktop.recover_clipped_entry = Mock(return_value=self.after)
        self.desktop.confirm_portal = Mock(return_value='confirmed')
        self.assertEqual(self.desktop.open_entry(self.before, False), 'confirmed')
        self.desktop.recover_clipped_entry.assert_called_once_with(self.before)
        self.desktop.confirm_portal.assert_called_once_with(self.after)


if __name__ == '__main__': unittest.main()

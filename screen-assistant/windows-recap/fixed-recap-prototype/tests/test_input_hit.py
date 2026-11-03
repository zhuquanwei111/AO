"""以实际鼠标命中的根窗口判断输入归属，不遍历无关的内部渲染层。"""
import unittest
from unittest.mock import Mock

from core import RecapError
from window_probe import Probe


class InputHitTests(unittest.TestCase):
    def probe(self):
        probe = object.__new__(Probe)
        target = {'handle': 1, 'rect': [0, 0, 800, 600]}
        probe.foreground = Mock(return_value=target)
        probe.u = Mock()
        probe.u.WindowFromPoint.return_value = 2
        probe.u.GetAncestor.return_value = 1
        probe.u.IsWindowVisible.return_value = True
        probe.u.IsWindowEnabled.return_value = True
        def classname(hwnd, buffer, size):
            buffer.value = 'Chrome_RenderWidgetHostHWND'
            return len(buffer.value)
        probe.u.GetClassNameW.side_effect = classname
        return probe, target

    def test_browser_hit_does_not_depend_on_client_area_lookup(self):
        probe, target = self.probe()
        probe.u.ScreenToClient.return_value = True
        probe.u.ChildWindowFromPointEx.return_value = 0
        probe.ensure_point(target, [40, 20])

    def test_browser_hit_is_not_rejected_for_a_different_disabled_render_layer(self):
        probe, target = self.probe()
        probe.u.ScreenToClient.return_value = True
        probe.u.ChildWindowFromPointEx.return_value = 3
        probe.u.IsWindowEnabled.side_effect = lambda hwnd: hwnd != 3
        probe.ensure_point(target, [40, 20])

    def test_other_window_or_missing_hit_is_rejected(self):
        for hit, root in ((2, 4), (0, 0)):
            probe, target = self.probe()
            probe.u.WindowFromPoint.return_value = hit
            probe.u.GetAncestor.return_value = root
            with self.assertRaisesRegex(RecapError, 'point_target_mismatch'):
                probe.ensure_point(target, [40, 20])

    def test_disabled_actual_hit_is_rejected(self):
        probe, target = self.probe()
        probe.u.IsWindowEnabled.return_value = False
        with self.assertRaisesRegex(RecapError, 'point_target_mismatch'):
            probe.ensure_point(target, [40, 20])

    def test_window_change_during_check_is_rejected(self):
        probe, target = self.probe()
        probe.u.ScreenToClient.return_value = True
        probe.u.ChildWindowFromPointEx.return_value = 2
        probe.foreground.side_effect = [target, {**target, 'rect': [1, 0, 801, 600]}]
        with self.assertRaisesRegex(RecapError, 'window_moved_before_action'):
            probe.ensure_point(target, [40, 20])

import unittest
import ctypes
from ctypes import wintypes as w
from unittest.mock import Mock
from core import RecapError, Token
from desktop import Desktop, View, select_edge_target
from window_probe import Probe


def candidate(handle, *, owner=0, enabled=True, cls='Chrome_WidgetWin_1'):
    return {'handle':handle,'pid':10,'process_created':100,'title':'new tab','rect':[0,0,800,600],
            'window_class':cls,'owner':owner,'root_owner':handle,'enabled':enabled,'visible':True,'minimized':False}


class WindowSafetyTests(unittest.TestCase):
    def test_unique_structural_target_with_unknown_background(self):
        target=candidate(1); unknown=candidate(2,owner=1,cls='other')
        self.assertEqual(select_edge_target([target,unknown]),target)
        with self.assertRaises(RecapError): select_edge_target([target,candidate(3)])
        with self.assertRaises(RecapError): select_edge_target([candidate(1,enabled=False)])

    def test_unique_portal_preferred_but_equal_matches_rejected(self):
        portal={**candidate(1),'title':'直播服务平台'}
        tab=candidate(2)
        self.assertEqual(select_edge_target([portal,tab]),portal)
        with self.assertRaises(RecapError):
            select_edge_target([portal,{**candidate(3),'title':'直播服务平台'}])

    def test_click_checks_hit_before_mcp(self):
        window=candidate(1); view=View([],b'',window,(800,600),'now',[])
        driver=object.__new__(Desktop); driver.guard=Mock(return_value=window)
        driver.probe=Mock(); driver.mcp=Mock()
        driver.probe.ensure_point.side_effect=RecapError('point_target_mismatch')
        with self.assertRaisesRegex(RecapError,'point_target_mismatch'):
            driver.click(view,Token('target',(10,10,30,30)))
        driver.mcp.call.assert_not_called()
        driver.probe.ensure_point.side_effect=None
        driver.click(view,Token('target',(10,10,30,30)))
        driver.mcp.call.assert_called_once()

    def test_moved_or_taken_over_window_never_receives_type(self):
        window=candidate(1); view=View([],b'',window,(800,600),'now',[])
        driver=object.__new__(Desktop); driver.probe=Mock(); driver.mcp=Mock()
        for guard in (Mock(return_value={**window,'rect':[10,0,810,600]}),
                      Mock(side_effect=RecapError('USER_CONTROL'))):
            driver.guard=guard
            with self.assertRaises(RecapError):
                driver.type_replacement(view,Token('target',(10,10,30,30)),'synthetic')
            driver.mcp.call.assert_not_called()

    def test_foreground_and_point_use_root_not_root_owner(self):
        probe=object.__new__(Probe); probe.u=Mock(); target=candidate(1)
        probe.get=Mock(return_value=target)
        probe.u.GetForegroundWindow.return_value=3
        probe.u.GetAncestor.side_effect=lambda hwnd,kind: {3:1,4:1,5:2}[hwnd] if kind==2 else 999
        probe.u.IsWindowEnabled.return_value=True
        probe.u.IsWindowVisible.return_value=True
        probe.u.ScreenToClient.return_value=True
        probe.u.ChildWindowFromPointEx.side_effect=[4,4]
        def classname(hwnd,buffer,size): buffer.value='Chrome_WidgetWin_1'; return len(buffer.value)
        probe.u.GetClassNameW.side_effect=classname
        probe.u.WindowFromPoint.return_value=4
        self.assertEqual(probe.foreground(target),target)
        probe.ensure_point(target,[10,10])
        probe.u.WindowFromPoint.return_value=5
        with self.assertRaisesRegex(RecapError,'point_target_mismatch'):
            probe.ensure_point(target,[10,10])
        probe.u.WindowFromPoint.return_value=4
        def static_class(hwnd,buffer,size): buffer.value='Static'; return len(buffer.value)
        probe.u.GetClassNameW.side_effect=static_class
        with self.assertRaisesRegex(RecapError,'point_target_mismatch'):
            probe.ensure_point(target,[10,10])
        probe.u.IsWindowEnabled.return_value=False
        with self.assertRaisesRegex(RecapError,'foreground_changed'):
            probe.foreground(target)

    def test_disabled_child_cannot_be_skipped_by_point_lookup(self):
        probe=object.__new__(Probe); probe.u=Mock(); target=candidate(1)
        probe.get=Mock(return_value=target)
        probe.u.GetForegroundWindow.return_value=1
        probe.u.GetAncestor.return_value=1
        probe.u.IsWindowVisible.return_value=True
        probe.u.IsWindowEnabled.side_effect=lambda hwnd: hwnd != 4
        probe.u.WindowFromPoint.return_value=1
        probe.u.ScreenToClient.return_value=True
        probe.u.ChildWindowFromPointEx.return_value=4
        def classname(hwnd,buffer,size): buffer.value='Chrome_WidgetWin_1'; return len(buffer.value)
        probe.u.GetClassNameW.side_effect=classname
        with self.assertRaisesRegex(RecapError,'point_target_mismatch'):
            probe.ensure_point(target,[10,10])

    def test_click_rechecks_occlusion_before_action(self):
        window=candidate(1); view=View([],b'',window,(800,600),'now',[])
        driver=object.__new__(Desktop); driver.guard=Mock(return_value=window)
        driver.probe=Mock(); driver.mcp=Mock()
        driver.probe.ensure_unobstructed.side_effect=[None,RecapError('window_occluded_or_unknown')]
        with self.assertRaisesRegex(RecapError,'window_occluded_or_unknown'):
            driver.click(view,Token('target',(10,10,30,30)))
        driver.mcp.call.assert_not_called()

    def test_unknown_edge_in_foreground_cannot_be_switched_away(self):
        probe=object.__new__(Probe); probe.u=Mock(); target=candidate(1)
        probe.get=Mock(return_value=target); probe.windows=Mock(return_value=[target,candidate(2)])
        probe.u.IsWindowEnabled.return_value=True; probe.u.GetForegroundWindow.return_value=3
        probe.u.GetAncestor.return_value=2
        with self.assertRaisesRegex(RecapError,'foreground_changed'):
            probe.ensure_switch_safe(target)

    def test_image_capture_stops_before_mcp_when_occluded(self):
        driver=object.__new__(Desktop); driver.guard=Mock(return_value=candidate(1))
        driver.probe=Mock(); driver.probe.contained.return_value=True
        driver.probe.ensure_unobstructed.side_effect=RecapError('window_occluded_or_unknown')
        driver.mcp=Mock()
        with self.assertRaisesRegex(RecapError,'window_occluded_or_unknown'):
            driver.observe(candidate(1),False)
        driver.mcp.call.assert_not_called()

    def test_above_window_overlap_rejected_but_background_not_touched(self):
        probe=object.__new__(Probe); probe.u=Mock()
        target=candidate(1); probe.foreground=Mock(return_value=target)
        probe.u.IsWindowVisible.return_value=True; probe.u.IsIconic.return_value=False
        probe.u.IsWindow.return_value=True
        def rect(hwnd,ptr):
            value=ctypes.cast(ptr,ctypes.POINTER(w.RECT)).contents
            value.left,value.top,value.right,value.bottom=(10,10,50,50)
            return True
        probe.u.GetWindowRect.side_effect=rect
        probe.u.GetWindow.side_effect=[2,0]
        with self.assertRaisesRegex(RecapError,'window_occluded_or_unknown'):
            probe.ensure_unobstructed(target)
        probe.u.GetWindow.side_effect=[0]
        probe.ensure_unobstructed(target)

    def test_z_order_cycle_or_destroyed_window_fails_closed(self):
        probe=object.__new__(Probe); probe.u=Mock(); target=candidate(1)
        probe.foreground=Mock(return_value=target)
        probe.u.GetWindow.return_value=1
        with self.assertRaisesRegex(RecapError,'window_occluded_or_unknown'):
            probe.ensure_unobstructed(target)
        probe.u.GetWindow.return_value=2; probe.u.IsWindow.return_value=False
        with self.assertRaisesRegex(RecapError,'window_occluded_or_unknown'):
            probe.ensure_unobstructed(target)

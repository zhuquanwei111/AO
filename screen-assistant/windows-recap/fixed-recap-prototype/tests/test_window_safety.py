import unittest
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes as w
from unittest.mock import Mock
from core import RecapError, Token
from desktop import Desktop, View, select_edge_target
from window_probe import Probe


def candidate(handle, *, owner=0, enabled=True, cls='Chrome_WidgetWin_1'):
    return {'handle':handle,'pid':10,'process_created':100,'title':'new tab','rect':[0,0,800,600],
            'window_class':cls,'owner':owner,'root_owner':handle,'enabled':enabled,'visible':True,'minimized':False}


class WindowSafetyTests(unittest.TestCase):
    def obstruction_probe(self):
        probe=object.__new__(Probe); probe.u=Mock()
        target=candidate(1); probe.foreground=Mock(return_value=target)
        probe.u.IsWindowVisible.return_value=True; probe.u.IsIconic.return_value=False
        probe.u.IsWindow.return_value=True; probe.u.GetWindow.side_effect=[2,0]
        def rect(hwnd,ptr):
            value=ctypes.cast(ptr,ctypes.POINTER(w.RECT)).contents
            value.left,value.top,value.right,value.bottom=(10,10,50,50)
            return True
        probe.u.GetWindowRect.side_effect=rect
        probe.occlusion_visibility=Mock(return_value={'category':'visible_or_unknown'})
        return probe,target

    def test_confirmed_hidden_window_does_not_block_capture(self):
        for category in ('cloaked','transparent_alpha_zero'):
            probe,target=self.obstruction_probe()
            probe.occlusion_visibility.return_value={'category':category}
            probe.ensure_unobstructed(target)

    def test_only_owned_official_overlay_is_exempt_from_rectangle_check(self):
        probe,target=self.obstruction_probe()
        probe.occlusion_visibility.return_value={'category':'own_mcp_overlay'}
        probe.ensure_unobstructed(target)
        probe.u.GetWindow.side_effect=[2,0]
        probe.occlusion_visibility.return_value={'category':'visible_or_unknown'}
        with self.assertRaisesRegex(RecapError,'window_occluded_or_unknown'):
            probe.ensure_unobstructed(target)

    def test_overlay_identity_needs_live_owned_process_and_exact_structure(self):
        probe=object.__new__(Probe); probe.u=Mock()
        probe.mcp=Mock(); probe.mcp.process.pid=100; probe.mcp.process.poll.return_value=None
        probe.process_parents=Mock(return_value={300:200,200:100})
        probe.process_created=Mock(side_effect=lambda pid:{100:10,200:20,300:30}.get(pid))
        def pid(hwnd,ptr): ctypes.cast(ptr,ctypes.POINTER(w.DWORD)).contents.value=300; return 1
        probe.u.GetWindowThreadProcessId.side_effect=pid
        probe.u.GetWindow.return_value=0; probe.u.GetAncestor.return_value=2
        cls='WindowsMCPControl_cursor_abc123'; style=0x080800a8
        self.assertTrue(probe.own_mcp_overlay(2,cls,style))
        for bad_cls,bad_style in (('WindowsMCPControl_unknown_abc123',style),
                                  (cls,style & ~0x20),('other',style)):
            self.assertFalse(probe.own_mcp_overlay(2,bad_cls,bad_style))
        probe.process_parents.return_value={300:999}
        self.assertFalse(probe.own_mcp_overlay(2,cls,style))
        probe.process_parents.return_value={300:200,200:100}
        probe.process_created.side_effect=lambda pid:{100:40,200:20,300:30}.get(pid)
        self.assertFalse(probe.own_mcp_overlay(2,cls,style))
        probe.mcp.process.poll.return_value=0
        self.assertFalse(probe.own_mcp_overlay(2,cls,style))

    def test_occlusion_diagnostic_has_stage_and_fixed_category_without_content(self):
        driver=object.__new__(Desktop); driver.mcp=Mock(); driver.probe=Mock()
        driver.probe.ensure_unobstructed.side_effect=RecapError('window_occluded_or_unknown')
        driver.probe.last_occlusion={'category':'visible_or_unknown','handle':2,'cloaked':0}
        with self.assertRaises(RecapError):
            driver.ensure_clear(candidate(1),'before_screenshot')
        driver.mcp.note.assert_called_once_with('window_guard_rejected',stage='before_screenshot',
            reason='window_occluded_or_unknown',target_handle=1,
            category='visible_or_unknown',handle=2,cloaked=0)

    def test_visibility_uses_dwm_and_only_confirmed_zero_global_alpha(self):
        probe=object.__new__(Probe); probe.u=Mock(); probe.d=Mock()
        probe.own_mcp_overlay=Mock(return_value=False)
        def cloak(hwnd,attribute,ptr,size):
            ctypes.cast(ptr,ctypes.POINTER(w.DWORD)).contents.value=2
            return 0
        probe.d.DwmGetWindowAttribute.side_effect=cloak
        self.assertEqual(probe.occlusion_visibility(2)['category'],'cloaked')
        probe.d.DwmGetWindowAttribute.side_effect=None
        probe.d.DwmGetWindowAttribute.return_value=0
        probe.u.GetWindowLongW.return_value=0x80020
        def layered(hwnd,color,alpha,flags):
            ctypes.cast(alpha,ctypes.POINTER(w.BYTE)).contents.value=0
            ctypes.cast(flags,ctypes.POINTER(w.DWORD)).contents.value=2
            return True
        probe.u.GetLayeredWindowAttributes.side_effect=layered
        self.assertEqual(probe.occlusion_visibility(2)['category'],'transparent_alpha_zero')
        for flags_value,alpha_value,success in ((1,0,True),(2,1,True),(2,0,False)):
            def uncertain(hwnd,color,alpha,flags):
                ctypes.cast(alpha,ctypes.POINTER(w.BYTE)).contents.value=alpha_value
                ctypes.cast(flags,ctypes.POINTER(w.DWORD)).contents.value=flags_value
                return success
            probe.u.GetLayeredWindowAttributes.side_effect=uncertain
            self.assertEqual(probe.occlusion_visibility(2)['category'],'visible_or_unknown')
        probe.d.DwmGetWindowAttribute.return_value=-1
        self.assertEqual(probe.occlusion_visibility(2)['category'],'visible_or_unknown')

    @unittest.skipUnless(os.name=='nt','需要Windows只读进程API')
    def test_real_process_snapshot_and_creation_times_for_owned_child(self):
        probe=Probe()
        env={key:value for key,value in os.environ.items() if key.upper() in ('SYSTEMROOT','WINDIR','PATH','TEMP','TMP')}
        child=subprocess.Popen([sys.executable,'-B','-c','import sys; sys.stdin.buffer.read()'],
            stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            self.assertEqual(probe.process_parents().get(child.pid),os.getpid())
            self.assertGreaterEqual(probe.process_created(child.pid),probe.process_created(os.getpid()))
            probe.u=Mock(); probe.mcp=Mock()
            probe.mcp.process.pid=os.getpid(); probe.mcp.process.poll.return_value=None
            probe.u.GetWindow.return_value=0; probe.u.GetAncestor.return_value=2
            def pid(hwnd,ptr): ctypes.cast(ptr,ctypes.POINTER(w.DWORD)).contents.value=child.pid; return 1
            probe.u.GetWindowThreadProcessId.side_effect=pid
            self.assertTrue(probe.own_mcp_overlay(2,'WindowsMCPControl_cursor_abc123',0x080800a8))
        finally:
            child.stdin.close()
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)

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

    def test_disabled_actual_hit_cannot_receive_input(self):
        probe=object.__new__(Probe); probe.u=Mock(); target=candidate(1)
        probe.get=Mock(return_value=target)
        probe.u.GetForegroundWindow.return_value=1
        probe.u.GetAncestor.return_value=1
        probe.u.IsWindowVisible.return_value=True
        probe.u.IsWindowEnabled.side_effect=lambda hwnd: hwnd != 4
        probe.u.WindowFromPoint.return_value=4
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
        probe.occlusion_visibility=Mock(return_value={'category':'visible_or_unknown'})
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

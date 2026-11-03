"""已有窗口选择的安全摘要与CLI早期失败贯通，不连接真实桌面。"""
from contextlib import redirect_stdout
import io
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'fixed-recap-prototype'))
from desktop import Desktop,edge_selection_summary,select_edge_target
from core import RecapError
from window_probe import Probe as Win32Probe


def window(**changes):
    return {'handle':1,'pid':2,'process_created':3,'title':'ordinary private page',
            'window_class':'Chrome_WidgetWin_1','owner':0,'root_owner':1,
            'visible':True,'enabled':True,'minimized':False,'rect':[0,0,800,600],**changes}


class ExistingSelectionTests(unittest.TestCase):
    def test_ordinary_browser_does_not_require_portal_or_newtab(self):
        row=window()
        self.assertEqual(select_edge_target([row]),row)
        summary=edge_selection_summary([row])
        self.assertEqual(summary['structural_count'],1)
        self.assertEqual(summary['usable_count'],1)
        self.assertEqual(summary['chosen_kind'],'ordinary')
        self.assertNotIn('private',json.dumps(summary))

    def test_reject_detail_distinguishes_known_unusable_and_unknown(self):
        for changes,code in (({'enabled':False},'main_disabled'),({'minimized':True},'main_minimized'),
                             ({'title':''},'main_untitled'),({'window_class':'unknown private class'},'no_structural_main'),
                             ({'process_created':None},'no_verified_identity')):
            with self.subTest(code=code):
                summary=edge_selection_summary([window(**changes)])
                self.assertEqual(summary['reject_code'],code)
                self.assertNotIn('private',json.dumps(summary))

    def test_equal_multiple_targets_reject_without_a_chosen_handle(self):
        summary=edge_selection_summary([window(),window(handle=4,root_owner=4)])
        self.assertEqual(summary['reject_code'],'multiple_main')
        self.assertIsNone(summary['chosen_handle'])

    def test_single_known_minimized_main_uses_official_restore_then_rechecks(self):
        row=window(minimized=True,rect=[0,0,-31000,-31000])
        restored=window()
        driver=object.__new__(Desktop); driver.guard=Mock(); driver.mcp=Mock(); driver.probe=Mock()
        driver.probe.get.return_value=row; driver.probe.windows.return_value=[row]
        driver.probe.foreground.side_effect=[RecapError('foreground_changed'),restored]
        target,restore=driver.select_existing_edge([row])
        self.assertTrue(restore)
        self.assertEqual(driver.focus(target,restore=True),restored)
        driver.probe.ensure_switch_safe.assert_called_once_with(row,allow_restore=True)
        driver.mcp.call.assert_called_once_with('App',mode='switch',name=row['title'])

    def test_restore_never_selects_disabled_unknown_or_multiple_main(self):
        cases=[[window(minimized=True,enabled=False)],
               [window(minimized=True,window_class='unknown')],
               [window(minimized=True),window(handle=4,root_owner=4,minimized=True)]]
        for rows in cases:
            driver=object.__new__(Desktop); driver.probe=Mock(); driver.mcp=Mock()
            with self.assertRaises(RecapError): driver.select_existing_edge(rows)
            driver.mcp.call.assert_not_called()

    def test_official_restore_does_not_bypass_foreground_failure_or_takeover(self):
        for takeover in (False,True):
            driver=object.__new__(Desktop); driver.probe=Mock(); driver.mcp=Mock()
            row=window(minimized=True)
            driver.guard=Mock(side_effect=RecapError('USER_CONTROL') if takeover else None)
            driver.probe.get.return_value=row
            driver.probe.windows.return_value=[row]
            driver.probe.foreground.side_effect=RecapError('foreground_changed')
            with self.assertRaises(RecapError): driver.focus(row,restore=True)
            self.assertEqual(driver.mcp.call.call_count,0 if takeover else 1)

    def test_restore_permission_still_rejects_unknown_foreground_modal_and_disabled(self):
        probe=object.__new__(Win32Probe); probe.u=Mock(); row=window(minimized=True)
        probe.get=Mock(return_value=row); probe.windows=Mock(return_value=[row,window(handle=4,root_owner=4,window_class='unknown')])
        probe.u.IsWindowEnabled.return_value=True; probe.u.GetForegroundWindow.return_value=4
        probe.u.GetAncestor.return_value=4
        with self.assertRaisesRegex(RecapError,'foreground_changed'):
            probe.ensure_switch_safe(row,allow_restore=True)
        probe.u.GetAncestor.return_value=1
        probe.u.IsWindowEnabled.return_value=False
        with self.assertRaisesRegex(RecapError,'foreground_changed'):
            probe.ensure_switch_safe(row,allow_restore=True)
        probe.u.IsWindowEnabled.return_value=True
        probe.get.return_value=window(minimized=True,owner=4,root_owner=4)
        with self.assertRaisesRegex(RecapError,'foreground_changed'):
            probe.ensure_switch_safe(row,allow_restore=True)

    def test_incomplete_inventory_never_launches_or_uses_partial_selection(self):
        from glm_desktop import GLMDesktop
        for rows in ([],[window()]):
            driver=object.__new__(GLMDesktop); driver.guard=Mock(); driver.mcp=Mock(); driver.probe=Mock()
            driver.probe.windows.return_value=rows
            driver.probe.last_window_inventory={'enum_ok':False,'raw_count':1}
            driver.wait_edge_started=Mock(); driver.focus=Mock()
            with self.assertRaisesRegex(RecapError,'edge_window_inventory_incomplete'): driver.bind('synthetic')
            driver.wait_edge_started.assert_not_called(); driver.focus.assert_not_called()
            self.assertEqual(driver.window_selection_diagnostics[0]['reject_code'],'inventory_incomplete')

    def test_malformed_identity_and_title_are_safe_diagnostic_categories(self):
        for changes in ({'pid':True},{'process_created':'private data'},{'title':None},{'window_class':None}):
            summary=edge_selection_summary([window(**changes)])
            self.assertIsNotNone(summary['reject_code'])
            self.assertNotIn('private data',json.dumps(summary))

    def test_probe_projects_actual_win32_types_and_inventory(self):
        probe=object.__new__(Win32Probe); probe.u=Mock(); probe.k=Mock()
        probe.callback=lambda callback:callback
        probe.u.IsWindowVisible.return_value=True
        probe.u.IsWindowEnabled.return_value=True; probe.u.IsIconic.return_value=False
        probe.u.GetWindow.return_value=0; probe.u.GetAncestor.return_value=1
        probe.u.GetWindowLongW.return_value=0
        def pid(hwnd,ptr): ctypes.cast(ptr,ctypes.POINTER(w.DWORD)).contents.value=2; return 1
        def path(handle,flags,buffer,size): buffer.value=r'C:\Program Files\Microsoft\Edge\Application\msedge.exe'; return True
        def times(handle,created,*values): ctypes.cast(created,ctypes.POINTER(w.FILETIME)).contents.dwLowDateTime=3; return True
        def title(hwnd,buffer,size): buffer.value='ordinary private page'; return len(buffer.value)
        def cls(hwnd,buffer,size): buffer.value='Chrome_WidgetWin_1'; return len(buffer.value)
        def rect(hwnd,ptr):
            row=ctypes.cast(ptr,ctypes.POINTER(w.RECT)).contents
            row.left,row.top,row.right,row.bottom=(0,0,800,600); return True
        probe.u.GetWindowThreadProcessId.side_effect=pid
        probe.k.QueryFullProcessImageNameW.side_effect=path; probe.k.GetProcessTimes.side_effect=times
        probe.u.GetWindowTextW.side_effect=title; probe.u.GetClassNameW.side_effect=cls
        probe.u.GetWindowRect.side_effect=rect
        probe.u.GetSystemMetrics.side_effect=lambda index:{76:0,77:0,78:1920,79:1080}[index]
        probe.u.EnumWindows.side_effect=lambda callback,unused:callback(1,0)
        rows=probe.windows()
        self.assertEqual(select_edge_target(rows)['handle'],1)
        self.assertEqual(probe.last_window_inventory['raw_count'],1)
        self.assertEqual(probe.last_window_inventory['verified_process_count'],1)
        self.assertTrue(probe.last_window_inventory['enum_ok'])
        self.assertEqual(edge_selection_summary(rows)['chosen_kind'],'ordinary')
        self.assertNotIn('private',json.dumps(edge_selection_summary(rows)))

    def test_ordinary_initial_bind_reaches_actual_window_vision(self):
        import test_memory_visual as fixtures
        from glm_desktop import GLMDesktop
        from glm_vision import GLMVision
        view=fixtures.MemoryVisualTests().view([])
        view.window['title']='ordinary private page'
        driver=object.__new__(GLMDesktop)
        driver.guard=Mock(side_effect=lambda window=None:window)
        driver.mcp=Mock(); driver.probe=Mock(); driver.probe.windows.return_value=[view.window]
        driver.focus=Mock(return_value=view.window); driver.key=Mock()
        driver.observe=Mock(return_value=view); driver.type_replacement=Mock()
        driver.portal_url='https://union.bytedance.com/open/portal/index'
        driver.wait_entry=Mock(side_effect=RecapError('fixture_after_navigation'))
        runtime=Mock(); runtime.run_crop.return_value={'status':'found','kind':'address_bar','box':[100,30,650,60]}
        driver.vision=GLMVision(runtime)
        with patch('glm_desktop.time.sleep'),self.assertRaisesRegex(RecapError,'fixture_after_navigation'):
            driver.bind('synthetic')
        runtime.run_crop.assert_called_once()
        driver.type_replacement.assert_called_once()
        self.assertEqual(driver.window_selection_diagnostics[0]['chosen_kind'],'ordinary')

    def test_cli_records_existing_selection_failure_before_vision(self):
        import recap_glm
        from glm_desktop import GLMDesktop
        row=window(enabled=False)
        wire=Mock(); wire.close.return_value=True
        wire.call.side_effect=lambda tool,**kwargs:{'content':[{'text':'{"state":"ready","generation":1,"wait_seconds":0}'}]}
        class Probe:
            def __init__(self,**kwargs): pass
            def windows(self): return [row]
        runtime=Mock(calls=0,http_requests=0,all_workers_finished=True,diagnostics=[])
        with tempfile.TemporaryDirectory() as folder:
            modules={'window_probe':types.SimpleNamespace(Probe=Probe),
                'ocr_local':types.SimpleNamespace(LocalOCR=Mock()),
                'transport':types.SimpleNamespace(MCP=Mock(return_value=wire))}
            args=['recap_glm.py','start','--task','synthetic','--account','synthetic',
                  '--session-id','synthetic','--source','android_app','--data',folder,
                  '--scratch',folder,'--desktop-authorized']
            with patch.object(sys,'argv',args),patch.dict(sys.modules,modules), \
                 patch.object(recap_glm,'GLMRuntime',return_value=runtime), \
                 patch.object(recap_glm,'require_gate_a'),patch.object(recap_glm,'require_sdk_runtime'), \
                 patch.dict('os.environ',{'TIANYI_API_KEY':'test-only'},clear=True), \
                 redirect_stdout(io.StringIO()) as output:
                self.assertEqual(recap_glm.main(),2)
            record=json.loads(output.getvalue())['record']
            self.assertEqual(record['window_selection_diagnostics'][0]['reject_code'],'main_disabled')
            self.assertEqual(record['window_selection_diagnostics'][0]['stage'],'existing')
            self.assertEqual(record['llm_runtime_invocations'],0)
            self.assertNotIn('private',output.getvalue())
            self.assertFalse(any(call.args[0] in ('App','Shortcut','Screenshot','Type','Click') for call in wire.call.call_args_list))

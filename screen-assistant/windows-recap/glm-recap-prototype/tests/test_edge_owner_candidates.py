"""基于已观察到的窗口归属关系的替身测试；不访问真实桌面。"""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'fixed-recap-prototype'))
from window_probe import Probe
sys.path.insert(0,str(ROOT/'llm-recap-prototype'))
from llm_desktop import LLMDesktop


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.root={'handle':101,'pid':5,'process_created':17,'title':'new tab','minimized':False}
        self.tool={'handle':102,'pid':5,'process_created':17,'title':'','minimized':False}
        self.windows=[self.tool,self.root]
        self.owners={101:0,102:101}
        self.styles={101:0,102:0x08000080}
        self.foreground=101
        self.enabled={101:True,102:True}
        self.probe=object.__new__(Probe)
        self.probe.u=SimpleNamespace(
            GetForegroundWindow=lambda:self.foreground,
            IsWindowEnabled=lambda h:self.enabled.get(h,False),
            GetWindow=lambda h,_:self.owners.get(h,0),
            GetWindowLongPtrW=lambda h,_:self.styles.get(h,0))

    def choose(self): return self.probe.initial_browser_candidates(self.windows)

    def test_owned_nonforeground_tool_with_foreground_enabled_owner_is_one_root(self):
        self.styles[102]=0x082000a0  # 本次只读元数据中观察到的扩展样式。
        self.assertEqual(self.choose(),[self.root])

    def test_independent_window_or_non_tool_popup_remains_ambiguous(self):
        for kind in ('independent','ordinary_popup'):
            with self.subTest(kind=kind):
                self.owners[102]=0 if kind=='independent' else 101
                self.styles[102]=0x08000080 if kind=='independent' else 0
                self.assertEqual(self.choose(),self.windows)

    def test_foreground_owner_disabled_or_foreground_unknown_remains_ambiguous(self):
        self.enabled[101]=False
        self.assertEqual(self.choose(),self.windows)
        self.enabled[101]=True
        for foreground in (0,102,999):
            self.foreground=foreground
            self.assertEqual(self.choose(),self.windows)

    def test_pid_creation_time_or_owner_mismatch_remains_ambiguous(self):
        for field,value in (('pid',6),('process_created',18)):
            original=self.tool[field]; self.tool[field]=value
            self.assertEqual(self.choose(),self.windows)
            self.tool[field]=original
        self.owners[102]=999
        self.assertEqual(self.choose(),self.windows)

    def test_multiple_independent_roots_remain_ambiguous(self):
        self.windows.append({'handle':103,'pid':5,'process_created':17,'minimized':False})
        self.assertEqual(self.choose(),self.windows)

    def test_minimized_owner_and_missing_tool_style_remain_ambiguous(self):
        self.root['minimized']=True
        self.assertEqual(self.choose(),self.windows)
        self.root['minimized']=False; self.styles[102]=0
        self.assertEqual(self.choose(),self.windows)
        self.styles[102]=0x80  # 可激活的工具窗口可能需要用户处理。
        self.assertEqual(self.choose(),self.windows)

    def test_single_candidate_preserves_existing_selection(self):
        self.assertEqual(self.probe.initial_browser_candidates([self.root]),[self.root])

    def test_launch_uses_resolved_root_but_logs_original_candidate_count(self):
        desktop=object.__new__(LLMDesktop)
        desktop.mcp=Mock(); desktop.guard=Mock(); desktop.probe=Mock()
        desktop.probe.windows.return_value=self.windows
        desktop.probe.initial_browser_candidates.return_value=[self.root]
        self.assertEqual(desktop.launch_edge(),[self.root])
        event=desktop.mcp.note.call_args.kwargs
        self.assertEqual(event['candidate_count'],2)
        self.assertEqual(event['selected_candidate_count'],1)

    def test_existing_edge_bind_selects_resolved_root_before_focus(self):
        desktop=object.__new__(LLMDesktop)
        desktop.mcp=Mock(); desktop.guard=Mock(); desktop.probe=Mock()
        desktop.probe.windows.return_value=self.windows
        desktop.probe.initial_browser_candidates.return_value=[self.root]
        class ReachedFocus(Exception): pass
        desktop.focus=Mock(side_effect=ReachedFocus())
        with self.assertRaises(ReachedFocus): desktop.bind('synthetic-account')
        desktop.focus.assert_called_once_with(self.root)


if __name__=='__main__': unittest.main()

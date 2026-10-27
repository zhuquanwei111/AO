import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from glm_desktop import GLMDesktop
from core import RecapError


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.before={'handle':1,'rect':[0,1000,1034,1079],
                     'raw_rect':[0,1000,1034,1800],'maximized':False}
        self.after={'handle':1,'rect':[0,0,1920,1040],
                    'raw_rect':[-8,-8,1928,1048],'maximized':True}
        self.desktop=object.__new__(GLMDesktop)
        self.desktop.guard=Mock(side_effect=[self.before,self.after])
        self.desktop.mcp=Mock()
        self.desktop.key=Mock()
        self.desktop.probe=Mock()
        self.desktop.probe.windows.return_value=[self.before]
        self.desktop.probe.u.IsWindowEnabled.return_value=True
        self.desktop.probe.u.GetWindow.return_value=0
        self.desktop.initial_browser_candidates=Mock(return_value=[self.before])

    def test_clipped_window_maximized_then_rechecked(self):
        self.assertEqual(self.desktop.recover_clipped_entry(self.before),self.after)
        self.desktop.key.assert_called_once_with(self.before,'win+up')
        self.assertEqual(self.desktop.guard.call_count,2)

    def test_normal_window_no_shortcut(self):
        self.before['raw_rect']=[0,0,1200,800]; self.before['rect']=[0,0,1200,800]
        self.assertEqual(self.desktop.recover_clipped_entry(self.before),self.before)
        self.desktop.key.assert_not_called()

    def test_ambiguous_disabled_or_owned_stop(self):
        for condition in ('ambiguous','disabled','owned'):
            with self.subTest(condition=condition):
                self.setUp()
                if condition=='ambiguous': self.desktop.initial_browser_candidates.return_value=[self.before,{'handle':2}]
                if condition=='disabled': self.desktop.probe.u.IsWindowEnabled.return_value=False
                if condition=='owned': self.desktop.probe.u.GetWindow.return_value=9
                with self.assertRaisesRegex(RecapError,'window_recovery_ambiguous'):
                    self.desktop.recover_clipped_entry(self.before)
                self.desktop.key.assert_not_called()

    def test_maximize_unconfirmed_stops(self):
        self.after['maximized']=False
        with self.assertRaisesRegex(RecapError,'window_maximize_unconfirmed'):
            self.desktop.recover_clipped_entry(self.before)

    def test_still_clipped_or_focus_lost_stops(self):
        self.after['rect']=[0,1000,1034,1079]
        with self.assertRaisesRegex(RecapError,'window_still_clipped'):
            self.desktop.recover_clipped_entry(self.before)
        self.setUp(); self.desktop.guard.side_effect=[self.before,RecapError('foreground_changed')]
        with self.assertRaisesRegex(RecapError,'foreground_changed'):
            self.desktop.recover_clipped_entry(self.before)

    def test_user_control_stops(self):
        self.desktop.guard.side_effect=RecapError('USER_CONTROL')
        with self.assertRaisesRegex(RecapError,'USER_CONTROL'):
            self.desktop.recover_clipped_entry(self.before)
        self.desktop.key.assert_not_called()

    def test_missing_or_invalid_geometry_stops(self):
        for raw in (None,[0,0,0,0]):
            with self.subTest(raw=raw):
                self.setUp(); self.before['raw_rect']=raw
                with self.assertRaises(RecapError): self.desktop.recover_clipped_entry(self.before)
                self.desktop.key.assert_not_called()

if __name__=='__main__': unittest.main()

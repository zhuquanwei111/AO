import unittest
from unittest.mock import Mock, patch

from core import RecapError
from desktop import Desktop, safe_window_metadata


def main_window():
    return {'handle':1,'pid':2,'process_created':3,'title':'Edge','window_class':'Chrome_WidgetWin_1',
            'owner':0,'root_owner':1,'visible':True,'enabled':True,'minimized':False}


class EdgeLaunchTests(unittest.TestCase):
    def test_window_diagnostics_do_not_include_title_or_unrecognized_class(self):
        row={'handle':1,'pid':2,'title':'private-title-example','window_class':'private-class',
             'owner':0,'root_owner':1,'style':0,'exstyle':0,'enabled':True,
             'minimized':False,'rect':[0,0,800,600]}
        value=str(safe_window_metadata([row]))
        self.assertNotIn('private-title-example',value)
        self.assertNotIn('private-class',value)
        self.assertIn('other_or_unknown',value)
    def driver(self, samples):
        driver = object.__new__(Desktop)
        driver.probe = Mock()
        driver.probe.windows.side_effect = samples
        driver.guard = Mock()
        driver.mcp = Mock()
        return driver

    def test_transient_multiple_candidates_wait_for_unique_window(self):
        main = main_window()
        helper = {'handle': 2, 'title': ''}
        driver = self.driver([[main, helper], [main]])
        with patch('desktop.time.monotonic', side_effect=[0, 1, 2]), patch('desktop.time.sleep'):
            self.assertEqual(driver.wait_edge_launch(), main)
        self.assertEqual(driver.guard.call_count, 1)

    def test_empty_then_multiple_then_unique_within_one_deadline(self):
        main = main_window()
        helper = {'handle': 2, 'title': ''}
        driver = self.driver([[], [main, helper], [main]])
        with patch('desktop.time.monotonic', side_effect=[0, 1, 2, 3]), patch('desktop.time.sleep'):
            self.assertEqual(driver.wait_edge_launch(), main)
        self.assertEqual(driver.probe.windows.call_count, 2)

    def test_single_candidate_returns_without_wait(self):
        main = main_window()
        driver = self.driver([[main]])
        with patch('desktop.time.monotonic', side_effect=[0, 1]), patch('desktop.time.sleep') as sleep:
            self.assertEqual(driver.wait_edge_launch(), main)
        sleep.assert_not_called()

    def test_deadline_is_not_reset_by_changing_candidates(self):
        main = main_window()
        helper = {**main_window(),'handle':2,'pid':3,'root_owner':2}
        driver = self.driver([[], [main, helper], [main]])
        with patch('desktop.time.monotonic', side_effect=[0, 1, 14, 16]), patch('desktop.time.sleep'):
            with self.assertRaisesRegex(RecapError, 'edge_launch_unconfirmed'):
                driver.wait_edge_launch()

    def test_no_window_or_multiple_main_windows_fails_closed(self):
        for candidates in ([], [{'handle': 1, 'title': 'A'}, {'handle': 2, 'title': 'B'}],
                           [{'handle': 3, 'title': ''}]):
            driver = self.driver([candidates])
            with patch('desktop.time.monotonic', side_effect=[0, 16]):
                with self.assertRaisesRegex(RecapError, 'edge_launch_unconfirmed'):
                    driver.wait_edge_launch()

    def test_unchanged_candidates_emit_one_bounded_safe_snapshot(self):
        candidates=[{'handle':1,'pid':5,'title':'private-title-A'},
                    {'handle':2,'pid':5,'title':''}]
        driver=self.driver([candidates,candidates,candidates])
        with patch('desktop.time.monotonic',side_effect=[0,1,2,16]),patch('desktop.time.sleep'):
            with self.assertRaisesRegex(RecapError,'edge_launch_unconfirmed'):
                driver.wait_edge_launch()
        changes=[item for item in driver.mcp.note.call_args_list if item.args[0]=='edge_launch_candidates']
        self.assertEqual(len(changes),1)
        self.assertNotIn('private-title-A',str(driver.mcp.note.call_args_list))

    def test_changes_are_bounded_and_final_event_marks_truncation(self):
        samples=[[{'handle':i,'pid':5,'title':''}] for i in range(1,11)]
        driver=self.driver(samples)
        with patch('desktop.time.monotonic',side_effect=[0,*range(1,10),16]),patch('desktop.time.sleep'):
            with self.assertRaisesRegex(RecapError,'edge_launch_unconfirmed'):
                driver.wait_edge_launch()
        changes=[item for item in driver.mcp.note.call_args_list if item.args[0]=='edge_launch_candidates']
        self.assertEqual(len(changes),8)
        final=driver.mcp.note.call_args_list[-1]
        self.assertTrue(final.kwargs['changes_truncated'])

    def test_user_control_stops_wait_without_following_window_read(self):
        driver = self.driver([])
        driver.guard.side_effect = RecapError('USER_CONTROL')
        with patch('desktop.time.monotonic', return_value=0):
            with self.assertRaisesRegex(RecapError, 'USER_CONTROL'):
                driver.wait_edge_launch()
        driver.probe.windows.assert_not_called()

    def test_bind_launches_once_then_waits_before_navigation(self):
        main = main_window()
        helper = {'handle': 2, 'title': ''}
        driver = self.driver([[], [main, helper], [main]])
        driver.focus = Mock(return_value=main)
        driver.key = Mock()
        driver.navigate_entry = Mock(side_effect=RecapError('fixture_stop'))
        with patch('desktop.time.monotonic', side_effect=[0, 1, 2]), patch('desktop.time.sleep'):
            with self.assertRaisesRegex(RecapError, 'fixture_stop'):
                driver.bind('fixture-account')
        launches = [call for call in driver.mcp.call.call_args_list if call.args[0] == 'App']
        self.assertEqual(len(launches), 1)
        driver.focus.assert_called_once_with(main)
        driver.navigate_entry.assert_called_once_with(main)

    def test_bind_propagates_launch_tool_failure_without_wait(self):
        driver = self.driver([[]])
        driver.mcp.call.side_effect = [None, RecapError('mcp_tool_error')]
        with self.assertRaisesRegex(RecapError, 'mcp_tool_error'):
            driver.bind('fixture-account')
        self.assertEqual(driver.probe.windows.call_count, 1)


if __name__ == '__main__': unittest.main()

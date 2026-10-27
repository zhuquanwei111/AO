from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'fixed-recap-prototype'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import RecapError
from flow import Store
from glm_desktop import GLMDesktop


class ClosedTaskHandoffTests(unittest.TestCase):
    def previous(self, folder, *, stopped, reason='edge_launch_unconfirmed', bound=False):
        store = Store(folder)
        store.create('first', 'demo_account', 'first-session', 'android_app')
        store.update('first', status='needs_input', reason=reason,
                     binding_evidence={'room_id': 'kept'} if bound else None)
        if stopped:
            store.stop_signal('first', 'android_app')
        return store

    def test_needs_input_is_not_a_terminal_status_even_with_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.previous(folder, stopped=True)
            old_bytes = store.path('first').read_bytes()
            with self.assertRaisesRegex(RecapError, 'busy'):
                store.create('second', 'demo_account', 'second-session', 'android_app')
            self.assertEqual(store.path('first').read_bytes(), old_bytes)

    def test_unclosed_needs_input_still_blocks_next_task(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.previous(folder, stopped=False)
            with self.assertRaisesRegex(RecapError, 'busy'):
                store.create('second', 'demo_account', 'second-session', 'android_app')

    def test_bound_or_unknown_outcome_still_blocks_even_after_stop(self):
        for options in ({'bound': True}, {'reason': 'mcp_timeout_outcome_unknown'}, {'reason': ''}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                store = self.previous(folder, stopped=True, **options)
                with self.assertRaisesRegex(RecapError, 'busy'):
                    store.create('second', 'demo_account', 'second-session', 'android_app')

    def test_stop_source_conflict_does_not_release_previous_task(self):
        with tempfile.TemporaryDirectory() as folder:
            store = self.previous(folder, stopped=False)
            store.stop_signal('first', 'ios_app')
            with self.assertRaisesRegex(RecapError, 'busy'):
                store.create('second', 'demo_account', 'second-session', 'android_app')


class EdgeLaunchConfirmationTests(unittest.TestCase):
    def driver(self, windows):
        desktop = object.__new__(GLMDesktop)
        desktop.mcp = Mock()
        desktop.probe = Mock()
        desktop.probe.windows.side_effect = windows
        desktop.guard = Mock()
        return desktop

    def test_delayed_window_is_waited_for_without_launching_twice(self):
        window = {'handle': 10}
        desktop = self.driver([[], [], [window]])
        with patch('llm_desktop.time.monotonic', return_value=0), patch('llm_desktop.time.sleep'):
            self.assertEqual(desktop.launch_edge(), [window])
        desktop.mcp.call.assert_called_once_with('App', mode='launch', name='Microsoft Edge')
        self.assertEqual(desktop.mcp.note.call_args.kwargs['candidate_counts'], [0, 0, 1])

    def test_ambiguous_window_counts_are_recorded_and_not_clicked(self):
        desktop = self.driver([[{'handle': 10}, {'handle': 20}]])
        with self.assertRaisesRegex(RecapError, 'edge_launch_unconfirmed'):
            desktop.launch_edge()
        self.assertEqual(desktop.mcp.note.call_args.kwargs['candidate_count'], 2)
        self.assertEqual([row['handle'] for row in desktop.mcp.note.call_args.kwargs['candidates']], [10, 20])
        self.assertEqual(desktop.mcp.call.call_count, 1)

    def test_no_window_stops_at_existing_fifteen_second_bound(self):
        desktop = self.driver([[]])
        with patch('llm_desktop.time.monotonic', side_effect=[0, 0, 16, 16]), patch('llm_desktop.time.sleep'):
            with self.assertRaisesRegex(RecapError, 'edge_launch_unconfirmed'):
                desktop.launch_edge()
        self.assertEqual(desktop.mcp.note.call_args.kwargs['candidate_count'], 0)

    def test_takeover_while_waiting_still_stops(self):
        desktop = self.driver([[]])
        desktop.guard.side_effect = RecapError('USER_CONTROL')
        with patch('llm_desktop.time.monotonic', return_value=0), patch('llm_desktop.time.sleep'):
            with self.assertRaisesRegex(RecapError, 'USER_CONTROL'):
                desktop.launch_edge()
        self.assertEqual(desktop.probe.windows.call_count, 1)


if __name__ == '__main__':
    unittest.main()

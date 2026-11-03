"""验证锁随本次执行退出释放，保留未确认执行及身份冲突的阻挡。"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

from core import RecapError
from flow import Store, Runner, lock


class TaskReleaseTests(unittest.TestCase):
    def test_terminated_lock_owner_does_not_leave_a_permanent_placeholder(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'desktop.lock'
            code = "from pathlib import Path; import sys; from flow import lock\nwith lock(Path(sys.argv[1])):\n print('ready', flush=True)\n sys.stdin.read()\n"
            process = subprocess.Popen([sys.executable, '-c', code, str(target)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                self.assertEqual(process.stdout.readline().strip(), b'ready')
                with self.assertRaisesRegex(RecapError, 'busy'):
                    with lock(target):
                        pass
                process.terminate()
                process.wait(timeout=5)
                with lock(target):
                    pass
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=5)

    def test_old_file_without_an_owner_is_not_a_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'state.lock'
            target.write_text('1234', encoding='ascii')
            with lock(target):
                pass

    def test_bound_failure_releases_only_after_stop_and_confirmed_execution_exit(self):
        for confirmed, source in ((False, 'android_app'), (True, 'ios_app'), (True, 'android_app')):
            with self.subTest(confirmed=confirmed, source=source), tempfile.TemporaryDirectory() as folder:
                store = Store(folder)
                store.create('A', 'account-A', 'session-A', 'android_app')
                store.update('A', phase='capturing', status='needs_input', reason='room_changed',
                             binding_evidence={'room_id': 'room-A'})
                store.stop_signal('A', source)
                Runner(store, None).finish_execution('A', cleanup_confirmed=confirmed)
                if confirmed and source == 'android_app':
                    self.assertTrue(store.create('B', 'account-B', 'session-B', 'android_app')[1])
                    self.assertEqual(store.get('A')['douyin_account'], 'account-A')
                else:
                    with self.assertRaisesRegex(RecapError, 'busy'):
                        store.create('B', 'account-B', 'session-B', 'android_app')

    def test_unstarted_dependency_failure_can_release_after_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(folder)
            store.create('A', 'account-A', 'session-A', 'android_app')
            store.update('A', status='needs_input', reason='visual_runtime_unavailable')
            Runner(store, None).finish_execution('A', cleanup_confirmed=True)
            with self.assertRaisesRegex(RecapError, 'busy'):
                store.create('B', 'account-B', 'session-B', 'android_app')
            store.stop_signal('A', 'android_app')
            self.assertTrue(store.create('B', 'account-B', 'session-B', 'android_app')[1])

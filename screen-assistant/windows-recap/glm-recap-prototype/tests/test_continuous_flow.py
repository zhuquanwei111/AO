"""合成页面验证连续三场的真实Store、绑定和指标解析；不替代实播验收。"""
from pathlib import Path
import sys
import tempfile
import unittest

from glm_desktop import GLMDesktop
from core import Token, RecapError
from desktop import View
from flow import Store, Runner

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'fixed-recap-prototype/tests'))
from test_fixed_path import Scenario


class ContinuousFlowTests(unittest.TestCase):
    def test_A_B_A_keeps_scene_results_and_releases_created_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            class Pages(Scenario):
                account = 'unset'
                scene = 0
                def note(self, *args, **kwargs):
                    pass
                def call(self, tool, **args):
                    if tool == 'ControlStatus':
                        return {'content': [{'text': '{"state":"ai","generation":7}'}]}
                    if tool == 'Type':
                        self.account = args['text']
                    if tool == 'Click' and self.stage == 'detail':
                        self.scene += 1
                        self.room.update(handle=self.scene + 1, root_owner=self.scene + 1)
                    if tool == 'Shortcut' and args['shortcut'] == 'alt+f4':
                        self.stage = 'home'
                        return {'content': []}
                    return super().call(tool, **args)
                def tokens(self):
                    return [Token(q.text.replace('demo', self.account).replace('1234567890123456789', str(1234567890123456789 + self.scene)),
                                  q.box, q.score) for q in super().tokens()]
                def get(self, signature):
                    matches = [row for row in self.windows() if row['handle'] == signature['handle']]
                    if len(matches) != 1:
                        raise RecapError('window_lost')
                    return matches[0]
            pages = Pages(Path(folder))
            class Vision:
                def next_search_action(self,image,guard):
                    found=self.locate(image,'主播列表下一步')
                    return Token('search_field' if pages.stage=='list' else 'navigation_entry',found.box,found.score,found.source)
                def locate(self, image, goal, toolbar=False, **kwargs):
                    name = {'home': '主播管理', 'management': '主播列表',
                            'list': '请输入主播昵称或抖音号', 'candidate': '抖音号:' + pages.account,
                            'filtered': '主播详情', 'detail': '进入直播间'}[pages.stage]
                    return next(q for q in image.tokens if q.text == name)
                def verify_identity(self, image, account, guard, live=False):
                    if not any(q.text == '抖音号:' + account for q in image.tokens):
                        raise RecapError('wrong_identity')
            class Driver(GLMDesktop):
                def observe(self, window, use_uia=False, *, ocr_optional=False):
                    return View(pages.tokens(), pages.image.read_bytes(), pages.get(window),
                                (600, 400), 'fixture', [])
            store = Store(Path(folder) / 'state')
            driver = Driver(pages, pages, None, Path(folder) / 'evidence',
                            'https://union.bytedance.com/open/portal/', vision=Vision())
            runner = Runner(store, driver)
            snapshots = {}
            rooms = set()
            for number, account in enumerate(('account-A', 'account-B', 'account-A')):
                task, session = 'task-' + str(number), 'session-' + str(number)
                started = runner.start(task, account, session, 'android_app')
                self.assertEqual(started['status'], 'waiting_for_stop', started)
                result = runner.stop(task, 'android_app')
                self.assertEqual(result['status'], 'captured_locally', result)
                self.assertEqual(result['douyin_account'], account)
                self.assertEqual(result['session_id'], session)
                self.assertEqual(len(result['metrics']), 5)
                self.assertEqual(result['window_cleanup'], 'closed_created_room')
                rooms.add(result['room_id'])
                snapshots[task] = store.path(task).read_bytes()
                self.assertEqual(pages.windows(), [pages.window])
                for previous, content in snapshots.items():
                    self.assertEqual(store.path(previous).read_bytes(), content)
            self.assertEqual(len(rooms), 3)

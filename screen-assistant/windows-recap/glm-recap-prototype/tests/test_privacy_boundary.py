"""截图仅在内存中处理，未确认安全裁区时不得送视觉服务。"""
import base64
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from glm_desktop import GLMDesktop
from glm_vision import GLMVision
from core import RecapError, Token
from desktop import View


class PrivacyBoundaryTests(unittest.TestCase):
    def test_observation_uses_memory_without_screenshot_file(self):
        buf = io.BytesIO()
        Image.new('RGB', (100, 100), 'white').save(buf, format='PNG')
        payload = base64.b64encode(buf.getvalue()).decode('ascii')
        window = {'handle': 1, 'pid': 2, 'title': 'test', 'rect': [0, 0, 100, 100]}
        wire = Mock()
        wire.call.side_effect = lambda tool, **kw: (
            {'content': [{'text': '{"state":"ai","generation":1,"wait_seconds":0}'}]}
            if tool == 'ControlStatus' else {'content': [{'image_data': payload}]})
        probe = Mock()
        probe.foreground.return_value = window
        probe.contained.return_value = True
        ocr = Mock()
        ocr.read.return_value = []
        with tempfile.TemporaryDirectory() as folder:
            desktop = GLMDesktop(wire, probe, ocr, folder,
                                 'https://union.bytedance.com/open/portal/index', vision=Mock())
            view = desktop.observe(window)
            self.assertIsInstance(view.image, bytes)
            self.assertEqual(ocr.read.call_args.args[0], view.image)
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_full_window_never_sent_to_visual_runtime(self):
        runtime = Mock()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'synthetic.png'
            Image.new('RGB', (100, 100), 'white').save(path)
            view = Mock()
            view.image = path
            with self.assertRaises(RecapError):
                GLMVision(runtime).locate(view, '任意导航')
        runtime.run.assert_not_called()

    def test_evidence_contains_only_independent_target_regions_and_rejects_login(self):
        image = Image.new('RGB', (200, 120), 'red')
        for box in [(5, 5, 80, 15), (5, 25, 80, 35), (90, 25, 130, 35)]:
            for y in range(box[1], box[3]):
                for x in range(box[0], box[2]):
                    image.putpixel((x, y), (0, 0, 0))
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        tokens = [Token('抖音号:demo', (5, 5, 80, 15)),
                  Token('已开播00:01', (5, 25, 80, 35)),
                  Token('累计观众:12', (90, 25, 130, 35))]
        with tempfile.TemporaryDirectory() as folder:
            desktop = object.__new__(GLMDesktop)
            desktop.evidence = Path(folder)
            desktop.expected_account = 'demo'
            desktop._verified_room_id = '123456789012345'
            window = {'rect': [0, 0, 200, 120]}
            desktop.guard = Mock(return_value=window)
            view = View(tokens, buf.getvalue(), window, (200, 120), 'time', [])
            result = desktop.save_minimal(view, 'metrics.png')
            with Image.open(result) as evidence:
                self.assertNotIn((255, 0, 0), set(evidence.convert('RGB').get_flattened_data()))
            runtime = Mock()
            runtime.run.return_value = ({'account':'demo','累计观众':'12'}, {})
            result_fields = GLMVision(runtime).read(result, ('account','累计观众'), '读取身份与指标')
            self.assertEqual(result_fields['累计观众'], '12')
            runtime.run.assert_called_once()
            result.path.write_bytes(b'tampered')
            with self.assertRaises(RecapError):
                GLMVision(runtime).read(result, ('account','累计观众'), '读取身份与指标')
            self.assertEqual(runtime.run.call_count, 1)
            from safe_evidence import read_verified_image
            with self.assertRaises(ValueError):
                read_verified_image(result.path, result.sha256)
            result.path.unlink()
            view.tokens.append(Token('请登录', (140, 5, 190, 15)))
            with self.assertRaises(RecapError):
                desktop.save_minimal(view, 'metrics.png')
            self.assertFalse(result.path.exists())

    def test_overlapping_other_text_never_persists_evidence(self):
        buf = io.BytesIO()
        Image.new('RGB', (100, 100), 'white').save(buf, format='PNG')
        tokens = [Token('抖音号:demo', (5, 5, 80, 15)),
                  Token('其他主播', (40, 5, 90, 15))]
        with tempfile.TemporaryDirectory() as folder:
            desktop = object.__new__(GLMDesktop)
            desktop.evidence = Path(folder)
            desktop.expected_account = 'demo'
            desktop._verified_room_id = '123456789012345'
            window = {'rect': [0, 0, 100, 100]}
            desktop.guard = Mock(return_value=window)
            view = View(tokens, buf.getvalue(), window, (100, 100), 'time', [])
            with self.assertRaises(RecapError):
                desktop.save_minimal(view, 'metrics.png')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_oversized_target_box_never_persists_full_window(self):
        buf = io.BytesIO()
        Image.new('RGB', (100, 100), 'white').save(buf, format='PNG')
        window = {'rect': [0, 0, 100, 100]}
        with tempfile.TemporaryDirectory() as folder:
            desktop = object.__new__(GLMDesktop)
            desktop.evidence = Path(folder)
            desktop.expected_account = 'demo'
            desktop._verified_room_id = '123456789012345'
            desktop.guard = Mock(return_value=window)
            view = View([Token('抖音号:demo', (0, 0, 100, 100))],
                        buf.getvalue(), window, (100, 100), 'time', [])
            with self.assertRaises(RecapError):
                desktop.save_minimal(view, 'metrics.png')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_five_metrics_are_independent_regions(self):
        names = ['累计观众', '新增粉丝', '评论人数', '送礼人数', '获得音浪']
        tokens = [Token('抖音号:demo', (5, 5, 80, 15))]
        for index, name in enumerate(names):
            x = index * 95 + 5
            tokens.extend([Token(str(index + 1), (x + 15, 65, x + 25, 75)),
                           Token(name, (x, 85, x + 60, 95))])
        image = Image.new('RGB', (500, 160), 'red')
        from PIL import ImageDraw
        draw = ImageDraw.Draw(image)
        for token in tokens:
            x,y,r,b = token.box
            draw.rectangle((x,y,r-1,b-1), fill='black')
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        window = {'rect': [0, 0, 500, 160]}
        with tempfile.TemporaryDirectory() as folder:
            desktop = object.__new__(GLMDesktop)
            desktop.evidence = Path(folder)
            desktop.expected_account = 'demo'
            desktop._verified_room_id = '123456789012345'
            desktop.guard = Mock(return_value=window)
            evidence = desktop.save_minimal(View(tokens, buf.getvalue(), window, (500,160), 'time', []), 'metrics.png')
            with Image.open(evidence) as result:
                self.assertEqual(result.height, 110)
                self.assertNotIn((255,0,0), set(result.convert('RGB').get_flattened_data()))

    def test_unverified_file_and_navigation_target_never_reach_visual_runtime(self):
        from llm_desktop import LLMDesktop
        runtime = Mock()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'unverified.png'
            Image.new('RGB', (20, 20), 'white').save(path)
            with self.assertRaises(RecapError):
                GLMVision(runtime).read(path, ('account',), '读取身份')
            driver = object.__new__(LLMDesktop)
            driver.vision = GLMVision(runtime)
            driver.observe = Mock()
            with self.assertRaises(RecapError):
                driver.target({}, '任意导航')
            driver.observe.assert_not_called()
        runtime.run.assert_not_called()

    def test_login_observation_never_saves_or_calls_visual(self):
        buf = io.BytesIO()
        Image.new('RGB', (100, 100), 'white').save(buf, format='PNG')
        payload = base64.b64encode(buf.getvalue()).decode('ascii')
        window = {'handle': 1, 'pid': 2, 'title': 'test', 'rect': [0, 0, 100, 100]}
        wire = Mock()
        wire.call.side_effect = lambda tool, **kw: (
            {'content': [{'text': '{"state":"ai","generation":1,"wait_seconds":0}'}]}
            if tool == 'ControlStatus' else {'content': [{'image_data': payload}]})
        probe = Mock()
        probe.foreground.return_value = window
        probe.contained.return_value = True
        ocr = Mock()
        ocr.read.return_value = [Token('请输入验证码', (0, 0, 50, 10))]
        vision = Mock()
        with tempfile.TemporaryDirectory() as folder:
            desktop = GLMDesktop(wire, probe, ocr, folder,
                                 'https://union.bytedance.com/open/portal/index', vision=vision)
            with self.assertRaises(RecapError):
                desktop.observe(window)
            self.assertEqual(list(Path(folder).iterdir()), [])
            vision.read.assert_not_called()
            vision.locate.assert_not_called()

    def test_mcp_image_wire_returns_memory_data_without_files(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'windows-mcp'))
        from image_wire import serialize_image
        with tempfile.TemporaryDirectory() as folder:
            block = Mock(data='YWJj', mimeType='image/png')
            result = serialize_image(block)
            self.assertEqual(result, {'image_data': 'YWJj', 'mime_type': 'image/png'})
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == '__main__':
    unittest.main()

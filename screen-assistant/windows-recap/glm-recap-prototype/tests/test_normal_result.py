"""身份裁图允许视觉复核，单项补读失败不能丢弃其余已确认读数。"""
import io
import unittest
from unittest.mock import Mock

from PIL import Image
from glm_desktop import GLMDesktop
from glm_vision import GLMVision
from core import RecapError, Token, METRICS
from desktop import View


def view(tokens):
    image = io.BytesIO()
    Image.new('RGB', (800, 600), 'white').save(image, 'PNG')
    return View(tokens, image.getvalue(), {'handle': 1, 'pid': 2, 'process_created': 3,
                'rect': [0, 0, 800, 600]}, (800, 600), 'fixture', [])


class NormalResultTests(unittest.TestCase):
    def test_split_account_label_is_verified_from_one_local_identity_crop(self):
        runtime = Mock()
        runtime.run_crop.return_value = {'account': 'demo', 'live_status': None}
        image = view([Token('抖音号:', (100, 80, 160, 100)),
                      Token('dem0', (170, 80, 230, 100), .5)])
        GLMVision(runtime).verify_identity(image, 'demo', lambda window: window)
        crop = runtime.run_crop.call_args.args[0]
        self.assertEqual(crop.purpose, 'identity')
        self.assertLess(crop.box[0], 100)
        self.assertGreaterEqual(crop.box[2], 230)
        self.assertLess(crop.box[2], 400)

    def test_model_must_confirm_exact_account_even_when_ocr_is_uncertain(self):
        runtime = Mock()
        runtime.run_crop.return_value = {'account': 'someone_else', 'live_status': None}
        image = view([Token('抖音号:dem0', (100, 80, 230, 100))])
        with self.assertRaisesRegex(RecapError, 'wrong_identity'):
            GLMVision(runtime).verify_identity(image, 'demo', lambda window: window)
        runtime.run_crop.assert_called_once()

    def test_multiple_unidentified_rows_are_not_sent_for_identity_verification(self):
        runtime = Mock()
        image = view([Token('抖音号:one', (100, 80, 230, 100)),
                      Token('抖音号:two', (100, 180, 230, 200))])
        with self.assertRaises(RecapError):
            GLMVision(runtime).verify_identity(image, 'demo', lambda window: window)
        runtime.run_crop.assert_not_called()

    def test_failed_optional_visual_read_preserves_four_confirmed_metrics(self):
        driver = object.__new__(GLMDesktop)
        driver.expected_account = 'demo'
        driver.guard = Mock()
        driver.vision = Mock()
        driver.vision.read.side_effect = RecapError('visual_worker_connection_error')
        missing = METRICS[-1]
        result = {'metrics': {name: (None if name == missing else 7) for name in METRICS},
                  'raw_displays': {}, 'approximate_metrics': [],
                  'missing_fields': [missing], 'missing_reasons': {missing: 'unreadable_value'}}
        actual = driver.refine_metrics(view([Token(missing, (100, 80, 160, 100))]), result)
        self.assertEqual(actual['metrics'], result['metrics'])
        self.assertEqual(actual['missing_fields'], [missing])
        self.assertEqual(actual['missing_reasons'][missing], 'visual_worker_connection_error')

    def test_identity_failure_is_never_treated_as_optional_metric_failure(self):
        driver = object.__new__(GLMDesktop)
        driver.expected_account = 'demo'
        driver.guard = Mock()
        driver.vision = Mock()
        driver.vision.read.side_effect = RecapError('wrong_identity')
        with self.assertRaisesRegex(RecapError, 'wrong_identity'):
            driver.refine_metrics(view([Token(METRICS[0], (100, 80, 160, 100))]),
                                  {'missing_fields': [METRICS[0]]})

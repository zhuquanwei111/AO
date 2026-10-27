from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import Mock
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from glm_desktop import GLMDesktop,TrackedMCP
from core import RecapError


class Wire:
    def __init__(self): self.status={'state':'ai','generation':4,'wait_seconds':0}
    def call(self,tool,**kwargs):
        import json
        return {'content':[{'text':json.dumps(self.status)}]} if tool=='ControlStatus' else {'content':[]}
    def note(self,*args,**kwargs): pass


class IdleTests(unittest.TestCase):
    def driver(self):
        d=object.__new__(GLMDesktop); wire=Wire(); d.mcp=TrackedMCP(wire)
        d.expected_generation=4; d._expected_state='ai'; d._checked_serial=0
        return d,wire

    def test_normal_idle_and_own_next_tool_reacquire(self):
        d,wire=self.driver()
        wire.status.update(state='ready',generation=5)
        d.guard()
        self.assertEqual(d.expected_generation,5)
        d.mcp.call('Click',loc=[1,1])
        wire.status.update(state='ai',generation=6)
        d.guard()
        self.assertEqual(d.expected_generation,6)

    def test_user_pending_unknown_or_skipped_epoch_still_stops(self):
        for state,generation in [('user',5),('takeover_pending',5),('unavailable',5),('ready',6),('ai',6)]:
            d,wire=self.driver(); wire.status.update(state=state,generation=generation)
            with self.assertRaises(RecapError): d.guard()

    def test_ai_reacquire_without_our_tool_is_rejected(self):
        d,wire=self.driver(); d.expected_generation=5; d._expected_state='ready'
        wire.status.update(state='ai',generation=6)
        with self.assertRaises(RecapError): d.guard()

    def test_screenshot_reacquire_observed_before_ocr_then_idle(self):
        with tempfile.TemporaryDirectory() as folder:
            image=Path(folder)/'screen.png'; Image.new('RGB',(100,100)).save(image)
            wire=Wire(); wire.status.update(state='ready',generation=7)
            old_call=wire.call
            def call(tool,**kwargs):
                if tool=='Screenshot':
                    wire.status.update(state='ai',generation=8)
                    return {'content':[{'image_path':str(image)}]}
                return old_call(tool,**kwargs)
            wire.call=call
            ocr=Mock()
            def read(path):
                wire.status.update(state='ready',generation=9)
                return []
            ocr.read.side_effect=read
            window={'handle':1,'pid':2,'title':'test','rect':[0,0,100,100]}
            probe=Mock(); probe.foreground.return_value=window; probe.contained.return_value=True
            d=GLMDesktop(wire,probe,ocr,folder,'https://union.bytedance.com/open/portal/index',vision=Mock())
            d.expected_generation=7; d._expected_state='ready'
            self.assertEqual(d.observe(window).window,window)
            self.assertEqual(d.expected_generation,9)

if __name__=='__main__': unittest.main()

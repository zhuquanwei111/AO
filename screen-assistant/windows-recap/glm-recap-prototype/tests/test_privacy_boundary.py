"""全流程拒绝图片持久化和图片模型发送；仅用合成数据。"""
import base64
import io
import os
import subprocess
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
    def image(self):
        buf=io.BytesIO()
        Image.new('RGB',(100,100),'white').save(buf,format='PNG')
        return buf.getvalue()

    def driver(self,folder,tokens):
        window={'handle':1,'pid':2,'title':'test','rect':[0,0,100,100]}
        wire=Mock()
        wire.call.side_effect=lambda tool,**kw: ({'content':[{'text':'{\"state\":\"ai\",\"generation\":1,\"wait_seconds\":0}'}]}
            if tool=='ControlStatus' else {'content':[{'image_data':base64.b64encode(self.image()).decode('ascii')}]})
        probe=Mock(); probe.foreground.return_value=window; probe.contained.return_value=True
        ocr=Mock(); ocr.read.return_value=tokens
        vision=Mock()
        driver=GLMDesktop(wire,probe,ocr,folder,'https://union.bytedance.com/open/portal/index',vision=vision)
        return driver,window,ocr,vision

    def test_current_observation_is_memory_only(self):
        with tempfile.TemporaryDirectory() as folder:
            driver,window,ocr,vision=self.driver(folder,[])
            view=driver.observe(window)
            self.assertIsInstance(view.image,bytes)
            self.assertEqual(ocr.read.call_args.args[0],view.image)
            self.assertEqual(list(Path(folder).iterdir()),[])
            vision.read.assert_not_called(); vision.locate.assert_not_called()

    def test_login_and_verification_never_save_or_send(self):
        for label in ('请登录','请输入验证码','扫码登录'):
            with tempfile.TemporaryDirectory() as folder:
                driver,window,ocr,vision=self.driver(folder,[Token(label,(0,0,50,10))])
                with self.assertRaises(RecapError): driver.observe(window)
                self.assertEqual(list(Path(folder).iterdir()),[])
                vision.read.assert_not_called(); vision.locate.assert_not_called()

    def test_all_image_visual_and_evidence_exits_reject(self):
        from llm_desktop import LLMDesktop
        from glm_runtime import GLMRuntime
        with tempfile.TemporaryDirectory() as folder:
            driver=object.__new__(GLMDesktop); driver.evidence=Path(folder)
            view=View([Token('抖音号:demo',(5,5,50,15))],self.image(),{},(100,100),'time',[])
            with self.assertRaises(RecapError): driver.save_minimal(view,'metrics.png')
            runtime=Mock(); visual=GLMVision(runtime)
            with self.assertRaises(RecapError): visual.read(view.image,('account',),'读取')
            with self.assertRaises(RecapError): visual.locate(view,'导航')
            runtime.run.assert_not_called()
            old=object.__new__(LLMDesktop); old.observe=Mock(); old.vision=visual
            with self.assertRaises(RecapError): old.target({},'导航')
            old.observe.assert_not_called()
            with self.assertRaises(RuntimeError): GLMRuntime(folder).run(view.image,'读取')
            self.assertEqual(list(Path(folder).glob('*.png')),[])

    def test_mcp_image_wire_never_creates_files(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'windows-mcp'))
        from image_wire import serialize_image
        with tempfile.TemporaryDirectory() as folder:
            block=Mock(data='YWJj',mimeType='image/png')
            self.assertEqual(serialize_image(block),{'image_data':'YWJj','mime_type':'image/png'})
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_legacy_image_worker_does_not_read_or_send_job(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'not-a-job.json'
            path.write_text('sensitive placeholder',encoding='utf-8')
            worker=Path(__file__).resolve().parents[1]/'agent_worker.py'
            allowed={'SYSTEMROOT','WINDIR','PATH','PATHEXT','TEMP','TMP','HOME','LANG','LC_ALL'}
            env={key:value for key,value in os.environ.items() if key.upper() in allowed}
            result=subprocess.run([sys.executable,str(worker),str(path)],capture_output=True,timeout=10,env=env)
            self.assertEqual(result.returncode,2)
            self.assertNotIn(b'sensitive placeholder',result.stdout+result.stderr)
            self.assertEqual([item.name for item in Path(folder).iterdir()],['not-a-job.json'])

if __name__=='__main__': unittest.main()

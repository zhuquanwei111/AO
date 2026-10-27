"""已知步骤的离线替身场景；不导入Win32 Probe、不启动MCP或浏览器。"""
from pathlib import Path
import tempfile
import unittest
from PIL import Image
from core import Token, RecapError
from desktop import Desktop, View
from flow import Store, Runner


class Scenario:
    def __init__(self,folder):
        self.folder=Path(folder); self.stage='home'; self.inputs=[]
        self.window={'handle':1,'pid':20,'process_created':100,'title':'直播服务平台',
                     'rect':[250,150,1450,950],'minimized':False}
        self.room={**self.window,'handle':2,'rect':[470,210,1670,1010]}
        self.image=self.folder/'fake.png'; Image.new('RGB',(600,400),'white').save(self.image)

    def windows(self): return [self.window,self.room] if self.stage=='room' else [self.window]
    def get(self,signature): return self.room if signature['handle']==2 else self.window
    def foreground(self,signature): return self.get(signature)

    def call(self,tool,**args):
        if tool=='ControlStatus': return {'content':[{'text':'{"state":"ai"}'}]}
        if tool=='Click':
            self.inputs.append(tool)
            self.stage={'home':'management','management':'list','candidate':'filtered',
                        'filtered':'detail','detail':'room'}.get(self.stage,self.stage)
        if tool=='Type':
            if 'loc' not in args: raise RecapError('invalid_type_signature')
            self.inputs.append(tool); self.stage='candidate'
        return {'content':[]}

    def tokens(self):
        def q(text,x,y,w=70): return Token(text,(x,y,x+w,y+12),.99)
        if self.stage=='home': return [q('主播管理',10,60),
            q('union.bytedance.com/open/portal/',100,10,300),q('直播服务平台·机构版',100,35,180)]
        if self.stage=='management': return [q('主播管理',10,60),q('主播列表',10,85)]
        if self.stage=='list': return [q('请输入主播昵称或抖音号',350,30,160)]
        if self.stage=='candidate': return [q('抖音号:demo',350,65,110)]
        if self.stage=='filtered': return [q('主播详情',450,120)]
        if self.stage=='detail': return [q('抖音号:demo',100,60,100),q('进入直播间',450,100)]
        return [q('抖音号:demo',100,60,100),q('跟播列表',10,35),q('已开播00:40:16',230,60,100),
                q('https://union.bytedance.com/open/portal/live_flow/followRoomDetail?roomID=1234567890123456789',0,8,595),
                q('获得音浪',0,130),q('300',15,110,25),q('送礼人数',90,130),q('2',115,110,10),
                q('评论人数',180,130),q('9',205,110,10),q('累计观众',270,130),q('100',290,110,25),
                q('新增粉丝',360,130),q('1',385,110,10)]


class FixedPathTests(unittest.TestCase):
    def test_start_wait_and_stop_full_fixed_steps_with_fake_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            scenario=Scenario(folder)
            class Driver(Desktop):
                def observe(self,window,use_uia=False):
                    return View(scenario.tokens(),scenario.image,scenario.get(window),(600,400),'fake-time',[])
            driver=Driver(scenario,scenario,None,Path(folder)/'state/evidence','https://union.bytedance.com/open/portal/')
            runner=Runner(Store(Path(folder)/'state'),driver)
            result=runner.start('test-one','demo',None,'test_simulated')
            self.assertEqual(result['status'],'waiting_for_stop')
            self.assertIsNone(result['metrics'])
            inputs=list(scenario.inputs)
            # 等待期间没有后台采样或后续动作，不启动任何常驻循环。
            self.assertEqual(runner.store.get('test-one')['status'],'waiting_for_stop')
            self.assertEqual(scenario.inputs,inputs)
            result=runner.stop('test-one','test_simulated')
            self.assertEqual(result['status'],'captured_locally')
            self.assertEqual(result['metrics'],{'累计观众':100,'新增粉丝':1,'评论人数':9,'送礼人数':2,'获得音浪':300})
            self.assertTrue((runner.store.root/result['capture_evidence']['metrics_screenshot']).is_file())
            self.assertEqual(runner.stop('test-one','test_simulated'),result)
            self.assertEqual(scenario.inputs,inputs)
            self.assertEqual(result['normal_path_llm_calls'],0)


if __name__=='__main__': unittest.main()

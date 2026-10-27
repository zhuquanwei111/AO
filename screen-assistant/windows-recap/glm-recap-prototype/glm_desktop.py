"""复用已实现固定页面步骤、范围／前台／接管保护和OCR，仅替换视觉运行器。"""
from pathlib import Path
import sys
import time
import json

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'llm-recap-prototype'))
from llm_desktop import LLMDesktop
from glm_runtime import MODEL
from core import RecapError
from transport import check_reply

ENTRY_URL='https://union.bytedance.com/open/portal/index'


class TrackedMCP:
    """只计数本程序成功调用的现成工具，不改变工具或输入保护。"""
    def __init__(self,wire): self.wire=wire; self.serial=0
    def call(self,tool,**arguments):
        result=self.wire.call(tool,**arguments)
        if tool!='ControlStatus': self.serial+=1
        return result
    def note(self,*args,**kwargs): return self.wire.note(*args,**kwargs)


class GLMDesktop(LLMDesktop):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.mcp=TrackedMCP(self.mcp)
        self._checked_serial=0; self._expected_state=None

    def guard(self,window=None):
        status=json.loads(check_reply(self.mcp.call('ControlStatus')))
        self.mcp.note('control_state',state=status.get('state'),generation=status.get('generation'),
                      wait_seconds=status.get('wait_seconds'))
        if status.get('controller')=='user' or status.get('state') in ('user','takeover_pending'):
            raise RecapError('USER_CONTROL')
        if status.get('controller',status.get('state')) not in ('ready','ai'):
            raise RecapError('unknown_control_status')
        generation=status.get('generation')
        if not isinstance(generation,int): raise RecapError('control_generation_missing')
        serial=self.mcp.serial
        if self.expected_generation is not None and generation!=self.expected_generation:
            normal_idle=(self._expected_state=='ai' and status.get('state')=='ready'
                and generation==self.expected_generation+1 and status.get('wait_seconds')==0)
            our_reacquire=(self._expected_state=='ready' and status.get('state')=='ai'
                and generation==self.expected_generation+1 and serial>self._checked_serial)
            if not (normal_idle or our_reacquire): raise RecapError('control_generation_changed')
            self.mcp.note('control_lease_transition',kind='normal_idle' if normal_idle else 'own_tool_reacquire',
                          previous_generation=self.expected_generation,generation=generation)
            self.expected_generation=generation
        self._checked_serial=serial; self._expected_state=status.get('state')
        self.last_generation=generation
        if window:
            try: return self.probe.foreground(window)
            except RecapError as error:
                if error.reason=='foreground_changed' and hasattr(self.probe,'foreground_info'):
                    self.mcp.note('foreground_mismatch',expected=window,actual=self.probe.foreground_info())
                raise
        return None

    def open_entry(self,window,need_open):
        # App仍是本机现有Edge，保留正常用户配置；没有AO浏览器／新浏览器配置。
        if not need_open:
            self.key(window,'esc')
            return self.confirm_portal(window)
        # 已在空白标签就复用；其他页面仅新开普通标签保护原内容，仍在同一配置。
        if not any(word in window.get('title','').casefold() for word in ('新建标签页','new tab')):
            self.key(window,'ctrl+t')
        self.key(window,'esc')
        try:
            view,bookmark=self.target(window,
                'Edge顶部收藏栏中的“直播服务平台·机构版”书签，可能显示为“直播服务平台·机构...”或“直播后台平台”；不要选网页快捷方式',True)
        except RecapError as error:
            if error.reason!='visual_missing': raise
            # 只有书签确实未找到才走用户明确给定网址；歧义／接管绝不切备用路径。
            view,address=self.target(window,'浏览器顶部网址地址输入框内部，不能选网页搜索框',True)
            self.key(window,'ctrl+l')
            self.type_replacement(view,address,ENTRY_URL)
            self.mcp.note('entry_route',route='explicit_index_url')
        else:
            self.click(view,bookmark)
            self.mcp.note('entry_route',route='existing_edge_favorite')
        time.sleep(1)  # 一次正常页面加载等待，不采样直播指标。
        return self.confirm_portal(window)

    def observe(self,window,use_uia=False):
        for attempt in range(5):
            try: return super().observe(window,use_uia)
            except RecapError as error:
                if error.reason!='foreground_changed' or attempt==4: raise
                window=self._recover_focus(window)
        raise RecapError('foreground_changed')

    def _recover_focus(self,window):
        recoveries=getattr(self,'_focus_recoveries',0)
        if recoveries>=4: raise RecapError('foreground_changed')
        self.guard()
        self._focus_recoveries=recoveries+1
        self.mcp.note('focus_recovery',reason='foreground_changed',attempt=self._focus_recoveries,
                      discarded_observation=True)
        return self.focus(window)

    def target(self,window,goal,toolbar=False):
        for attempt in range(5):
            view,token=super().target(window,goal,toolbar)
            try:
                self.guard(view.window)
                return view,token
            except RecapError as error:
                if error.reason!='foreground_changed' or attempt==4: raise
                window=self._recover_focus(window)
        raise RecapError('foreground_changed')

    def bind(self,account):
        result=super().bind(account)
        result['locator']=MODEL+' visual decisions + guarded Windows-MCP fixed steps'
        return result

    def capture(self,binding):
        result=super().capture(binding)
        if '; missing-only visual review' in result['capture_method']:
            result['capture_method']=result['capture_method'].split(';',1)[0]+'; missing-only visual review '+MODEL
        return result

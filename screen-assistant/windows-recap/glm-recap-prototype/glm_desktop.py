"""复用已实现固定页面步骤、范围／前台／接管保护和OCR，仅替换视觉运行器。"""
from pathlib import Path
import sys
import time
import json

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'llm-recap-prototype'))
from llm_desktop import LLMDesktop
from glm_runtime import MODEL
from core import RecapError, compact, MIN_SCORE
from transport import check_reply

ENTRY_URL='https://union.bytedance.com/open/portal/index'


def visible_bookmark(view):
    """只选择当前截图中与收藏栏锚点同排的唯一机构收藏。"""
    width,height=view.size
    valid=[q for q in view.tokens if q.score>=MIN_SCORE and
           0<=q.box[0]<q.box[2]<=width and 0<=q.box[1]<q.box[3]<=height]
    anchors=[q for q in valid if compact(q.text)=='导入收藏夹' and q.y<height*.18]
    candidates=[q for q in valid if compact(q.text).startswith('直播服务平台·机构') and q.y<height*.18]
    if not anchors: raise RecapError('bookmark_bar_unconfirmed')
    if len(anchors)!=1: raise RecapError('bookmark_ambiguous')
    anchor=anchors[0]
    matches=[q for q in candidates if q.x>anchor.x and abs(q.y-anchor.y)<=max(q.h,anchor.h)]
    if len(matches)!=1:
        raise RecapError('bookmark_missing' if not matches else 'bookmark_ambiguous')
    return matches[0]


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
        window=self.recover_clipped_entry(window)
        if not need_open:
            self.key(window,'esc')
            return self.confirm_portal(window)
        # 已在空白标签就复用；其他页面仅新开普通标签保护原内容，仍在同一配置。
        if not any(word in window.get('title','').casefold() for word in ('新建标签页','new tab')):
            self.key(window,'ctrl+t')
        self.key(window,'esc')
        view=self.observe(window,False)
        try:
            bookmark=visible_bookmark(view)
        except RecapError as error:
            if error.reason not in ('bookmark_missing','bookmark_bar_unconfirmed'): raise
            # 收藏栏不可用时沿既有用户指定网址与地址框核对路径。
            view,address=self.target(window,'浏览器顶部网址地址输入框内部，不能选网页搜索框',True)
            self.key(window,'ctrl+l')
            self.type_replacement(view,address,ENTRY_URL)
            self.mcp.note('entry_route',route='explicit_index_url')
        else:
            self.click(view,bookmark)
            self.mcp.note('entry_route',route='current_ocr_verified_favorite')
        time.sleep(1)  # 一次正常页面加载等待，不采样直播指标。
        return self.confirm_portal(window)

    def recover_clipped_entry(self,window):
        live=self.guard(window)
        raw=live.get('raw_rect'); rect=live.get('rect')
        if not (isinstance(raw,list) and isinstance(rect,list) and len(raw)==len(rect)==4):
            raise RecapError('window_geometry_missing')
        rw,rh=raw[2]-raw[0],raw[3]-raw[1]
        vw,vh=rect[2]-rect[0],rect[3]-rect[1]
        if min(rw,rh,vw,vh)<=0: raise RecapError('window_geometry_invalid')
        clipped=(vw*2<rw or vh*2<rh)
        self.mcp.note('entry_window_geometry',window_handle=live['handle'],raw_rect=raw,
                      visible_rect=rect,maximized=live.get('maximized'),severely_clipped=clipped)
        if not clipped: return live
        selected=self.initial_browser_candidates(self.probe.windows())
        u=self.probe.u
        if (len(selected)!=1 or selected[0]['handle']!=live['handle']
                or not u.IsWindowEnabled(live['handle']) or u.GetWindow(live['handle'],4)):
            raise RecapError('window_recovery_ambiguous')
        self.key(live,'win+up')
        current=self.guard(live)
        if not current.get('maximized'): raise RecapError('window_maximize_unconfirmed')
        new_raw=current.get('raw_rect'); new_rect=current.get('rect')
        if not (isinstance(new_raw,list) and isinstance(new_rect,list) and len(new_raw)==len(new_rect)==4):
            raise RecapError('window_geometry_missing')
        nrw,nrh=new_raw[2]-new_raw[0],new_raw[3]-new_raw[1]
        nvw,nvh=new_rect[2]-new_rect[0],new_rect[3]-new_rect[1]
        if min(nrw,nrh,nvw,nvh)<=0 or nvw*2<nrw or nvh*2<nrh:
            raise RecapError('window_still_clipped')
        if not u.IsWindowEnabled(current['handle']): raise RecapError('window_recovery_ambiguous')
        self.mcp.note('entry_window_recovered',window_handle=current['handle'],
                      raw_rect=current.get('raw_rect'),visible_rect=current['rect'])
        return current

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

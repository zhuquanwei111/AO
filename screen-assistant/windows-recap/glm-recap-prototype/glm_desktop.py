"""复用已实现固定页面步骤、范围／前台／接管保护和OCR，仅替换视觉运行器。"""
from pathlib import Path
import sys
import time
import json

sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'llm-recap-prototype'))
from llm_desktop import LLMDesktop
from desktop import Desktop, portal_page_evidence, select_edge_target
from core import RecapError, unique_label, compact
from vision import fill_missing
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
                    self.mcp.note('foreground_mismatch')
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

    def observe(self,window,use_uia=False,*,ocr_optional=False):
        for attempt in range(5):
            try: return super().observe(window,use_uia,ocr_optional=ocr_optional)
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
        missing_retries=0
        for attempt in range(5):
            view=self.observe(window,False,ocr_optional=True)
            try:
                token=self.vision.locate(view,goal,toolbar,guard=self.guard)
                self.guard(view.window)
                return view,token
            except RecapError as error:
                if error.reason=='visual_missing' and missing_retries<1:
                    missing_retries+=1
                    time.sleep(1)
                    continue
                if error.reason!='foreground_changed' or attempt==4: raise
                window=self._recover_focus(window)
        raise RecapError('foreground_changed')

    def bind(self,account):
        self.expected_account=account
        self._verified_room_id=None
        self.guard()
        self.mcp.call('Snapshot',use_vision=False,use_dom=False,use_ui_tree=False,use_annotation=False)
        windows=self.probe.windows()
        if windows:
            target,restore=self.select_existing_edge(windows)
        else:
            selection=self.record_edge_selection(windows,'existing')
            if selection['reject_code']=='inventory_incomplete': raise RecapError('edge_window_inventory_incomplete')
            target=self.wait_edge_started(); restore=False
        portal='直播服务平台' in target['title']
        window=self.focus(target,restore=True) if restore else self.focus(target)
        if not portal:
            self.key(window,'ctrl+t')
            view=self.navigate_entry(window)
        else:
            view=self.wait_entry(window)
        if not any('请输入主播' in compact(q.text) for q in view.tokens):
            if not any(compact(q.text)=='主播列表' for q in view.tokens):
                view,token=self.target(window,'侧边导航主播管理的展开箭头'); self.click(view,token)
            view,token=self.target(window,'侧边导航主播列表'); self.click(view,token)
        view,search=self.target(window,'主播列表页面搜索主播昵称或抖音号的输入框，不是侧边导航')
        self.type_account(view,search,account)
        view,token=self.target(window,'搜索框下方抖音号完全为 '+account+' 的唯一主播候选，不能选搜索框或其他账号')
        self.vision.verify_identity(view,account,self.guard); self.click(view,token)
        view,token=self.target(window,'当前搜索结果中抖音号完全为 '+account+' 的主播所在行的主播详情，加载中或不确定则missing')
        self.vision.verify_identity(view,account,self.guard); self.click(view,token)
        view,token=self.target(window,'当前主播详情页的进入直播间')
        self.vision.verify_identity(view,account,self.guard)
        before={item['handle'] for item in self.probe.windows()}; self.click(view,token)
        deadline=time.monotonic()+20; room_windows=[]
        while time.monotonic()<deadline:
            self.guard()
            room_windows=[item for item in self.probe.windows() if item['handle'] not in before and '直播服务平台' in item['title']]
            if room_windows: break
            time.sleep(.5)
        if len(room_windows)!=1: raise RecapError('room_window_missing_or_ambiguous')
        room=self.focus(room_windows[0])
        def room_loaded(current):
            if not any(any(label in compact(q.text) for label in ('抖音号','累计观众','新增粉丝','跟播列表')) for q in current.tokens):
                raise RecapError('locator_missing')
            return True
        view,_=self.wait(room,room_loaded,timeout=60,use_uia=False)
        self.vision.verify_identity(view,account,self.guard,live=True)
        room_id=self.room_identity(view)
        self._verified_room_id=room_id
        return {'douyin_account':account,'room_id':room_id,
                'window':{key:room[key] for key in ('handle','pid','process_created')},
                'account_verified_on_page':True,'room_id_source':'visible_browser_address_bar_UIA_or_OCR',
                'page_live_status':'已开播','visible_live_time':[], 'observed_at':view.captured_at,
                'locator':'guarded target-window visual decisions','new_window_created':True}

    def wait_edge_started(self):
        self.guard(); self.mcp.call('App',mode='launch',name='Microsoft Edge')
        return self.wait_edge_launch()

    def navigate_entry(self,window):
        live=self.guard(window)
        self.key(live,'ctrl+l')
        self.key(live,'esc')
        view=self.observe(live,False,ocr_optional=True)
        target=self.vision.locate(view,'地址栏',guard=self.guard)
        self.type_replacement(view,target,self.portal_url)
        time.sleep(1)
        return self.wait_entry(live)

    def wait_entry(self,window,timeout=20):
        view=self.observe(window,False,ocr_optional=True)
        if not all(portal_page_evidence(view.tokens).values()):
            self.vision.verify_portal(view,self.guard)
        return view

    def capture(self,binding):
        return Desktop.capture(self,binding)

    def refine_metrics(self,view,result):
        self._used_visual_metric=False
        for name in list(result.get('missing_fields', [])):
            try:
                try:
                    anchor=unique_label(view.tokens,name)
                except RecapError as error:
                    if error.reason != 'locator_missing': raise
                    anchor=None
                values=self.vision.read(view,(name,),'读取单项',anchor=anchor,guard=self.guard,
                                        account=self.expected_account)
                updated=fill_missing(result,values)
                self._used_visual_metric |= updated['metrics'] != result['metrics']
                result=updated
            except RecapError as error:
                if error.reason.startswith('visual_worker_'):
                    result.setdefault('missing_reasons',{})[name]=error.reason
                    continue
                if error.reason not in ('locator_missing','locator_ambiguous','visual_crop_anchor_invalid',
                        'visual_crop_anchor_ambiguous','visual_crop_content_ambiguous','visual_crop_too_large',
                        'visual_metric_unconfirmed'):
                    raise
        return result

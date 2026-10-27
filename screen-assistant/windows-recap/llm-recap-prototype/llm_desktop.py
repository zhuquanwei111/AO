"""单主播单场固定步骤。视觉选择经原Desktop.guard/click/type校验后才执行。"""
from pathlib import Path
from PIL import Image
import sys
import time
import re

LEGACY=Path(__file__).resolve().parent.parent/'fixed-recap-prototype'
if str(LEGACY) not in sys.path: sys.path.insert(0,str(LEGACY))
from core import RecapError, compact, require_account, metrics_from_tokens, METRICS
from desktop import Desktop, portal_page_evidence
from vision import fill_missing


class LLMDesktop(Desktop):
    def __init__(self,*args,vision,**kwargs):
        super().__init__(*args,**kwargs)
        self.vision=vision; self.expected_generation=None

    def guard(self,window=None):
        # 复用原保护检查；另确认观察后没有发生真人接管代际变化。
        from transport import check_reply
        import json
        reply=self.mcp.call('ControlStatus'); status=json.loads(check_reply(reply))
        if status.get('controller')=='user' or status.get('state') in ('user','takeover_pending'):
            raise RecapError('USER_CONTROL')
        if status.get('controller',status.get('state')) not in ('ready','ai'):
            raise RecapError('unknown_control_status')
        generation=status.get('generation')
        if not isinstance(generation,int): raise RecapError('control_generation_missing')
        if self.expected_generation is not None and generation!=self.expected_generation:
            raise RecapError('control_generation_changed')
        self.last_generation=generation
        return self.probe.foreground(window) if window else None

    def observe(self,window,use_uia=False):
        view=super().observe(window,use_uia)
        self.guard(view.window)
        self.expected_generation=self.last_generation
        return view

    def target(self,window,goal,toolbar=False):
        # 有界加载恢复：只对missing多观察一次；歧义、保护、身份问题立即退出。
        for attempt in range(2):
            view=self.observe(window,False)
            try: return view,self.vision.locate(view,goal,toolbar)
            except RecapError as error:
                if error.reason!='visual_missing' or attempt: raise
                self.mcp.note('loading_wait',reason='visual_missing',observations=attempt+1)
                time.sleep(1)

    def navigate_entry(self,window):
        # Esc关闭地址建议，模型不接收浏览器历史。Ctrl+L/输入/Enter仍是程序既定动作。
        self.key(window,'esc')
        view,token=self.target(window,'浏览器顶部网址地址输入框内部，不能选网页搜索框',True)
        self.key(window,'ctrl+l')
        self.type_replacement(view,token,self.portal_url)
        return self.confirm_portal(window)

    def open_entry(self,window,need_open):
        if need_open:
            self.key(window,'ctrl+t')
            return self.navigate_entry(window)
        self.key(window,'esc')
        return self.confirm_portal(window)

    def type_account(self,view,token,account):
        # 搜索候选由键入触发，不按Enter预先选择；每段短于现成Type剪贴板分支阈值。
        for index,start in enumerate(range(0,len(account),19)):
            live=self.guard(view.window)
            if live['rect']!=view.window['rect']: raise RecapError('window_moved_before_action')
            point=view.point(token); x,y,r,b=live['rect']
            if not (x<point[0]<r and y<point[1]<b): raise RecapError('locator_outside_window')
            self.mcp.call('Type',loc=point,text=account[start:start+19],clear=index==0,
                          caret_position='end',press_enter=False)

    def confirm_portal(self,window):
        view=self.observe(window,False)
        if not all(portal_page_evidence(view.tokens).values()):
            data=self.vision.read(view.image,('origin','brand','navigation'),
                '逐字读取浏览器地址中的机构主机路径origin、机构产品名brand、主播管理或工作台navigation。')
            origin=compact(data['origin'] or '')
            if not re.match(r'^(?:https?://)?union\.bytedance\.com/open/portal(?:[/?#]|$)',origin,re.I):
                raise RecapError('portal_origin_unconfirmed')
            if '直播服务平台' not in (data['brand'] or '') or not any(q in (data['navigation'] or '') for q in ('主播','工作台')):
                raise RecapError('portal_page_unconfirmed')
        return view

    def identity(self,view,account,live=False):
        try:
            require_account(view.tokens,account)
            if not live or any('已开播' in compact(q.text) for q in view.tokens): return
        except RecapError: pass
        try: image=self.save_minimal(view,'identity-check.png')
        except RecapError as error:
            if error.reason!='evidence_region_missing': raise
            # 列表资料行可能用抖音图标标识账号；裸账号只作裁图锚点，仍须视觉核对身份。
            matches=[q for q in view.tokens if compact(q.text)==account and q.score>=.85]
            if not matches: raise
            height=max(q.h for q in matches)
            left=min(q.box[0] for q in matches); right=max(q.box[2] for q in matches)
            top=min(q.box[1] for q in matches); bottom=max(q.box[3] for q in matches)
            if bottom-top>height*8 or right-left>max(q.w for q in matches)*2:
                raise RecapError('identity_region_ambiguous')
            image=self.evidence/'identity-check.png'
            with Image.open(view.image) as img:
                img.crop((max(0,int(left-height*4)),max(0,int(top-height*4)),
                          min(img.width,int(right+height*4)),min(img.height,int(bottom+height*4)))).save(image)
        data=self.vision.read(image,('account','live_status'),
            '逐字读取主播资料或列表资料行的抖音号account，可能以抖音图标标识而没有“抖音号”文字。不能把搜索框输入当身份；不是主播资料或无法确认则account=null。读取“已开播”及可见时长为live_status。')
        if data['account']!=account: raise RecapError('wrong_identity')
        if live and '已开播' not in (data['live_status'] or ''): raise RecapError('live_status_unconfirmed')

    def launch_edge(self):
        began=time.monotonic()
        self.mcp.call('App',mode='launch',name='Microsoft Edge')
        deadline=time.monotonic()+15
        windows=self.probe.windows(); counts=[len(windows)]
        # 复用原固定Desktop已有的15秒窗口确认上限，不重复启动浏览器。
        while not windows and time.monotonic()<deadline:
            time.sleep(.5); self.guard()
            windows=self.probe.windows(); counts.append(len(windows))
        self.mcp.note('edge_launch_diagnostics',candidate_count=len(windows),
            candidate_counts=counts,source='current_Win32_Edge_windows',
            candidates=[{key:window.get(key) for key in
                ('handle','pid','process_created','title','rect','minimized')} for window in windows],
            elapsed_ms=round((time.monotonic()-began)*1000))
        if len(windows)!=1: raise RecapError('edge_launch_unconfirmed')
        return windows

    def bind(self,account):
        self.guard(); windows=self.probe.windows()
        portals=[w for w in windows if '直播服务平台' in w['title']]
        if len(portals)>1 or (not portals and len(windows)>1): raise RecapError('edge_window_ambiguous')
        if not windows:
            windows=self.launch_edge()
        window=self.focus(portals[0] if portals else windows[0])
        view=self.open_entry(window,not bool(portals))
        if not any('请输入主播' in compact(q.text) for q in view.tokens):
            if not any(compact(q.text)=='主播列表' for q in view.tokens):
                view,token=self.target(window,'侧边导航“主播管理”这一行右侧的展开箭头，用来展开其子菜单'); self.click(view,token)
            view,token=self.target(window,'“主播管理”展开后的侧边导航“主播列表”，不是表格内容'); self.click(view,token)
        view,search=self.target(window,'主播列表页面右上方用于搜索主播昵称或抖音号/ID的输入框，可能提示“请输入主播昵称或ID”或“请输入主播昵称或抖音号”；不是左侧导航')
        self.type_account(view,search,account)
        view,token=self.target(window,'搜索输入框下方候选中显示“抖音号：'+account+'”的唯一候选；不要选搜索框或相似账号')
        self.identity(view,account); self.click(view,token)
        def filtered_ready(current):
            # 裸账号只判断加载就绪，后续仍由identity核对资料身份。
            try: require_account(current.tokens,account)
            except RecapError:
                if not any(compact(q.text)==account and q.score>=.85 for q in current.tokens):
                    raise RecapError('locator_missing')
            return True
        self.wait(window,filtered_ready,timeout=60,use_uia=False)
        view,token=self.target(window,'已完成加载的筛选结果中，抖音号完全为“'+account+'”的主播所在行“主播详情”；看不到该账号或仍显示旧列表/加载遮罩则missing，不能选别的主播行')
        self.identity(view,account); self.click(view,token)
        view,token=self.target(window,'当前主播详情页“进入直播间”'); self.identity(view,account)
        before={w['handle'] for w in self.probe.windows()}; self.click(view,token)
        deadline=time.monotonic()+20
        while True:
            new=[w for w in self.probe.windows() if w['handle'] not in before and '直播服务平台' in w['title']]
            if new or time.monotonic()>=deadline: break
            time.sleep(.5)
        if len(new)!=1: raise RecapError('room_window_missing_or_ambiguous')
        room=self.focus(new[0])
        def room_loaded(current):
            if not any(any(label in compact(q.text) for label in ('抖音号','累计观众','新增粉丝','跟播列表'))
                       for q in current.tokens): raise RecapError('locator_missing')
            return True
        view,_=self.wait(room,room_loaded,timeout=60,use_uia=False)
        self.identity(view,account,True)
        room_id=self.room_identity(view)  # 保留可见URL/UIA/OCR的原场次核对，不让模型猜roomID。
        evidence=self.save_minimal(view,'binding.png')
        return {'douyin_account':account,'room_id':room_id,'window':room,
                'screenshot':str(evidence.relative_to(self.evidence.parent)),
                'account_verified_on_page':True,'room_id_source':'visible_browser_address_bar_UIA_or_OCR',
                'page_live_status':'已开播','observed_at':view.captured_at,
                'locator':'gpt-6.1-sol restricted visual decisions + guarded Windows-MCP',
                'visible_live_time':[q.text for q in view.tokens if '已开播' in compact(q.text)],
                'new_window_created':True}

    def capture(self,binding):
        room=self.focus(binding['window']); self.key(room,'esc'); view=self.observe(room,False)
        self.identity(view,binding['douyin_account'])
        if self.room_identity(view)!=binding['room_id']: raise RecapError('room_changed')
        view=self.observe(room,False); self.identity(view,binding['douyin_account'])
        result=metrics_from_tokens(view.tokens)
        evidence=self.save_minimal(view,'metrics.png')
        method='Windows-MCP screenshot + local RapidOCR 3.10.0'
        if result['missing_fields']:
            raws=self.vision.read(evidence,('account',)+METRICS,
                '读取抖音号account及左侧五项汇总的显示原文。仅本场：累计观众、新增粉丝、评论人数、送礼人数、获得音浪；保留万等单位，读不清null。')
            if raws['account']!=binding['douyin_account']: raise RecapError('wrong_identity')
            result=fill_missing(result,raws); method+='; missing-only visual review gpt-6.1-sol'
        return {**result,'metrics_captured_at':view.captured_at,'capture_method':method,
                'capture_evidence':{'metrics_screenshot':str(evidence.relative_to(self.evidence.parent)),
                                    'account_verified_on_page':True,'room_id_reverified':binding['room_id']}}

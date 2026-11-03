"""单主播单场固定步骤。视觉选择经原Desktop.guard/click/type校验后才执行。"""
from pathlib import Path
import sys
import time

LEGACY=Path(__file__).resolve().parent.parent/'fixed-recap-prototype'
if str(LEGACY) not in sys.path: sys.path.insert(0,str(LEGACY))
from core import RecapError, compact, require_account
from desktop import Desktop, portal_page_evidence


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

    def observe(self,window,use_uia=False,*,ocr_optional=False):
        view=super().observe(window,use_uia,ocr_optional=ocr_optional)
        self.guard(view.window)
        self.expected_generation=self.last_generation
        return view

    def target(self,window,goal,toolbar=False):
        raise RecapError('navigation_visual_not_authorized')

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

    def confirm_portal(self,window):
        view=self.observe(window,False)
        if not all(portal_page_evidence(view.tokens).values()):
            raise RecapError('portal_page_unconfirmed')
        return view

    def identity(self,view,account,live=False):
        require_account(view.tokens,account)
        if live and not any('已开播' in compact(q.text) for q in view.tokens):
            raise RecapError('live_status_unconfirmed')

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
                ('handle','pid','process_created','rect','minimized')} for window in windows],
            elapsed_ms=round((time.monotonic()-began)*1000))
        if len(windows)!=1: raise RecapError('edge_launch_unconfirmed')
        return windows

    def capture(self,binding):
        return Desktop.capture(self,binding)

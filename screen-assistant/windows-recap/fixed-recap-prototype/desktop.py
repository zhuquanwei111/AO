"""本后台有限步骤适配。只用现有Windows-MCP输入，UIA可见控件／当前截图OCR。"""
from dataclasses import dataclass
from pathlib import Path
import re
import time
from urllib.parse import urlparse
from PIL import Image

from core import Token, RecapError, compact, unique_label, require_account, metrics_from_tokens
from flow import now
from transport import check_reply


@dataclass
class View:
    tokens: list
    image: Path
    window: dict
    size: tuple
    captured_at: str
    uia: list

    def point(self, token):
        if token.source=='uia': return [round(token.x),round(token.y)]
        x,y,r,b=self.window['rect']
        return [round(x+token.x*(r-x)/self.size[0]),round(y+token.y*(b-y)/self.size[1])]


def uia_tokens(text,title):
    """仅解析本版Snapshot可见树格式，静态无坐标文字只用于核对，绝不猜位置。"""
    result=[]; selected=False
    tree=text.split('UI Tree:',1)[-1] if 'UI Tree:' in text else ''
    for line in tree.splitlines():
        window=re.search(r'window "([^"]*)"',line)
        if window:
            selected=window[1]==title
        if not selected: continue
        point=re.search(r'\((-?\d+),\s*(-?\d+)\).*?"([^"]*)"',line)
        if point:
            x,y=int(point[1]),int(point[2])
            result.append(Token(point[3],(x-4,y-4,x+4,y+4),1.,'uia'))
            value=re.search(r'\[value:"([^"]+)"\]',line)
            if value: result.append(Token(value[1],(x-4,y-4,x+4,y+4),1.,'uia'))
        else:
            static=re.search(r'\b(?:text|pane|group) "([^"]+)"',line)
            if static: result.append(Token(static[1],(0,0,0,0),1.,'uia_text'))
    return result


def room_from_tokens(tokens):
    ids=set()
    for token in tokens:
        if token.score<.85: continue
        text=compact(token.text)
        # 使用可见地址栏，不查询网络；必须同时具有正确主机及本场路径。
        if 'union.bytedance.com/open/portal/live_flow/followRoomDetail' not in text: continue
        for match in re.finditer(r'(?:\?|&)room(?:ID|Id|id|lD)=(\d{15,22})(?=&|$)',text):
            ids.add(match[1])
    if len(ids)!=1: raise RecapError('room_id_missing_or_ambiguous')
    return ids.pop()


def _blank_entry_boxes(image,anchor):
    """仅当前入口工具栏行的浅色宽框，复用OpenCV；不识别任意应用控件。"""
    if image is None or not Path(image).is_file(): return []
    import cv2
    import numpy as np
    with Image.open(image) as img: pixels=np.asarray(img.convert('RGB'))
    height,width=pixels.shape[:2]
    y0=max(0,round(anchor.y-anchor.h*.3)); y1=min(height,round(anchor.y+anchor.h*.3))
    right=min(width,round(anchor.box[0]))
    if y1<=y0 or right<=0: return []
    mask=cv2.inRange(pixels[y0:y1,:right],(248,248,248),(255,255,255))
    stable=((mask>0).all(axis=0).astype('uint8')*255)[None,:]
    contours,_=cv2.findContours(stable,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]
    for contour in contours:
        x,_,span,_=cv2.boundingRect(contour)
        # 必须有可见左右边界；整片浅色背景或窄按钮不能作为输入框。
        if x>0 and x+span<right and span>=max(width*.35,anchor.h*12):
            boxes.append(Token('blank_address_textbox',(x,y0,x+span,y1),1.,'visual_box'))
    return boxes


def entry_address_candidates(tokens,image=None):
    """入口专用：可读提示须与当前新标签／工具栏锚点相符，不能选网页搜索框。"""
    hints={'搜索或输入web地址','搜索或输入网址','searchorenterwebaddress'}
    good=[q for q in tokens if q.score>=.85]
    tabs=[q for q in good if compact(q.text).casefold() in ('新建标签页','newtab')]
    raw=[q for q in good if compact(q.text).casefold() in hints]
    toolbar=[]
    if len(tabs)==1:
        tab=tabs[0]
        toolbar=[q for q in good if compact(q.text).casefold() in ('聊天','chat','copilot')
                 and q.box[0]>tab.box[2] and 0<q.y-tab.y<tab.h*3]
    candidates=[]
    if len(toolbar)==1:
        anchor=toolbar[0]
        candidates=[q for q in raw if q.box[2]<anchor.box[0] and
                    q.box[1]>tabs[0].box[3] and abs(q.y-anchor.y)<=max(q.h,anchor.h)*.75]
    ocr_count=len(candidates); visual=[]
    visual_examined=len(toolbar)==1 and not raw and image is not None and Path(image).is_file()
    if visual_examined:
        visual=_blank_entry_boxes(image,toolbar[0]); candidates=visual
    return candidates,{'source':'current_window_screenshot_toolbar_box' if visual_examined else 'current_window_screenshot_ocr',
        'ocr_hint_count':len(raw),'ocr_toolbar_candidates':ocr_count,'visual_box_examined':visual_examined,
        'visual_box_candidates':len(visual),
        'new_tab_anchor_count':len(tabs),'toolbar_anchor_count':len(toolbar),
        'toolbar_candidate_count':len(candidates)}


def snapshot_diagnostics(text,title,uia_requested,ocr_tokens):
    tree_present=uia_requested and 'UI Tree:' in text
    tree=text.split('UI Tree:',1)[-1] if tree_present else ''
    parsed=uia_tokens(text,title) if tree_present else []
    bars=[q for q in parsed if q.source=='uia' and any(name in q.text
          for name in ('地址和搜索栏','地址和搜索','Address and search bar'))]
    _,ocr=entry_address_candidates(ocr_tokens)
    return {'uia_requested':uia_requested,'raw_tree_present':bool(tree_present),
            'tree_truncated':bool(re.search(r'\[truncated:\s*reached the \d+-element',tree)) if tree_present else None,
            'target_window_headers':sum(name==title for name in re.findall(r'window "([^"]*)"',tree)) if tree_present else None,
            'uia_address_candidates':len(bars) if tree_present else None,**ocr}


def portal_page_evidence(tokens):
    texts=[compact(q.text) for q in tokens if q.score>=.85]
    origin=any(re.search(r'(?<![\w.-])(?:https?://)?union\.bytedance\.com/open/portal(?:[/?#]|$)',text,re.I)
               for text in texts)
    brand=any('直播服务平台' in text for text in texts)
    navigation=any(text in ('工作台','主播管理','主播列表') or '请输入主播' in text for text in texts)
    return {'portal_origin_visible':origin,'portal_brand_visible':brand,'portal_navigation_visible':navigation}


class Desktop:
    def __init__(self,mcp,probe,ocr,evidence,portal_url):
        parsed=urlparse(portal_url)
        if parsed.scheme!='https' or parsed.hostname!='union.bytedance.com' or parsed.username or parsed.password:
            raise RecapError('invalid_portal_url')
        if not parsed.path.startswith('/open/portal'): raise RecapError('invalid_portal_url')
        self.mcp,self.probe,self.ocr=mcp,probe,ocr
        self.evidence=Path(evidence); self.evidence.mkdir(parents=True,exist_ok=True)
        self.portal_url=portal_url

    def guard(self,window=None):
        reply=self.mcp.call('ControlStatus')
        text=check_reply(reply)
        # 兼容本版JSON字符串，未知保护状态也不放行输入。
        import json
        try: status=json.loads(text)
        except ValueError: raise RecapError('unknown_control_status') from None
        if hasattr(self.mcp,'note'):
            self.mcp.note('control_state',state=status.get('state'),generation=status.get('generation'),
                          wait_seconds=status.get('wait_seconds'))
        if status.get('controller')=='user' or status.get('state')=='user': raise RecapError('USER_CONTROL')
        if status.get('controller',status.get('state')) not in ('ready','ai'):
            raise RecapError('unknown_control_status')
        if window: return self.probe.foreground(window)

    def focus(self,window):
        self.guard()
        live=self.probe.get(window)
        try: return self.probe.foreground(live)
        except RecapError: pass
        matches=[w for w in self.probe.windows() if w['title']==live['title']]
        if len(matches)!=1: raise RecapError('window_title_ambiguous')
        self.mcp.call('App',mode='switch',name=live['title'])
        return self.probe.foreground(live)

    def observe(self,window,use_uia=False):
        live=self.guard(window)
        region=live['rect']
        if not self.probe.contained(region): raise RecapError('window_outside_screen')
        if use_uia:
            reply=self.mcp.call('Snapshot',use_vision=True,use_dom=False,use_annotation=False,
                                use_ui_tree=True,region=region)
        else:
            reply=self.mcp.call('Screenshot',use_annotation=False,region=region)
        captured_at=now()  # 工具返回截图时，不把后续OCR结束时间冒作截图时间。
        self.guard(live)  # 在耗时OCR前记录本次截图正常取得的租约，并复核前台。
        text=check_reply(reply)
        paths=[Path(q['image_path']) for q in reply.get('content',[]) if 'image_path' in q]
        if len(paths)!=1 or not paths[0].is_file(): raise RecapError('screenshot_missing')
        image=paths[0]
        with Image.open(image) as img: size=img.size
        uia=uia_tokens(text,live['title']) if use_uia else []
        tokens=self.ocr.read(image)
        if hasattr(self.mcp,'note'):
            self.mcp.note('snapshot_diagnostics',window_handle=live['handle'],window_pid=live['pid'],
                          screenshot=image.name,**snapshot_diagnostics(text,live['title'],use_uia,tokens))
        for q in tokens:
            if compact(q.text) in ('扫码登录','请登录','安全验证','滑块验证','短信登录'):
                raise RecapError('needs_login_or_verification')
        return View(tokens,image,live,size,captured_at,uia)

    def wait(self,window,select,timeout=20,use_uia=True):
        began=time.monotonic(); deadline=began+timeout
        reason='locator_missing'; first=True; attempts=0
        while True:
            attempts+=1
            view=self.observe(window,use_uia and first); first=False
            try:
                selected=select(view)
                if attempts>1 and hasattr(self.mcp,'note'):
                    self.mcp.note('loading_wait',reason=reason,observations=attempts,
                                  elapsed_ms=round((time.monotonic()-began)*1000))
                return view,selected
            except RecapError as error:
                if error.reason=='locator_ambiguous':
                    raise
                if error.reason not in ('locator_missing','locator_ambiguous','wrong_identity',
                    'room_id_missing_or_ambiguous'): raise
                reason=error.reason
            if time.monotonic()>=deadline:
                if hasattr(self.mcp,'note'):
                    self.mcp.note('loading_timeout',reason=reason,observations=attempts,
                                  elapsed_ms=round((time.monotonic()-began)*1000))
                raise RecapError(reason)
            time.sleep(.75)

    def label(self,view,label,contains=False):
        candidates=[q for q in view.uia if q.source=='uia']
        try: return unique_label(candidates,label,contains)
        except RecapError as error:
            if error.reason=='locator_ambiguous': raise
        return unique_label(view.tokens,label,contains)

    def click(self,view,token):
        current=self.guard(view.window)
        if current['rect']!=view.window['rect']: raise RecapError('window_moved_before_action')
        point=view.point(token)
        x,y,r,b=current['rect']
        if not (x<point[0]<r and y<point[1]<b): raise RecapError('locator_outside_window')
        self.mcp.call('Click',loc=point,button='left',clicks=1)

    def key(self,window,key):
        self.guard(window); self.mcp.call('Shortcut',shortcut=key)

    def type_replacement(self,view,token,text):
        # 0.8.8每次先点击；clear在点击后Ctrl+A/Back。每段<20，避开读取/恢复剪贴板的长文本分支。
        chunks=[text[i:i+19] for i in range(0,len(text),19)]
        if not chunks: raise RecapError('empty_input')
        for index,chunk in enumerate(chunks):
            live=self.guard(view.window)
            if live['rect']!=view.window['rect']: raise RecapError('window_moved_before_action')
            point=view.point(token)
            x,y,r,b=live['rect']
            if not (x<point[0]<r and y<point[1]<b): raise RecapError('locator_outside_window')
            self.mcp.call('Type',loc=point,text=chunk,clear=index==0,
                          caret_position='end',press_enter=index==len(chunks)-1)

    def wait_entry(self,window,timeout=20):
        def confirmed(view):
            evidence=portal_page_evidence(view.tokens)
            if hasattr(self.mcp,'note'):
                self.mcp.note('portal_confirmation',window_handle=view.window['handle'],**evidence)
            if not all(evidence.values()): raise RecapError('locator_missing')
            return True
        try:
            view,_=self.wait(window,confirmed,timeout=timeout,use_uia=False)
            return view
        except RecapError as error:
            if error.reason=='locator_missing': raise RecapError('portal_page_unconfirmed') from None
            raise

    def navigate_entry(self,window):
        live=self.guard(window)
        matches=[q for q in self.probe.windows() if q['title']==live['title']]
        if len(matches)!=1: raise RecapError('edge_window_ambiguous')
        self.key(live,'ctrl+l')
        view=self.observe(live,False)  # 不枚举全桌面UIA来定位地址栏。
        candidates,diagnostic=entry_address_candidates(view.tokens,view.image)
        if hasattr(self.mcp,'note'):
            self.mcp.note('entry_address_candidates',window_handle=view.window['handle'],
                          window_pid=view.window['pid'],window_title=view.window['title'],
                          screenshot=view.image.name,**diagnostic)
        if not candidates: raise RecapError('address_bar_missing')
        if len(candidates)>1: raise RecapError('address_bar_ambiguous')
        self.type_replacement(view,candidates[0],self.portal_url)
        return self.wait_entry(live)

    def bind(self,account):
        self.guard()
        # 先建立只读快照；不截取无关应用图像或提取其UI树。
        self.mcp.call('Snapshot',use_vision=False,use_dom=False,use_ui_tree=False,use_annotation=False)
        windows=self.probe.windows()
        portal=[w for w in windows if '直播服务平台' in w['title']]
        if len(portal)>1: raise RecapError('window_title_ambiguous')
        if portal: window=portal[0]; need_open=False
        else:
            if len(windows)>1: raise RecapError('edge_window_ambiguous')
            if not windows:
                self.guard(); self.mcp.call('App',mode='launch',name='Microsoft Edge')
                deadline=time.monotonic()+15
                while not windows and time.monotonic()<deadline:
                    time.sleep(.5); windows=self.probe.windows()
                if len(windows)!=1: raise RecapError('edge_launch_unconfirmed')
            window=windows[0]; need_open=True
        window=self.focus(window)
        if need_open:
            # 新标签保护原用户页面；仅输入获准机构入口。
            self.key(window,'ctrl+t')
            view=self.navigate_entry(window)
        else:
            view=self.wait_entry(window)
        if not any('请输入主播' in compact(q.text) for q in view.tokens+view.uia):
            view,button=self.wait(window,lambda v:self.label(v,'主播管理'))
            self.click(view,button)
            def sidebar(v):
                manager=unique_label(v.tokens,'主播管理')
                items=[q for q in v.tokens if compact(q.text)=='主播列表'
                       and abs(q.x-manager.x)<manager.w and 0<q.y-manager.y<manager.h*5]
                return unique_label(items,'主播列表')
            view,button=self.wait(window,sidebar); self.click(view,button)
        view,search=self.wait(window,lambda v:self.label(v,'请输入主播',True))
        self.click(view,search); self.guard(window)
        self.mcp.call('Type',loc=view.point(search),text=account,clear=True,press_enter=False)
        def candidate(v):
            # 只选当前输入框下方、显示完全相同抖音号的候选，不碰原列表同名项。
            sx,sy=view.point(search)
            nearby=[]
            for q in v.tokens:
                x,y=v.point(q)
                if sy<y<sy+search.h*15 and abs(x-sx)<max(search.w*1.5,search.h*6):
                    try: require_account([q],account); nearby.append(q)
                    except RecapError: pass
            if len(nearby)!=1: raise RecapError('locator_ambiguous' if nearby else 'locator_missing')
            return nearby[0]
        view,button=self.wait(window,candidate); self.click(view,button)
        view,button=self.wait(window,lambda v:self.label(v,'主播详情'))
        self.click(view,button)
        def detail(v):
            require_account(v.uia+v.tokens,account)
            return self.label(v,'进入直播间')
        view,button=self.wait(window,detail)
        before={w['handle'] for w in self.probe.windows()}
        self.click(view,button)
        deadline=time.monotonic()+20; room_windows=[]
        while time.monotonic()<deadline:
            room_windows=[w for w in self.probe.windows() if w['handle'] not in before
                          and '直播服务平台' in w['title']]
            if room_windows: break
            time.sleep(.5)
        if len(room_windows)!=1: raise RecapError('room_window_missing_or_ambiguous')
        room=self.focus(room_windows[0])
        def room_ready(v):
            require_account(v.uia+v.tokens,account)
            if not any(compact(q.text)=='跟播列表' for q in v.tokens+v.uia): raise RecapError('locator_missing')
            if not any('已开播' in compact(q.text) for q in v.tokens+v.uia): raise RecapError('locator_missing')
            return True
        view,_=self.wait(room,room_ready)
        room_id=self.room_identity(view)
        evidence=self.save_minimal(view,'binding.png')
        return {'douyin_account':account,'room_id':room_id,'window':room,
                'screenshot':str(evidence.relative_to(self.evidence.parent)),'account_verified_on_page':True,
                'room_id_source':'visible_browser_address_bar_UIA_or_OCR',
                'page_live_status':'已开播',
                'visible_live_time':[q.text for q in view.tokens if '已开播' in compact(q.text)],
                'observed_at':view.captured_at,'locator':'UIA if available; current screenshot OCR',
                'new_window_created':True}

    def room_identity(self,view):
        try: return room_from_tokens(view.uia+view.tokens)
        except RecapError: pass
        # 地址栏可见UIA优先；若当前截断，聚焦到地址栏末尾再OCR，不读剪贴板。
        self.key(view.window,'ctrl+l'); self.key(view.window,'end')
        address=self.observe(view.window,True)
        self.key(view.window,'esc')
        return room_from_tokens(address.uia+address.tokens)

    def save_minimal(self,view,name):
        anchors=[q for q in view.tokens if any(label in compact(q.text)
                 for label in ('抖音号','累计观众','新增粉丝','评论人数','送礼人数','获得音浪','跟播列表'))]
        if not anchors: raise RecapError('evidence_region_missing')
        top=min(q.box[1] for q in anchors); bottom=max(q.box[3] for q in anchors)
        left=min(q.box[0] for q in anchors); right=max(q.box[2] for q in anchors)
        margin=max(q.h for q in anchors)*4
        path=self.evidence/name
        with Image.open(view.image) as img:
            img.crop((max(0,int(left-margin)),max(0,int(top-margin)),
                      min(img.width,int(right+margin)),min(img.height,int(bottom+margin)))).save(path)
        return path

    def capture(self,binding):
        room=self.focus(binding['window'])
        view=self.observe(room,True)
        require_account(view.uia+view.tokens,binding['douyin_account'])
        try:
            room_id=room_from_tokens(view.uia+view.tokens)
        except RecapError:
            room_id=self.room_identity(view)
            # 只有地址栏动作改变页面焦点时才再取一次清晰指标截图。
            view=self.observe(room,True)
        if room_id!=binding['room_id']: raise RecapError('room_changed')
        require_account(view.uia+view.tokens,binding['douyin_account'])
        result=metrics_from_tokens(view.uia)
        method='Windows-MCP visible UIA'
        if result['missing_fields']:
            result=metrics_from_tokens(view.tokens)
            method='Windows-MCP screenshot + local RapidOCR 3.10.0'
        evidence=self.save_minimal(view,'metrics.png')
        return {**result,'metrics_captured_at':view.captured_at,'capture_method':method,
                'capture_evidence':{'metrics_screenshot':str(evidence.relative_to(self.evidence.parent)),'account_verified_on_page':True,
                                    'room_id_reverified':binding['room_id']}}

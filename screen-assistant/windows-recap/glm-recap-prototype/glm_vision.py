"""当次图片定位／字段读取；不包含AO规则、开发报告或历史会话。"""
import math
import io
from dataclasses import dataclass
from pathlib import Path
import sys
from PIL import Image

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'fixed-recap-prototype'))
from core import Token,RecapError,compact,METRICS,require_account

_CROP_ISSUER = object()
_NAVIGATION_LABELS = {'主播管理', '主播列表', '主播详情', '进入直播间', '搜索或输入网址', '搜索或输入web地址'}
_AUTH_MARKERS = ('扫码登录', '请登录', '安全验证', '滑块验证', '短信登录', '验证码', '密码')


@dataclass(frozen=True)
class LocalCrop:
    image: bytes
    purpose: str
    _issuer: object
    _window: dict
    _guard: object

    def ensure_current(self):
        current = self._guard(self._window)
        if not isinstance(current, dict) or any(current.get(k) != self._window.get(k)
                                                for k in ('handle', 'pid', 'process_created', 'rect')):
            raise RecapError('visual_crop_window_changed')


def make_local_crop(view, anchor, purpose, guard, account=None):
    """从本轮唯一文本锚点取得独立小区，仅供受限内存传输。"""
    if purpose not in ('navigation', 'identity', 'metric') or not isinstance(view.image, bytes):
        raise RecapError('visual_crop_source_invalid')
    if not view.captured_at or not isinstance(view.window, dict):
        raise RecapError('visual_crop_source_invalid')
    window = view.window
    if not window.get('handle') or not window.get('pid') or not window.get('process_created') or len(window.get('rect', [])) != 4:
        raise RecapError('visual_crop_source_invalid')
    live = guard(window)
    if not isinstance(live, dict) or any(live.get(k) != window.get(k) for k in ('handle', 'pid', 'process_created', 'rect')):
        raise RecapError('visual_crop_window_changed')
    tokens = view.tokens
    if any(any(marker in compact(q.text) for marker in _AUTH_MARKERS) for q in tokens + view.uia):
        raise RecapError('needs_login_or_verification')
    if (sum(q == anchor for q in tokens) != 1 or anchor.source != 'ocr' or anchor.score < .85
            or sum(compact(q.text) == compact(anchor.text) and q.score >= .85 for q in tokens) != 1):
        raise RecapError('visual_crop_anchor_ambiguous')
    name = compact(anchor.text)
    if purpose == 'navigation' and name not in _NAVIGATION_LABELS:
        raise RecapError('visual_crop_anchor_invalid')
    if purpose == 'metric' and name not in METRICS:
        raise RecapError('visual_crop_anchor_invalid')
    if purpose == 'identity':
        if not isinstance(account, str) or not account or '抖音号' not in name:
            raise RecapError('visual_crop_anchor_invalid')
        require_account(tokens, account)
    with Image.open(io.BytesIO(view.image)) as image:
        if image.size != view.size:
            raise RecapError('visual_crop_source_invalid')
        x, y, right, bottom = anchor.box
        if not (0 <= x < right <= image.width and 0 <= y < bottom <= image.height):
            raise RecapError('visual_crop_outside_image')
        margin = max(1, int(anchor.h))
        box = (max(0, int(x-margin)), max(0, int(y-margin)),
               min(image.width, int(right+margin)), min(image.height, int(bottom+margin)))
        if (box[2]-box[0])*(box[3]-box[1]) > image.width*image.height/8:
            raise RecapError('visual_crop_too_large')
        for item in tokens:
            if item == anchor:
                continue
            l, t, r, b = item.box
            if max(l, box[0]) < min(r, box[2]) and max(t, box[1]) < min(b, box[3]):
                raise RecapError('visual_crop_content_ambiguous')
        out = io.BytesIO()
        image.crop(box).save(out, format='PNG')
    current = guard(window)
    if not isinstance(current, dict) or any(current.get(k) != window.get(k) for k in ('handle', 'pid', 'process_created', 'rect')):
        raise RecapError('visual_crop_window_changed')
    return LocalCrop(out.getvalue(), purpose, _CROP_ISSUER,
                     {**window, 'rect': list(window['rect'])}, guard)


def validate_box(value,size):
    if value.get('status') not in ('found','missing','ambiguous','blocked'): raise ValueError('invalid_visual_status')
    box=value.get('box')
    if value['status']!='found':
        if box is not None: raise ValueError('unexpected_visual_box')
        return
    if not isinstance(box,list) or len(box)!=4 or any(type(q) not in (int,float) or not math.isfinite(q) for q in box):
        raise ValueError('invalid_visual_box')
    x,y,r,b=box
    if not (0<=x<r<=size[0] and 0<=y<b<=size[1]): raise ValueError('visual_box_outside_image')


class GLMVision:
    def __init__(self,runtime): self.runtime=runtime

    def locate(self,view,goal,toolbar=False):
        # 页面导航不属于可发送给视觉服务的身份或指标裁区。
        raise RecapError('navigation_visual_not_authorized')

    def read(self,image,fields,instruction):
        raise RecapError('image_visual_disabled')

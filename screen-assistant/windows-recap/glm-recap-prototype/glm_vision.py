"""当次图片定位／字段读取；不包含AO规则、开发报告或历史会话。"""
import math
from pathlib import Path
import sys
from PIL import Image

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'fixed-recap-prototype'))
from core import Token,RecapError


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

"""当次图片定位／字段读取；不包含AO规则、开发报告或历史会话。"""
import math
from pathlib import Path
import sys
from PIL import Image

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'fixed-recap-prototype'))
from core import Token,RecapError
from safe_evidence import SafeEvidence
import hashlib


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
        allowed={'account','累计观众','新增粉丝','评论人数','送礼人数','获得音浪'}
        if (not isinstance(image,SafeEvidence) or image.kind!='metrics.png'
                or not set(fields).issubset(allowed) or not fields):
            raise RecapError('safe_visual_region_unconfirmed')
        try:
            if hashlib.sha256(image.path.read_bytes()).hexdigest()!=image.sha256:
                raise RecapError('safe_visual_region_changed')
            prompt=instruction+' 直接返回JSON对象，仅含这些字段：'+','.join(fields)+'；每项逐字字符串，读不清null。'
            result,_=self.runtime.run(image,prompt)
        except (OSError,ValueError,RuntimeError) as error: raise RecapError(str(error)) from None
        if set(result)!=set(fields) or any(q is not None and not isinstance(q,str) for q in result.values()):
            raise RecapError('invalid_visual_fields')
        return result

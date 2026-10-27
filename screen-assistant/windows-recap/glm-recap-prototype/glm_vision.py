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
        image=view.image
        if toolbar:
            # 原固定流程先Esc关闭建议；比例仅裁图，不是动作位置。
            with Image.open(image) as img:
                image=self.runtime.scratch/('toolbar-'+Path(image).name)
                img.crop((0,0,img.width,max(1,round(img.height*.18)))).save(image)
        with Image.open(image) as img: size=img.size
        prompt=(f'定位唯一目标：{goal}。图片宽{size[0]}高{size[1]}像素。'
            '直接返回含status、box的JSON对象：清楚且唯一时status=found，box为本图像像素框[x1,y1,x2,y2]；'
            '没有目标missing、多目标ambiguous、登录/验证码/风控blocked，这三种box=null。'
            '有页面加载遮罩时返回missing，不能按遮罩下旧列表内容定位。不能用桌面坐标，不能猜未显示位置。')
        try:
            result,_=self.runtime.run(image,prompt)
            validate_box(result,size)
        except (ValueError,RuntimeError) as error: raise RecapError(str(error)) from None
        if result['status']!='found': raise RecapError('visual_'+result['status'])
        return Token('model_target',tuple(result['box']),1.,'vision')

    def read(self,image,fields,instruction):
        prompt=instruction+' 直接返回JSON对象，仅含这些字段：'+','.join(fields)+'；每项逐字字符串，读不清null。'
        try: result,_=self.runtime.run(image,prompt)
        except (ValueError,RuntimeError) as error: raise RecapError(str(error)) from None
        if set(result)!=set(fields) or any(q is not None and not isinstance(q,str) for q in result.values()):
            raise RecapError('invalid_visual_fields')
        return result

"""显式本地RapidOCR；截图只能为本地文件，不接受URL。"""
from pathlib import Path
from core import Token, RecapError
from provision_ocr import verify, MODELS


class LocalOCR:
    def __init__(self,models):
        from rapidocr import RapidOCR
        verify(models)
        params={role+'.model_path':str((Path(models)/data[0]).resolve()) for role,data in MODELS.items()}
        params.update({'Global.log_level':'error','Global.use_cls':False,
            'EngineConfig.onnxruntime.intra_op_num_threads':2,
            'EngineConfig.onnxruntime.inter_op_num_threads':1})
        self.engine=RapidOCR(params=params)

    def read(self,path):
        path=Path(path)
        if not path.is_file(): raise RecapError('screenshot_missing')
        result=self.engine(str(path.resolve()))
        if result.boxes is None or result.txts is None: return []
        return [Token(text,(float(min(p[0] for p in box)),float(min(p[1] for p in box)),
                            float(max(p[0] for p in box)),float(max(p[1] for p in box))),float(score))
                for box,text,score in zip(result.boxes,result.txts,result.scores)]

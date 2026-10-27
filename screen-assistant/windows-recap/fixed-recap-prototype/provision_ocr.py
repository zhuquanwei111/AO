"""只在显式准备阶段复制官方包自带模型；不触碰桌面。"""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil

MODELS = {
    'Det': ('PP-OCRv6_det_small.onnx','090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f','PP-OCRv6/det'),
    'Rec': ('PP-OCRv6_rec_small.onnx','6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884','PP-OCRv6/rec'),
    'Cls': ('ch_ppocr_mobile_v2.0_cls_mobile.onnx','e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c','PP-OCRv4/cls'),
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify(folder):
    if metadata.version('rapidocr') != '3.10.0' or metadata.version('onnxruntime') != '1.23.2':
        raise RuntimeError('ocr_version_mismatch')
    for filename, expected, _ in MODELS.values():
        path=Path(folder)/filename
        if not path.is_file() or sha(path)!=expected:
            raise RuntimeError('ocr_model_missing_or_hash_mismatch')


def provision(folder):
    dist=metadata.distribution('rapidocr')
    if dist.version!='3.10.0':
        raise RuntimeError('rapidocr_version_mismatch')
    folder=Path(folder).resolve(); folder.mkdir(parents=True,exist_ok=True)
    manifest={'rapidocr':'3.10.0','onnxruntime':'1.23.2','code_license':'Apache-2.0',
        'weights_license':'Apache-2.0; Baidu/PaddleOCR ownership retained',
        'license_source':'https://rapidai.github.io/RapidOCRDocs/latest/model_licenses/',
        'package_source':'https://pypi.org/project/rapidocr/3.10.0/',
        'models':[]}
    for role,(filename,expected,route) in MODELS.items():
        source=Path(dist.locate_file('rapidocr/models/'+filename))
        if not source.is_file() or sha(source)!=expected:
            raise RuntimeError('bundled_model_hash_mismatch')
        target=folder/filename
        if target.exists() and sha(target)!=expected:
            raise RuntimeError('refuse_overwrite_different_model')
        if not target.exists(): shutil.copyfile(source,target)
        manifest['models'].append({'role':role,'filename':filename,'sha256':expected,
            'source':f'https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.10.0/onnx/{route}/{filename}',
            'provisioned_from':'official rapidocr 3.10.0 bundled wheel model; no conversion or modification'})
    # 保留安装包中的许可证及归属；不把代码许可代替权重声明。
    notices=folder/'notices'; notices.mkdir(exist_ok=True)
    for item in dist.files or []:
        if '.dist-info/licenses/' in str(item).replace('\\','/'):
            source=Path(dist.locate_file(item))
            if source.is_file(): shutil.copyfile(source,notices/source.name)
    verify(folder)
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models',type=Path,required=True)
    args=parser.parse_args()
    data=provision(args.models)
    print(json.dumps({'verified_models':len(data['models']),'models':str(args.models)},ensure_ascii=False))

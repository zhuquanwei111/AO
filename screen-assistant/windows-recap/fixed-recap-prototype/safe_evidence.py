"""已限定裁区的本地证据句柄，不包含图像处理或网络调用。"""
from dataclasses import dataclass
import hashlib
from pathlib import Path


@dataclass(frozen=True)
class SafeEvidence:
    path: Path
    sha256: str
    kind: str

    def __fspath__(self): return str(self.path)
    def relative_to(self, other): return self.path.relative_to(other)


def read_verified_image(path, expected_sha256):
    data=Path(path).read_bytes()
    if not isinstance(expected_sha256,str) or hashlib.sha256(data).hexdigest()!=expected_sha256:
        raise ValueError('safe_visual_region_changed')
    return data

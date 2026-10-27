"""固定业务规则；不导入桌面、网络或模型客户端。"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
import unicodedata

METRICS = ('累计观众', '新增粉丝', '评论人数', '送礼人数', '获得音浪')
CARD_LABELS = METRICS + ('人气', '加粉丝团', '点赞数')
MIN_SCORE = .85


class RecapError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Token:
    text: str
    box: tuple
    score: float = 1.0
    source: str = 'ocr'

    @property
    def x(self): return (self.box[0]+self.box[2])/2
    @property
    def y(self): return (self.box[1]+self.box[3])/2
    @property
    def h(self): return max(1, self.box[3]-self.box[1])
    @property
    def w(self): return max(1, self.box[2]-self.box[0])


def compact(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def parse_count(raw):
    text = compact(raw)
    match = re.fullmatch(r'(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?([万亿千wWkK]?)', text)
    if not match or (match[2] and not match[3]):
        return None, False
    multipliers = {'':1, '万':10000, 'w':10000, 'W':10000, '亿':100000000,
                   '千':1000, 'k':1000, 'K':1000}
    try:
        value = Decimal((match[1]+(match[2] or '')).replace(',', '')) * multipliers[match[3]]
        if value != int(value) or value > 10**15:
            return None, False
        return int(value), bool(match[3])
    except (InvalidOperation, ValueError):
        return None, False


def unique_label(tokens, label, contains=False):
    matched = [q for q in tokens if q.score >= MIN_SCORE and
               (label in compact(q.text) if contains else compact(q.text) == compact(label))]
    if not matched:
        raise RecapError('locator_missing')
    if len(matched) != 1:
        raise RecapError('locator_ambiguous')
    return matched[0]


def require_account(tokens, account):
    # 仅裸账号不足以核对，避免把搜索框输入或相似账号当主播身份。
    for q in tokens:
        text = compact(q.text)
        if q.score >= MIN_SCORE and re.search(r'抖音号[:：]?' + re.escape(account) + r'(?![\w.\-])', text):
            return
    for label in tokens:
        if compact(label.text) in ('抖音号', '抖音号:') and label.score >= MIN_SCORE:
            nearby = [q for q in tokens if compact(q.text) == account and q.score >= MIN_SCORE
                      and 0 <= q.box[0]-label.box[2] <= label.h*5
                      and abs(q.y-label.y) <= label.h]
            if len(nearby) == 1:
                return
    raise RecapError('wrong_identity')


def metrics_from_tokens(tokens):
    values, raws, approx, missing = {}, {}, [], {}
    for name in METRICS:
        labels = [q for q in tokens if name in compact(q.text) and q.score >= MIN_SCORE]
        value, raw, approximate = None, None, False
        reason = 'missing_label'
        if len(labels) > 1:
            reason = 'ambiguous_label'
        elif labels:
            label = labels[0]
            remainder = compact(label.text).replace(name, '').strip(':')
            if remainder:
                raw = remainder
                value, approximate = parse_count(raw)
                reason = 'unreadable_value'
            else:
                # 已知卡片布局：数字在对应文字正上方；按当前文字框几何邻接，不按序号。
                peers = [q for q in tokens if compact(q.text) in CARD_LABELS
                         and abs(q.y-label.y) <= label.h]
                left = max([q.x for q in peers if q.x < label.x], default=label.x-label.w*2)
                right = min([q.x for q in peers if q.x > label.x], default=label.x+label.w*2)
                lo, hi = (left+label.x)/2, (right+label.x)/2
                candidates = [q for q in tokens if lo < q.x < hi and
                              0 < label.y-q.y < label.h*5 and
                              abs(q.x-label.x) <= max(label.w*.8,label.h*2) and
                              (parse_count(q.text)[0] is not None or compact(q.text) in ('--','-'))]
                if candidates:
                    nearest_y = max(q.y for q in candidates)
                    row = [q for q in candidates if abs(q.y-nearest_y) <= label.h*.7]
                    if len(row) == 1:
                        raw = row[0].text
                        if row[0].score >= MIN_SCORE:
                            value, approximate = parse_count(raw)
                            reason = 'unreadable_value'
                        else:
                            reason = 'low_confidence'
                    else:
                        reason = 'ambiguous_value'
                else:
                    reason = 'missing_value'
        values[name], raws[name] = value, raw
        if approximate:
            approx.append(name)
        if value is None:
            missing[name] = reason
    return {'metrics':values, 'raw_displays':raws, 'approximate_metrics':approx,
            'missing_fields':list(missing), 'missing_reasons':missing}

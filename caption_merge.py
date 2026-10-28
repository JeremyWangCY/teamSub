# -*- coding: utf-8 -*-
"""碎句合并：把 ASR 的增量片段折回一句话。

会议软件的实时字幕按「显示行」下发，一句口语常被切成 2–3 行；同一行还会先出
半句、再补全（ASR 反复改写）。两者在列表里都表现为「同一人连着两条」，与飞书
这类自己重解码整句的客户端观感差距明显。

本模块只放纯函数，不碰时间线、不碰界面：

* :func:`looks_like_revision` —— 新文本是旧文本的改写（含中文叠字、吞标点）
* :func:`join_caption`        —— 把真正的续写拼成一句
* :func:`should_merge`        —— 加时间窗与发言人条件，决定要不要折
"""
import re
from difflib import SequenceMatcher

# 中日韩统一表意文字（含扩展 A 与兼容区）
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
# 终止性标点：出现即认为这句话语法闭合，不再往下折
_SENT_END = re.compile(r"[.?!。？！…]\s*$")
_SPACES = re.compile(r"[\s\u3000]+")
# 归一化时剥掉的边缘标点（中文逗号不参与内容比较）
_TRIM = "。．.！!？?，,、；;：:…~～\"'“”‘’()（）[]【】 \t"
# 续写拼接时，这些结尾标点后不再补空格
_SOFT_TAIL = "，,、；;：:"


def has_cjk(text: str) -> bool:
    return bool(_CJK.search(text or ""))


def ends_sentence(text: str) -> bool:
    """以终止标点收尾（与 translator.is_sentence_end 同义，此处独立实现避免循环导入）。"""
    return bool(_SENT_END.search((text or "").strip()))


def _norm(text: str) -> str:
    """去掉空白与边缘标点，用于内容比较；不做大小写以外的改写。"""
    return _SPACES.sub("", text or "").lower().strip(_TRIM)


def _overlap(part: str, whole: str) -> float:
    """part 的字符有多大比例按顺序出现在 whole 里（0–1）。"""
    if not part or not whole:
        return 0.0
    matcher = SequenceMatcher(None, part, whole, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(part)


def looks_like_revision(prev: str, new: str) -> bool:
    """判断 new 是不是 prev 的改写版（同一句话的再次下发）。

    中文没有词边界，因此不能沿用英文的词级前缀判断：「这个方案」-> 「这个方案
    周四再确认」这类叠字+补全，前缀不成立但显然是同一句，用「短句被新句覆盖的
    比例」判定更稳。
    """
    a, b = _norm(prev), _norm(new)
    if not a or not b:
        return False
    if a == b:
        return True
    if b.startswith(a) or a.startswith(b):
        return True
    if has_cjk(a) or has_cjk(b):
        if len(a) <= 24 and _overlap(a, b) >= 0.70:
            return True                      # 短句基本被新句包含 → 改写
        if len(a) >= 8 and _overlap(a, b) >= 0.85:
            return True                      # 长句整句重排 → 内容几乎一致
        return SequenceMatcher(None, a, b[:len(a) + 8], autojunk=False).ratio() >= 0.62
    # 英文：词级前缀（忽略标点差异）
    ta = re.findall(r"[\w']+", a)
    tb = re.findall(r"[\w']+", b)
    if ta and tb:
        short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if long_[:len(short)] == short:
            return True
    if max(len(a), len(b)) >= 40:
        sa, sb = set(re.findall(r"[\w']+", a)), set(re.findall(r"[\w']+", b))
        if sa and sb and len(sa & sb) / len(sa | sb) >= 0.60:
            return True
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= 0.72


def _tail_head_overlap(prev: str, new: str, limit: int = 8) -> int:
    """prev 的尾部与 new 的头部重叠了几个字，用于拼接时去重。"""
    for k in range(min(limit, len(prev), len(new)), 1, -1):
        if prev[-k:] == new[:k]:
            return k
    return 0


def join_caption(prev: str, new: str) -> str:
    """把同一说话人的两条连续片段拼成一句。

    * 新片段是旧片段的改写 → 取内容更全的那条（不产生重复前缀）
    * 新片段是续写 → 中英分别处理边界：中文直接相连，英文补一个空格
    """
    p = (prev or "").strip()
    n = (new or "").strip()
    if not p:
        return n
    if not n:
        return p
    if looks_like_revision(p, n):
        return n if len(_norm(n)) >= len(_norm(p)) else p
    cut = _tail_head_overlap(p, n)
    if cut:
        return p + n[cut:]
    if p[-1] in _SOFT_TAIL or (has_cjk(p[-1]) and has_cjk(n[0])):
        return p + n
    return p + " " + n


def should_merge(prev, name: str, text: str, now: float, gap: float,
                 limit: int = 160, revision_factor: float = 4.0) -> bool:
    """时间窗 + 发言人条件：要不要把新片段折进 prev 这条记录。

    prev 为 None、或 gap <= 0（关闭合并）时一律不折。首轮拉到的历史字幕带
    backlog 标记，时间不可信，也不折。

    同一句被 ASR 重发（改写）时时间窗放宽 ``revision_factor`` 倍：这类重发前后
    常差 2–4 秒，按续写窗口会漏掉，结果就是同一句话在列表里出现两次。
    """
    if not prev or gap <= 0:
        return False
    if prev.get("backlog"):
        return False
    if prev.get("name") != name:
        return False
    cur = (prev.get("text") or "").strip()
    incoming = (text or "").strip()
    if not cur or not incoming:
        return False
    if len(cur) >= limit:
        return False
    seen = prev.get("_seen") or 0.0
    age = now - seen
    if age < 0:
        return False
    if looks_like_revision(cur, incoming):
        return age <= gap * revision_factor
    if ends_sentence(cur):
        return False
    return age <= gap

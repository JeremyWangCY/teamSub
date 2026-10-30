# -*- coding: utf-8 -*-
"""
icons —— 统一的线性图标
=======================
所有图标画在 24×24 的网格上，按 4 倍超采样后缩小，得到与系统字体无关、
在任意主题色下都清晰的抗锯齿图标。颜色在调用时传入，按 (名称, 尺寸, 颜色)
缓存 PhotoImage，避免重绘按钮时反复生成。

新增图标：在 _SHAPES 里加一个函数，签名 (d, p, color, w)。
  d     ImageDraw
  p     把网格坐标换算成像素坐标的函数，p(x, y) -> (px, py)
  w     线宽（像素，已乘超采样倍数）
"""
import math
from PIL import Image, ImageDraw, ImageTk

SS = 4                      # 超采样倍数
STROKE = 1.75               # 网格单位下的线宽


def _line(d, p, pts, color, w):
    pts = [p(*xy) for xy in pts]
    d.line(pts, fill=color, width=w, joint="curve")
    r = w / 2
    for x, y in (pts[0], pts[-1]):          # 圆头端点
        d.ellipse((x - r, y - r, x + r, y + r), fill=color)


def _circle(d, p, cx, cy, r, color, w=None, fill=False):
    x1, y1 = p(cx - r, cy - r)
    x2, y2 = p(cx + r, cy + r)
    if fill:
        d.ellipse((x1, y1, x2, y2), fill=color)
    else:
        d.ellipse((x1, y1, x2, y2), outline=color, width=w)


def _arc(d, p, cx, cy, r, start, end, color, w):
    x1, y1 = p(cx - r, cy - r)
    x2, y2 = p(cx + r, cy + r)
    d.arc((x1, y1, x2, y2), start, end, fill=color, width=w)
    rr = w / 2
    for ang in (start, end):                # 圆头端点
        a = math.radians(ang)
        ex, ey = p(cx + r * math.cos(a), cy + r * math.sin(a))
        d.ellipse((ex - rr, ey - rr, ex + rr, ey + rr), fill=color)


def _rrect(d, p, x1, y1, x2, y2, r, color, w=None, fill=False):
    a, b = p(x1, y1)
    c, e = p(x2, y2)
    rad = r * (c - a) / max(.001, x2 - x1)
    if fill:
        d.rounded_rectangle((a, b, c, e), radius=rad, fill=color)
    else:
        d.rounded_rectangle((a, b, c, e), radius=rad, outline=color, width=w)


# ------------------------------------------------------------------ 图形
def _live(d, p, c, w):
    _circle(d, p, 12, 12, 2.2, c, fill=True)
    _arc(d, p, 12, 12, 5.5, 135, 225, c, w)
    _arc(d, p, 12, 12, 5.5, -45, 45, c, w)
    _arc(d, p, 12, 12, 9.2, 140, 220, c, w)
    _arc(d, p, 12, 12, 9.2, -40, 40, c, w)


def _history(d, p, c, w):
    _arc(d, p, 12, 12, 8.5, -150, 160, c, w)
    _line(d, p, [(4.0, 8.2), (3.6, 12.0)], c, w)      # 回转箭头的尾巴
    _line(d, p, [(1.6, 9.8), (3.6, 12.0), (6.0, 10.4)], c, w)
    _line(d, p, [(12, 7.5), (12, 12), (15, 14)], c, w)


def _settings(d, p, c, w):
    for y, k in ((6.5, 15), (12, 8), (17.5, 13)):
        _line(d, p, [(4, y), (20, y)], c, w)
        _circle(d, p, k, y, 2.3, c, fill=True)


def _help(d, p, c, w):
    _circle(d, p, 12, 12, 8.6, c, w)
    _arc(d, p, 12, 10, 2.7, 190, 400, c, w)
    _line(d, p, [(12, 12.7), (12, 13.6)], c, w)
    _circle(d, p, 12, 16.6, 1.05, c, fill=True)


def _search(d, p, c, w):
    _circle(d, p, 10.5, 10.5, 6, c, w)
    _line(d, p, [(15, 15), (19.5, 19.5)], c, w)


def _export(d, p, c, w):
    _line(d, p, [(12, 4), (12, 14.5)], c, w)
    _line(d, p, [(7.5, 10.5), (12, 15), (16.5, 10.5)], c, w)
    _line(d, p, [(4.5, 15.5), (4.5, 19.5), (19.5, 19.5), (19.5, 15.5)], c, w)


def _more(d, p, c, w):
    for x in (5.5, 12, 18.5):
        _circle(d, p, x, 12, 1.6, c, fill=True)


def _translate(d, p, c, w):
    # 「文A」：左侧方块字示意，右侧拉丁字母 A
    _line(d, p, [(3, 6), (12, 6)], c, w)
    _line(d, p, [(7.5, 3.5), (7.5, 6)], c, w)
    _line(d, p, [(10, 6), (8.6, 10.5), (4, 15)], c, w)
    _line(d, p, [(5.6, 8.8), (11, 14)], c, w)
    _line(d, p, [(12.5, 20.5), (16.5, 11), (20.5, 20.5)], c, w)
    _line(d, p, [(14, 17.4), (19, 17.4)], c, w)


def _pause(d, p, c, w):
    _rrect(d, p, 6.5, 5, 9.8, 19, 1.2, c, fill=True)
    _rrect(d, p, 14.2, 5, 17.5, 19, 1.2, c, fill=True)


def _play(d, p, c, w):
    pts = [p(7.5, 5), p(19, 12), p(7.5, 19)]
    d.polygon(pts, fill=c)
    d.line(pts + [pts[0]], fill=c, width=w, joint="curve")


def _plus(d, p, c, w):
    _line(d, p, [(12, 5), (12, 19)], c, w)
    _line(d, p, [(5, 12), (19, 12)], c, w)


def _stop(d, p, c, w):
    _rrect(d, p, 6, 6, 18, 18, 2.5, c, fill=True)


def _trash(d, p, c, w):
    _line(d, p, [(4, 6.5), (20, 6.5)], c, w)
    _line(d, p, [(9, 6.5), (9, 4), (15, 4), (15, 6.5)], c, w)
    _line(d, p, [(6, 6.5), (7, 20), (17, 20), (18, 6.5)], c, w)
    _line(d, p, [(10.2, 10.5), (10.2, 16)], c, w)
    _line(d, p, [(13.8, 10.5), (13.8, 16)], c, w)


def _folder(d, p, c, w):
    _line(d, p, [(3.5, 18.5), (3.5, 5.5), (9.5, 5.5), (11.5, 8), (20.5, 8),
                 (20.5, 18.5), (3.5, 18.5)], c, w)


def _refresh(d, p, c, w):
    _arc(d, p, 12, 12, 7.5, -160, 120, c, w)
    _line(d, p, [(4.3, 4.6), (4.6, 9.4), (9.3, 8.8)], c, w)


def _back(d, p, c, w):
    _line(d, p, [(14.5, 5.5), (8, 12), (14.5, 18.5)], c, w)


def _chevron_down(d, p, c, w):
    _line(d, p, [(6.5, 9.5), (12, 15), (17.5, 9.5)], c, w)


def _arrow_down(d, p, c, w):
    _line(d, p, [(12, 5), (12, 19)], c, w)
    _line(d, p, [(6.5, 13.5), (12, 19), (17.5, 13.5)], c, w)


def _clear(d, p, c, w):
    # 橡皮擦：斜放的圆角块 + 底线
    pts = [p(9, 19.5), p(4.2, 14.7), p(13.5, 5.4), p(20.3, 12.2), p(12.9, 19.5)]
    d.line(pts + [pts[0]], fill=c, width=w, joint="curve")
    _line(d, p, [(8.6, 10.3), (15.4, 17.1)], c, w)
    _line(d, p, [(12.9, 19.5), (20, 19.5)], c, w)


def _text_size(d, p, c, w):
    _line(d, p, [(3, 19), (8.5, 5), (14, 19)], c, w)
    _line(d, p, [(5.2, 14), (11.8, 14)], c, w)
    _line(d, p, [(15.5, 12), (21, 12)], c, w)
    _line(d, p, [(18.25, 9.2), (18.25, 19)], c, w)


def _captions(d, p, c, w):
    _rrect(d, p, 2.5, 5, 21.5, 19, 3, c, w)
    _line(d, p, [(6.5, 11), (14, 11)], c, w)
    _line(d, p, [(16.5, 11), (17.5, 11)], c, w)
    _line(d, p, [(6.5, 15), (8, 15)], c, w)
    _line(d, p, [(10.5, 15), (17.5, 15)], c, w)


def _float(d, p, c, w):
    _rrect(d, p, 3, 4.5, 21, 19.5, 2.5, c, w)
    _rrect(d, p, 11.5, 12, 18.5, 17, 1.2, c, fill=True)


def _check(d, p, c, w):
    _line(d, p, [(5, 12.5), (10, 17.5), (19, 7)], c, w)


def _close(d, p, c, w):
    _line(d, p, [(6, 6), (18, 18)], c, w)
    _line(d, p, [(18, 6), (6, 18)], c, w)


def _edit(d, p, c, w):
    _line(d, p, [(4.5, 19.5), (5.2, 15.6), (15.8, 5), (19, 8.2), (8.4, 18.8),
                 (4.5, 19.5)], c, w)
    _line(d, p, [(13.5, 7.3), (16.7, 10.5)], c, w)


def _link(d, p, c, w):
    # 续接：两节链环
    _rrect(d, p, 2.5, 8.5, 13, 15.5, 3.5, c, w)
    _rrect(d, p, 11, 8.5, 21.5, 15.5, 3.5, c, w)


def _info(d, p, c, w):
    _circle(d, p, 12, 12, 8.6, c, w)
    _line(d, p, [(12, 11), (12, 16.5)], c, w)
    _circle(d, p, 12, 7.8, 1.05, c, fill=True)


_SHAPES = {
    "live": _live, "history": _history, "settings": _settings, "help": _help,
    "search": _search, "export": _export, "more": _more, "translate": _translate,
    "pause": _pause, "play": _play, "plus": _plus, "stop": _stop, "trash": _trash,
    "folder": _folder, "refresh": _refresh, "back": _back,
    "chevron-down": _chevron_down, "arrow-down": _arrow_down, "clear": _clear,
    "text-size": _text_size, "captions": _captions, "float": _float,
    "check": _check, "close": _close, "edit": _edit, "link": _link, "info": _info,
}


def names():
    return sorted(_SHAPES)


def render(name: str, size: int, color: str, stroke: float = STROKE) -> Image.Image:
    """返回 RGBA 图标（size×size）。未知名称抛 KeyError，便于尽早发现拼写错误。"""
    shape = _SHAPES[name]
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    k = big / 24.0

    def p(x, y):
        return x * k, y * k

    shape(d, p, color, max(1, round(stroke * k)))
    return img.resize((size, size), Image.Resampling.LANCZOS)


def photo(name: str, size: int, color: str, master):
    """PhotoImage 按 Tk 根窗口缓存：图片属于创建它的解释器，换根窗口必须重建。"""
    import ui_kit
    size = max(1, ui_kit.px(size))          # 逻辑尺寸 → 物理像素
    root = master._root()
    cache = root.__dict__.setdefault("_icon_cache", {})
    key = (name, size, color.lower())
    img = cache.get(key)
    if img is None:
        img = cache[key] = ImageTk.PhotoImage(render(name, size, color), master=root)
    return img

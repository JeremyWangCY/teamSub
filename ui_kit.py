# -*- coding: utf-8 -*-
"""
ui_kit —— TeamSub 的视觉层组件
===============================
设计取向：暖灰底色、圆角与低饱和蓝色；按钮仅用蓝、灰、柔红三类。

tkinter 原生没有圆角，这里用 Canvas 画形状、再用 window item 承载内容。
要点：内容框按 radius 内缩，使内容方角的四个角落在圆角形状内部，视觉上无缝。
"""
import tkinter as tk
import tkinter.font as tkfont
from PIL import Image, ImageDraw, ImageTk
import icons
import math
import re


# ------------------------------------------------------------------ 主题引擎
_LIGHT = {
    "N0": "#FCFCFB", "N50": "#F5F5F3", "N100": "#EDEDEA", "N150": "#E5E5E1",
    "N200": "#DADAD6", "N300": "#C4C4BF", "LINE": "#E8E8E4",
    "LINE_D": "#DDDDD8", "SHADOW": "#E2E2DE",
    "TEXT": "#292A27", "SUB": "#666861", "T3": "#74766F", "DISABLED": "#A3A59E",
    "FOCUS_OUTER": "#D3D4CE", "FOCUS_INNER": "#ACAEA4",
    "KNOB": "#FCFCFB",
    "A600": "#345CB0", "A500": "#4168BA", "A100": "#DFE7F7", "A50": "#EBF0FA",
    "FLOAT_BG": "#FCFCFB", "FLOAT_LINE": "#E3E8EF", "FLOAT_TEXT": "#222C3A",
    "FLOAT_MUTED": "#778393", "FLOAT_ACCENT": "#285BC5",
    "FLOAT_BORDER": "#9EAFBF",
    "BUTTON_RED": "#9C5755",
    "GREEN": "#1C843A", "GREEN_F": "#34C759", "GREEN_S": "#E8F9ED",
    "AMBER": "#B25000", "AMBER_F": "#FF9F0A", "AMBER_S": "#FFF4E0",
    "RED": "#D70015", "RED_F": "#FF3B30", "RED_S": "#FFECEB",
}
# 暗色阶：文字强调色提亮，实心按钮保留足够的白字对比度。
_DARK = {
    "N0": "#1A1A19", "N50": "#151515", "N100": "#20201E", "N150": "#292927",
    "N200": "#3B3B37", "N300": "#55554F", "LINE": "#292927",
    "LINE_D": "#363632", "SHADOW": "#0B0B0A",
    "TEXT": "#E9E9E4", "SUB": "#B6B8AE", "T3": "#96998D", "DISABLED": "#72756A",
    "FOCUS_OUTER": "#383A32", "FOCUS_INNER": "#626759",
    "KNOB": "#E9E9E4",
    "A600": "#A9B8E8", "A500": "#586DB6", "A100": "#354267", "A50": "#2D3448",
    "FLOAT_BG": "#1A1A19", "FLOAT_LINE": "#323631", "FLOAT_TEXT": "#E9E9E4",
    "FLOAT_MUTED": "#B0BBC9", "FLOAT_ACCENT": "#A7C0FF",
    "FLOAT_BORDER": "#64758A",
    "BUTTON_RED": "#D99C96",
    "GREEN": "#4CD964", "GREEN_F": "#4CD964", "GREEN_S": "#1E3A26",
    "AMBER": "#FFB340", "AMBER_F": "#FFB340", "AMBER_S": "#3E2F16",
    "RED": "#FF6B60", "RED_F": "#FF6B60", "RED_S": "#3E2320",
}

_SPEAKERS = {
    "light": ["#2B6CB8", "#C04A3A", "#2A816A", "#8A5ABF", "#9C6619",
              "#2D789C", "#AF5487", "#527D28", "#6A6AB2", "#227B88",
              "#8A6A4A", "#577687"],
    # 暗底变体：整体提亮约 18%，保持同明度差
    "dark": ["#5B9BE0", "#E58A7A", "#5FBF9E", "#B394E8", "#D2A45A",
             "#5FA8CC", "#D68BB0", "#8CBF62", "#9A9AD8", "#55B5C2",
             "#C2A184", "#8AA6B5"],
}


class _TokenMeta(type):
    """让 `C.XXX` 在运行时按当前主题取值，旧写法全部保持不变。"""

    _state = {"active": "light"}

    def __getattr__(cls, name):
        if name.startswith("_"):
            raise AttributeError(name)
        theme = _THEMES.get(cls._state["active"], _THEMES["light"])
        if name in theme:
            return theme[name]
        raise AttributeError(f"未知令牌 C.{name}")

    def __setattr__(cls, name, value):
        raise AttributeError("令牌只读，请走 set_theme()")


class C(metaclass=_TokenMeta):
    """唯一取色点。`C.XXX` 按当前主题解析，禁止散落裸色值。"""


_THEMES = {"light": _LIGHT, "dark": _DARK}


def set_theme(name: str):
    """切换主题；返回是否发生了变化（调用方据此重建界面）。"""
    if name not in _THEMES or name == _TokenMeta._state["active"]:
        return False
    _TokenMeta._state["active"] = name
    # Tk 仅支持 normal/bold；暗色使用常规字重，避免伪造不支持的 360 字重。
    global F_TITLE, F_HEAD, F_NAME
    weight = "normal" if name == "dark" else "bold"
    F_TITLE, F_HEAD, F_NAME = ((FAMILY, 17, weight), (FAMILY, 11, weight),
                              (FAMILY, 10, weight))
    return True


def theme() -> str:
    return _TokenMeta._state["active"]


def speaker_colors() -> list:
    """当前主题的说话人色板（白/暗底两套，头像与正文同源）。"""
    return _SPEAKERS.get(theme(), _SPEAKERS["light"])


def _blend(c1: str, c2: str, t: float) -> str:
    """线性插值两色，用于动效过渡。"""
    a, b = c1.lstrip("#"), c2.lstrip("#")
    return "#%02x%02x%02x" % tuple(
        round(int(a[i:i + 2], 16) + (int(b[i:i + 2], 16) - int(a[i:i + 2], 16)) * t)
        for i in (0, 2, 4))


def lighten(hex_color: str, amount: float = 0.88) -> str:
    """与"面"混合得到浅底色（浅色主题混白，暗色主题混暗面，头像底用）。"""
    base = (C.N0 if theme() == "light" else C.N100).lstrip("#")
    h = hex_color.lstrip("#")
    return "#%02x%02x%02x" % tuple(
        round(int(h[i:i + 2], 16) + (int(base[i:i + 2], 16) - int(h[i:i + 2], 16)) * amount)
        for i in (0, 2, 4))

FAMILY = "Microsoft YaHei UI"
F_TITLE = (FAMILY, 17, "bold")
F_HEAD  = (FAMILY, 11, "bold")
F_UI    = (FAMILY, 10)
F_SMALL = (FAMILY, 10)
F_MINI  = (FAMILY, 9)
F_NAME  = (FAMILY, 10, "bold")
F_BODY  = (FAMILY, 12)
F_TIME  = ("Consolas", 9)

# ------------------------------------------------------------------ DPI 缩放
# 界面代码一律写「逻辑像素」（100% 缩放下的像素）。进程声明 DPI 感知后，
# Tk 拿到的是物理像素：字体按磅值自动放大，其余写死的像素不会。这里在
# Tk 这一层统一换算一次，业务代码不用到处乘系数：
#   · pack/grid/place 的边距与位置、Frame/Toplevel 宽高、wraplength、
#     Text 的行距与缩进 —— 传入时乘 S；
#   · Canvas 整体工作在逻辑坐标：画图、coords、事件坐标、winfo 尺寸都换算；
#   · 字体的 measure/metrics 返回逻辑像素，和上面两类数值同一把尺子；
#   · PIL 位图（圆角、图标）按物理尺寸渲染，保证清晰。
# S == 1 时什么都不装，行为与旧版完全一致。
S = 1.0


def px(v):
    """逻辑像素 → 物理像素（整数），用于窗口几何等只认整数的场合。"""
    return int(round(v * S))


def lpx(v):
    """物理像素（winfo_* 读出来的）→ 逻辑像素。"""
    return v / S


def _sc(v):
    """把一个像素量乘 S；元组（如 padx=(0, 8)）逐项处理，非数字原样返回。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v * S
    if isinstance(v, (tuple, list)):
        return type(v)(_sc(i) for i in v)
    if isinstance(v, str):
        parts = v.split()
        try:
            return " ".join(str(float(p) * S) for p in parts)
        except ValueError:
            return v
    return v


_GEOM_KEYS = {"padx", "pady", "ipadx", "ipady"}
_PLACE_KEYS = {"x", "y", "width", "height"}
_SIZE_WIDGETS = {"frame", "toplevel", "labelframe", "canvas"}
_WIDGET_KEYS = {"wraplength", "padx", "pady", "spacing1", "spacing2", "spacing3"}
_CANVAS_KEYS = {"scrollregion", "xscrollincrement", "yscrollincrement"}
_TAG_KEYS = {"spacing1", "spacing2", "spacing3", "lmargin1", "lmargin2", "rmargin", "offset"}
_ITEM_KEYS = {"width", "height", "activewidth", "disabledwidth"}


def _scaled(opts, keys):
    if not opts:
        return opts
    return {k: (_sc(v) if k.rstrip("_") in keys else v) for k, v in opts.items()}


def _widget_keys(widget):
    keys = set(_WIDGET_KEYS)
    name = getattr(widget, "widgetName", "")
    if name in _SIZE_WIDGETS:
        keys |= {"width", "height"}
    if name == "canvas":
        keys |= _CANVAS_KEYS
    return keys


def install_scale(scale: float):
    """按缩放系数挂上换算层；只能在建任何控件之前调用一次。"""
    global S
    scale = round(float(scale), 3)
    if scale <= 0 or abs(scale - 1.0) < 0.01 or S != 1.0:
        return
    S = scale
    Misc, Canvas = tk.Misc, tk.Canvas

    def wrap_geom(orig, keys):
        def f(self, cnf={}, **kw):
            if isinstance(cnf, dict) and cnf:
                kw = {**cnf, **kw}
                cnf = {}
            return orig(self, cnf, **_scaled(kw, keys))
        return f

    for cls, names, keys in ((tk.Pack, ("pack", "pack_configure"), _GEOM_KEYS),
                             (tk.Grid, ("grid", "grid_configure"), _GEOM_KEYS),
                             (tk.Place, ("place", "place_configure"), _GEOM_KEYS | _PLACE_KEYS)):
        orig = getattr(cls, names[1])
        for n in names:
            setattr(cls, n, wrap_geom(orig, keys))

    orig_grid_conf = Misc._grid_configure

    def grid_conf(self, command, index, cnf, kw):
        if isinstance(cnf, dict):
            cnf = _scaled(cnf, {"minsize", "pad"})
        return orig_grid_conf(self, command, index, cnf, _scaled(kw, {"minsize", "pad"}))
    Misc._grid_configure = grid_conf

    orig_init = tk.BaseWidget.__init__

    def base_init(self, master, widgetName, cnf={}, kw={}, extra=()):
        self.widgetName = widgetName
        keys = _widget_keys(self)
        return orig_init(self, master, widgetName, _scaled(dict(cnf), keys),
                         _scaled(dict(kw), keys), extra)
    tk.BaseWidget.__init__ = base_init

    orig_configure = Misc._configure

    def configure(self, cmd, cnf, kw):
        if isinstance(cnf, str) or (not cnf and not kw):
            return orig_configure(self, cmd, cnf, kw)
        if cmd == "configure":
            keys = _widget_keys(self)
        elif isinstance(cmd, tuple) and cmd[0] == "itemconfigure" and isinstance(self, Canvas):
            keys = _ITEM_KEYS
        elif isinstance(cmd, tuple) and cmd[:2] == ("tag", "configure"):
            keys = _TAG_KEYS
        else:
            return orig_configure(self, cmd, cnf, kw)
        cnf = _scaled(dict(cnf), keys) if isinstance(cnf, dict) else cnf
        return orig_configure(self, cmd, cnf, _scaled(kw, keys))
    Misc._configure = configure

    orig_cget = Misc.cget

    def cget(self, key):
        value = orig_cget(self, key)
        if isinstance(self, Canvas) and key in ("width", "height"):
            return float(value) / S
        return value
    Misc.cget = Misc.__getitem__ = cget

    # ---- Canvas：逻辑坐标 ----
    def flat_scale(args):
        return tuple(a * S if isinstance(a, (int, float)) and not isinstance(a, bool) else a
                     for a in tk._flatten(args))

    orig_create = Canvas._create

    def create(self, itemType, args, kw):
        args = tk._flatten(args)
        cnf = {}
        if args and isinstance(args[-1], dict):
            args, cnf = args[:-1], args[-1]
        return orig_create(self, itemType, flat_scale(args),
                           _scaled({**cnf, **kw}, _ITEM_KEYS))
    Canvas._create = create

    orig_coords = Canvas.coords

    def coords(self, *args):
        if len(args) > 1:
            args = (args[0],) + flat_scale(args[1:])
        return [c / S for c in orig_coords(self, *args)]
    Canvas.coords = coords

    orig_move, orig_moveto = Canvas.move, Canvas.moveto
    Canvas.move = lambda self, *a: orig_move(self, *(a[:1] + flat_scale(a[1:])))
    Canvas.moveto = lambda self, tag, x="", y="": orig_moveto(
        self, tag, _sc(x) if x != "" else x, _sc(y) if y != "" else y)

    orig_bbox = Canvas.bbox

    def bbox(self, *args):
        box = orig_bbox(self, *args)
        return tuple(v / S for v in box) if box else box
    Canvas.bbox = bbox

    for name in ("canvasx", "canvasy"):
        def conv(self, screen, gridspacing=None, _orig=getattr(Canvas, name)):
            return _orig(self, screen * S, gridspacing) / S
        setattr(Canvas, name, conv)

    for name in ("find_overlapping", "find_enclosed", "find_closest"):
        def find(self, *a, _orig=getattr(Canvas, name)):
            return _orig(self, *flat_scale(a))
        setattr(Canvas, name, find)

    for name in ("winfo_width", "winfo_height", "winfo_reqwidth", "winfo_reqheight"):
        def size(self, _orig=getattr(Misc, name)):
            return _orig(self) / S
        setattr(Canvas, name, size)

    orig_sub = Misc._substitute

    def substitute(self, *args):
        res = orig_sub(self, *args)
        e = res[0] if isinstance(res, tuple) and res else None
        if isinstance(e, tk.Event):
            for attr in ("x", "y", "width", "height"):
                v = getattr(e, attr, None)
                if isinstance(v, int):
                    setattr(e, attr, v / S)
        return res
    Canvas._substitute = substitute

    # ---- 字体量尺：逻辑像素 ----
    orig_measure, orig_metrics = tkfont.Font.measure, tkfont.Font.metrics

    def measure(self, text, displayof=None):
        return round(orig_measure(self, text, displayof) / S)

    def metrics(self, *options, **kw):
        res = orig_metrics(self, *options, **kw)
        if isinstance(res, dict):
            return {k: (round(v / S) if k != "fixed" else v) for k, v in res.items()}
        return res if options and options[0] == "fixed" else round(res / S)
    tkfont.Font.measure, tkfont.Font.metrics = measure, metrics


# 形状标尺
R_CARD  = 16     # 大卡（主内容、设置、确认）
R_POP   = 12     # 弹层
R_FIELD = 8      # 输入框、列表、分段
H_PILL  = 36     # 胶囊默认高度


def style_menu(menu):
    """把 tk.Menu 涂成当前主题（原生菜单不吃 bg 配置时的兜底）。"""
    try:
        menu.configure(bg=C.N0, fg=C.TEXT, activebackground=C.A50,
                       activeforeground=C.A600, bd=0, relief="flat",
                       font=F_SMALL)
    except Exception:
        pass


def attach_text_menu(text, extra=None, on_menu=None):
    """给只读 Text 挂右键菜单：复制选中 / 全选（可选再追加条目）。

    为什么要它：正文默认只读（state="disabled"），Ctrl+C 其实一直是能用的，
    但**右键什么都不出**——用户的第一反应就是右键，摸不到菜单就等于不能复制。
    这里只提供复制相关的几项，不改只读属性，也不接管左键（选区仍然照常工作）。

    extra: [(label, callback)]，追加在分隔线之后（例如「修正译文」）。
    on_menu: 菜单弹出前调用，可用来按当下状态启用/禁用条目。
    """
    menu = tk.Menu(text, tearoff=0)
    style_menu(menu)

    def selection():
        try:
            return text.get("sel.first", "sel.last")
        except tk.TclError:
            return ""

    def copy_selection():
        value = selection()
        if not value:
            return
        text.clipboard_clear()
        text.clipboard_append(value)

    def copy_all():
        try:
            value = text.get("1.0", "end-1c")
        except tk.TclError:
            return
        if value:
            text.clipboard_clear()
            text.clipboard_append(value)

    def select_all():
        # 只读态也能建选区，所以「全选」后按 Ctrl+C 或再右键复制都行
        text.tag_add("sel", "1.0", "end-1c")

    state = {"sel": ""}

    def refresh():
        state["sel"] = selection()
        menu.entryconfigure(0, state="normal" if state["sel"] else "disabled")
        if on_menu is not None:
            on_menu(menu)

    menu.add_command(label="复制", command=copy_selection)
    menu.add_command(label="全选", command=select_all)
    menu.add_command(label="复制全部", command=copy_all)
    if extra:
        menu.add_separator()
        for label, callback in extra:
            menu.add_command(label=label, command=callback)

    def popup(event):
        refresh()
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    text.bind("<Button-3>", popup, add="+")
    text._copy_menu = menu          # 便于测试与外部追加条目
    return menu


def _parent_bg(w) -> str:
    try:
        return w.cget("bg")
    except Exception:
        return C.N50


def round_rect(cv: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """Antialiased rounded geometry, with references scoped to the owning canvas."""
    # 入参是逻辑坐标；位图按物理像素画，高缩放下边缘依旧锐利
    scale = 3
    # 未布局的画布只有 1px（缩放后不足 1 个逻辑像素），收成零尺寸而不是倒置
    x2, y2 = max(x1, x2), max(y1, y2)
    x1, y1, x2, y2, r = x1 * S, y1 * S, x2 * S, y2 * S, r * S
    left, top = math.floor(x1), math.floor(y1)
    width, height = max(1, math.ceil(x2)-left+1), max(1, math.ceil(y2)-top+1)
    shape = Image.new('RGBA', (width*scale, height*scale))
    draw = ImageDraw.Draw(shape)
    box = ((x1-left)*scale, (y1-top)*scale, (x2-left)*scale, (y2-top)*scale)
    fill, outline = kw.pop('fill', None), kw.pop('outline', None)
    line_width = max(1, round(kw.pop('width', 1)*S*scale))
    draw.rounded_rectangle(box, radius=max(0,r)*scale, fill=fill or None, outline=outline or None, width=line_width)
    photo = ImageTk.PhotoImage(shape.resize((width,height), Image.Resampling.LANCZOS), master=cv)
    refs = getattr(cv, '_round_images', {})
    for item in list(refs):
        if not cv.type(item):
            del refs[item]
    item = cv.create_image(left / S, top / S, image=photo, anchor='nw', **kw)
    refs[item] = photo
    cv._round_images = refs
    return item


def ring_rect(cv: tk.Canvas, x1, y1, x2, y2, r, fill, active,
              ring=None, tags=None):
    """无描边底色；聚焦时用两层内嵌中性色模拟双环阴影，完整留在画布内。"""
    kw = {"tags": tags} if tags else {}
    round_rect(cv, x1, y1, x2, y2, r, fill=fill, outline=fill, width=1, **kw)
    if active:
        round_rect(cv, x1 + .75, y1 + .75, x2 - .75, y2 - .75,
                   max(1.0, r - .75), fill="", outline=C.FOCUS_OUTER,
                   width=1.5, **kw)
        round_rect(cv, x1 + 2, y1 + 2, x2 - 2, y2 - 2,
                   max(1.0, r - 2), fill="", outline=ring or C.FOCUS_INNER,
                   width=.75, **kw)


# ------------------------------------------------------------------ 圆角卡片
class Card(tk.Frame):
    """圆角白卡；内容放 self.body。"""

    def __init__(self, master, radius=16, fill=None, border=None,
                 padding=None, bg=None, autosize=False):
        # 默认值必须在调用时解析：类定义时的 C.N0/C.LINE 绑定的是导入那一刻
        # 的主题（浅色），深色模式下会残留白底白发丝边
        fill = C.N0 if fill is None else fill
        border = fill if border is None else border
        outer = bg or _parent_bg(master)
        super().__init__(master, bg=outer, highlightthickness=0, bd=0)
        self.radius, self.fill, self.border = radius, fill, border
        self.pad = radius if padding is None else padding
        cv_kw = dict(bg=outer, highlightthickness=0, bd=0)
        if autosize:
            cv_kw["height"] = 1          # 见 _autosize：避免 Canvas 默认 265px
        self.cv = tk.Canvas(self, **cv_kw)
        self.cv.pack(fill="both", expand=True)
        inner_pad = self.pad
        self.body = tk.Frame(self.cv, bg=fill)
        self._win = self.cv.create_window(inner_pad, inner_pad, anchor="nw", window=self.body)
        self.cv.bind("<Configure>", self._resize)
        self.autosize = autosize

    def fit(self):
        """内容填完后调用：把卡片高度收紧到内容高度。

        Canvas 默认请求 265px，内容少的卡片会被撑开；而 body 的 <Configure>
        只在"分配尺寸变化"时触发，内容增长不触发，所以必须显式算一次。
        """
        self.update_idletasks()
        h = lpx(self.body.winfo_reqheight()) + 2 * self.pad
        self.cv.configure(height=h)

    def _resize(self, e):
        p = self.pad
        self.cv.itemconfigure(self._win, width=max(1, e.width - 2 * p),
                              height=max(1, e.height - 2 * p))
        self.cv.delete("bgshape")
        round_rect(self.cv, 0.5, 0.5, e.width - 0.5, e.height - 0.5,
                   self.radius, fill=self.fill, outline=self.border, tags="bgshape")
        self.cv.tag_lower("bgshape")


# ------------------------------------------------------------------ 悬停提示
class Tooltip:
    """延时出现的小提示；纯图标按钮靠它说明用途。text 可为可调用对象（取实时文案）。"""

    DELAY = 450

    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self._after = None
        self._win = None
        widget.bind("<Enter>", self._queue, add="+")
        for ev in ("<Leave>", "<ButtonPress>", "<Destroy>"):
            widget.bind(ev, self._hide, add="+")

    def _queue(self, _=None):
        self._hide()
        self._after = self.widget.after(self.DELAY, self._show)

    def _show(self):
        self._after = None
        text = self.text() if callable(self.text) else self.text
        if not text or not self.widget.winfo_exists():
            return
        win = self._win = tk.Toplevel(self.widget)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(win, text=text, bg=C.TEXT, fg=C.N0, font=F_MINI,
                 padx=8, pady=4, justify="left").pack()
        win.update_idletasks()
        # 屏幕定位全用物理像素（Canvas 的 winfo_width 已换成逻辑值，这里绕开）
        ww, wh = tk.Misc.winfo_width(self.widget), tk.Misc.winfo_height(self.widget)
        x = self.widget.winfo_rootx() + (ww - win.winfo_reqwidth()) // 2
        y = self.widget.winfo_rooty() + wh + px(6)
        x = max(0, min(x, self.widget.winfo_screenwidth() - win.winfo_reqwidth()))
        win.geometry(f"+{x}+{y}")

    def _hide(self, _=None):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None
        if self._win is not None:
            try:
                self._win.destroy()
            except tk.TclError:
                pass
            self._win = None


# ------------------------------------------------------------------ 胶囊按钮
class Pill(tk.Canvas):
    """圆角按钮；蓝色强调、灰色常规、柔红色谨慎操作。"""

    def __init__(self, master, text, command=None, kind="ghost", height=H_PILL,
                 padx=14, bg=None, font=F_SMALL, align="center", icon=None,
                 icon_size=16, tooltip=None):
        self.bgcol = bg or _parent_bg(master)
        self.icon, self.icon_size = icon, icon_size
        self.font = font
        self.align = align
        self.kind = kind
        self.command = command
        self.padx, self.h = padx, height
        self.text = text
        super().__init__(master, height=height, bg=self.bgcol,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._hover = False
        self._focused = False
        self._pressed = False
        self._resize_to_text()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(takefocus=True)
        self.bind("<Return>", self._activate)
        self.bind("<space>", self._activate)
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        if self.align == "left":
            self.bind("<Configure>", lambda e: self._paint())
        if tooltip or (icon and not text):
            Tooltip(self, tooltip or "")

    # -- 内部 --
    def _measure(self, s):
        return tkfont.Font(family=self.font[0], size=self.font[1],
                           weight=self.font[2] if len(self.font) > 2 else "normal").measure(s)

    def _colors(self):
        if self.kind == "primary":
            fill = _blend(C.A500, C.TEXT, .16 if self._pressed else .08) if self._hover else C.A500
            return fill, "#FFFFFF", ""
        if self.kind == "tab":
            return (C.N150 if self._hover else self.bgcol), C.SUB, ""
        if self.kind == "selected":
            return C.A50, C.A600, ""
        if self.kind == "danger":
            return lighten(C.BUTTON_RED, .84 if self._hover else .92), C.BUTTON_RED, ""
        if self.kind == "secondary":
            return (C.N150 if self._hover else C.N100), C.TEXT, ""
        if self.kind == "tint":
            # 文字使用适配深浅主题的强调色。
            return (C.A50 if not self._hover else C.A100), C.A600, ""
        fill = C.N150 if self._hover else self.bgcol
        return fill, (C.TEXT if self._hover else C.SUB), ("" if self._hover else "")

    def _paint(self):
        w = self.winfo_width() if self.align == "left" and self.winfo_width() > 1 else int(self["width"])
        self.delete("all")
        fill, fg, outline = self._colors()
        if fill != self.bgcol:
            round_rect(self, 0.5, 0.5, w - 0.5, self.h - 0.5, min(10, self.h / 2),
                       fill=fill, outline=outline or fill)
        if self._focused:
            round_rect(self, 2, 2, w - 2, self.h - 2, min(8, self.h / 2 - 2),
                       outline=C.A600, width=1)
        icon_w = self._icon_w()
        x = self.padx if self.align == "left" else (w - icon_w - self._measure(self.text)) / 2
        name = self._icon_name()
        if name:
            img = icons.photo(name, self.icon_size, fg, master=self)
            self.create_image(round(x), round((self.h - self.icon_size) / 2),
                              image=img, anchor="nw")
            x += icon_w
        if self.text:
            self.create_text(x, self.h / 2 + 0.5, text=self.text, anchor="w",
                             fill=fg, font=self.font)

    def _icon_name(self):
        # icon 可以是按文案切换的映射，例如 {"暂停采集": "pause", "继续采集": "play"}
        if isinstance(self.icon, dict):
            return self.icon.get(self.text)
        return self.icon

    def _icon_w(self):
        if not self._icon_name():
            return 0
        return self.icon_size + (8 if self.text else 0)

    def _resize_to_text(self):
        w = self._icon_w() + self._measure(self.text) + self.padx * 2
        self.configure(width=w)
        self._paint()

    # -- 对外 --
    def set_text(self, text):
        self.text = text
        self._resize_to_text()

    def set_kind(self, kind):
        self.kind = kind
        self._paint()

    def set_icon(self, icon):
        self.icon = icon
        self._resize_to_text()

    # -- 事件 --
    def _on_enter(self, _):
        self._hover = True
        self._paint()

    def _on_leave(self, _):
        self._hover = False
        self._paint()

    def _on_press(self, _):
        self.focus_set()
        self._pressed = True
        self._paint()

    def _on_release(self, e):
        fire = self._pressed and 0 <= e.x < self.winfo_width() and 0 <= e.y < self.h
        self._pressed = False
        self._paint()
        if fire:
            self._activate()

    def _activate(self, _=None):
        if self.command:
            self.command()
        return "break"

    def _set_focus(self, focused):
        self._focused = focused
        self._paint()


# ------------------------------------------------------------------ 分段控件
class Segmented(tk.Canvas):
    """Apple 风分段控件。"""

    def __init__(self, master, options, command=None, height=36, bg=None, font=F_SMALL):
        self.bgcol = bg or _parent_bg(master)
        self.font = font
        self.options = list(options)
        self.command = command
        self.index = 0
        self._focused = False
        self.h = height
        self.seg_w = 0
        super().__init__(master, height=height, bg=self.bgcol, highlightthickness=0, bd=0)
        f = tkfont.Font(family=font[0], size=font[1])
        self.seg_w = max(f.measure(o) for o in self.options) + 22
        self.configure(width=self.seg_w * len(self.options) + 4,
                       cursor="hand2")
        self.bind("<Button-1>", self._click)
        self.configure(takefocus=True)
        self.bind("<Left>", lambda e: self.set((self.index-1)%len(self.options), notify=True))
        self.bind("<Right>", lambda e: self.set((self.index+1)%len(self.options), notify=True))
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self._paint()

    def _paint(self):
        self.delete("all")
        w, h = int(self["width"]), self.h
        round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, h / 2, fill=C.N100, outline=C.N100)
        x = 2
        for i, opt in enumerate(self.options):
            if i == self.index:
                round_rect(self, x + 1, 3, x + self.seg_w - 1, h - 3, (h - 6) / 2,
                           fill=C.N0, outline=C.N0)
            self.create_text(x + self.seg_w / 2, h / 2 + 0.5, text=opt,
                             fill=(C.TEXT if i == self.index else C.SUB), font=self.font)
            x += self.seg_w
        if self._focused:
            round_rect(self, 2, 2, w - 2, h - 2, h / 2 - 2, outline=C.A600, width=1)

    def _set_focus(self, focused):
        self._focused = focused
        self._paint()

    def _click(self, e):
        self.focus_set()
        i = int((e.x - 2) // self.seg_w)
        if 0 <= i < len(self.options) and i != self.index:
            self.set(i, notify=True)

    def set(self, i, notify=False):
        self.index = max(0, min(len(self.options) - 1, i))
        self._paint()
        if notify and self.command:
            self.command(self.options[self.index])


# ------------------------------------------------------------------ 状态胶囊
class StatusPill(tk.Frame):
    def __init__(self, master, text="连接中", bg=None):
        bg = bg or _parent_bg(master)
        super().__init__(master, bg=bg)
        self.cv = tk.Canvas(self, width=10, height=10, bg=bg, highlightthickness=0, bd=0)
        self.cv.pack(side="left", padx=(0, 6))
        self._dot = self.cv.create_oval(1, 1, 9, 9, fill=C.DISABLED, outline="")
        self.lbl = tk.Label(self, text=text, bg=bg, fg=C.SUB, font=F_MINI)
        self.lbl.pack(side="left")

    def set(self, text=None, color=None):
        if text is not None:
            self.lbl.configure(text=text)
        if color is not None:
            self.cv.itemconfigure(self._dot, fill=color)


# ------------------------------------------------------------------ 搜索框
class SearchBox(tk.Frame):
    def __init__(self, master, width=210, placeholder="搜索字幕", on_change=None, bg=None):
        bg = bg or _parent_bg(master)
        super().__init__(master, bg=bg)
        self.var = tk.StringVar()
        self.placeholder = placeholder
        self.on_change = on_change
        self._ph = True
        self._focus = False
        self._hover = False
        self.cv = tk.Canvas(self, width=width, height=H_PILL, bg=bg,
                            highlightthickness=0, bd=0)
        self.cv.pack()
        self.entry = tk.Entry(self.cv, textvariable=self.var, bd=0,
                              relief="flat", bg=C.N100, fg=C.TEXT,
                              insertbackground=C.TEXT,
                              font=F_SMALL, highlightthickness=0)
        self._win = self.cv.create_window(30, 15, anchor="w", window=self.entry,
                                          width=width - 40, height=20)
        self.cv.bind("<Configure>", self._resize)
        # 统一线性图标，光学居中；不依赖系统字体里的搜索符号。
        self.cv.create_image(9, H_PILL // 2, anchor="w",
                             image=icons.photo("search", 15, C.T3, master=self.cv))
        self.var.trace_add("write", lambda *a: (not self._ph) and self.on_change and self.on_change())
        self._show_placeholder()
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self.entry.bind("<Escape>", self.clear)
        for w in (self.cv, self.entry):
            w.bind("<Enter>", lambda e: self._set_hover(True))
            w.bind("<Leave>", lambda e: self._set_hover(False))

    def _show_placeholder(self):
        self._ph = True
        self.entry.configure(fg=C.DISABLED)
        self.var.set(self.placeholder)

    def _on_focus_in(self, _):
        if self._ph:
            self._ph = False
            self.var.set("")
            self.entry.configure(fg=C.TEXT)
        self._focus = True
        self._paint()

    def _on_focus_out(self, _):
        if not self.var.get().strip():
            self._show_placeholder()
        self._focus = False
        self._paint()

    def _set_hover(self, on):
        if on != self._hover:
            self._hover = on
            self._paint()

    def value(self) -> str:
        return "" if self._ph else self.var.get().strip()

    def clear(self, _=None):
        self._ph = False
        self.entry.configure(fg=C.TEXT)
        self.var.set("")
        self.entry.focus_set()
        return "break"

    def _paint(self):
        # 层级：聚焦(强调描边) > 悬停(浅灰) > 静态，与 Field 一致
        w = int(self.cv["width"])
        if w <= 1:
            return
        self.cv.delete("shape")
        fill = C.N100 if (self._focus or not self._hover) else C.N150
        if self.entry.cget("bg") != fill:
            self.entry.configure(bg=fill)
        ring_rect(self.cv, 0.5, 0.5, w - 0.5, H_PILL - 0.5, R_FIELD,
                  fill=fill, active=self._focus, tags="shape")
        self.cv.tag_lower("shape")
        self.cv.coords(self._win, 30, H_PILL / 2)
        self.cv.itemconfigure(self._win, width=max(20, w - 42), height=20)

    def _resize(self, e):
        self._paint()


# ------------------------------------------------------------------ 说话人列表
class SpeakerList(tk.Canvas):
    """头像 + 名字 + 条数，点击筛选。全部用 Canvas 画，避免 Treeview 的土气样式。"""

    ROW_H = 38

    def __init__(self, master, on_pick=None, bg=None):
        bg = C.N0 if bg is None else bg      # 同 Card：默认值运行时解析
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self.on_pick = on_pick
        self.items = []          # [(key, name, count, color)]
        self.selected = None     # None = 全部
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Configure>", lambda e: self._paint())
        self.configure(yscrollincrement=self.ROW_H)
        self.bind("<MouseWheel>", lambda e: (self.yview_scroll(int(-e.delta/120), "units"), "break")[1])
        self._hover = -1

    def set_items(self, items, selected=None):
        self.items = items
        self.selected = selected
        self._paint()

    def _rows(self):
        rows = [(None, "全部发言人", sum(i[2] for i in self.items) or 0, C.A500)]
        for key, name, count, color in self.items:
            rows.append((key, name, count, color))
        return rows

    def _paint(self):
        self.delete("all")
        w = self.winfo_width()
        if w <= 1:
            return
        rows = self._rows()
        self.configure(scrollregion=(0,0,w,len(rows)*self.ROW_H))
        for idx, (key, name, count, color) in enumerate(rows):
            y = idx * self.ROW_H
            if key == self.selected:
                round_rect(self, 6, y + 3, w - 6, y + self.ROW_H - 3, 9,
                           fill=C.A50, outline=C.A50)
            elif idx == self._hover:
                round_rect(self, 6, y + 3, w - 6, y + self.ROW_H - 3, 9,
                           fill=C.N150, outline=C.N150)
            cx, cy = 26, y + self.ROW_H / 2
            self.create_oval(cx - 11, cy - 11, cx + 11, cy + 11,
                             fill=lighten(color, 0.84), outline="")
            self.create_text(cx, cy, text=("全" if key is None else _initials(name)),
                             fill=color, font=(FAMILY, 8, "bold"))
            available = max(10, w-44-32)
            font = tkfont.Font(font=F_SMALL)
            clipped = name
            while len(clipped)>1 and font.measure(clipped)>available:
                clipped = clipped[:-2]+'…' if clipped.endswith('…') else clipped[:-1]+'…'
            self.create_text(44, cy, text=clipped, anchor="w",
                             fill=(C.TEXT if key == self.selected else C.SUB),
                             font=F_SMALL)
            self.create_text(w - 14, cy, text=str(count), anchor="e",
                             fill=C.T3, font=F_TIME)

    def _row_at(self, y):
        idx = int(self.canvasy(y) // self.ROW_H)
        rows = self._rows()
        return idx if 0 <= idx < len(rows) else -1

    def _click(self, e):
        idx = self._row_at(e.y)
        if idx < 0:
            return
        rows = self._rows()
        self.selected = rows[idx][0]
        self._paint()
        if self.on_pick:
            self.on_pick(self.selected)

    def _motion(self, e):
        idx = self._row_at(e.y)
        if idx != self._hover:
            self._hover = idx
            self._paint()


def _initials(name: str) -> str:
    # 中日韩名字取首字：两个方块字塞不进 22px 的头像圆
    if name and "⺀" <= name[0] <= "鿿":
        return name[0]
    parts = [p for p in name.replace("-", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def fit_text(value, font, width):
    """按真实字宽省略，保留完整数据；中英文、数字和不同 DPI 共用同一判据。"""
    value = str(value)
    face = tkfont.Font(font=font)
    if face.measure(value) <= width:
        return value
    if width < face.measure("…"):
        return ""
    low, high = 0, len(value)
    while low < high:
        mid = (low + high + 1) // 2
        if face.measure(value[:mid] + "…") <= width:
            low = mid
        else:
            high = mid - 1
    return value[:low] + "…"


# ---------------------------------------------------------------- 中文断行
# 断行禁则：行首不能出现的字符 / 行尾不能留下的字符（缺字会看起来像排版事故）
_NO_LINE_START = set("，。、；：？！）】》」』”’％%,.;:?!)]}…·～·")
_NO_LINE_END = set("（【《「『“‘([{")
# 西文单词整体不可拆；汉字逐字可断；其余符号各成一个 token
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’]*|\s+|.", re.S)


def _measure_cache(face):
    cache = {}

    def width(chunk):
        if chunk in cache:
            return cache[chunk]
        cache[chunk] = face.measure(chunk)
        return cache[chunk]
    return width


def wrap_cjk(text, font, width, max_lines=0):
    """按像素宽度手动断行，遵守中文禁则；西文单词不拆开。

    Tk 的 ``wraplength`` 只在空格处断行：中英混排时会出现「现在看 C / 柱」这种
    被空格提前截断的孤字行，也让「，」跑到行首。这里自己排，宽度用真实字体量。
    """
    body = str(text or "")
    if not body or width <= 0 or not body.strip():
        return body
    face = tkfont.Font(font=font)
    width_of = _measure_cache(face)
    lines, cur = [], ""
    for tok in _TOKEN.findall(body):
        if tok in ("\n", "\r\n"):
            lines.append(cur.rstrip())
            cur = ""
            continue
        if tok.isspace():
            if cur:
                cur += " "
            continue
        if cur and width_of(cur + tok) > width:
            stripped = cur.rstrip()
            carry = ""
            # 行尾禁则：开括号不能留在行尾
            while stripped and stripped[-1] in _NO_LINE_END:
                carry = stripped[-1] + carry
                stripped = stripped[:-1].rstrip()
            if tok in _NO_LINE_START and len(stripped) > 1:
                # 行首禁则：带上最后一个字一起换行，标点就不会落在行首
                carry = stripped[-1] + carry
                stripped = stripped[:-1].rstrip()
            if stripped:
                lines.append(stripped)
            cur = carry + tok
            continue
        cur += tok
    if cur.strip():
        lines.append(cur.rstrip())
    if max_lines and len(lines) > max_lines:
        head = lines[:max_lines]
        head[-1] = fit_text(head[-1] + "…", font, width)
        lines = head
    return "\n".join(lines)


# ------------------------------------------------------------------ 细滚动条
class ThinScroll(tk.Canvas):
    """Apple 风的细滚动条：只有一根圆头滑块，无箭头。"""

    def __init__(self, master, target, bg=None, bar=None, width=6, height=1,
                 on_user_scroll=None):
        bg = C.N0 if bg is None else bg      # 同 Card：默认值运行时解析
        # height 必须给最小值：Canvas 默认请求 265px，会把 grid 行撑高
        super().__init__(master, width=width + 6, height=height,
                         bg=bg, highlightthickness=0, bd=0)
        self.target = target
        self.on_user_scroll = on_user_scroll
        self.bar = bar or C.N300
        self.w = width
        self.first, self.last = 0.0, 1.0
        self._drag = None
        self._sync_after = None
        self.bind("<Configure>", lambda e: self._paint())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        target.configure(yscrollcommand=self._on_scroll)
        target.bind("<Configure>", self._schedule_sync, add="+")
        self.bind("<Destroy>", self._dispose_sync, add="+")
        self.bind("<MouseWheel>", self._wheel)

    def _on_scroll(self, first, last):
        self.first, self.last = float(first), float(last)
        self._paint()
        self._schedule_sync()

    def _schedule_sync(self, _=None):
        # Text 的标签/字体布局可能比 yscrollcommand 晚一帧，下一帧校正真实范围。
        if self._sync_after is None:
            self._sync_after = self.after(16, self._sync_range)

    def _sync_range(self):
        self._sync_after = None
        if self.winfo_exists() and self.target.winfo_exists():
            self.first, self.last = self.target.yview()
            self._paint()

    def _dispose_sync(self, _=None):
        if self._sync_after is not None:
            self.after_cancel(self._sync_after)
            self._sync_after = None

    def _paint(self):
        self.delete("all")
        h = max(1, self.winfo_height())
        if self.last - self.first >= 0.999:
            return
        y1, y2 = self._thumb_bounds()
        round_rect(self, 3, y1, 3 + self.w, y2, self.w / 2, fill=self.bar, outline=self.bar)

    def _thumb_bounds(self):
        track = max(1, self.winfo_height() - 6)
        span = max(0.0, min(1.0, self.last - self.first))
        size = min(track, max(18, span * track))
        start = 3 + (track - size) * min(1.0, self.first / max(.0001, 1 - span))
        return start, start + size

    def _notify_user(self):
        if self.on_user_scroll:
            self.on_user_scroll()

    def _wheel(self, e):
        self.target.yview_scroll(int(-e.delta / 120), "units")
        self._notify_user()
        return "break"

    def _press(self, e):
        h = max(1, self.winfo_height())
        y1, y2 = self._thumb_bounds()
        if y1 <= e.y <= y2:
            self._drag = (e.y, self.first)
        else:
            self.target.yview_moveto(max(0.0, min(1.0, e.y / h - (self.last - self.first) / 2)))
        self._notify_user()

    def _move(self, e):
        if not self._drag:
            return
        h = max(1, self.winfo_height())
        y0, f0 = self._drag
        top, bottom = self._thumb_bounds()
        travel = max(1, h - 6 - (bottom - top))
        self.target.yview_moveto(max(0.0, min(1.0, f0 + (e.y - y0) / travel *
                                                    (1 - self.last + self.first))))
        self._notify_user()


# ------------------------------------------------------------------ 圆角输入框
class Field(tk.Frame):
    """圆角单行输入框。

    Canvas 画圆角底与聚焦环，内部放原生无边框 Entry；Entry 按半径内缩，
    方角正好落在圆角形状内部，视觉上无缝（与 Card 同一套做法）。
    """

    R = R_FIELD

    def __init__(self, master, width=180, height=38, secret=False, value="",
                 font=None, bg=None, fill=None):
        outer = bg or _parent_bg(master)
        super().__init__(master, bg=outer, highlightthickness=0, bd=0,
                         width=width, height=height)
        self.pack_propagate(False)
        self.fill = fill or C.N100
        self.h = height
        self._focus = False
        self._hover = False
        self.cv = tk.Canvas(self, bg=outer, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.entry = tk.Entry(self.cv, bd=0, relief="flat", highlightthickness=0,
                              bg=self.fill, fg=C.TEXT, font=font or F_UI,
                              insertbackground=C.TEXT, selectbackground=C.A100,
                              selectforeground=C.TEXT, show="•" if secret else "")
        self._win = self.cv.create_window(self.R, height / 2, anchor="w",
                                          window=self.entry, height=height - 10)
        self._reveal_button = None
        if secret:
            self._reveal_button = Pill(self.cv, "显示", self._toggle_secret, height=26,
                                       padx=7, bg=self.fill, font=F_MINI)
            self._reveal_win = self.cv.create_window(width - self.R, height / 2,
                                                     anchor="e", window=self._reveal_button)
        self.cv.bind("<Configure>", self._resize)
        self.entry.bind("<FocusIn>",
                        lambda e: (setattr(self, "_focus", True), self._paint()))
        self.entry.bind("<FocusOut>",
                        lambda e: (setattr(self, "_focus", False), self._paint()))
        for w in (self.cv, self.entry):
            w.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._paint()))
            w.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._paint()))
        if value:
            self.entry.insert(0, value)
        self._paint()

    def _paint(self):
        w, h = self.cv.winfo_width(), self.h
        if w <= 1:
            return
        self.cv.delete("shape")
        # 聚焦用内嵌双环，悬停只改变表面明度。
        fill = self.fill if (self._focus or not self._hover) else C.N150
        if self.entry.cget("bg") != fill:
            self.entry.configure(bg=fill)
        ring_rect(self.cv, 0.5, 0.5, w - 0.5, h - 0.5, self.R,
                  fill=fill, active=self._focus, tags="shape")
        self.cv.tag_lower("shape")
        if self._reveal_button and self._reveal_button.bgcol != fill:
            self._reveal_button.bgcol = fill
            self._reveal_button.configure(bg=fill)
            self._reveal_button._paint()

    def _resize(self, e):
        reserve = int(self._reveal_button["width"]) + 6 if self._reveal_button else 0
        self.cv.itemconfigure(self._win, width=max(10, e.width - 2 * self.R - reserve),
                              height=max(12, e.height - 10))
        self.cv.coords(self._win, self.R, e.height / 2)
        if self._reveal_button:
            self.cv.coords(self._reveal_win, e.width - self.R, e.height / 2)
        self._paint()

    def _toggle_secret(self):
        concealed = bool(self.entry.cget("show"))
        self.entry.configure(show="" if concealed else "•")
        self._reveal_button.set_text("隐藏" if concealed else "显示")

    def get(self) -> str:
        return self.entry.get()

    def set(self, v: str):
        self.entry.delete(0, "end")
        if v:
            self.entry.insert(0, v)
        if self._reveal_button:
            self.entry.configure(show="•")
            self._reveal_button.set_text("显示")

    def focus(self):
        self.entry.focus_set()


# ------------------------------------------------------------------ 圆角文本域
class RoundedText(tk.Frame):
    """圆角多行文本框：Canvas 画底，Text 承载内容，超高时右侧浮出细滚动条。"""

    R = R_FIELD

    def __init__(self, master, lines=3, width=320, font=None, bg=None,
                 fill=None, wrap="word", padx=10):
        outer = bg or _parent_bg(master)
        font = font or F_UI
        # 可见高度取行距整数倍：多出的一行只露 0px，避免最后一行被腰斩
        lh = tkfont.Font(font=font).metrics("linespace")
        h = lines * lh + 20
        super().__init__(master, bg=outer, highlightthickness=0, bd=0,
                         width=width, height=h)
        self.pack_propagate(False)
        self.h, self.lh = h, lh
        self.fill = fill or C.N100
        self._focus = False
        self._hover = False
        self._bar_on = False
        self.cv = tk.Canvas(self, bg=outer, highlightthickness=0, bd=0)
        self.cv.pack(fill="both", expand=True)
        self.text = tk.Text(self.cv, bd=0, relief="flat", highlightthickness=0,
                            bg=self.fill, fg=C.TEXT, insertbackground=C.TEXT,
                            font=font, wrap=wrap, padx=padx, pady=6,
                            selectbackground=C.A100, selectforeground=C.TEXT)
        self._win = self.cv.create_window(self.R, 4, anchor="nw", window=self.text)
        self.bar = ThinScroll(self.cv, self.text, bg=self.fill, width=5, height=h)
        self.text.configure(yscrollcommand=self._on_scroll)
        self.cv.bind("<Configure>", self._resize)
        self.text.bind("<FocusIn>",
                       lambda e: (setattr(self, "_focus", True), self._paint()))
        self.text.bind("<FocusOut>",
                       lambda e: (setattr(self, "_focus", False), self._paint()))
        for w in (self.cv, self.text):
            w.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._paint()))
            w.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._paint()))
        self._paint()

    def _paint(self):
        w, h = self.cv.winfo_width(), self.cv.winfo_height()
        if w <= 1:
            return
        self.cv.delete("shape")
        fill = self.fill if (self._focus or not self._hover) else C.N150
        ring_rect(self.cv, 0.5, 0.5, w - 0.5, h - 0.5, self.R,
                  fill=fill, active=self._focus, tags="shape")
        self.cv.tag_lower("shape")
        if self.text.cget("bg") != fill:
            self.text.configure(bg=fill)

    def _resize(self, e):
        self._layout()

    def _layout(self):
        w, h = self.cv.winfo_width(), self.cv.winfo_height()
        if w <= 1:
            return
        self.cv.itemconfigure(self._win, width=max(10, w - 2 * self.R - 12),
                              height=max(20, h - 8))
        if self._bar_on:
            self.bar.place(x=max(0, w - 11), y=4, height=max(10, h - 8))
        self._paint()

    def _on_scroll(self, first, last):
        self.bar._on_scroll(first, last)
        need = float(last) - float(first) < 0.999
        if need and not self._bar_on:
            self._bar_on = True
            self._layout()
        elif not need and self._bar_on:
            self._bar_on = False
            self.bar.place_forget()
            self._layout()

    def get(self, *a) -> str:
        return self.text.get(*a) if a else self.text.get("1.0", "end-1c")

    def set(self, v: str):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", v)

    def set_lines(self, n: int):
        """动态改高度（行数是唯一变量，高度 = 行数 × 行距 + 内衬）。"""
        h = max(1, n) * self.lh + 20
        if h == self.h:
            return
        self.h = h
        self.configure(height=h)
        self.cv.configure(height=h)
        self.bar.configure(height=h)
        self._layout()


# ------------------------------------------------------------------ 下拉选择
_OPEN_POPS = []


def close_pops():
    """关闭所有展开的下拉层（切换内容层时调用，避免残留悬浮画布）。"""
    for p in list(_OPEN_POPS):
        try:
            p.grab_release()
        except Exception:
            pass
        try:
            p.destroy()
        except Exception:
            pass
    _OPEN_POPS.clear()


class Select(tk.Canvas):
    """扁平下拉框。展开层是盖在本窗内的画布，不弹新窗口（遵守层级约束）。"""

    ROW_H = 34

    def __init__(self, master, options, value=None, command=None,
                 width=200, height=38, bg=None, font=None):  # 值文字用 F_UI
        self.bgcol = bg or _parent_bg(master)
        self.options = list(options)
        self.font = font or F_UI
        self.command = command
        self.value = value if value in self.options else (self.options[0] if self.options else "")
        self.h, self.pad = height, 10
        self._hover = False
        self._focused = False
        self._pop, self._rows = None, []
        super().__init__(master, width=width, height=height, bg=self.bgcol,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.configure(takefocus=True)
        self.bind("<Button-1>", lambda e: self.toggle())
        self.bind("<Return>", lambda e: self.toggle())
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Down>", lambda e: self._step(1))
        self.bind("<Up>", lambda e: self._step(-1))
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._paint()))
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._paint()))
        self.bind("<Configure>", lambda e: self._paint())
        self._paint()

    def _paint(self):
        self.delete("all")
        w, h = self.winfo_width(), self.h
        if w <= 1:
            w = int(self["width"])
        on = self._pop is not None
        fill = C.N100 if (on or not self._hover) else C.N150
        ring_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, R_FIELD,
                  fill=fill, active=on or self._focused, tags="shape")
        self.tag_lower("shape")
        self.create_text(self.pad, h / 2 + 0.5,
                         text=fit_text(self.value, self.font, w - self.pad - 30),
                         anchor="w", fill=C.TEXT, font=self.font, tags="value")
        cx, cy = w - 14, h / 2
        self.create_line(cx - 3.5, cy - 1.5, cx, cy + 2, fill=C.SUB, width=1.5,
                         capstyle="round")
        self.create_line(cx, cy + 2, cx + 3.5, cy - 1.5, fill=C.SUB, width=1.5,
                         capstyle="round")

    def _set_focus(self, focused):
        self._focused = focused
        self._paint()

    def _step(self, direction):
        if self.options:
            index = self.options.index(self.value) if self.value in self.options else 0
            self.set(self.options[(index + direction) % len(self.options)], notify=True)
            if self.winfo_exists() and self._pop is not None:
                self.close()
                self._open()
        return "break"

    def toggle(self):
        self.close() if self._pop else self._open()

    def close(self):
        p, self._pop, self._rows = self._pop, None, []
        if p is None:
            return
        try:
            p.grab_release()
        except Exception:
            pass
        try:
            p.destroy()
        except Exception:
            pass
        if p in _OPEN_POPS:
            _OPEN_POPS.remove(p)
        if self.winfo_exists() and self.winfo_ismapped():
            self.focus_set()
        self._paint()

    def _open(self):
        top = self.winfo_toplevel()
        if not self.options:
            return
        w = max(1, self.winfo_width())
        h = self.ROW_H * len(self.options) + 8
        # 画布项里的控件 rootx 不可靠，一律用 place 的 in_ 相对锚点定位
        oy = max(0, lpx(self.winfo_rooty() - top.winfo_rooty()))
        below = (oy + self.h + 4 + h <= lpx(top.winfo_height())) or oy == 0
        pop = tk.Canvas(top, width=w, height=h, bg=self.bgcol,
                        highlightthickness=0, bd=0)
        pop.place(in_=self, x=0, y=(self.h + 4) if below else -(h + 4))
        # Canvas 的 lift() 是画布项命令，抬升窗口必须直接调 raise
        pop.tk.call("raise", pop._w)
        round_rect(pop, 3.5, 4.5, w - 0.5, h + 1.5, R_POP, fill=C.SHADOW, outline=C.SHADOW)
        round_rect(pop, 1.5, 1.5, w - 1.5, h - 1.5, R_POP, fill=C.N0, outline=C.LINE)
        for i, opt in enumerate(self.options):
            a = 4 + i * self.ROW_H
            b = a + self.ROW_H
            self._rows.append((opt, a, b))
            on = opt == self.value
            if on:
                round_rect(pop, 5, a + 1, w - 5, b - 1, 7,
                           fill=C.A50, outline=C.A50)
            pop.create_text(self.pad + 2, (a + b) / 2,
                            text=fit_text(opt, self.font, w - self.pad - 34), anchor="w",
                            fill=(C.TEXT if on else C.SUB), font=self.font)
            if on:
                pop.create_text(w - 10, (a + b) / 2, text="✓", anchor="e",
                                fill=C.A500, font=self.font)
        self._pop = pop
        _OPEN_POPS.append(pop)
        self._paint()               # 触发框切换到高亮环状态

        def down(e):
            if not (0 <= e.x < w and any(a <= e.y < b for _, a, b in self._rows)):
                self.close()
            return "break"

        def up(e):
            if not 0 <= e.x < w:
                self.close()
                return "break"
            for opt, a, b in self._rows:
                if a <= e.y < b:
                    self.set(opt, notify=True)
                    self.close()
                    break
            return "break"

        pop.bind("<Button-1>", down, add="+")
        pop.bind("<ButtonRelease-1>", up)
        pop.bind("<Escape>", lambda e: (self.close(), "break")[1])
        pop.bind("<Down>", lambda e: self._step(1))
        pop.bind("<Up>", lambda e: self._step(-1))
        pop.bind("<Return>", lambda e: (self.close(), "break")[1])
        pop.bind("<space>", lambda e: (self.close(), "break")[1])
        try:
            pop.focus_set()
            pop.grab_set()
        except Exception:
            pass

    def get(self) -> str:
        return self.value

    def set(self, v, notify=False):
        if v not in self.options or v == self.value:
            return
        self.value = v
        self._paint()
        if notify and self.command:
            self.command(v)


# ------------------------------------------------------------------ 模式切换
class ModeSwitch(tk.Canvas):
    """会议模式切换控件：色点胶囊触发 + 列表弹层（与 Select 同款机制）。

    modes: [{"key","name","short","desc","dot","dot_dark","available"}]
    未上线模式置灰标注「即将支持」，点击走 on_blocked 只提示、不切换。
    """

    ROW_H = 34
    PAD = 12

    def __init__(self, master, modes, value=None, command=None,
                 on_blocked=None, bg=None):
        self.bgcol = bg or _parent_bg(master)
        self.modes = list(modes)
        self.font = F_SMALL
        self.command = command
        self.on_blocked = on_blocked
        keys = [m["key"] for m in self.modes]
        self.value = value if value in keys else (keys[0] if keys else "")
        self.h = H_PILL
        self._hover = False
        self._focused = False
        self._pop, self._rows = None, []
        super().__init__(master, height=self.h, bg=self.bgcol,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Button-1>", lambda e: self.toggle())
        self.configure(takefocus=True)
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Return>", lambda e: self.toggle())
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._paint()))
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._paint()))
        self._paint()

    # -- 内部 --
    def _mode(self, key):
        for m in self.modes:
            if m["key"] == key:
                return m
        return None

    def _dot(self, m):
        return (m.get("dot_dark") or m.get("dot")) if theme() == "dark" \
            else m.get("dot")

    def _trigger_w(self, label):
        f = tkfont.Font(family=self.font[0], size=self.font[1])
        return self.PAD + 8 + 6 + f.measure(label) + 8 + 7 + self.PAD

    def _paint(self):
        self.delete("all")
        m = self._mode(self.value) or {}
        label = m.get("short", "模式")
        w = self._trigger_w(label)
        self.configure(width=w)
        on = self._pop is not None
        fill = C.N100 if (on or not self._hover) else C.N150
        ring_rect(self, 0.5, 0.5, w - 0.5, self.h - 0.5, self.h / 2,
                  fill=fill, active=on or self._focused, tags="shape")
        self.tag_lower("shape")
        x = self.PAD
        self.create_oval(x, self.h / 2 - 4, x + 8, self.h / 2 + 4,
                         fill=self._dot(m) or C.A500, outline="")
        self.create_text(x + 14, self.h / 2 + 0.5, text=label, anchor="w",
                         fill=C.TEXT, font=self.font)
        cx, cy = w - self.PAD - 3.5, self.h / 2
        self.create_line(cx - 3.5, cy - 1.5, cx, cy + 2, fill=C.SUB, width=1.5,
                         capstyle="round")
        self.create_line(cx, cy + 2, cx + 3.5, cy - 1.5, fill=C.SUB, width=1.5,
                         capstyle="round")

    def _set_focus(self, focused):
        self._focused = focused
        self._paint()

    def toggle(self):
        self.close() if self._pop else self._open()

    def close(self):
        p, self._pop, self._rows = self._pop, None, []
        if p is None:
            return
        try:
            p.grab_release()
        except Exception:
            pass
        try:
            p.destroy()
        except Exception:
            pass
        if p in _OPEN_POPS:
            _OPEN_POPS.remove(p)
        if self.winfo_exists() and self.winfo_ismapped():
            self.focus_set()
        self._paint()

    def _open(self):
        top = self.winfo_toplevel()
        w = max(int(self["width"]), 210)
        h = self.ROW_H * len(self.modes) + 8
        # 画布项里的控件 rootx 不可靠，一律用 place 的 in_ 相对锚点定位
        oy = max(0, lpx(self.winfo_rooty() - top.winfo_rooty()))
        below = (oy + self.h + 4 + h <= lpx(top.winfo_height())) or oy == 0
        pop = tk.Canvas(top, width=w, height=h, bg=self.bgcol,
                        highlightthickness=0, bd=0)
        pop.place(in_=self, x=0, y=(self.h + 4) if below else -(h + 4))
        # Canvas 的 lift() 是画布项命令，抬升窗口必须直接调 raise
        pop.tk.call("raise", pop._w)
        round_rect(pop, 3.5, 4.5, w - 0.5, h + 1.5, R_POP, fill=C.SHADOW, outline=C.SHADOW)
        round_rect(pop, 1.5, 1.5, w - 1.5, h - 1.5, R_POP, fill=C.N0, outline=C.LINE)
        for i, m in enumerate(self.modes):
            a = 4 + i * self.ROW_H
            b = a + self.ROW_H
            self._rows.append((m, a, b))
            on = m["key"] == self.value
            avail = m.get("available", True)
            if on:
                round_rect(pop, 5, a + 1, w - 5, b - 1, 7,
                           fill=C.A50, outline=C.A50)
            cy = (a + b) / 2
            pop.create_oval(self.PAD + 2, cy - 4, self.PAD + 10, cy + 4,
                            fill=self._dot(m) or C.A500, outline="")
            pop.create_text(self.PAD + 18, cy, text=m.get("name", m["key"]),
                            anchor="w", fill=(C.TEXT if avail else C.T3), font=F_UI)
            if on:
                pop.create_text(w - 10, cy, text="✓", anchor="e",
                                fill=C.A500, font=F_UI)
            elif not avail:
                pop.create_text(w - 10, cy, text="即将支持", anchor="e",
                                fill=C.T3, font=F_MINI)
        self._pop = pop
        _OPEN_POPS.append(pop)
        self._paint()               # 触发胶囊切换到高亮环状态

        def down(e):
            if not (0 <= e.x < w and any(a <= e.y < b for _, a, b in self._rows)):
                self.close()
            return "break"

        def up(e):
            if not 0 <= e.x < w:
                self.close()
                return "break"
            for m, a, b in self._rows:
                if a <= e.y < b:
                    if m.get("available", True):
                        self.set(m["key"], notify=True)
                    elif self.on_blocked:
                        self.on_blocked(m["key"])
                    self.close()
                    break
            return "break"

        pop.bind("<Button-1>", down, add="+")
        pop.bind("<ButtonRelease-1>", up)
        pop.bind("<Escape>", lambda e: (self.close(), "break")[1])
        try:
            pop.focus_set()
            pop.grab_set()
        except Exception:
            pass

    def get(self) -> str:
        return self.value

    def set(self, key, notify=False):
        if key != self.value:
            self.value = key
            self._paint()
        if notify and self.command:
            self.command(key)


# ------------------------------------------------------------------ 行列表
class RowList(tk.Canvas):
    """两行式可多选列表（标题 + 元信息，右侧附注），替代原生 Listbox。

    对外保留 Listbox 的常用子集：insert/delete/get/size、selection_set/
    selection_clear/curselection、yview 系列，用户改选时发 <<ListboxSelect>>，
    业务代码与测试可以照旧按 Listbox 的方式驱动。
    """

    ROW_H = 54

    def __init__(self, master, bg=None, font=None):
        self.bgcol = C.N0 if bg is None else bg
        super().__init__(master, bg=self.bgcol, highlightthickness=0, bd=0,
                         height=1, takefocus=True, yscrollincrement=self.ROW_H)
        self.items = []          # [{"text","title","meta","aside"}]
        self.sel = set()
        self._anchor = None
        self._hover = -1
        self._focused = False
        self.font = font or F_UI
        self.bind("<Configure>", lambda e: self._paint())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: self._set_hover(-1))
        self.bind("<MouseWheel>", lambda e: (self.yview_scroll(int(-e.delta / 120), "units"), "break")[1])
        self.bind("<Up>", lambda e: self._step(-1, e))
        self.bind("<Down>", lambda e: self._step(1, e))
        self.bind("<Control-a>", self._select_all)
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))

    # -- Listbox 兼容 --
    def _index(self, i):
        return len(self.items) - 1 if i == "end" else int(i)

    def _span(self, first, last):
        a = self._index(first)
        b = a if last is None else self._index(last)
        return range(max(0, a), min(len(self.items) - 1, b) + 1)

    def insert(self, index, text, title=None, meta=None, aside=None):
        item = {"text": text, "title": title or text, "meta": meta or "", "aside": aside or ""}
        pos = len(self.items) if index == "end" else int(index)
        self.items.insert(pos, item)
        self._paint()

    def delete(self, first, last=None):
        idx = list(self._span(first, last))
        if not idx:
            return
        del self.items[idx[0]:idx[-1] + 1]
        self.sel = {i for i in self.sel if i < len(self.items)}
        self._paint()

    def get(self, i):
        return self.items[self._index(i)]["text"]

    def size(self):
        return len(self.items)

    def curselection(self):
        return tuple(sorted(self.sel))

    def selection_set(self, first, last=None):
        self.sel.update(self._span(first, last))
        self._paint()

    def selection_clear(self, first, last=None):
        self.sel.difference_update(self._span(first, last))
        self._paint()

    # -- 绘制 --
    def _paint(self):
        self.delete_items()
        w = self.winfo_width()
        if w <= 1:
            return
        n = len(self.items)
        self.configure(scrollregion=(0, 0, w, max(n * self.ROW_H, 1)))
        title_font, meta_font = self.font, F_MINI
        for i, item in enumerate(self.items):
            y = i * self.ROW_H
            on = i in self.sel
            if on or i == self._hover:
                fill = C.A50 if on else C.N100
                round_rect(self, 0.5, y + 2, w - 0.5, y + self.ROW_H - 2, 10,
                           fill=fill, outline=fill)
            elif i and (i - 1) not in self.sel and (i - 1) != self._hover:
                self.create_line(14, y, w - 14, y, fill=C.LINE)
            aside_w = 0
            if item["aside"]:
                aside_w = tkfont.Font(font=F_TIME).measure(item["aside"]) + 16
                self.create_text(w - 14, y + self.ROW_H / 2, text=item["aside"],
                                 anchor="e", fill=C.T3, font=F_TIME)
            room = max(40, w - 28 - aside_w)
            self.create_text(14, y + 18, anchor="w", font=title_font,
                             text=fit_text(item["title"], title_font, room),
                             fill=C.A600 if on else C.TEXT)
            self.create_text(14, y + 38, anchor="w", font=meta_font,
                             text=fit_text(item["meta"], meta_font, room), fill=C.SUB)
        if self._focused and self._anchor is not None and self._anchor < n:
            y = self._anchor * self.ROW_H
            round_rect(self, 1, y + 3, w - 1, y + self.ROW_H - 3, 9,
                       outline=C.FOCUS_INNER, width=1)

    def delete_items(self):
        tk.Canvas.delete(self, "all")

    # -- 交互 --
    def _row_at(self, y):
        i = int(self.canvasy(y) // self.ROW_H)
        return i if 0 <= i < len(self.items) else -1

    def _notify(self):
        self._paint()
        self.event_generate("<<ListboxSelect>>")

    def _click(self, e):
        self.focus_set()
        i = self._row_at(e.y)
        if i < 0:
            return
        if e.state & 0x0001 and self._anchor is not None:          # Shift：连选
            lo, hi = sorted((self._anchor, i))
            self.sel = set(range(lo, hi + 1))
        elif e.state & 0x0004:                                      # Ctrl：增减
            self.sel ^= {i}
            self._anchor = i
        else:
            self.sel = {i}
            self._anchor = i
        self._notify()

    def _step(self, d, e):
        if not self.items:
            return "break"
        cur = self._anchor if self._anchor is not None else (-1 if d > 0 else len(self.items))
        i = max(0, min(len(self.items) - 1, cur + d))
        self._anchor = i
        self.sel = {i}
        self._reveal(i)
        self._notify()
        return "break"

    def _reveal(self, i):
        top, bottom = self.yview()
        n = max(1, len(self.items))
        if i / n < top:
            self.yview_moveto(i / n)
        elif (i + 1) / n > bottom:
            self.yview_moveto(max(0.0, (i + 1) / n - (bottom - top)))

    def _select_all(self, _=None):
        self.sel = set(range(len(self.items)))
        self._notify()
        return "break"

    def _set_hover(self, i):
        if i != self._hover:
            self._hover = i
            self._paint()

    def _motion(self, e):
        self._set_hover(self._row_at(e.y))

    def _set_focus(self, on):
        self._focused = on
        self._paint()


# ------------------------------------------------------------------ 输入对话框
def ask_string(parent, title, prompt="", initial=""):
    """主题化的单行输入框（模态）。确定返回文本，取消/关闭返回 None。"""
    root = parent.winfo_toplevel()
    win = tk.Toplevel(root)
    win.withdraw()
    win.title(title)
    win.transient(root)
    win.resizable(False, False)
    win.configure(bg=C.N50)
    result = {"value": None}
    body = tk.Frame(win, bg=C.N50)
    body.pack(fill="both", expand=True, padx=20, pady=(18, 16))
    if prompt:
        tk.Label(body, text=prompt, bg=C.N50, fg=C.SUB, font=F_SMALL,
                 anchor="w").pack(fill="x", pady=(0, 8))
    field = Field(body, width=320, value=initial, bg=C.N50)
    field.pack(fill="x")
    row = tk.Frame(body, bg=C.N50)
    row.pack(fill="x", pady=(16, 0))

    def ok(_=None):
        result["value"] = field.get()
        win.destroy()

    def cancel(_=None):
        win.destroy()

    Pill(row, "确定", ok, kind="primary", bg=C.N50).pack(side="right")
    Pill(row, "取消", cancel, kind="secondary", bg=C.N50).pack(side="right", padx=(0, 8))
    win.bind("<Return>", ok)
    win.bind("<Escape>", cancel)
    win.protocol("WM_DELETE_WINDOW", cancel)
    win.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() - win.winfo_reqwidth()) // 2
    y = root.winfo_rooty() + (root.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    win.deiconify()
    field.entry.focus_set()
    field.entry.select_range(0, "end")
    try:
        win.grab_set()
    except tk.TclError:
        pass
    root.wait_window(win)
    return result["value"]


# ------------------------------------------------------------------ 复选框
class Check(tk.Canvas):
    """圆角复选框 + 文案，绑定 BooleanVar；原生 Checkbutton 的方框在高 DPI 下过小。"""

    BOX = 16

    def __init__(self, master, text, variable, command=None, bg=None, font=None):
        self.bgcol = bg or _parent_bg(master)
        self.text, self.var, self.command = text, variable, command
        self.font = font or F_SMALL
        self._hover = self._focused = False
        self.h = 26
        w = self.BOX + 8 + tkfont.Font(font=self.font).measure(text) + 2
        super().__init__(master, width=w, height=self.h, bg=self.bgcol,
                         highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        for seq in ("<Button-1>", "<space>"):
            self.bind(seq, self.toggle)
        self.bind("<Enter>", lambda e: self._set("_hover", True))
        self.bind("<Leave>", lambda e: self._set("_hover", False))
        self.bind("<FocusIn>", lambda e: self._set("_focused", True))
        self.bind("<FocusOut>", lambda e: self._set("_focused", False))
        self._trace = variable.trace_add("write", lambda *a: self._paint())
        self.bind("<Destroy>", self._untrace, add="+")
        self._paint()

    def _untrace(self, _=None):
        try:
            self.var.trace_remove("write", self._trace)
        except (tk.TclError, ValueError):
            pass

    def _set(self, attr, value):
        setattr(self, attr, value)
        self._paint()

    def _paint(self):
        self.delete("all")
        on = bool(self.var.get())
        b, y = self.BOX, (self.h - self.BOX) / 2
        if on:
            round_rect(self, 0.5, y + 0.5, b - 0.5, y + b - 0.5, 4, fill=C.A500, outline=C.A500)
            self.create_image(1, y + 1, anchor="nw",
                              image=icons.photo("check", b - 2, "#FFFFFF", master=self))
        else:
            edge = C.SUB if (self._hover or self._focused) else C.N300
            round_rect(self, 0.5, y + 0.5, b - 0.5, y + b - 0.5, 4, fill=C.N0, outline=edge, width=1.2)
        self.create_text(b + 8, self.h / 2 + 0.5, text=self.text, anchor="w",
                         fill=C.TEXT if self._hover else C.SUB, font=self.font)

    def toggle(self, _=None):
        self.focus_set()
        self.var.set(not bool(self.var.get()))
        if self.command:
            self.command()
        return "break"


# ------------------------------------------------------------------ 开关
class Switch(tk.Canvas):
    """iOS 式开关。"""

    W, H = 38, 22

    def __init__(self, master, value=False, command=None, bg=None):
        super().__init__(master, width=self.W, height=self.H,
                         bg=bg or _parent_bg(master),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.value = bool(value)
        self.command = command
        self._hover = False
        self._focused = False
        self._progress = None
        self._gen = 0
        self.bind("<Button-1>", lambda e: self.toggle())
        self.configure(takefocus=True)
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Return>", lambda e: self.toggle())
        self.bind("<FocusIn>", lambda e: self._set_focus(True))
        self.bind("<FocusOut>", lambda e: self._set_focus(False))
        self.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._paint()))
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._paint()))
        self._paint()

    def _set_focus(self, focused):
        self._focused = focused
        self._paint()

    def _paint(self):
        self.delete("all")
        on = self.value
        off = C.N150 if (self._hover and not on) else C.N200
        # 动画中途轨道色向目标色过渡，避免底色跳变
        fill = C.A500 if on else off
        if self._progress is not None:
            t = self._progress if on else 1 - self._progress
            fill = _blend(C.N200, C.A500, max(0.0, min(1.0, t)))
        round_rect(self, 0.5, 0.5, self.W - 0.5, self.H - 0.5, self.H / 2,
                   fill=fill, outline=fill)
        k0, k1 = 2 + self.H / 2, self.W - 2 - self.H / 2
        p = self._progress if self._progress is not None else (1.0 if on else 0.0)
        kx = k0 + (k1 - k0) * p
        r = self.H / 2 - 2
        self.create_oval(kx - r, self.H / 2 - r, kx + r, self.H / 2 + r,
                         fill=C.KNOB, outline=C.KNOB)
        if self._focused:
            round_rect(self, 1, 1, self.W - 1, self.H - 1, self.H / 2 - 1,
                       outline=C.A600, width=1)

    def _animate(self):
        """滑块从当前位置滑向目标位置（约 120ms，3 帧）。"""
        self._gen += 1
        gen, target = self._gen, 1.0 if self.value else 0.0
        start = 1.0 - target

        def step(i=0):
            if gen != self._gen or not self.winfo_exists():
                return
            self._progress = start + (target - start) * (i / 3)
            self._paint()
            if i < 3:
                self.after(40, step, i + 1)
            else:
                self._progress = None
                self._paint()

        step()

    def get(self) -> bool:
        return self.value

    def set(self, v, notify=False):
        v = bool(v)
        if v == self.value:
            return
        self.value = v
        self._animate()
        if notify and self.command:
            self.command(v)

    def toggle(self):
        self.set(not self.value, notify=True)


# ------------------------------------------------------------------ 自绘列表
class PickList(tk.Canvas):
    """圆角浅底列表：圆角选中块 + 悬停高亮 + 可编辑项标记，支持滚轮。"""

    ROW_H = 26

    def __init__(self, master, on_pick=None, width=170, rows=7, bg=None, font=None):
        super().__init__(master, width=width, height=rows * self.ROW_H + 10,
                         bg=bg or _parent_bg(master), highlightthickness=0,
                         bd=0, cursor="hand2")
        self.items = []            # [(key, label, marked)]
        self.key = None
        self.on_pick = on_pick
        self.font = font or F_SMALL
        self.rows = rows
        self.offset = 0
        self._hover = -1
        # 高度不再写死：窗口缩小时列表跟着变矮，可见行数按实际高度重算。
        # 固定 rows 会在小窗口里把整页撑高，下面的控件被挤出可视区。
        self.bind("<Configure>", self._on_resize)
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<MouseWheel>", self._wheel)
        self._paint()

    def _on_resize(self, _event=None):
        """按当前高度更新可见行数（滚轮与翻页都依赖 self.rows）。"""
        try:
            visible = max(2, int((self.winfo_height() - 10) // self.ROW_H))
            if visible != self.rows:
                self.rows = visible
                self._clamp()
        except Exception:
            pass
        self._paint()

    def set_items(self, items, key=None, scroll=True):
        self.items = list(items)
        if key is not None:
            self.key = key
        if scroll:
            self.reveal(self.key)
        self._paint()

    def reveal(self, key):
        keys = [i[0] for i in self.items]
        if key not in keys:
            return
        idx = keys.index(key)
        if idx < self.offset:
            self.offset = idx
        elif idx >= self.offset + self.rows:
            self.offset = idx - self.rows + 1
        self._clamp()

    def _clamp(self):
        self.offset = max(0, min(self.offset, max(0, len(self.items) - self.rows)))

    def _wheel(self, e):
        if len(self.items) <= self.rows:
            return "break"
        self.offset += -1 if e.delta > 0 else 1
        self._clamp()
        self._paint()
        return "break"

    def _paint(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1:
            return
        self._clamp()
        round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, R_FIELD,
                   fill=C.N100, outline=C.N100, tags="shape")
        self.tag_lower("shape")
        for r, (key, label, marked) in enumerate(self.items[self.offset:self.offset + self.rows]):
            idx = self.offset + r
            y = 5 + r * self.ROW_H
            if key == self.key:
                round_rect(self, 4, y, w - 4, y + self.ROW_H - 3, 7,
                           fill=C.A50, outline=C.A50)
            elif idx == self._hover:
                round_rect(self, 4, y, w - 4, y + self.ROW_H - 3, 7,
                           fill=C.N150, outline=C.N150)
            cy = y + (self.ROW_H - 3) / 2
            if marked:
                self.create_text(11, cy, text="✎", anchor="w", fill=C.A500,
                                 font=(FAMILY, 8))
            self.create_text(22, cy, text=_clip(label, 9), anchor="w",
                             fill=(C.TEXT if key == self.key else C.SUB),
                             font=self.font)
        if len(self.items) > self.rows:
            self.create_text(w - 7, h - 5, text="⋮", anchor="e", fill=C.DISABLED,
                             font=F_SMALL)

    def _row_at(self, y):
        r = int((y - 5) // self.ROW_H)
        if not (0 <= r < self.rows):
            return -1
        idx = self.offset + r
        return idx if idx < len(self.items) else -1

    def _click(self, e):
        idx = self._row_at(e.y)
        if idx < 0:
            return
        key = self.items[idx][0]
        if key == self.key:
            return
        self.key = key
        self._paint()
        if self.on_pick:
            self.on_pick(key)

    def _motion(self, e):
        idx = self._row_at(e.y)
        if idx != self._hover:
            self._hover = idx
            self._paint()

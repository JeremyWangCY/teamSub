# -*- coding: utf-8 -*-
"""
ui_kit —— TeamsSub 的视觉层组件
===============================
设计取向：暖灰底色、圆角与低饱和蓝色；按钮仅用蓝、灰、柔红三类。

tkinter 原生没有圆角，这里用 Canvas 画形状、再用 window item 承载内容。
要点：内容框按 radius 内缩，使内容方角的四个角落在圆角形状内部，视觉上无缝。
"""
import tkinter as tk
import tkinter.font as tkfont
from PIL import Image, ImageDraw, ImageTk
import math


# ------------------------------------------------------------------ 主题引擎
_LIGHT = {
    "N0": "#FCFBF8", "N50": "#F7F6F2", "N100": "#EFEEE8", "N150": "#E7E5DE",
    "N200": "#DFDFE4", "N300": "#D6D6DB", "LINE": "#E3E0D8",
    "LINE_D": "#D8D8DE", "SHADOW": "#E2E2E8",
    "TEXT": "#292824", "SUB": "#69675F", "T3": "#827F76", "DISABLED": "#AEAEB2",
    "A600": "#455994", "A500": "#586DB6", "A100": "#DCE3F4", "A50": "#EBEFF8",
    "BUTTON_RED": "#9C5755",
    "GREEN": "#1C843A", "GREEN_F": "#34C759", "GREEN_S": "#E8F9ED",
    "AMBER": "#B25000", "AMBER_F": "#FF9F0A", "AMBER_S": "#FFF4E0",
    "RED": "#D70015", "RED_F": "#FF3B30", "RED_S": "#FFECEB",
}
# 暗色阶：文字强调色提亮，实心按钮保留足够的白字对比度。
_DARK = {
    "N0": "#262521", "N50": "#1D1C19", "N100": "#302F2A", "N150": "#403E37",
    "N200": "#48484A", "N300": "#545456", "LINE": "#403E37",
    "LINE_D": "#48484A", "SHADOW": "#000000",
    "TEXT": "#F0EEE7", "SUB": "#B8B4A9", "T3": "#827F76", "DISABLED": "#69675F",
    "A600": "#A9B8E8", "A500": "#586DB6", "A100": "#354267", "A50": "#2D3448",
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
    scale = 3
    left, top = math.floor(x1), math.floor(y1)
    width, height = max(1, math.ceil(x2)-left+1), max(1, math.ceil(y2)-top+1)
    shape = Image.new('RGBA', (width*scale, height*scale))
    draw = ImageDraw.Draw(shape)
    box = ((x1-left)*scale, (y1-top)*scale, (x2-left)*scale, (y2-top)*scale)
    fill, outline = kw.pop('fill', None), kw.pop('outline', None)
    line_width = max(1, round(kw.pop('width', 1)*scale))
    draw.rounded_rectangle(box, radius=max(0,r)*scale, fill=fill or None, outline=outline or None, width=line_width)
    photo = ImageTk.PhotoImage(shape.resize((width,height), Image.Resampling.LANCZOS), master=cv)
    refs = getattr(cv, '_round_images', {})
    for item in list(refs):
        if not cv.type(item):
            del refs[item]
    item = cv.create_image(left, top, image=photo, anchor='nw', **kw)
    refs[item] = photo
    cv._round_images = refs
    return item


def ring_rect(cv: tk.Canvas, x1, y1, x2, y2, r, fill, active,
              ring=None, tags=None):
    """圆角底 + 可选聚焦/选中环。

    环必须画在「内缩 1.5px 的独立路径」上（宽 2、无填充），不能直接在
    底形上叠 outline+width：平滑多边形的宽描边一旦贴住画布边缘，外半侧
    会被画布裁剪，底边渲染成碎块（顶边完整、底边断裂的怪相即源于此）。
    """
    kw = {"tags": tags} if tags else {}
    round_rect(cv, x1, y1, x2, y2, r, fill=fill, outline=fill, width=1, **kw)
    if active:
        round_rect(cv, x1 + 1.5, y1 + 1.5, x2 - 1.5, y2 - 1.5,
                   max(1.0, r - 1.5), fill="", outline=ring or C.A500,
                   width=2, **kw)


# ------------------------------------------------------------------ 圆角卡片
class Card(tk.Frame):
    """圆角白卡；内容放 self.body。"""

    def __init__(self, master, radius=16, fill=None, border=None,
                 padding=None, bg=None, autosize=False):
        # 默认值必须在调用时解析：类定义时的 C.N0/C.LINE 绑定的是导入那一刻
        # 的主题（浅色），深色模式下会残留白底白发丝边
        fill = C.N0 if fill is None else fill
        border = C.LINE if border is None else border
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
        h = self.body.winfo_reqheight() + 2 * self.pad
        self.cv.configure(height=h)

    def _resize(self, e):
        p = self.pad
        self.cv.itemconfigure(self._win, width=max(1, e.width - 2 * p),
                              height=max(1, e.height - 2 * p))
        self.cv.delete("bgshape")
        round_rect(self.cv, 0.5, 0.5, e.width - 0.5, e.height - 0.5,
                   self.radius, fill=self.fill, outline=self.border, tags="bgshape")
        self.cv.tag_lower("bgshape")


# ------------------------------------------------------------------ 胶囊按钮
class Pill(tk.Canvas):
    """圆角按钮；蓝色强调、灰色常规、柔红色谨慎操作。"""

    def __init__(self, master, text, command=None, kind="ghost", height=H_PILL,
                 padx=14, bg=None, font=F_SMALL, align="center"):
        self.bgcol = bg or _parent_bg(master)
        self.font = font
        self.align = align
        self.kind = kind
        self.command = command
        self.padx, self.h = padx, height
        self.text = text
        super().__init__(master, height=height, bg=self.bgcol,
                         highlightthickness=0, bd=0, cursor="hand2")
        self._hover = False
        self._resize_to_text()
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.configure(takefocus=True)
        self.bind("<Return>", lambda e: self.command() if self.command else None)
        self.bind("<space>", lambda e: self.command() if self.command else None)
        self.bind("<FocusIn>", self._on_enter)
        self.bind("<FocusOut>", self._on_leave)
        if self.align == "left":
            self.bind("<Configure>", lambda e: self._paint())

    # -- 内部 --
    def _measure(self, s):
        return tkfont.Font(family=self.font[0], size=self.font[1],
                           weight=self.font[2] if len(self.font) > 2 else "normal").measure(s)

    def _colors(self):
        if self.kind == "primary":
            fill = _blend(C.A500, "#000000", .12) if self._hover else C.A500
            return fill, "#FFFFFF", ""
        if self.kind == "tab":
            return (C.N150 if self._hover else self.bgcol), C.SUB, ""
        if self.kind == "selected":
            return C.A50, C.A600, ""
        if self.kind == "danger":
            return lighten(C.BUTTON_RED, .84 if self._hover else .92), C.BUTTON_RED, ""
        if self.kind == "secondary":
            return (C.N100 if self._hover else C.N0), C.TEXT, C.LINE_D
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
        self.create_text(self.padx if self.align == "left" else w / 2, self.h / 2 + 0.5,
                         text=self.text, anchor="w" if self.align == "left" else "center",
                         fill=fg, font=self.font)

    def _resize_to_text(self):
        w = self._measure(self.text) + self.padx * 2
        self.configure(width=w)
        self._paint()

    # -- 对外 --
    def set_text(self, text):
        self.text = text
        self._resize_to_text()

    def set_kind(self, kind):
        self.kind = kind
        self._paint()

    # -- 事件 --
    def _on_enter(self, _):
        self._hover = True
        self._paint()

    def _on_leave(self, _):
        self._hover = False
        self._paint()

    def _on_press(self, _):
        self._paint()

    def _on_release(self, _):
        if self.command:
            self.command()
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
        self._paint()

    def _paint(self):
        self.delete("all")
        w, h = int(self["width"]), self.h
        round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, h / 2, fill=C.N100, outline=C.N100)
        x = 2
        for i, opt in enumerate(self.options):
            if i == self.index:
                round_rect(self, x + 1, 3, x + self.seg_w - 1, h - 3, (h - 6) / 2,
                           fill=C.N0, outline=C.LINE)
            self.create_text(x + self.seg_w / 2, h / 2 + 0.5, text=opt,
                             fill=(C.TEXT if i == self.index else C.SUB), font=self.font)
            x += self.seg_w

    def _click(self, e):
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
                              font=(FAMILY, 9), highlightthickness=0)
        self._win = self.cv.create_window(28, 15, anchor="w", window=self.entry,
                                          width=width - 40, height=20)
        self.cv.bind("<Configure>", self._resize)
        self.cv.create_text(14, 15, text="⌕", fill=C.DISABLED, font=(FAMILY, 12))
        self.var.trace_add("write", lambda *a: (not self._ph) and self.on_change and self.on_change())
        self._show_placeholder()
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
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
        self.cv.coords(self._win, 28, H_PILL / 2)
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
                             fill=C.T3, font=F_MINI)

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
    parts = [p for p in name.replace("-", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ------------------------------------------------------------------ 细滚动条
class ThinScroll(tk.Canvas):
    """Apple 风的细滚动条：只有一根圆头滑块，无箭头。"""

    def __init__(self, master, target, bg=None, bar=None, width=6, height=1):
        bg = C.N0 if bg is None else bg      # 同 Card：默认值运行时解析
        # height 必须给最小值：Canvas 默认请求 265px，会把 grid 行撑高
        super().__init__(master, width=width + 6, height=height,
                         bg=bg, highlightthickness=0, bd=0)
        self.target = target
        self.bar = bar or C.N300
        self.w = width
        self.first, self.last = 0.0, 1.0
        self._drag = None
        self.bind("<Configure>", lambda e: self._paint())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))
        target.configure(yscrollcommand=self._on_scroll)
        self.bind("<MouseWheel>", lambda e: (target.yview_scroll(int(-e.delta / 120), "units"), "break")[1])

    def _on_scroll(self, first, last):
        self.first, self.last = float(first), float(last)
        self._paint()

    def _paint(self):
        self.delete("all")
        h = max(1, self.winfo_height())
        if self.last - self.first >= 0.999:
            return
        y1, y2 = self.first * h + 3, self.last * h - 3
        if y2 - y1 < 18:
            y2 = y1 + 18
        round_rect(self, 3, y1, 3 + self.w, y2, self.w / 2, fill=self.bar, outline=self.bar)

    def _press(self, e):
        h = max(1, self.winfo_height())
        if self.first * h + 3 <= e.y <= self.last * h - 3:
            self._drag = (e.y, self.first)
        else:
            self.target.yview_moveto(max(0.0, min(1.0, e.y / h - (self.last - self.first) / 2)))

    def _move(self, e):
        if not self._drag:
            return
        h = max(1, self.winfo_height())
        y0, f0 = self._drag
        self.target.yview_moveto(max(0.0, min(1.0, f0 + (e.y - y0) / h)))


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
        w, h = self.winfo_width(), self.h
        if w <= 1:
            return
        self.cv.delete("shape")
        # 层级：聚焦(描边) > 悬停(浅灰) > 静态
        fill = self.fill if (self._focus or not self._hover) else C.N150
        if self.entry.cget("bg") != fill:
            self.entry.configure(bg=fill)
        ring_rect(self.cv, 0.5, 0.5, w - 0.5, h - 0.5, self.R,
                  fill=fill, active=self._focus, tags="shape")
        self.cv.tag_lower("shape")

    def _resize(self, e):
        self.cv.itemconfigure(self._win, width=max(10, e.width - 2 * self.R),
                              height=max(12, e.height - 10))
        self.cv.coords(self._win, self.R, e.height / 2)
        self._paint()

    def get(self) -> str:
        return self.entry.get()

    def set(self, v: str):
        self.entry.delete(0, "end")
        if v:
            self.entry.insert(0, v)

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
        self._pop, self._rows = None, []
        super().__init__(master, width=width, height=height, bg=self.bgcol,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.configure(takefocus=True)
        self.bind("<Button-1>", lambda e: self.toggle())
        self.bind("<Return>", lambda e: self.toggle())
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Down>", lambda e: self.set(self.options[(self.options.index(self.value)+1)%len(self.options)],notify=True))
        self.bind("<Up>", lambda e: self.set(self.options[(self.options.index(self.value)-1)%len(self.options)],notify=True))
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
                  fill=fill, active=on, tags="shape")
        self.tag_lower("shape")
        self.create_text(self.pad, h / 2 + 0.5, text=self.value, anchor="w",
                         fill=C.TEXT, font=self.font)
        cx, cy = w - 14, h / 2
        self.create_line(cx - 3.5, cy - 1.5, cx, cy + 2, fill=C.SUB, width=1.5,
                         capstyle="round")
        self.create_line(cx, cy + 2, cx + 3.5, cy - 1.5, fill=C.SUB, width=1.5,
                         capstyle="round")

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
        self._paint()

    def _open(self):
        top = self.winfo_toplevel()
        w = int(self["width"])
        h = self.ROW_H * len(self.options) + 8
        # 画布项里的控件 rootx 不可靠，一律用 place 的 in_ 相对锚点定位
        oy = max(0, self.winfo_rooty() - top.winfo_rooty())
        below = (oy + self.h + 4 + h <= top.winfo_height()) or oy == 0
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
            pop.create_text(self.pad + 2, (a + b) / 2, text=opt, anchor="w",
                            fill=(C.TEXT if on else C.SUB), font=self.font)
            if on:
                pop.create_text(w - 10, (a + b) / 2, text="✓", anchor="e",
                                fill=C.A500, font=self.font)
        self._pop = pop
        _OPEN_POPS.append(pop)
        self._paint()               # 触发框切换到高亮环状态

        def down(e):
            if not any(a <= e.y <= b for _, a, b in self._rows):
                self.close()
            return "break"

        def up(e):
            for opt, a, b in self._rows:
                if a <= e.y <= b:
                    self.set(opt, notify=True)
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
        self._pop, self._rows = None, []
        super().__init__(master, height=self.h, bg=self.bgcol,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.bind("<Button-1>", lambda e: self.toggle())
        self.configure(takefocus=True)
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Return>", lambda e: self.toggle())
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
                  fill=fill, active=on, tags="shape")
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
        self._paint()

    def _open(self):
        top = self.winfo_toplevel()
        w = max(int(self["width"]), 210)
        h = self.ROW_H * len(self.modes) + 8
        # 画布项里的控件 rootx 不可靠，一律用 place 的 in_ 相对锚点定位
        oy = max(0, self.winfo_rooty() - top.winfo_rooty())
        below = (oy + self.h + 4 + h <= top.winfo_height()) or oy == 0
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
            if not any(a <= e.y <= b for _, a, b in self._rows):
                self.close()
            return "break"

        def up(e):
            for m, a, b in self._rows:
                if a <= e.y <= b:
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
        self._progress = None
        self._gen = 0
        self.bind("<Button-1>", lambda e: self.toggle())
        self.configure(takefocus=True)
        self.bind("<space>", lambda e: self.toggle())
        self.bind("<Return>", lambda e: self.toggle())
        self.bind("<Enter>", lambda e: (setattr(self, "_hover", True), self._paint()))
        self.bind("<Leave>", lambda e: (setattr(self, "_hover", False), self._paint()))
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
                         fill=C.N0, outline=C.N0)

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
        self.bind("<Configure>", lambda e: self._paint())
        self.bind("<Button-1>", self._click)
        self.bind("<Motion>", self._motion)
        self.bind("<MouseWheel>", self._wheel)
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

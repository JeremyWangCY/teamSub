# -*- coding: utf-8 -*-
"""floatbar —— 悬浮字幕窗

一个无边框、置顶的小条：显示最新一条原文与译文，可拖动、可缩放、可回主界面。
颜色全部走 ui_kit 令牌，跟随深浅主题；`overrideredirect` 自绘圆角与按钮。

窗口本身是直角的（Windows 无边框窗口默认如此）。只把卡片画圆没有用：卡片
外面那圈窗口底色会在四角露出来，看起来就是个「方底托着一张圆卡」，越靠边
越明显。所以这里用 SetWindowRgn 把整个窗口裁成圆角，露出来的四角是桌面。
"""
import os
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk

import ui_kit as UK

W, H = 460, 132          # 默认尺寸
MIN_W, MIN_H = 300, 92   # 缩放下限：再小连说话人一行都放不下
ALPHA = 1.0              # 实心卡面，避免桌面纹理干扰阅读
RADIUS = 8               # 悬浮窗的四角使用同一半径


class FloatBar:
    """悬浮字幕条。由 App 创建/销毁，数据通过 sync() 灌入。"""

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        w = self.win
        w.overrideredirect(True)
        # 必须给窗口一个标题：单实例接管要靠 EnumWindows + 标题前缀认出
        # 「哪个窗口属于 TeamsSub」。overrideredirect 窗口不显示标题栏，但
        # 窗口文本照样存在；标题为空时新实例找不到任何窗口，只能放弃启动。
        w.title("TeamsSub 悬浮字幕")
        w.attributes("-topmost", True)
        w.attributes("-alpha", ALPHA)
        # 窗口底色取卡片填充色：卡片圆角外那圈像素与卡面同色，裁圆角后
        # 不会有灰边残留（取 N50 就会在圆角处透出一圈浅灰）。
        w.configure(bg=UK.C.FLOAT_BG)

        cfg = getattr(app, "tcfg", None)
        self.w = max(MIN_W, int(getattr(cfg, "float_w", W) or W))
        # user_h 是「用户手动拖出来的高度」；0 表示按内容自适应
        self.user_h = max(0, int(getattr(cfg, "float_h", 0) or 0))
        self.h = max(MIN_H, self.user_h or H)
        w.geometry(f"{self.w}x{self.h}+{w.winfo_screenwidth() - self.w - 48}+96")

        # 拖动记录
        self._drag = None
        self._resize = None

        # 卡片直接铺满窗口：外圈不再留底色边，圆角由窗口裁剪负责
        self.card = UK.Card(w, radius=RADIUS, padding=14, bg=UK.C.FLOAT_BG, fill=UK.C.FLOAT_BG,
                            border=UK.C.FLOAT_BORDER)
        self.card.pack(fill="both", expand=True)

        body = self.card.body
        body.columnconfigure(0, weight=1)

        # 顶部信息与操作保持紧凑，字幕内容独立滚动。
        top = tk.Frame(body, bg=UK.C.FLOAT_BG)
        top.grid(row=0, column=0, sticky="we")
        top.columnconfigure(4, weight=1)
        self.dot = tk.Canvas(top, width=14, height=14, bg=UK.C.FLOAT_BG,
                             highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=0, padx=(0, 7))
        self._dot_id = self.dot.create_image(7, 7)
        self._paint_dot(UK.C.FLOAT_ACCENT)
        self.lbl_name = tk.Label(top, text="", bg=UK.C.FLOAT_BG, fg=UK.C.FLOAT_TEXT,
                                 font=UK.F_NAME)
        self.lbl_name.grid(row=0, column=1, sticky="w")
        self.lbl_time = tk.Label(top, text="", bg=UK.C.FLOAT_BG, fg=UK.C.FLOAT_MUTED,
                                 font=UK.F_TIME)
        self.name_separator = tk.Frame(top, bg=UK.C.FLOAT_LINE, width=1, height=16)
        self.name_separator.grid(row=0, column=2, padx=(10, 10))
        self.lbl_time.grid(row=0, column=3, sticky="w")
        self.btn_back = self._icon_button(top, "back", self.back_to_main)
        self.btn_back.grid(row=0, column=5, padx=(4, 4))
        tk.Frame(top, bg=UK.C.FLOAT_LINE, width=1, height=18).grid(row=0, column=6)
        self.btn_close = self._icon_button(top, "close", self.close)
        self.btn_close.grid(row=0, column=7, padx=(4, 0))

        self.header_line = tk.Frame(body, bg=UK.C.FLOAT_LINE, height=1)
        self.header_line.grid(row=1, column=0, sticky="we", pady=(8, 0))
        viewport = tk.Frame(body, bg=UK.C.FLOAT_BG)
        viewport.grid(row=2, column=0, sticky="nsew")
        body.rowconfigure(2, weight=1)
        self.content_canvas = tk.Canvas(viewport, bg=UK.C.FLOAT_BG, highlightthickness=0,
                                        bd=0, height=50)
        self.scroll = UK.ThinScroll(viewport, self.content_canvas, bg=UK.C.FLOAT_BG, width=4)
        self.content_canvas.pack(side="left", fill="both", expand=True)
        self.content = tk.Frame(self.content_canvas, bg=UK.C.FLOAT_BG)
        self.content.columnconfigure(0, weight=1)
        self._content_window = self.content_canvas.create_window(0, 0, anchor="nw", window=self.content)
        size = getattr(app, "reading_size", 12)
        self._font_src = (UK.FAMILY, size)
        self._font_tr = (UK.FAMILY, size + 1, "normal" if UK.theme() == "dark" else "bold")
        self._font_prev = (UK.FAMILY, max(9, size - 1))
        self._src_text = ""
        self._tr_text = ""
        self._prev_items = []
        self._wrap_px = 0
        # 上一条：淡色留在上方当语境，翻译时不用来回切窗口
        self.prevbox = tk.Frame(self.content, bg=UK.C.FLOAT_BG)
        self.prevbox.grid(row=0, column=0, sticky="we")
        self.prevbox.columnconfigure(0, weight=1)
        self.lbl_src = tk.Label(self.content, text="", bg=UK.C.FLOAT_BG, fg=UK.C.FLOAT_TEXT,
                                font=self._font_src, anchor="nw", justify="left")
        self.lbl_src.grid(row=1, column=0, sticky="we", pady=(9, 8))
        self.content_line = tk.Frame(self.content, bg=UK.C.FLOAT_LINE, height=1)
        self.content_line.grid(row=2, column=0, sticky="we")
        self.trrow = tk.Frame(self.content, bg=UK.C.FLOAT_BG)
        self.trrow.grid(row=3, column=0, sticky="we", pady=(8, 2))
        self.trrow.columnconfigure(0, weight=1)
        self.lbl_arrow = tk.Label(self.trrow, text="", bg=UK.C.FLOAT_BG)
        self.lbl_tr = tk.Label(self.trrow, text="", bg=UK.C.FLOAT_BG, fg=UK.C.FLOAT_ACCENT,
                               font=self._font_tr, anchor="nw", justify="left")
        self.lbl_tr.grid(row=0, column=0, sticky="we")

        # ---- 右下角缩放手柄 ----
        # overrideredirect 窗口没有系统边框，不能靠系统拖边缩放，只能自绘。
        # 内缩 8px：窗口裁了圆角，贴着角画的把手有一半落在裁剪区外。
        self.grip = tk.Canvas(w, width=14, height=14, bg=UK.C.FLOAT_BG,
                              highlightthickness=0, bd=0,
                              cursor="bottom_right_corner")
        self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-8, y=-8)
        for off in (3, 6, 9):
            self.grip.create_line(14 - off, 12, 12, 14 - off,
                                  fill=UK.C.FLOAT_MUTED, width=1)
        self.grip.bind("<Button-1>", self._resize_press)
        self.grip.bind("<B1-Motion>", self._resize_motion)
        self.grip.bind("<ButtonRelease-1>", self._resize_release)

        self._apply_wrap()
        self._fit()
        self._round_window()
        w.bind("<Configure>", self._on_window_configure)

        # ---- 交互 ----
        for wd in (self.card, body, top, self.content_canvas, self.content, self.prevbox,
                   self.lbl_src, self.trrow, self.lbl_arrow, self.lbl_tr, self.lbl_name,
                   self.lbl_time):
            wd.bind("<Button-1>", self._press)
            wd.bind("<B1-Motion>", self._motion)
            wd.bind("<ButtonRelease-1>", self._release)
            wd.bind("<Double-Button-1>", lambda e: self.back_to_main())
        for wd in (self.content_canvas, self.content, self.lbl_src, self.trrow, self.lbl_tr):
            wd.bind("<MouseWheel>", self._scroll_content)
        w.bind("<Button-1>", self._press)
        w.bind("<B1-Motion>", self._motion)
        w.bind("<ButtonRelease-1>", self._release)
        w.bind("<Double-Button-1>", lambda e: self.back_to_main())
        w.bind("<Button-3>", self._menu)
        self._menu_widget = None

    # ---------------- 数据 ----------------
    def _paint_dot(self, color):
        # 内缩留白，四倍采样后缩小，避免原生小椭圆的锯齿及画布边缘裁切。
        if getattr(self, '_dot_color', None) == color:
            return
        shape = Image.new('RGBA', (56, 56))
        ImageDraw.Draw(shape).ellipse((8, 8, 47, 47), fill=color)
        self._dot_image = ImageTk.PhotoImage(
            shape.resize((14, 14), Image.Resampling.LANCZOS), master=self.dot)
        self.dot.itemconfigure(self._dot_id, image=self._dot_image)
        self._dot_color = color

    def sync(self, rec, history=()):
        """灌入最新一条记录；history 为之前的若干条（淡色留在上方当语境）。"""
        text, translation = "", ""
        color = UK.C.FLOAT_ACCENT
        mode = getattr(self.app, "display_mode", "both")
        if rec is None:
            self._full_name = ""
            self.name_separator.grid_remove()
            self.lbl_name.configure(text="")
            self.lbl_time.configure(text="")
            text = "暂无字幕"
            self._paint_dot(UK.C.DISABLED)
        else:
            self.name_separator.grid()
            name = self.app.name_of(rec)
            self._full_name = name
            self._paint_dot(self.app.speaker_color(rec["name"]))
            self.lbl_name.configure(text=UK.fit_text(name, UK.F_NAME, max(24, self.w - 240)))
            self.lbl_time.configure(text=self.app.ts_of(rec))
            if mode != "translation":
                text = rec.get("text", "")
            if mode != "source":
                translation = rec.get("tr") or ""
                if not translation and rec.get("tr_status") == "pending":
                    translation, color = "翻译中…", UK.C.FLOAT_MUTED
                elif not translation and rec.get("tr_status") == "fail":
                    translation, color = "翻译失败", UK.C.RED
                elif not translation and mode == "translation":
                    translation, color = "暂无译文", UK.C.FLOAT_MUTED
        self._src_text = text
        self._tr_text = translation
        self._prev_items = [r for r in history if (r.get("text") or "").strip()]
        self.lbl_src.configure(fg=UK.C.FLOAT_MUTED if rec is None else UK.C.FLOAT_TEXT)
        self.lbl_tr.configure(fg=color)
        if text:
            self.lbl_src.grid()
        else:
            self.lbl_src.grid_remove()
        if translation:
            self.trrow.grid()
        else:
            self.trrow.grid_remove()
        if text and translation:
            self.content_line.grid()
        else:
            self.content_line.grid_remove()
        self._apply_wrap()
        self.content_canvas.yview_moveto(0)
        self._fit()

    def _render_history(self):
        """上一句：小一号的灰色原文，最多两行，超出省略。"""
        for child in self.prevbox.winfo_children():
            child.destroy()
        if not self._prev_items:
            self.prevbox.grid_remove()
            return
        self.prevbox.grid()
        # 只看译文时，上一句也优先给译文，避免两种语言交替打断阅读
        translated_only = getattr(self.app, "display_mode", "both") == "translation"
        for row, rec in enumerate(self._prev_items):
            last = row == len(self._prev_items) - 1
            body = rec.get("text") or ""
            if translated_only and (rec.get("tr") or "").strip():
                body = rec["tr"]
            label = tk.Label(self.prevbox, text="", bg=UK.C.FLOAT_BG, fg=UK.C.FLOAT_MUTED,
                             font=self._font_prev, anchor="nw", justify="left", wraplength=0)
            label.grid(row=row, column=0, sticky="we", pady=(0, 8 if last else 4))
            label.configure(text=UK.wrap_cjk(body, self._font_prev,
                                             self._wrap_px, max_lines=2))
            label.bind("<Button-1>", self._press)
            label.bind("<B1-Motion>", self._motion)
            label.bind("<ButtonRelease-1>", self._release)
            label.bind("<Double-Button-1>", lambda e: self.back_to_main())
            label.bind("<MouseWheel>", self._scroll_content)

    def _scroll_content(self, event):
        self.content_canvas.yview_scroll(-int(event.delta / 120), "units")
        return "break"

    def _icon_button(self, master, icon, command):
        """线条图标不依赖系统字体的符号形状。"""
        button = tk.Canvas(master, width=30, height=28, bg=UK.C.FLOAT_BG,
                           highlightthickness=0, bd=0, cursor="hand2", takefocus=True)
        stroke = dict(fill=UK.C.FLOAT_MUTED, width=1.5, capstyle="round", joinstyle="round")
        if icon == "back":
            button.create_line(15, 6, 23, 6, 23, 22, 15, 22, **stroke)
            button.create_line(6, 14, 18, 14, **stroke)
            button.create_line(10, 10, 6, 14, 10, 18, **stroke)
        else:
            button.create_line(8, 7, 22, 21, **stroke)
            button.create_line(22, 7, 8, 21, **stroke)
        button.bind("<Enter>", lambda e: button.configure(bg=UK.C.A50))
        button.bind("<Leave>", lambda e: button.configure(bg=UK.C.FLOAT_BG))
        button.bind("<FocusIn>", lambda e: button.configure(bg=UK.C.A50))
        button.bind("<FocusOut>", lambda e: button.configure(bg=UK.C.FLOAT_BG))
        button.bind("<Button-1>", lambda e: "break")
        def activate(event):
            command()
            return "break"
        button.bind("<ButtonRelease-1>", activate)
        button.bind("<Return>", activate)
        button.bind("<space>", activate)
        return button

    def _on_window_configure(self, event):
        if event.widget is self.win:
            self._round_window()

    def _round_window(self):
        """以原生窗口的实际边界裁四角，尺寸改变后重新计算。"""
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            u32.GetAncestor.restype = wintypes.HWND
            u32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
            hwnd = u32.GetAncestor(self.win.winfo_id(), 2)
            if not hwnd:
                return
            rect = wintypes.RECT()
            if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width <= 1 or height <= 1:
                return
            # Tk 已经处理 DPI；再次乘 GetDpiForWindow 会让三个圆角落到窗外。
            self.dpi_scale = width / max(1, self.win.winfo_width())
            diameter = max(2, round(RADIUS * 2 * self.dpi_scale))
            geometry = (hwnd, width, height, diameter)
            if geometry == getattr(self, "_region_geometry", None):
                return
            gdi = ctypes.windll.gdi32
            gdi.CreateRoundRectRgn.argtypes = (ctypes.c_int,) * 6
            gdi.CreateRoundRectRgn.restype = wintypes.HRGN
            gdi.DeleteObject.argtypes = (wintypes.HGDIOBJ,)
            u32.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN, wintypes.BOOL)
            region = gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1, diameter, diameter)
            if not region:
                return
            if u32.SetWindowRgn(hwnd, region, True):
                self._region_geometry = geometry  # 成功后区域由系统持有。
            else:
                gdi.DeleteObject(region)
        except (tk.TclError, OSError):
            pass

    def _apply_wrap(self):
        """文字随窗口宽度换行（自己排，见 UK.wrap_cjk）。"""
        if hasattr(self, '_full_name'):
            self.lbl_name.configure(text=UK.fit_text(self._full_name, UK.F_NAME, max(24, self.w - 240)))
        usable = max(120, self.w - 2 * self.card.pad - 10)
        if self.scroll.winfo_manager():
            usable = max(120, usable - 18)
        self.content_canvas.itemconfigure(self._content_window, width=usable)
        self._wrap_px = max(60, usable - 6)
        # wraplength=0：交给 wrap_cjk 断行，避免 Tk 只在空格处断造成的孤字行
        self.lbl_src.configure(wraplength=0,
                               text=UK.wrap_cjk(self._src_text, self._font_src, self._wrap_px))
        self.lbl_tr.configure(wraplength=0,
                              text=UK.wrap_cjk(self._tr_text, self._font_tr, self._wrap_px))
        self._render_history()

    def _fit(self):
        """自适应内容；超长字幕限制高度并提供滚动。"""
        try:
            max_h = max(MIN_H, int(self.win.winfo_screenheight() * 0.55))
            self._apply_wrap()
            self.card.update_idletasks()
            content_h = self.content.winfo_reqheight()
            overhead = self.card.body.winfo_reqheight() - self.content_canvas.winfo_reqheight() + 2 * self.card.pad + 12
            viewport_h = max(30, max_h - overhead)
            needs_scroll = content_h > viewport_h
            if needs_scroll and not self.scroll.winfo_manager():
                self.scroll.pack(side="right", fill="y", before=self.content_canvas)
                self._apply_wrap()
                self.card.update_idletasks()
                content_h = self.content.winfo_reqheight()
            elif not needs_scroll and self.scroll.winfo_manager():
                self.scroll.pack_forget()
                self._apply_wrap()
                self.card.update_idletasks()
                content_h = self.content.winfo_reqheight()
            self.content_canvas.configure(height=min(content_h, viewport_h),
                                          scrollregion=(0, 0, 0, content_h))
            h = max(MIN_H, min(max_h, max(overhead + content_h, self.user_h)))
            if abs(h - self.h) > 1:
                self.h = h
                x, y = self.win.winfo_x(), self.win.winfo_y()
                self.win.geometry(f"{self.w}x{h}+{x}+{y}")
                self._round_window()
        except tk.TclError:
            pass

    # ---------------- 缩放 ----------------
    def _resize_press(self, e):
        self._resize = (e.x_root, e.y_root, self.w, self.h)
        return "break"

    def _resize_motion(self, e):
        if not self._resize:
            return "break"
        x0, y0, w0, h0 = self._resize
        nw = max(MIN_W, w0 + (e.x_root - x0))
        nh = max(MIN_H, h0 + (e.y_root - y0))
        # self.h 必须同步跟上：_fit 用它与目标高度比较，不同步的话松开鼠标后
        # 会误判「尺寸没变」而跳过重排。
        self.w, self.user_h, self.h = nw, nh, nh
        self._apply_wrap()
        try:
            self.win.geometry(f"{nw}x{nh}+{self.win.winfo_x()}+{self.win.winfo_y()}")
        except tk.TclError:
            pass
        self._round_window()
        return "break"

    def _resize_release(self, _=None):
        if not self._resize:
            return "break"
        self._resize = None
        self._fit()                     # 内容比拖出来的高度更高时自动补足
        self._save_size()
        return "break"

    def _save_size(self):
        cfg = getattr(self.app, "tcfg", None)
        if cfg is None:
            return
        try:
            cfg.float_w = int(self.w)
            cfg.float_h = int(self.h)
            cfg.save()
        except Exception:
            pass

    # ---------------- 交互 ----------------
    def _press(self, e):
        if e.widget is self.grip:
            return
        self._drag = (e.x_root - self.win.winfo_x(),
                      e.y_root - self.win.winfo_y())

    def _motion(self, e):
        if e.widget is self.grip or not self._drag:
            return
        self.win.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _release(self, _):
        self._drag = None

    def back_to_main(self):
        self.app.hide_float()

    def close(self):
        self.app.set_float(False)

    def _menu(self, e):
        m = tk.Menu(self.win, tearoff=0)
        UK.style_menu(m)
        m.add_command(label="回到主界面", command=self.back_to_main)
        m.add_separator()
        m.add_command(label="恢复默认大小",
                      command=lambda: self.reset_size())
        m.add_command(label="关闭悬浮窗", command=self.close)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def reset_size(self):
        """回到默认宽 + 按内容自适应高度。"""
        self.w, self.user_h = W, 0
        self._apply_wrap()
        self._fit()
        self._save_size()

    # ---------------- 生命周期 ----------------
    def repaint(self):
        """主题切换后整体重建（App 负责调用）。"""
        w, h, user_h = self.w, self.h, self.user_h
        self.destroy()
        bar = FloatBar(self.app)
        bar.w, bar.h, bar.user_h = w, h, user_h
        try:
            # 新实例按配置值建窗，这里要按旧的实时尺寸摆回去（_fit 会因
            # 「尺寸没变」提前返回，不会替我们改窗口）
            bar.win.geometry(f"{w}x{h}+{bar.win.winfo_x()}+{bar.win.winfo_y()}")
        except tk.TclError:
            pass
        bar._apply_wrap()
        bar._fit()
        bar._round_window()
        self.app.float_bar = bar
        self.app.float_sync(force=True)   # 新实例标签为空，强制灌一次

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

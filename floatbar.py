# -*- coding: utf-8 -*-
"""floatbar —— 悬浮字幕窗

一个无边框、置顶的小窗：滚动显示最近若干条原文与译文（新句在底部，旧句留在上方
直到滚出去），可拖动、可缩放、可回主界面。
颜色全部走 ui_kit 令牌，跟随深浅主题；`overrideredirect` 自绘圆角与按钮。

窗口本身是直角的（Windows 无边框窗口默认如此）。只把卡片画圆没有用：卡片
外面那圈窗口底色会在四角露出来，看起来就是个「方底托着一张圆卡」，越靠边
越明显。所以这里用 SetWindowRgn 把整个窗口裁成圆角，露出来的四角是桌面。
"""
import os
import tkinter as tk
from PIL import Image, ImageDraw, ImageTk

import ui_kit as UK

W, H = 480, 300          # 默认尺寸：能放下三四条带译文的字幕
MIN_W, MIN_H = 300, 120  # 缩放下限：再小连一条字幕都放不下
KEEP = 40                # 最多保留多少条；更早的回主界面看
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
        # 「哪个窗口属于 TeamSub」。overrideredirect 窗口不显示标题栏，但
        # 窗口文本照样存在；标题为空时新实例找不到任何窗口，只能放弃启动。
        w.title("TeamSub 悬浮字幕")
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
        # self.w/self.h 都是逻辑像素，只在落到窗口几何时换算
        w.geometry(f"{UK.px(self.w)}x{UK.px(self.h)}+{w.winfo_screenwidth() - UK.px(self.w + 48)}+{UK.px(96)}")

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
        self.lbl_name = tk.Label(top, text="", bg=UK.C.FLOAT_BG, fg=UK.C.FLOAT_MUTED,
                                 font=UK.F_MINI, anchor="w")
        self.lbl_name.grid(row=0, column=1, columnspan=4, sticky="we")
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
        self._wrap_px = 0
        self._entries = []          # 每条字幕一组控件，旧 → 新
        self._follow = True         # 停在底部时新句自动滚入；用户往上翻就不打扰
        self.lbl_empty = tk.Label(self.content, text="暂无字幕", bg=UK.C.FLOAT_BG,
                                  fg=UK.C.FLOAT_MUTED, font=self._font_src, anchor="w")

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
        for wd in (self.card, body, top, self.content_canvas, self.content, self.lbl_name,
                   self.lbl_empty):
            self._bind_drag(wd)
        for wd in (self.content_canvas, self.content, self.lbl_empty):
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
            shape.resize((UK.px(14), UK.px(14)), Image.Resampling.LANCZOS), master=self.dot)
        self.dot.itemconfigure(self._dot_id, image=self._dot_image)
        self._dot_color = color

    def _bind_drag(self, wd):
        wd.bind("<Button-1>", self._press)
        wd.bind("<B1-Motion>", self._motion)
        wd.bind("<ButtonRelease-1>", self._release)
        wd.bind("<Double-Button-1>", lambda e: self.back_to_main())

    def sync(self, rec, history=()):
        """灌入最新一条与之前的若干条：逐条增量更新，旧句不清空。"""
        records = [r for r in list(history)[-(KEEP - 1):] if (r.get("text") or "").strip()]
        if rec is not None:
            records.append(rec)
        self._paint_dot(UK.C.DISABLED if rec is None
                        else self.app.speaker_color(rec["name"]))
        self._show_meeting()
        # 先记下是否停在底部：增删条目后 yview 会变
        try:
            self._follow = not self._entries or self.content_canvas.yview()[1] >= 0.999
        except tk.TclError:
            self._follow = True
        keyed = {e["id"]: e for e in self._entries}
        wanted = [r.get("id") or id(r) for r in records]
        for entry in self._entries:
            if entry["id"] not in wanted:
                entry["frame"].destroy()
        entries = []
        for row, r in enumerate(records):
            entry = keyed.get(wanted[row]) or self._new_entry(wanted[row])
            self._fill_entry(entry, r, latest=row == len(records) - 1)
            entry["frame"].grid(row=row, column=0, sticky="we", pady=(0, 10))
            entries.append(entry)
        self._entries = entries
        if entries:
            self.lbl_empty.grid_remove()
        else:
            self.lbl_empty.grid(row=0, column=0, sticky="we", pady=(8, 0))
        self._fit()

    def _show_meeting(self):
        meeting = getattr(self.app, "_meeting_txt", "") or ""
        self.lbl_name.configure(text=UK.fit_text(meeting, UK.F_MINI, max(40, self.w - 120)))

    def _new_entry(self, rid):
        frame = tk.Frame(self.content, bg=UK.C.FLOAT_BG)
        frame.columnconfigure(0, weight=1)
        head = tk.Frame(frame, bg=UK.C.FLOAT_BG)
        head.grid(row=0, column=0, sticky="we")
        name = tk.Label(head, text="", bg=UK.C.FLOAT_BG, font=UK.F_NAME)
        name.pack(side="left")
        when = tk.Label(head, text="", bg=UK.C.FLOAT_BG, fg=UK.C.FLOAT_MUTED, font=UK.F_TIME)
        when.pack(side="left", padx=(8, 0))
        src = tk.Label(frame, text="", bg=UK.C.FLOAT_BG, font=self._font_src,
                       anchor="nw", justify="left")
        tr = tk.Label(frame, text="", bg=UK.C.FLOAT_BG, font=self._font_tr,
                      anchor="nw", justify="left")
        for wd in (frame, head, name, when, src, tr):
            self._bind_drag(wd)
            wd.bind("<MouseWheel>", self._scroll_content)
        return dict(id=rid, sig=None, frame=frame, name=name, time=when, src=src, tr=tr,
                    src_text="", tr_text="")

    def _fill_entry(self, entry, rec, latest):
        mode = getattr(self.app, "display_mode", "both")
        text, translation, color = "", "", UK.C.FLOAT_ACCENT
        if mode != "translation":
            text = rec.get("text", "")
        if mode != "source":
            translation = rec.get("tr") or ""
            if not translation and rec.get("tr_status") == "pending":
                translation, color = "翻译中…", UK.C.FLOAT_MUTED
            elif not translation and rec.get("tr_status") == "fail":
                translation, color = "翻译失败", UK.C.RED
            elif not translation and mode == "translation":
                # 只看译文时没有译文（同语种、未开翻译）就退回原文，不留空条
                translation, color = rec.get("text", ""), UK.C.FLOAT_TEXT
        stamp = self.app.ts_of(rec)
        sig = (rec.get("name"), text, translation, color, latest, stamp)
        if sig == entry["sig"]:
            return
        entry["sig"] = sig
        entry["src_text"], entry["tr_text"] = text, translation
        entry["name"].configure(text=self.app.name_of(rec),
                                fg=self.app.speaker_color(rec["name"]))
        entry["time"].configure(text=stamp)
        # 旧句原文略淡，视线自然落到最新一句；译文保持强调色，回看时照样好读
        entry["src"].configure(fg=UK.C.FLOAT_TEXT if latest else UK.C.FLOAT_MUTED)
        entry["tr"].configure(fg=color)
        self._wrap_entry(entry)

    def _wrap_entry(self, entry):
        width = self._wrap_px
        if entry["src_text"]:
            entry["src"].configure(text=UK.wrap_cjk(entry["src_text"], self._font_src, width))
            entry["src"].grid(row=1, column=0, sticky="we", pady=(2, 0))
        else:
            entry["src"].grid_remove()
        if entry["tr_text"]:
            entry["tr"].configure(text=UK.wrap_cjk(entry["tr_text"], self._font_tr, width))
            entry["tr"].grid(row=2, column=0, sticky="we", pady=(2, 0))
        else:
            entry["tr"].grid_remove()

    def _scroll_content(self, event):
        self.content_canvas.yview_scroll(-int(event.delta / 120), "units")
        try:
            self._follow = self.content_canvas.yview()[1] >= 0.999
        except tk.TclError:
            pass
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
            self.dpi_scale = width / max(1, self.win.winfo_width()) * UK.S
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
        usable = max(120, self.w - 2 * self.card.pad - 10)
        if self.scroll.winfo_manager():
            usable = max(120, usable - 18)
        self.content_canvas.itemconfigure(self._content_window, width=usable)
        wrap = max(60, usable - 6)
        if wrap != self._wrap_px:
            self._wrap_px = wrap
            for entry in self._entries:
                self._wrap_entry(entry)

    def _fit(self):
        """窗口高度固定（默认值或用户拖出来的），内容超出时滚动；跟随时贴底。"""
        try:
            h = max(MIN_H, self.user_h or H)
            if abs(h - self.h) > 1:
                self.h = h
                self.win.geometry(f"{UK.px(self.w)}x{UK.px(h)}+{self.win.winfo_x()}+{self.win.winfo_y()}")
                self._round_window()
            self._apply_wrap()
            self.card.update_idletasks()
            content_h = UK.lpx(self.content.winfo_reqheight())
            viewport_h = max(30, UK.lpx(self.content_canvas.winfo_height()))
            needs_scroll = content_h > viewport_h
            if needs_scroll != bool(self.scroll.winfo_manager()):
                if needs_scroll:
                    self.scroll.pack(side="right", fill="y", before=self.content_canvas)
                else:
                    self.scroll.pack_forget()
                self._apply_wrap()
                self.card.update_idletasks()
                content_h = UK.lpx(self.content.winfo_reqheight())
            self.content_canvas.configure(scrollregion=(0, 0, 0, max(content_h, viewport_h)))
            if self._follow:
                self.content_canvas.yview_moveto(1.0)
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
        nw = max(MIN_W, w0 + UK.lpx(e.x_root - x0))
        nh = max(MIN_H, h0 + UK.lpx(e.y_root - y0))
        # self.h 必须同步跟上：_fit 用它与目标高度比较，不同步的话松开鼠标后
        # 会误判「尺寸没变」而跳过重排。
        self.w, self.user_h, self.h = nw, nh, nh
        self._apply_wrap()
        self._show_meeting()
        try:
            self.win.geometry(f"{UK.px(nw)}x{UK.px(nh)}+{self.win.winfo_x()}+{self.win.winfo_y()}")
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
            bar.win.geometry(f"{UK.px(w)}x{UK.px(h)}+{bar.win.winfo_x()}+{bar.win.winfo_y()}")
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

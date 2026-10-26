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

import ui_kit as UK

W, H = 460, 132          # 默认尺寸
MIN_W, MIN_H = 300, 92   # 缩放下限：再小连说话人一行都放不下
ALPHA = 0.96             # 不透明度


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
        w.configure(bg=UK.C.N0)

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
        self.card = UK.Card(w, radius=UK.R_CARD, padding=14, bg=UK.C.N0)
        self.card.pack(fill="both", expand=True)

        body = self.card.body
        body.columnconfigure(0, weight=1)

        # ---- 顶部：状态点 + 说话人 + 时间 + 右侧两个按钮 ----
        top = tk.Frame(body, bg=UK.C.N0)
        top.grid(row=0, column=0, sticky="we")
        top.columnconfigure(1, weight=1)
        self.dot = tk.Canvas(top, width=8, height=8, bg=UK.C.N0,
                             highlightthickness=0, bd=0)
        self.dot.grid(row=0, column=0, padx=(0, 6))
        self._dot_id = self.dot.create_oval(0, 0, 8, 8, fill=UK.C.GREEN_F, outline="")
        self.lbl_name = tk.Label(top, text="", bg=UK.C.N0, fg=UK.C.TEXT,
                                 font=UK.F_SMALL)
        self.lbl_name.grid(row=0, column=1, sticky="w")
        self.lbl_time = tk.Label(top, text="", bg=UK.C.N0, fg=UK.C.T3,
                                 font=UK.F_MINI)
        self.lbl_time.grid(row=0, column=2, sticky="e", padx=(8, 8))
        for i, (text, cmd) in enumerate((("⤢", self.back_to_main), ("✕", self.close))):
            b = UK.Pill(top, text, cmd, kind="ghost", height=22, padx=8,
                        font=UK.F_SMALL, bg=UK.C.N0)
            b.grid(row=0, column=3 + i, padx=(2, 0))

        # ---- 原文 ----
        self.lbl_src = tk.Label(body, text="", bg=UK.C.N0, fg=UK.C.TEXT,
                                font=(UK.FAMILY, getattr(app, "reading_size", 12)),
                                anchor="w", justify="left")
        self.lbl_src.grid(row=1, column=0, sticky="we", pady=(6, 0))

        # ---- 译文 ----
        # 箭头单独占一列：Tk 的 Label 只在空格处折行，中文长句整段会被当成
        # 一个「词」整体推到下一行，箭头就孤零零留在上一行（"→" 独占一行）。
        # 拆成两列后箭头永远贴着第一行，正文自己换行。
        self.trrow = tk.Frame(body, bg=UK.C.N0)
        self.trrow.grid(row=2, column=0, sticky="we", pady=(3, 0))
        self.trrow.columnconfigure(1, weight=1)
        font = (UK.FAMILY, getattr(app, "reading_size", 12))
        self.lbl_arrow = tk.Label(self.trrow, text="", bg=UK.C.N0, fg=UK.C.A500,
                                  font=font, anchor="nw")
        self.lbl_arrow.grid(row=0, column=0, sticky="nw", padx=(0, 6))
        self.lbl_tr = tk.Label(self.trrow, text="", bg=UK.C.N0, fg=UK.C.A500,
                               font=font, anchor="w", justify="left")
        self.lbl_tr.grid(row=0, column=1, sticky="we")

        # ---- 右下角缩放手柄 ----
        # overrideredirect 窗口没有系统边框，不能靠系统拖边缩放，只能自绘。
        # 内缩 8px：窗口裁了圆角，贴着角画的把手有一半落在裁剪区外。
        self.grip = tk.Canvas(w, width=14, height=14, bg=UK.C.N0,
                              highlightthickness=0, bd=0,
                              cursor="bottom_right_corner")
        self.grip.place(relx=1.0, rely=1.0, anchor="se", x=-8, y=-8)
        for off in (3, 6, 9):
            self.grip.create_line(14 - off, 12, 12, 14 - off,
                                  fill=UK.C.T3, width=1)
        self.grip.bind("<Button-1>", self._resize_press)
        self.grip.bind("<B1-Motion>", self._resize_motion)
        self.grip.bind("<ButtonRelease-1>", self._resize_release)

        self._apply_wrap()
        self._fit()
        self._round_window()

        # ---- 交互 ----
        for wd in (self.card, body, top, self.lbl_src, self.trrow,
                   self.lbl_arrow, self.lbl_tr, self.lbl_name, self.lbl_time):
            wd.bind("<Button-1>", self._press)
            wd.bind("<B1-Motion>", self._motion)
            wd.bind("<ButtonRelease-1>", self._release)
            wd.bind("<Double-Button-1>", lambda e: self.back_to_main())
        w.bind("<Button-1>", self._press)
        w.bind("<B1-Motion>", self._motion)
        w.bind("<ButtonRelease-1>", self._release)
        w.bind("<Double-Button-1>", lambda e: self.back_to_main())
        w.bind("<Button-3>", self._menu)
        self._menu_widget = None

    # ---------------- 数据 ----------------
    def sync(self, rec):
        """灌入最新一条记录；rec 为 None 时显示空态。"""
        arrow, body = "", ""
        if rec is None:
            self.lbl_name.configure(text="")
            self.lbl_time.configure(text="")
            self.lbl_src.configure(text="暂无字幕", fg=UK.C.T3)
            self.dot.itemconfigure(self._dot_id, fill=UK.C.DISABLED)
            color = UK.C.A500
        else:
            name = self.app.name_of(rec)
            color = UK.C.A500
            self.dot.itemconfigure(self._dot_id,
                                   fill=self.app.speaker_color(rec["name"]))
            self.lbl_name.configure(text=name, fg=UK.C.TEXT)
            self.lbl_time.configure(text=self.app.ts_of(rec))
            mode = getattr(self.app, "display_mode", "both")
            self.lbl_src.configure(
                text=rec.get("text", "") if mode != "translation" else "",
                fg=UK.C.TEXT)
            tr = rec.get("tr") or ""
            if mode != "source":
                if tr:
                    arrow, body = "→", tr
                elif rec.get("tr_status") == "pending":
                    arrow, body, color = "→", "翻译中…", UK.C.T3
                elif rec.get("tr_status") == "fail":
                    arrow, body, color = "→", "翻译失败", UK.C.RED
        self.lbl_arrow.configure(text=arrow, fg=color)
        self.lbl_tr.configure(text=body, fg=color)
        self._fit()

    def _round_window(self):
        """把整个窗口裁成圆角（Windows 上无边框窗口默认是直角的）。

        只把卡片画圆没用：卡片和窗口边缘之间还有一圈窗口底色，四个角会把它
        露成方底。用 SetWindowRgn 直接裁窗口，四角透出的是桌面，和卡片弧线
        完全重合。几何每次变化都要重裁一次，否则拖大之后右/下边是直的。
        """
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes
            u32 = ctypes.windll.user32
            u32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            u32.GetAncestor.restype = wintypes.HWND
            hwnd = u32.GetAncestor(self.win.winfo_id(), 2)   # GA_ROOT
            if not hwnd:
                return
            scale = 1.0
            try:
                dpi = u32.GetDpiForWindow(wintypes.HWND(hwnd)) or 96
                scale = max(1.0, dpi / 96.0)
            except Exception:
                pass
            # 记下来：Tk 的 geometry 是逻辑像素，裁剪区要按物理像素算，
            # 125% 缩放下两者差 1.25 倍（回归测试也用它换算）
            self.dpi_scale = scale
            gdi = ctypes.windll.gdi32
            gdi.CreateRoundRectRgn.argtypes = (ctypes.c_int,) * 6
            gdi.CreateRoundRectRgn.restype = wintypes.HRGN
            u32.SetWindowRgn.argtypes = (wintypes.HWND, wintypes.HRGN, wintypes.BOOL)
            r = int(round(UK.R_CARD * 2 * scale))        # 参数是直径，不是半径
            width = int(round(self.w * scale))
            height = int(round(self.h * scale))
            rgn = gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1, r, r)
            if not rgn:
                return
            # 成功后区域归系统所有，不能再 DeleteObject
            if not u32.SetWindowRgn(hwnd, rgn, True):
                gdi.DeleteObject(rgn)
        except Exception:
            pass

    def _apply_wrap(self):
        """换行宽度跟随窗口宽度。

        固定值（旧版是 W-60）在用户把条子拖宽后会让文本提前折行、右侧留出
        一大片空白；拖窄时又会顶破卡片右边。这里按「窗口宽 - 卡片内边距」
        实时算（窗口已无外圈边框，只扣卡片自身的内边距）。
        """
        usable = max(120, self.w - 2 * self.card.pad - 10)
        self.lbl_src.configure(wraplength=usable)
        # 译文要扣掉箭头那一列，否则最后一行会顶到右边多出几个字
        self.lbl_tr.configure(wraplength=max(80, usable - 22))

    def _fit(self):
        """按内容重排窗口高度（宽度由用户决定，保持位置不变）。

        不能用 ``card.cv.winfo_reqheight()`` 反推：那是上一帧的分配值，短文本
        时窗口会被连着两帧地压小，最后一行文字就压在卡片边缘外（旧版「文字
        漫出框」就是这么来的）。直接问 body 要「内容真正需要多高」。
        """
        try:
            self.card.update_idletasks()
            need = self.card.body.winfo_reqheight() + 2 * self.card.pad + 12
        except tk.TclError:
            return
        h = max(need, MIN_H, self.user_h)
        if abs(h - self.h) <= 1:
            return
        self.h = h
        try:
            x, y = self.win.winfo_x(), self.win.winfo_y()
            self.win.geometry(f"{self.w}x{h}+{x}+{y}")
        except tk.TclError:
            pass
        self._round_window()            # 尺寸变了，裁剪区必须跟着换

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

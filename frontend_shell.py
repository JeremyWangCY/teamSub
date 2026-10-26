"""Workspace shell; App owns capture and translation state."""
import tkinter as tk
import ui_kit as UK
import modes as MD

def build(self):
    r = self.root
    m = MD.get(self.mode) or MD.get(MD.DEFAULT_MODE)
    r.title(f"TeamsSub · {m['short']} 实时字幕")
    if not getattr(self, "_built_once", False):
        r.geometry("1280x820")
    self._built_once = True
    r.minsize(1000, 640)
    r.configure(bg=UK.C.N50)

    self.f_ui, self.f_ui_b = UK.F_UI, UK.F_HEAD
    self.f_small, self.f_mono, self.f_body = UK.F_SMALL, UK.F_BODY, UK.F_BODY
    self.var_pin = tk.BooleanVar(value=self.pinned)
    self.var_simplify = tk.BooleanVar(value=self.simplify)

    # Keep destinations together; meeting actions live beside the transcript.
    nav = tk.Frame(r, bg=UK.C.N100, width=160)
    nav.pack(side="left", fill="y")
    nav.pack_propagate(False)
    tk.Label(nav, text="TeamsSub", font=UK.F_HEAD, bg=UK.C.N100, fg=UK.C.TEXT).pack(
        anchor="w", padx=22, pady=(24, 20))
    self.nav_buttons = {}
    for label, action in [("实时会议", self.show_live), ("历史会议", self.open_history),
                          ("设置", self.open_settings)]:
        btn = UK.Pill(nav, label, action, kind="selected" if label == "实时会议" else "ghost",
                      height=42, padx=14, bg=UK.C.N100, font=(UK.FAMILY, 11), align="left")
        btn.pack(fill="x", padx=8, pady=2)
        self.nav_buttons[label] = btn
    help_btn = UK.Pill(nav, "帮助", self.show_help, kind="ghost", height=42, padx=14,
                       bg=UK.C.N100, font=(UK.FAMILY, 11), align="left")
    help_btn.pack(side="bottom", fill="x", padx=8, pady=(0, 16))
    self.nav_buttons["帮助"] = help_btn
    r = tk.Frame(self.root, bg=UK.C.N50)
    r.pack(side="left", fill="both", expand=True)
    # ---------- 顶栏 ----------
    head = tk.Frame(r, bg=UK.C.N50)
    head.pack(fill="x", padx=24, pady=(20, 10))

    lt = tk.Frame(head, bg=UK.C.N50)
    lt.pack(side="left")
    # 标题行：产品名 + 会议模式切换控件（入口在界面左上角）
    trow = tk.Frame(lt, bg=UK.C.N50)
    trow.pack(anchor="w")
    tk.Label(trow, text="实时会议", bg=UK.C.N50, fg=UK.C.TEXT,
             font=UK.F_TITLE).pack(side="left")
    self.mode_sw = UK.ModeSwitch(trow, modes=MD.all_modes(), value=self.mode,
                                 command=self.set_mode,
                                 on_blocked=self.on_mode_blocked,
                                 bg=UK.C.N50)
    self.mode_sw.pack(side="left", padx=(14, 0))
    self.lbl_meeting = tk.Label(lt, text=self._meeting_txt, bg=UK.C.N50,
                                fg=UK.C.SUB, font=UK.F_SMALL)
    self.lbl_meeting.pack(anchor="w", pady=(3, 0))

    rt = tk.Frame(head, bg=UK.C.N50)
    rt.pack(side="right")
    self.status = UK.StatusPill(rt, "连接中", bg=UK.C.N50)
    self.status.pack(side="left", padx=(0, 10))
    self.btn_export = UK.Pill(rt, "导出整场会议", self.export, kind="primary", height=36)
    self.btn_export.pack(side="right", padx=(12, 0))
    self.btn_more = UK.Pill(rt, "⋯", self._open_menu, kind="ghost", padx=11)
    self.btn_more.pack(side="right", padx=(8, 0))
    self.btn_settings = UK.Pill(rt, "设置", self.open_settings, kind="ghost")
    # 设置入口也常驻在左侧导航里，这里不再重复放一个按钮。
    self.btn_tr = UK.Pill(rt, "翻译 开" if self.trans_on else "翻译 关", self.toggle_translate,
                          kind="tint" if self.trans_on else "ghost")
    self.btn_tr.pack(side="right")

    # ---------- 检索 + 翻译方向 ----------
    bar = tk.Frame(r, bg=UK.C.N50)
    bar.pack(fill="x", padx=24, pady=(0, 14))
    self.search = UK.SearchBox(bar, width=240, placeholder="搜索原文、译文或发言人",
                               on_change=self.apply_search, bg=UK.C.N50)
    self.search.pack(side="left")
    self.seg = UK.Segmented(bar, ["双向互译", "全部译中", "全部译英"],
                            command=self.on_mode_pick, bg=UK.C.N50)
    self.seg.pack(side="right")
    self.seg.set({"auto": 0, "zh": 1, "en": 2}.get(self.tcfg.mode, 0))

    # ---------- 内容层（sheet 会覆盖在这一层之上） ----------
    self.content = tk.Frame(r, bg=UK.C.N50)
    self.content.pack(fill="both", expand=True)

    # ---------- 底栏 ----------
    foot = tk.Frame(self.content, bg=UK.C.N50)
    foot.pack(fill="x", side="bottom", padx=24, pady=(10, 16))
    self.lbl_stat = tk.Label(foot, text=f"已捕获 {len(self.records)} 条 · {len({r['name'] for r in self.records})} 位发言人", bg=UK.C.N50,
                             fg=UK.C.SUB, font=UK.F_MINI)
    self.lbl_stat.pack(side="left")
    self.lbl_cache = tk.Label(foot, text="", bg=UK.C.N50,
                              fg=UK.C.T3, font=UK.F_MINI)
    self.lbl_cache.pack(side="left", padx=(16, 0))
    self.lbl_save = tk.Label(foot, text=f"已保存 · {self.save_path.stem[-12:]}" if self.save_path else "自动保存至本机", bg=UK.C.N50,
                             fg=UK.C.T3, font=UK.F_MINI)
    self.lbl_save.pack(side="right")

    # ---------- 主体 ----------
    body = tk.Frame(self.content, bg=UK.C.N50)
    body.pack(fill="both", expand=True, padx=24)

    side = UK.Card(body, radius=UK.R_CARD, padding=16, bg=UK.C.N50)
    side.configure(width=200)
    side.pack(side="left", fill="y")
    side.pack_propagate(False)
    tk.Label(side.body, text="发言人", bg=UK.C.N0, fg=UK.C.T3,
             font=UK.F_MINI).pack(anchor="w", padx=8, pady=(0, 8))
    self.speakers = UK.SpeakerList(side.body, on_pick=self.on_pick_speaker,
                                   bg=UK.C.N0)
    self.speakers.pack(fill="both", expand=True)

    readbar = tk.Frame(self.content, bg=UK.C.N50)
    readbar.pack(fill="x", padx=24, pady=(0, 10), before=body)
    self.display_switch = UK.Segmented(readbar, ["双语", "原文", "译文"], command=self.set_display, bg=UK.C.N50)
    self.display_switch.pack(side="left")
    self.display_switch.set({"both":0, "source":1, "translation":2}.get(getattr(self, "display_mode", "both"), 0))
    UK.Pill(readbar, "字号 / 间距", self.open_reading, kind="ghost").pack(side="left", padx=12)
    self.btn_pause = UK.Pill(readbar, "继续采集" if self.paused else "暂停采集", self.toggle_pause, kind="ghost")
    self.btn_pause.pack(side="right")
    UK.Pill(readbar, "结束", self.end_meeting, kind="danger", padx=10).pack(side="right", padx=4)
    UK.Pill(readbar, "新建", self.new_meeting, kind="ghost", padx=10).pack(side="right", padx=4)
    UK.Pill(readbar, "清屏", self.clear, kind="ghost").pack(side="right", padx=8)
    card = UK.Card(body, radius=UK.R_CARD, padding=16, bg=UK.C.N50)
    card.pack(side="left", fill="both", expand=True, padx=(14, 0))

    self.txt = tk.Text(card.body, wrap="word", bg=UK.C.N0, fg=UK.C.TEXT,
                       font=UK.F_BODY, bd=0, highlightthickness=0,
                       padx=6, pady=0, spacing1=2, spacing3=6,
                       selectbackground=UK.C.A100, selectforeground=UK.C.TEXT,
                       insertbackground=UK.C.TEXT, undo=False)
    sb = UK.ThinScroll(card.body, self.txt, bg=UK.C.N0)
    sb.pack(side="right", fill="y", padx=(6, 0))
    self.txt.pack(side="left", fill="both", expand=True)

    # 正文标签由 transcript_view 统一配置：实时会议与历史阅读共用一份，
    # 改字号/缩进只改一处，两个界面不会走形
    import transcript_view as TV
    TV.configure_tags(self.txt, reading_size=getattr(self, "reading_size", 12),
                      density=getattr(self, "density", "comfort"))
    self.txt.tag_configure("empty", foreground=UK.C.T3, font=UK.F_UI,
                           spacing1=4, spacing3=4)

    self.to_bottom = UK.Pill(card.body, "↓  回到底部", self._jump_bottom,
                             kind="tint", height=28)

    self.empty_box = tk.Frame(card.body, bg=UK.C.N0)
    tk.Label(self.empty_box, text="暂无字幕", bg=UK.C.N0, fg=UK.C.T3,
             font=UK.F_UI).pack()
    tk.Label(self.empty_box, text="请在会议软件中开启实时字幕", bg=UK.C.N0,
             fg=UK.C.SUB, font=UK.F_SMALL, justify="center").pack(pady=(12, 0))
    self.empty_box.place(relx=0.5, rely=0.38, anchor="center")

    self.txt.tag_bind("trfail", "<Button-1>", self.on_retry_click)
    self.txt.tag_bind("trfail", "<Enter>",
                      lambda e: self.txt.configure(cursor="hand2"))
    self.txt.tag_bind("trfail", "<Leave>",
                      lambda e: self.txt.configure(cursor="xterm"))
    self.txt.bind("<Button-1>", self.on_click, add="+")
    self.txt.bind("<MouseWheel>", self.on_wheel, add="+")
    self.txt.bind("<Double-Button-1>", self.on_dblclick)
    self.txt.bind("<Button-3>", self.on_right_click)
    # 右键菜单：命中译文行时 on_right_click 会返回 "break" 接管（修正译法），
    # 没命中就落到这里弹复制菜单——只读正文也能一键复制。
    UK.attach_text_menu(self.txt)

    # ---------- 内推层 ----------
    self.sheet_layer = tk.Frame(self.content, bg=UK.C.N50)
    self._sheet_open = False
    self._sheet_stack = []
    self._sheet_title, self._sheet_builder = "", None
    self.root.bind("<Escape>", lambda e: self._close_sheet() if self._sheet_open else None)

    self.root.bind("<Control-e>", lambda e: self.export())
    self.root.bind("<Control-f>", lambda e: self.search.entry.focus_set())
    self.root.bind("<Control-l>", lambda e: self.clear())
    self.root.bind("<Control-f>", lambda e: self.search.entry.focus_set())
    self.show_placeholder()


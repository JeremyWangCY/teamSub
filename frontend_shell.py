"""Workspace shell; App owns capture and translation state."""
import tkinter as tk
import ui_kit as UK
import modes as MD

def fit_initial_window(root):
    """Fit the first window inside the available desktop, including its taskbar."""
    width, height = root.winfo_screenwidth(), root.winfo_screenheight()
    try:
        import ctypes
        from ctypes import wintypes
        area = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0):
            width, height = area.right - area.left, area.bottom - area.top
    except (AttributeError, OSError):
        pass
    usable_width, usable_height = max(640, width - 32), max(480, height - 64)
    root.minsize(min(1000, usable_width), min(640, usable_height))
    root.geometry(f'{min(1280, usable_width)}x{min(820, usable_height)}')

def refresh_topic(self):
    from collections import Counter
    from translator import all_topics
    topics = all_topics(self.tcfg)
    counts = Counter(t.get('name') or k for k, t in topics.items())
    choices = {}
    for key, topic in topics.items():
        name = topic.get('name') or key
        label = name if counts[name] == 1 else f'{name} [{key}]'
        while label in choices:
            label += f' [{key}]'
        choices[label] = key
    self._topic_choices = choices
    select = self.topic_select
    select.options = list(choices)
    select.value = next((label for label, key in choices.items() if key == self.tcfg.topic),
                        self._translation_topic_name())
    select._paint()

NAV = [("实时会议", "live", "show_live", "Ctrl+1"),
       ("历史会议", "history", "open_history", "Ctrl+2"),
       ("设置", "settings", "open_settings", "Ctrl+,")]


def build_nav(self, parent):
    """左侧导航：品牌标 + 目的地 + 帮助/版本。"""
    from PIL import ImageTk
    import brand
    nav = tk.Frame(parent, bg=UK.C.N100, width=176)
    nav.pack(side="left", fill="y")
    nav.pack_propagate(False)
    head = tk.Frame(nav, bg=UK.C.N100)
    head.pack(fill="x", padx=18, pady=(22, 22))
    self._brand_img = ImageTk.PhotoImage(brand.render(28), master=nav)
    tk.Label(head, image=self._brand_img, bg=UK.C.N100).pack(side="left")
    tk.Label(head, text="TeamsSub", font=UK.F_HEAD, bg=UK.C.N100,
             fg=UK.C.TEXT).pack(side="left", padx=(10, 0))
    self.nav_buttons = {}
    font = (UK.FAMILY, 11)
    for label, icon, action, key in NAV:
        btn = UK.Pill(nav, label, getattr(self, action),
                      kind="selected" if label == "实时会议" else "ghost",
                      height=40, padx=12, bg=UK.C.N100, font=font, align="left",
                      icon=icon, icon_size=18, tooltip=f"{label}（{key}）")
        btn.pack(fill="x", padx=10, pady=2)
        self.nav_buttons[label] = btn
    try:
        from teamsub import APP_VERSION
    except ImportError:
        APP_VERSION = ""
    tk.Label(nav, text=f"v{APP_VERSION}" if APP_VERSION else "", font=UK.F_MINI,
             bg=UK.C.N100, fg=UK.C.T3, anchor="w").pack(side="bottom", fill="x",
                                                        padx=24, pady=(0, 16))
    help_btn = UK.Pill(nav, "帮助", self.show_help, kind="ghost", height=40, padx=12,
                       bg=UK.C.N100, font=font, align="left", icon="help", icon_size=18,
                       tooltip="帮助与快捷键（F1）")
    help_btn.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
    self.nav_buttons["帮助"] = help_btn
    return nav


def build(self):
    root = self.root
    m = MD.get(self.mode) or MD.get(MD.DEFAULT_MODE)
    root.title(f"TeamsSub · {m['short']} 实时字幕")
    if not getattr(self, "_built_once", False):
        fit_initial_window(root)
    self._built_once = True
    root.configure(bg=UK.C.N50)

    self.f_ui, self.f_ui_b = UK.F_UI, UK.F_HEAD
    self.f_small, self.f_mono, self.f_body = UK.F_SMALL, UK.F_BODY, UK.F_BODY
    self.var_pin = tk.BooleanVar(value=self.pinned)
    self.var_simplify = tk.BooleanVar(value=self.simplify)

    build_nav(self, root)
    # 右侧主区：实时会议页与内推层（历史/设置/帮助…）互为覆盖关系，
    # 内推层盖住整个主区，页面标题与导航始终一致。
    self.main_area = tk.Frame(root, bg=UK.C.N50)
    self.main_area.pack(side="left", fill="both", expand=True)
    r = self.live_page = tk.Frame(self.main_area, bg=UK.C.N50)
    r.pack(fill="both", expand=True)
    # ---------- 顶栏 ----------
    head = tk.Frame(r, bg=UK.C.N50)
    head.pack(fill="x", padx=24, pady=(20, 12))

    lt = tk.Frame(head, bg=UK.C.N50)
    lt.pack(side="left")
    # 标题行：页面名 + 会议模式切换控件
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
    self.status.pack(side="left", padx=(0, 12))
    self.btn_export = UK.Pill(rt, "导出整场会议", self.export, kind="primary", height=36,
                              icon="export", tooltip="导出整场会议（Ctrl+E）")
    self.btn_export.pack(side="right", padx=(8, 0))
    self.btn_more = UK.Pill(rt, "", self._open_menu, kind="ghost", padx=10,
                            icon="more", icon_size=18, tooltip="更多操作")
    self.btn_more.pack(side="right", padx=(6, 0))
    self.btn_settings = UK.Pill(rt, "设置", self.open_settings, kind="ghost", icon="settings")
    # 设置入口也常驻在左侧导航里，这里不再重复放一个按钮。
    self.btn_tr = UK.Pill(rt, "翻译 开" if self.trans_on else "翻译 关", self.toggle_translate,
                          kind="tint" if self.trans_on else "ghost", icon="translate",
                          tooltip="开关实时翻译（服务在 设置 → 服务 中配置）")
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

    # 常驻上下文只读取已保存的配置，浏览设置草稿不会改变这里。
    topic_slot = tk.Frame(bar, bg=UK.C.N50, width=1, height=36)
    topic_slot.pack(side="left", fill="x", expand=True, padx=12)
    topic_slot.pack_propagate(False)
    tk.Label(topic_slot, text="翻译主题", font=UK.F_MINI, bg=UK.C.N50,
             fg=UK.C.SUB).pack(side="left", padx=(0, 8))
    self.topic_select = UK.Select(topic_slot, [], command=self.on_topic_pick,
                                   height=36, width=120, bg=UK.C.N50, font=UK.F_SMALL)
    self.topic_select.pack(side="left", fill="x", expand=True)
    refresh_topic(self)

    # 长名称可通过悬停或键盘聚焦查看全称；提示随控件销毁而销毁。
    tip = {"window": None, "after": None}
    def hide_topic_tip(event=None):
        if tip["after"] is not None:
            self.root.after_cancel(tip["after"])
            tip["after"] = None
        if tip["window"] is not None:
            tip["window"].destroy()
            tip["window"] = None
    def show_topic_tip():
        tip["after"] = None
        if not self.topic_select.winfo_exists() or self.topic_select._pop is not None:
            return
        win = tip["window"] = tk.Toplevel(self.topic_select)
        win.overrideredirect(True)
        tk.Label(win, text=f"当前翻译主题：{self._translation_topic_name()}\n切换后立即保存，新字幕按此主题翻译\n在设置 → 主题中编辑会议背景与译名对照",
                 bg=UK.C.N100, fg=UK.C.TEXT, font=UK.F_SMALL, padx=12, pady=8,
                 wraplength=360, justify="left").pack()
        win.update_idletasks()
        x = min(self.topic_select.winfo_rootx(),
                self.root.winfo_screenwidth() - win.winfo_reqwidth())
        win.geometry(f"+{max(0, x)}+{self.topic_select.winfo_rooty() + 40}")
    def queue_topic_tip(event=None):
        hide_topic_tip()
        tip["after"] = self.root.after(400, show_topic_tip)
    for event in ("<Enter>", "<FocusIn>"):
        self.topic_select.bind(event, queue_topic_tip, add="+")
    for event in ("<Leave>", "<FocusOut>", "<Button-1>", "<KeyPress>", "<Destroy>"):
        self.topic_select.bind(event, hide_topic_tip, add="+")

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
    # 短反馈覆盖统计的显示区域，统计本身继续更新；提示结束不会恢复过期文本。
    self.lbl_feedback = tk.Label(foot, text="", bg=UK.C.N50,
                                 fg=UK.C.A600, font=UK.F_MINI, anchor="w")

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
    UK.Pill(readbar, "字号 / 间距", self.open_reading, kind="ghost", icon="text-size",
            tooltip="正文字号与行距").pack(side="left", padx=12)
    self.btn_pause = UK.Pill(readbar, "继续采集" if self.paused else "暂停采集", self.toggle_pause,
                             kind="ghost", icon={"暂停采集": "pause", "继续采集": "play"},
                             icon_size=14, tooltip="暂停时不记录新字幕")
    self.btn_pause.pack(side="right")
    UK.Pill(readbar, "结束", self.end_meeting, kind="danger", padx=12, icon="stop", icon_size=14,
            tooltip="归档当前会议并暂停采集").pack(side="right", padx=4)
    UK.Pill(readbar, "新建", self.new_meeting, kind="ghost", padx=12, icon="plus",
            tooltip="归档当前会议并开始新记录").pack(side="right", padx=4)
    UK.Pill(readbar, "清屏", self.clear, kind="ghost", icon="clear",
            tooltip="只隐藏显示，记录保留（Ctrl+L）").pack(side="right", padx=4)
    card = UK.Card(body, radius=UK.R_CARD, padding=16, bg=UK.C.N50)
    card.pack(side="left", fill="both", expand=True, padx=(14, 0))

    self.txt = tk.Text(card.body, wrap="word", bg=UK.C.N0, fg=UK.C.TEXT,
                       font=UK.F_BODY, bd=0, highlightthickness=0,
                       padx=6, pady=0, spacing1=2, spacing3=6,
                       selectbackground=UK.C.A100, selectforeground=UK.C.TEXT,
                       insertbackground=UK.C.TEXT, undo=False)
    sb = UK.ThinScroll(card.body, self.txt, bg=UK.C.N0,
                       on_user_scroll=self._sync_scroll_hint)
    sb.pack(side="right", fill="y", padx=(6, 0))
    self.txt.pack(side="left", fill="both", expand=True)

    # 正文标签由 transcript_view 统一配置：实时会议与历史阅读共用一份，
    # 改字号/缩进只改一处，两个界面不会走形
    import transcript_view as TV
    TV.configure_tags(self.txt, reading_size=getattr(self, "reading_size", 12),
                      density=getattr(self, "density", "comfort"))
    self.txt.tag_configure("empty", foreground=UK.C.T3, font=UK.F_UI,
                           spacing1=4, spacing3=4)

    self.to_bottom = UK.Pill(card.body, "回到底部", self._jump_bottom,
                             kind="tint", height=30, icon="arrow-down", icon_size=14)

    build_empty_state(self, card.body)

    self.txt.tag_bind("trfail", "<Button-1>", self.on_retry_click)
    self.txt.tag_bind("trfail", "<Enter>",
                      lambda e: self.txt.configure(cursor="hand2"))
    self.txt.tag_bind("trfail", "<Leave>",
                      lambda e: self.txt.configure(cursor="xterm"))
    self.txt.bind("<Button-1>", self.on_click, add="+")
    self.txt.bind("<MouseWheel>", self.on_wheel, add="+")
    for key in ("Up", "Down", "Prior", "Next", "Home", "End"):
        self.txt.bind(f"<KeyRelease-{key}>", self.on_wheel, add="+")
    self.txt.bind("<Double-Button-1>", self.on_dblclick)
    self.txt.bind("<Button-3>", self.on_right_click)
    # 右键菜单：命中译文行时 on_right_click 会返回 "break" 接管（修正译法），
    # 没命中就落到这里弹复制菜单——只读正文也能一键复制。
    UK.attach_text_menu(self.txt)

    # ---------- 内推层：覆盖整个主区 ----------
    self.sheet_layer = tk.Frame(self.main_area, bg=UK.C.N50)
    self._sheet_open = False
    self._sheet_stack = []
    self._sheet_title, self._sheet_builder = "", None
    bind_shortcuts(self)
    self.show_placeholder()


def bind_shortcuts(self):
    """全局快捷键。实时页专属的操作在内推层打开时不响应，避免作用到看不见的控件。"""
    root = self.root

    def live_only(action):
        def handler(_event=None):
            if not getattr(self, "_sheet_open", False):
                action()
            return "break"
        return handler

    def go(action):
        def handler(_event=None):
            action()
            return "break"
        return handler

    root.bind("<Escape>", lambda e: self._close_sheet() if self._sheet_open else None)
    root.bind("<Control-e>", live_only(self.export))
    root.bind("<Control-f>", live_only(self.search.entry.focus_set))
    root.bind("<Control-l>", live_only(self.clear))
    root.bind("<Control-Key-1>", go(self.show_live))
    root.bind("<Control-Key-2>", go(self.open_history))
    root.bind("<Control-comma>", go(self.open_settings))
    root.bind("<F1>", go(self.show_help))


EMPTY_STATES = {
    "waiting": ("等待字幕", "在会议软件中打开实时字幕，内容会自动出现在这里。"),
    "cleared": ("已清屏", "新的字幕会继续显示在这里，整场记录仍保存并可导出。"),
    "filtered": ("没有匹配的字幕", "换个关键词，或清空搜索与发言人筛选。"),
}


def build_empty_state(self, parent):
    """正文区的空状态：图标 + 标题 + 说明，文案随原因切换（见 set_empty_state）。"""
    import icons
    box = self.empty_box = tk.Frame(parent, bg=UK.C.N0)
    badge = tk.Canvas(box, width=64, height=64, bg=UK.C.N0, highlightthickness=0, bd=0)
    badge.pack()
    UK.round_rect(badge, 0.5, 0.5, 63.5, 63.5, 20, fill=UK.C.A50, outline=UK.C.A50)
    badge.create_image(32, 32, image=icons.photo("captions", 32, UK.C.A600, master=badge))
    self.empty_title = tk.Label(box, text="", bg=UK.C.N0, fg=UK.C.TEXT, font=UK.F_HEAD)
    self.empty_title.pack(pady=(14, 0))
    self.empty_hint = tk.Label(box, text="", bg=UK.C.N0, fg=UK.C.SUB, font=UK.F_SMALL,
                               justify="center", wraplength=480)
    self.empty_hint.pack(pady=(6, 0))
    set_empty_state(self, "waiting")
    box.place(relx=0.5, rely=0.4, anchor="center")


def set_empty_state(self, reason):
    title, hint = EMPTY_STATES.get(reason, EMPTY_STATES["waiting"])
    if reason == "waiting":
        how = (MD.get(getattr(self, "mode", "")) or {}).get("hint")
        if how:
            hint = f"{hint}\n{how}"
    try:
        self.empty_title.configure(text=title)
        self.empty_hint.configure(text=hint)
    except (AttributeError, tk.TclError):
        pass


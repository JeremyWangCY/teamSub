import copy
import json
from pathlib import Path
import re
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import ui_kit as UK
from translator import (all_topics, TOPICS, TranslationService,
                        REASONING_LABELS, REASONING_TITLES,
                        format_topic_export, parse_topics_import)
import translator as TR

_ENGINE_TO_LABEL = {'openai': 'openai 兼容', 'deepl': 'DeepL', 'azure': 'Azure'}
_LABEL_TO_ENGINE = {v: k for k, v in _ENGINE_TO_LABEL.items()}
_ENGINE_OPTIONS = ['openai 兼容', 'DeepL', 'Azure']


def open_settings(self, page="服务"):
    """Tabbed settings, drafts validated before applying to the running service."""
    initial_page = '参数' if page in ('翻译参数', '请求参数') else page

    def build(win):
        W = UK.C.N0
        card = UK.Card(win, padding=20, autosize=False)
        card.pack(fill="both", expand=True)
        tabs = tk.Frame(card.body, bg=UK.C.N100, padx=4, pady=4)
        tabs.pack(anchor='w', pady=(0, 14))
        host = tk.Frame(card.body, bg=W)
        host.pack(fill="both", expand=True)
        page_titles = ['服务', '主题', '参数', '用量', '更新']
        pages = [tk.Frame(host, bg=W) for _ in page_titles]
        tab_buttons = []

        def lab(parent, text, *, fg=None, font=None, bg=None):
            return tk.Label(parent, text=text, bg=bg or W, fg=fg or UK.C.SUB,
                            font=font or UK.F_SMALL, anchor='w')

        def make_field(parent, title, value, *, secret=False):
            frame = tk.Frame(parent, bg=W)
            lab(frame, title).pack(anchor='w', pady=(0, 4))
            w = UK.Field(frame, value=str(value), secret=secret, bg=W, height=36)
            w.pack(fill='x')
            return frame, w

        # ============================================================== 0. 服务
        service = pages[0]
        switchrow = tk.Frame(service, bg=W)
        switchrow.pack(fill='x', pady=(0, 12))
        lab(switchrow, '实时翻译').pack(side='left')
        sw_tr = UK.Switch(switchrow, value=bool(self.tcfg.enabled), bg=W)
        sw_tr.pack(side='left', padx=(10, 0))

        profile_fields = ('engine', 'model', 'base_url', 'api_key', 'azure_region')
        profiles = copy.deepcopy(self.tcfg.engine_profiles)
        if not profiles:
            profiles = {'当前配置': {key: getattr(self.tcfg, key) for key in profile_fields}}
        active_profile = {'name': self.tcfg.active_engine_profile
                          if self.tcfg.active_engine_profile in profiles else next(iter(profiles))}
        profile_row = tk.Frame(switchrow, bg=W)
        profile_row.pack(side='right', fill='x', expand=True, padx=(24, 0))
        lab(profile_row, '已存配置').pack(side='left', padx=(0, 8))
        profile_select = UK.Select(profile_row, list(profiles), value=active_profile['name'],
                                   command=lambda name: switch_profile(name), bg=W, height=34, width=180)
        profile_select.pack(side='left', fill='x', expand=True)
        UK.Pill(profile_row, '新建', lambda: new_profile(), kind='secondary', icon='plus',
                bg=W, height=34, padx=10).pack(side='left', padx=(8, 0))
        UK.Pill(profile_row, '重命名', lambda: rename_profile(), kind='ghost', icon='edit',
                bg=W, height=34, padx=10).pack(side='left', padx=(4, 0))
        UK.Pill(profile_row, '删除', lambda: delete_profile(), kind='danger', icon='trash',
                bg=W, height=34, padx=10).pack(side='left', padx=(4, 0))

        tk.Frame(service, bg=UK.C.LINE, height=1).pack(fill='x', pady=(0, 14))

        form = tk.Frame(service, bg=W)
        form.pack(fill='x')
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=1)

        # Row 0: 引擎 (Segmented) + 模型 (仅 openai 兼容)
        row_engine = tk.Frame(form, bg=W)
        row_engine.grid(row=0, column=0, columnspan=2, sticky='we', pady=(0, 12))
        eng_box = tk.Frame(row_engine, bg=W)
        eng_box.pack(side='left', padx=(0, 20))
        lab(eng_box, '引擎').pack(anchor='w', pady=(0, 4))
        sel_engine = UK.Segmented(eng_box, _ENGINE_OPTIONS,
                                  command=lambda _v: sync_engine_fields(),
                                  height=36, bg=W)
        sel_engine.pack(anchor='w')
        sel_engine.set(_ENGINE_TO_LABEL.get(self.tcfg.engine, 'openai 兼容'))

        model_box, e_model = make_field(row_engine, '模型', self.tcfg.model)

        # Row 1: Base URL
        base_box, e_base = make_field(form, 'Base URL', self.tcfg.base_url)
        base_box.grid(row=1, column=0, columnspan=2, sticky='we', pady=(0, 12))

        # Row 2: API Key + Azure 区域 (仅 Azure)
        row_auth = tk.Frame(form, bg=W)
        row_auth.grid(row=2, column=0, columnspan=2, sticky='we', pady=(0, 12))
        key_box, e_key = make_field(row_auth, 'API Key', self.tcfg.api_key, secret=True)
        key_box.pack(side='left', fill='x', expand=True)
        region_box, e_region = make_field(row_auth, 'Azure 区域', self.tcfg.azure_region)

        # Row 3: 思考强度 (Segmented) + 检测按钮 (仅 openai 兼容)
        reason_box = tk.Frame(form, bg=W)
        lab(reason_box, '思考强度').pack(anchor='w', pady=(0, 4))
        reason_row = tk.Frame(reason_box, bg=W)
        reason_row.pack(anchor='w')
        sel_reason = UK.Segmented(reason_row, list(REASONING_LABELS), height=36, bg=W)
        sel_reason.pack(side='left')
        sel_reason.set(REASONING_TITLES.get(self.tcfg.reasoning, '自动'))
        probing = {'on': False}

        def do_probe():
            if probing['on']:
                return
            probing['on'] = True
            level = REASONING_LABELS.get(sel_reason.get(), 'auto')
            candidate = copy.deepcopy(self.tcfg)
            candidate.engine = _LABEL_TO_ENGINE[sel_engine.get()]
            candidate.model = e_model.get().strip()
            candidate.base_url = e_base.get().strip()
            candidate.api_key = e_key.get().strip()
            candidate.azure_region = e_region.get().strip()
            candidate.reasoning = level
            self.flash('正在检测上游是否支持思考参数…')

            def apply(result):
                field_name, text = result
                probing['on'] = False
                if field_name:
                    self.tcfg.reasoning_field = field_name
                self.flash(text)

            def work():
                try:
                    field_name, message = TR.probe_reasoning(candidate, level, persist=False)
                except Exception as exc:
                    field_name, message = '', f'检测失败：{exc}'
                self.q.put(('ui', (apply, (field_name, ('✓ ' if field_name else '✗ ') + message))))

            threading.Thread(target=work, daemon=True).start()

        UK.Pill(reason_row, '检测支持', do_probe, kind='secondary', icon='probe', bg=W, height=36, padx=12,
                tooltip='检测当前模型支持的思考强度参数').pack(side='left', padx=(10, 0))

        def sync_engine_fields():
            eng = _LABEL_TO_ENGINE.get(sel_engine.get(), 'openai')
            if eng == 'openai':
                if not model_box.winfo_manager():
                    model_box.pack(side='left', fill='x', expand=True)
                reason_box.grid(row=3, column=0, columnspan=2, sticky='we', pady=(0, 8))
            else:
                model_box.pack_forget()
                reason_box.grid_forget()
            if eng == 'azure':
                if not region_box.winfo_manager():
                    region_box.pack(side='left', fill='x', expand=True, padx=(16, 0))
            else:
                region_box.pack_forget()

        sync_engine_fields()

        def snapshot_profile():
            profiles[active_profile['name']] = {
                'engine': _LABEL_TO_ENGINE[sel_engine.get()],
                'model': e_model.get().strip(), 'base_url': e_base.get().strip(),
                'api_key': e_key.get().strip(), 'azure_region': e_region.get().strip()}

        def show_profile(name):
            p = profiles[name]
            sel_engine.set(_ENGINE_TO_LABEL.get(p.get('engine'), 'openai 兼容'))
            sync_engine_fields()
            for widget, key in ((e_model, 'model'), (e_base, 'base_url'),
                                (e_key, 'api_key'), (e_region, 'azure_region')):
                widget.set(p.get(key, ''))
            active_profile['name'] = name
            profile_select.options = list(profiles)
            profile_select.set(name)

        def switch_profile(name):
            if name != active_profile['name']:
                snapshot_profile()
                show_profile(name)

        def new_profile():
            if len(profiles) >= 8:
                messagebox.showinfo('新建引擎配置', '最多保存 8 个配置，请先删除不需要的配置。', parent=self.root)
                return
            name = UK.ask_string(self.root, '新建引擎配置')
            if name is None:
                return
            name = name.strip()
            if not name or name in profiles:
                messagebox.showerror('新建引擎配置', '名称不能为空或与已有配置重复。', parent=self.root)
                return
            snapshot_profile()
            profiles[name] = {'engine': 'openai', 'model': '', 'base_url': '',
                              'api_key': '', 'azure_region': ''}
            show_profile(name)

        def delete_profile():
            name = active_profile['name']
            if len(profiles) == 1:
                messagebox.showinfo('删除引擎配置', '请至少保留一个配置。', parent=self.root)
                return
            if not messagebox.askyesno('删除引擎配置', f'删除配置“{name}”？保存后生效。', parent=self.root):
                return
            del profiles[name]
            show_profile(next(iter(profiles)))

        def rename_profile():
            old = active_profile['name']
            name = UK.ask_string(self.root, '重命名引擎配置', initial=old)
            if name is None:
                return
            name = name.strip()
            if name == old:
                return
            if not name or name in profiles:
                messagebox.showerror('重命名引擎配置', '名称不能为空或与已有配置重复。', parent=self.root)
                return
            snapshot_profile()
            profiles[name] = profiles.pop(old)
            show_profile(name)

        # ============================================================== 1. 主题
        topics = all_topics(self.tcfg)
        order = list(topics)
        state = {'key': self.tcfg.topic if self.tcfg.topic in topics else
                 ('general' if 'general' in topics else (order[0] if order else None)),
                 'busy': False, 'gloss_mode': '表格'}
        hidden = list(self.tcfg.topics_hidden or [])

        topic_page = pages[1]
        body = tk.Frame(topic_page, bg=W)
        body.pack(fill='both', expand=True)
        lb = UK.PickList(body, on_pick=lambda key: on_pick(key), width=170, rows=6, bg=W)
        lb.pack(side='left', fill='y', padx=(0, 18))
        right = tk.Frame(body, bg=W)
        right.pack(side='left', fill='both', expand=True)

        e_tname = UK.Field(right, bg=W, height=34)
        e_tname.pack(fill='x')
        tb = tk.Frame(right, bg=W)
        tb.pack(fill='x', pady=(6, 6))

        header = tk.Frame(right, bg=W)
        header.pack(fill='x', pady=(0, 4))
        lab(header, '会议背景').pack(side='left')
        lbl_topic_state = lab(header, '')
        lbl_topic_state.pack(side='right')

        edits = tk.Frame(right, bg=W)
        edits.pack(fill='both', expand=True)
        edits.columnconfigure(0, weight=1)
        edits.rowconfigure(0, weight=1, uniform='ed')
        edits.rowconfigure(2, weight=1, uniform='ed')
        t_brief = UK.RoundedText(edits, lines=2, bg=W)
        t_brief.grid(row=0, column=0, sticky='nsew')

        terms_header = tk.Frame(edits, bg=W)
        terms_header.grid(row=1, column=0, sticky='we', pady=(8, 4))
        lab(terms_header, '译名对照').pack(side='left')

        gloss_host = tk.Frame(edits, bg=W)
        gloss_host.grid(row=2, column=0, sticky='nsew')

        # 原始文本视图（兜底与批量粘贴）
        t_gloss = UK.RoundedText(gloss_host, lines=2, bg=W)

        # 结构化键值行视图
        kv_wrap = tk.Frame(gloss_host, bg=UK.C.N100, padx=8, pady=6)
        kv_head = tk.Frame(kv_wrap, bg=UK.C.N100)
        kv_head.pack(fill='x', pady=(0, 4))
        kv_head.columnconfigure(0, weight=1)
        kv_head.columnconfigure(2, weight=1)
        tk.Label(kv_head, text='原文', bg=UK.C.N100, fg=UK.C.T3, font=UK.F_MINI, anchor='w').grid(
            row=0, column=0, sticky='w')
        tk.Label(kv_head, text=' ', bg=UK.C.N100, font=UK.F_MINI).grid(row=0, column=1, padx=8)
        tk.Label(kv_head, text='译文', bg=UK.C.N100, fg=UK.C.T3, font=UK.F_MINI, anchor='w').grid(
            row=0, column=2, sticky='w')
        tk.Label(kv_head, text='   ', bg=UK.C.N100, font=UK.F_MINI).grid(row=0, column=3, padx=(4, 8))
        kv_canvas = tk.Canvas(kv_wrap, bg=UK.C.N100, highlightthickness=0, bd=0, height=52)
        kv_sb = UK.ThinScroll(kv_wrap, kv_canvas, bg=UK.C.N100, width=4)
        kv_sb.pack(side='right', fill='y', padx=(4, 0))
        kv_canvas.pack(side='left', fill='both', expand=True)
        kv_inner = tk.Frame(kv_canvas, bg=UK.C.N100)
        kv_win = kv_canvas.create_window(0, 0, anchor='nw', window=kv_inner)
        kv_rows = []  # [(row_frame, e_src, e_dst)]

        def _sync_kv_scroll(_e=None):
            kv_canvas.itemconfigure(kv_win, width=max(100, kv_canvas.winfo_width()))
            kv_canvas.configure(scrollregion=kv_canvas.bbox('all') or (0, 0, 0, 0))

        kv_canvas.bind('<Configure>', _sync_kv_scroll)
        kv_inner.bind('<Configure>', _sync_kv_scroll)
        kv_canvas.bind('<MouseWheel>',
                       lambda e: (kv_canvas.yview_scroll(int(-e.delta / 120), 'units'), 'break')[1])

        def _add_kv_row(src='', dst='', focus=False):
            rf = tk.Frame(kv_inner, bg=UK.C.N100)
            rf.pack(fill='x', pady=(0, 4))
            rf.columnconfigure(0, weight=1)
            rf.columnconfigure(2, weight=1)
            e_s = tk.Entry(rf, bg=UK.C.N0, fg=UK.C.TEXT, font=UK.F_SMALL, relief='flat',
                           highlightthickness=1, highlightbackground=UK.C.LINE,
                           highlightcolor=UK.C.FOCUS_INNER, insertbackground=UK.C.TEXT)
            e_s.grid(row=0, column=0, sticky='we', ipady=3)
            if src:
                e_s.insert(0, src)
            tk.Label(rf, text='→', bg=UK.C.N100, fg=UK.C.T3, font=UK.F_MINI).grid(
                row=0, column=1, padx=6)
            e_d = tk.Entry(rf, bg=UK.C.N0, fg=UK.C.TEXT, font=UK.F_SMALL, relief='flat',
                           highlightthickness=1, highlightbackground=UK.C.LINE,
                           highlightcolor=UK.C.FOCUS_INNER, insertbackground=UK.C.TEXT)
            e_d.grid(row=0, column=2, sticky='we', ipady=3)
            if dst:
                e_d.insert(0, dst)
            pair = (rf, e_s, e_d)
            kv_rows.append(pair)

            def remove_row():
                if pair in kv_rows:
                    kv_rows.remove(pair)
                rf.destroy()
                _sync_kv_scroll()

            btn_del = tk.Label(rf, text='×', bg=UK.C.N100, fg=UK.C.T3, font=UK.F_UI,
                               cursor='hand2', padx=5)
            btn_del.grid(row=0, column=3, padx=(4, 0))
            btn_del.bind('<Button-1>', lambda _e: remove_row())
            btn_del.bind('<Enter>', lambda _e: btn_del.configure(fg=UK.C.RED))
            btn_del.bind('<Leave>', lambda _e: btn_del.configure(fg=UK.C.T3))
            for wd in (rf, e_s, e_d):
                wd.bind('<MouseWheel>',
                        lambda e: (kv_canvas.yview_scroll(int(-e.delta / 120), 'units'), 'break')[1])
            _sync_kv_scroll()
            if focus:
                e_s.focus_set()
                kv_canvas.yview_moveto(1.0)

        def _load_kv_from_text(raw_text):
            for rf, _, _ in list(kv_rows):
                rf.destroy()
            kv_rows.clear()
            lines = [ln.strip() for ln in (raw_text or '').splitlines() if ln.strip()]
            for ln in lines:
                if '=' in ln:
                    s, d = ln.split('=', 1)
                    _add_kv_row(s.strip(), d.strip())
                else:
                    _add_kv_row(ln, '')
            if not kv_rows:
                _add_kv_row('', '')
            _sync_kv_scroll()

        def _serialize_kv():
            out = []
            for _, e_s, e_d in kv_rows:
                s, d = e_s.get().strip(), e_d.get().strip()
                if s or d:
                    out.append(f'{s}={d}')
            return '\n'.join(out)

        def get_glossary_text():
            if state['gloss_mode'] == '表格':
                return _serialize_kv()
            return t_gloss.get().strip()

        def set_glossary_text(text):
            t_gloss.set(text or '')
            _load_kv_from_text(text or '')

        def switch_gloss_mode(mode):
            if mode == state['gloss_mode']:
                return
            if mode == '文本':
                t_gloss.set(_serialize_kv())
                kv_wrap.pack_forget()
                btn_add_term.pack_forget()
                t_gloss.pack(fill='both', expand=True)
            else:
                _load_kv_from_text(t_gloss.get())
                t_gloss.pack_forget()
                kv_wrap.pack(fill='both', expand=True)
                btn_add_term.pack(side='right', padx=(0, 8))
            state['gloss_mode'] = mode

        seg_gloss = UK.Segmented(terms_header, ['表格', '文本'],
                                 command=switch_gloss_mode, height=26, bg=W, font=UK.F_MINI)
        seg_gloss.pack(side='right')
        btn_add_term = UK.Pill(terms_header, '添加', lambda: _add_kv_row('', '', focus=True),
                               kind='secondary', icon='plus', icon_size=12, height=26,
                               padx=8, bg=W, font=UK.F_MINI)
        btn_add_term.pack(side='right', padx=(0, 8))
        kv_wrap.pack(fill='both', expand=True)

        # ============================================================== 2. 参数（合并翻译 + 请求）
        params_page = pages[2]
        params_cols = tk.Frame(params_page, bg=W)
        params_cols.pack(fill='both', expand=True)
        params_cols.columnconfigure(0, weight=1, uniform='pcol')
        params_cols.columnconfigure(1, weight=1, uniform='pcol')

        left_p = tk.Frame(params_cols, bg=W)
        left_p.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
        right_p = tk.Frame(params_cols, bg=W)
        right_p.grid(row=0, column=1, sticky='nsew', padx=(16, 0))

        def param_section(parent, title):
            tk.Label(parent, text=title, bg=W, fg=UK.C.TEXT, font=UK.F_NAME,
                     anchor='w').pack(anchor='w', pady=(0, 4))
            tk.Frame(parent, bg=UK.C.LINE, height=1).pack(fill='x', pady=(0, 10))
            grid = tk.Frame(parent, bg=W)
            grid.pack(fill='x')
            grid.columnconfigure(0, weight=1, uniform='step')
            grid.columnconfigure(1, weight=1, uniform='step')
            return grid

        grid_tr = param_section(left_p, '翻译与上下文')
        grid_req = param_section(right_p, '请求与合批')

        ents = {}
        extras = {}

        def add_stepper(grid, title, value, low, high, step, unit, row, col):
            cell_f = tk.Frame(grid, bg=W)
            cell_f.grid(row=row, column=col, sticky='we',
                        padx=(0, 12 if col == 0 else 0), pady=(0, 10))
            lab(cell_f, title).pack(anchor='w', pady=(0, 4))
            st = UK.Stepper(cell_f, value=value, min_val=low, max_val=high,
                            step=step, unit=unit, bg=W, height=34)
            st.pack(fill='x')
            return st

        # 左栏：翻译与上下文（8 项）
        ents['ctx'] = add_stepper(grid_tr, '上文条数', self.tcfg.context_n, 0, 20, 1, '条', 0, 0)
        ents['sent'] = add_stepper(grid_tr, '句末等待', self.tcfg.sentence_end_grace_ms, 150, 60000, 50, 'ms', 0, 1)
        ents['settle'] = add_stepper(grid_tr, '兜底等待', self.tcfg.settle_ms, 300, 120000, 100, 'ms', 1, 0)
        extras['supersede_grace_ms'] = add_stepper(grid_tr, '新句后等待', self.tcfg.supersede_grace_ms, 150, 60000, 50, 'ms', 1, 1)
        extras['ctx_min_len'] = add_stepper(grid_tr, '短句上下文阈值', self.tcfg.ctx_min_len, 0, 10000, 10, '字', 2, 0)
        extras['ctx_max_chars'] = add_stepper(grid_tr, '上下文最大字符', self.tcfg.ctx_max_chars, 0, 100000, 100, '字', 2, 1)
        extras['backlog_max'] = add_stepper(grid_tr, '启动补译上限', self.tcfg.backlog_max, 0, 10000, 5, '条', 3, 0)
        extras['merge_gap_ms'] = add_stepper(grid_tr, '碎句合并', self.tcfg.merge_gap_ms, 0, 10000, 100, 'ms', 3, 1)

        # 右栏：请求与合批（7 项）
        ents['batch'] = add_stepper(grid_req, '批量条数', self.tcfg.batch_size, 1, 100, 1, '条', 0, 0)
        ents['chars'] = add_stepper(grid_req, '批量字符', self.tcfg.batch_chars, 200, 100000, 100, '字', 0, 1)
        extras['batch_window_ms'] = add_stepper(grid_req, '合批等待', self.tcfg.batch_window_ms, 0, 60000, 50, 'ms', 1, 0)
        ents['conc'] = add_stepper(grid_req, '并发数', self.tcfg.concurrency, 1, 8, 1, '路', 1, 1)
        extras['min_interval_ms'] = add_stepper(grid_req, '请求间隔', self.tcfg.min_interval_ms, 0, 60000, 50, 'ms', 2, 0)
        extras['timeout_s'] = add_stepper(grid_req, '请求超时', self.tcfg.timeout_s, 1, 300, 5, 's', 2, 1)
        extras['max_retry'] = add_stepper(grid_req, '重试次数', self.tcfg.max_retry, 0, 10, 1, '次', 3, 0)

        advanced = [('短句上下文阈值', 'ctx_min_len', 0, 10000),
                    ('上下文最大字符', 'ctx_max_chars', 0, 100000),
                    ('新句后等待 ms', 'supersede_grace_ms', 150, 60000),
                    ('启动补译上限', 'backlog_max', 0, 10000),
                    ('合批等待 ms', 'batch_window_ms', 0, 60000),
                    ('请求间隔 ms', 'min_interval_ms', 0, 60000),
                    ('请求超时 s', 'timeout_s', 1, 300),
                    ('重试次数', 'max_retry', 0, 10),
                    ('碎句合并 ms', 'merge_gap_ms', 0, 10000)]

        # ============================================================== 3. 用量
        usage_page = pages[3]
        kpi_grid = tk.Frame(usage_page, bg=W)
        kpi_grid.pack(fill='x', pady=(0, 14))
        for c in range(3):
            kpi_grid.columnconfigure(c, weight=1, uniform='kpi')

        usage_labels = {}
        kpi_specs = [
            ('输入 tokens', 'input_tokens'),
            ('输出 tokens', 'output_tokens'),
            ('API 请求', 'calls'),
            ('缓存命中 tokens', 'cache_hit_tokens'),
            ('缓存未命中 tokens', 'cache_miss_tokens'),
            ('缓存命中率', 'cache_rate'),
        ]
        for idx, (title, key) in enumerate(kpi_specs):
            r, c = divmod(idx, 3)
            tile = tk.Frame(kpi_grid, bg=UK.C.N50, highlightbackground=UK.C.LINE,
                            highlightthickness=1, padx=16, pady=14)
            tile.grid(row=r, column=c, sticky='nsew',
                      padx=(0, 12 if c < 2 else 0), pady=(0, 12 if r == 0 else 0))
            val_lbl = tk.Label(tile, text='—', bg=UK.C.N50, fg=UK.C.TEXT,
                               font=('Consolas', 18, 'bold'), anchor='w')
            val_lbl.pack(anchor='w', pady=(0, 6))
            tk.Label(tile, text=title, bg=UK.C.N50, fg=UK.C.SUB,
                     font=UK.F_SMALL, anchor='w').pack(anchor='w')
            usage_labels[key] = val_lbl

        usage_scope = lab(usage_page, '统计范围：本次翻译服务（重启或切换服务后归零）',
                          fg=UK.C.T3, font=UK.F_MINI)
        usage_scope.pack(anchor='w')

        def refresh_usage():
            if not usage_page.winfo_exists():
                return
            stats = self.svc.stats() if self.svc else {'calls': 0}
            calls = stats.get('calls', 0)
            for key, label in usage_labels.items():
                if key == 'cache_rate':
                    hit = stats.get('cache_hit_tokens')
                    miss = stats.get('cache_miss_tokens')
                    if hit is not None and miss is not None and (hit + miss) > 0:
                        text = f'{hit * 100 / (hit + miss):.1f}%'
                    else:
                        text = '—'
                else:
                    value = stats.get(key)
                    text = '—' if value is None else f'{value:,}'
                    if value is not None and key in stats.get('token_reports', {}) and stats['token_reports'][key] < calls:
                        text += '*'
                label.configure(text=text)
            self.root.after(1500, refresh_usage)
        refresh_usage()

        # ============================================================== 4. 更新
        import sys
        import app_updates
        import brand
        from PIL import ImageTk
        import update_workflow as UW
        from teamsub import APP_VERSION
        update_page = pages[page_titles.index('更新')]

        ver_row = tk.Frame(update_page, bg=UK.C.N50, highlightbackground=UK.C.LINE,
                           highlightthickness=1, padx=16, pady=14)
        ver_row.pack(fill='x', pady=(0, 14))
        update_page._logo_img = ImageTk.PhotoImage(brand.render(UK.px(32)), master=ver_row)
        tk.Label(ver_row, image=update_page._logo_img, bg=UK.C.N50).pack(side='left', padx=(0, 12))
        ver_info = tk.Frame(ver_row, bg=UK.C.N50)
        ver_info.pack(side='left', fill='x', expand=True)
        tk.Label(ver_info, text=f'TeamSub {APP_VERSION}', bg=UK.C.N50, fg=UK.C.TEXT,
                 font=UK.F_HEAD, anchor='w').pack(anchor='w')
        state_update = UW.state_for(self)
        update_status = tk.Label(ver_info, text='尚未检查', bg=UK.C.N50, fg=UK.C.SUB,
                                 font=UK.F_SMALL, anchor='w', wraplength=640, justify='left')
        update_status.pack(anchor='w', pady=(4, 0))

        update_bar = tk.Canvas(update_page, height=8, bg=W, bd=0, highlightthickness=0)
        update_actions = tk.Frame(update_page, bg=W)
        update_actions.pack(anchor='w')

        def open_release():
            import webbrowser
            release = state_update['release']
            if release:
                try:
                    opened = webbrowser.open(release.url)
                except OSError:
                    opened = False
                if not opened:
                    state_update['error'] = '无法打开浏览器，请稍后重试'
                    render_update()

        def check_updates():
            if state_update['phase'] in UW.BUSY_PHASES:
                return
            state_update.update(phase='checking', error='', release=None, path=None)
            render_update()

            def run():
                try:
                    release = app_updates.check_latest_release()
                    error = ''
                except Exception as exc:
                    release = None
                    error = str(exc) if isinstance(exc, app_updates.UpdateCheckError) else '检查失败，请稍后重试'

                def show_result(_):
                    state_update.update(phase='available', release=release, error=error, checked=True)
                    if update_status.winfo_exists():
                        render_update()
                self.q.put(('ui', (show_result, None)))
            threading.Thread(target=run, daemon=True).start()

        def download_update():
            if state_update['path']:
                UW.begin_install(self)
            else:
                UW.start_download(self, state_update['release'])
            render_update()

        check_button = UK.Pill(update_actions, '检查更新', check_updates, kind='primary', icon='refresh', bg=W, height=36)
        check_button.pack(side='left')
        release_button = UK.Pill(update_actions, '前往下载', open_release, kind='secondary', icon='link', bg=W, height=36)
        manual_button = UK.Pill(update_actions, '手动下载', open_release, kind='ghost', icon='download', bg=W, height=36)
        cancel_button = UK.Pill(update_actions, '取消下载', lambda: UW.cancel_download(self), kind='ghost', icon='close', bg=W, height=36)

        def render_update():
            phase = state_update['phase']
            busy = phase in UW.BUSY_PHASES
            check_button.command = None if busy else check_updates
            check_button.set_text('检查中…' if phase == 'checking' else
                                  ('重新检查' if state_update.get('checked') else '检查更新'))
            release_button.pack_forget()
            manual_button.pack_forget()
            cancel_button.pack_forget()
            update_bar.pack_forget()
            color = UK.C.SUB
            release = state_update['release']
            if phase == 'checking':
                text = '正在检查…'
            elif phase in ('downloading', 'cancelling'):
                received, total = state_update['received'], max(1, state_update['total'])
                text = (f'下载中 {received * 100 // total}% · {received / 1048576:.1f} / {total / 1048576:.1f} MB'
                        if phase == 'downloading' else '正在取消…')
                update_bar.pack(fill='x', before=update_actions, pady=(0, 14))
                width = max(20, UK.lpx(update_page.winfo_width()))
                update_bar.delete('all')
                UK.round_rect(update_bar, 0, 0, width - 1, 7, 4, fill=UK.C.N100)
                if received:
                    UK.round_rect(update_bar, 0, 0, max(8, (width - 1) * min(1, received / total)), 7, 4, fill=UK.C.A500)
                cancel_button.set_text('取消下载')
                if phase == 'downloading':
                    cancel_button.pack(side='left', padx=(12, 0))
            elif phase == 'saving':
                text = '正在保存会议…'
                cancel_button.set_text('取消更新')
                cancel_button.pack(side='left', padx=(12, 0))
            elif phase == 'recovering':
                text = '正在恢复会议记录…'
            elif phase == 'installing':
                text = '正在安装，即将重启…'
            elif state_update['error']:
                text = state_update['error']
                color = UK.C.RED
            elif release and release.newer_than(APP_VERSION):
                text = f'发现新版本 {release.version} · 当前版本 {APP_VERSION}'
                color = UK.C.A600
            elif release:
                text = f'已是最新正式版本 · 当前版本 {APP_VERSION}'
                color = UK.C.GREEN
            else:
                text = '尚未检查'
            update_status.configure(text=text, fg=color)
            if not busy and release and release.newer_than(APP_VERSION):
                automatic = bool(getattr(sys, 'frozen', False) and release.installer)
                release_button.set_text(('安装并重启' if state_update['path'] else '下载并更新')
                                        if automatic else '前往下载')
                release_button.command = download_update if automatic else open_release
                release_button.pack(side='left', padx=(12, 0))
                if automatic:
                    manual_button.pack(side='left', padx=(8, 0))

        def poll_update():
            if update_status.winfo_exists():
                render_update()
                self.root.after(200, poll_update)
        poll_update()

        # ============================================================== 底栏
        self._sheet_footer.pack(side='bottom', fill='x', padx=24, pady=(8, 12))
        bottom = tk.Frame(self._sheet_footer, bg=UK.C.N50)
        bottom.pack(fill='x')
        lbl_test = tk.Label(bottom, text='', bg=UK.C.N50, fg=UK.C.SUB, font=UK.F_MINI,
                            wraplength=300, justify='left')
        lbl_test.pack(side='left')

        def current_topic_hint():
            name = UK.fit_text(self._translation_topic_name(), UK.F_MINI, 220)
            return f'当前主题：{name}'

        lbl_saved = tk.Label(bottom, text=current_topic_hint(), bg=UK.C.N50, fg=UK.C.SUB,
                             font=UK.F_MINI, wraplength=300, justify='left')
        lbl_saved.pack(side='left', padx=12)

        # ---------------- 主题逻辑 ----------------
        def refresh_topic_state():
            current = state['key'] == self.tcfg.topic and state['key'] in topics
            lbl_topic_state.configure(
                text='' if current else '待切换',
                fg=UK.C.SUB if current else UK.C.A600)

        def load_fields(key):
            t = topics.get(key, {})
            e_tname.set(t.get("name", key))
            t_brief.set(t.get("brief", "") or "")
            set_glossary_text(t.get("glossary", "") or "")
            refresh_topic_state()

        def snapshot(key=None):
            key = key or state["key"]
            if not key:
                return
            t = dict(topics.get(key, {}))
            t["name"] = e_tname.get().strip() or key
            t["brief"] = t_brief.get().strip()
            t["glossary"] = get_glossary_text()
            topics[key] = t

        def refresh(select_key=None):
            state["busy"] = True
            key = select_key or state["key"]
            if key in order:
                state["key"] = key
            lb.set_items([(k, topics[k].get("name", k),
                           bool(topics[k].get("custom")))
                          for k in order if k in topics],
                         key=state["key"])
            load_fields(state["key"])
            state["busy"] = False

        def on_pick(key):
            if state["busy"] or key == state["key"]:
                return
            snapshot()
            state["key"] = key
            load_fields(key)

        def new_key(base="custom"):
            i = 1
            while f"{base}{i}" in topics:
                i += 1
            return f"{base}{i}"

        def do_new():
            snapshot()
            k = new_key()
            topics[k] = {"name": "新主题", "brief": "", "glossary": "", "custom": True}
            order.append(k)
            refresh(k)

        def do_clone():
            snapshot()
            src = topics.get(state["key"], {})
            k = new_key("copy")
            topics[k] = {"name": (src.get("name", "") + " 副本")[:20],
                         "brief": src.get("brief", ""),
                         "glossary": src.get("glossary", ""), "custom": True}
            order.append(k)
            refresh(k)

        def do_delete():
            k = state["key"]
            if not k or not messagebox.askyesno("删除主题", "删除当前主题？内置主题可以恢复。", parent=self.root):
                return
            topics.pop(k, None)
            order.remove(k)
            if k in TOPICS and k not in hidden:
                hidden.append(k)
            state["key"] = order[0] if order else None
            refresh()

        def do_restore():
            snapshot()
            for k in list(hidden):
                topics[k] = copy.deepcopy(TOPICS[k])
                if k not in order:
                    order.append(k)
            hidden.clear()
            refresh()

        def do_reset():
            if state["key"] in TOPICS:
                topics[state["key"]] = dict(TOPICS[state["key"]])
                load_fields(state["key"])
                refresh(state["key"])

        def do_export_topic():
            snapshot()
            k = state["key"]
            if not k or k not in topics:
                return
            t = topics[k]
            payload = format_topic_export(t)
            safe_name = re.sub(r'[\\/:*?"<>|]+', "_", payload["name"]).strip(" ._") or "topic"
            path = filedialog.asksaveasfilename(
                parent=self.root,
                title="导出当前主题（JSON，可发给 AI 仿写）",
                defaultextension=".json",
                initialfile=f"TeamSub_主题_{safe_name}.json",
                filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
            if not path:
                return
            try:
                Path(path).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
                self.flash(f"已导出主题「{payload['name']}」")
            except OSError as exc:
                messagebox.showerror("导出主题", f"导出失败：{exc}", parent=self.root)

        def do_import_topic():
            snapshot()
            path = filedialog.askopenfilename(
                parent=self.root,
                title="导入主题（JSON）",
                filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")])
            if not path:
                return
            try:
                raw = Path(path).read_text(encoding="utf-8-sig")
                imported = parse_topics_import(raw)
            except (OSError, UnicodeError, ValueError) as exc:
                messagebox.showerror("导入主题", f"无法导入该文件：\n{exc}", parent=self.root)
                return
            last_key = state["key"]
            for item in imported:
                existing_key = next(
                    (k for k in order if k in topics
                     and (topics[k].get("name") or "").strip() == item["name"]),
                    None)
                if existing_key is not None:
                    if messagebox.askyesno(
                            "同名主题已存在",
                            f"已存在名为「{item['name']}」的主题。\n\n"
                            "是：覆盖该主题的内容\n"
                            "否：作为新主题导入",
                            parent=self.root):
                        topics[existing_key] = {**item, "custom": True}
                        last_key = existing_key
                        continue
                k = new_key("import")
                topics[k] = {**item, "custom": True}
                order.append(k)
                last_key = k
            refresh(last_key)
            self.flash(f"已导入 {len(imported)} 个主题，点击右下角「保存」后生效")

        # 主题工具条分组：左编辑 / 分隔线 / 右导入导出与恢复
        for text, cmd, icon, kind, tip in (
                ("新建", do_new, "plus", "ghost", "新建空白主题"),
                ("复制", do_clone, "copy", "ghost", "以当前主题为模板复制一份"),
                ("重置", do_reset, "undo", "ghost", "内置主题恢复为默认内容"),
                ("删除", do_delete, "trash", "danger", "删除当前主题")):
            UK.Pill(tb, text, cmd, kind=kind, icon=icon, icon_size=14, tooltip=tip,
                    height=30, padx=8, bg=W).pack(side="left", padx=(0, 2))
        tk.Frame(tb, bg=UK.C.LINE, width=1, height=18).pack(side="left", padx=8)
        for text, cmd, icon, tip in (
                ("导入", do_import_topic, "download", "从 JSON 文件导入 AI 编写的主题"),
                ("导出", do_export_topic, "export", "把当前主题导出为 JSON（可作为模板发给 AI）"),
                ("恢复内置", do_restore, "restore", "找回已删除的内置主题")):
            UK.Pill(tb, text, cmd, kind="ghost", icon=icon, icon_size=14, tooltip=tip,
                    height=30, padx=8, bg=W).pack(side="left", padx=(0, 2))

        def collect():
            specs = {"ctx": ("上文条数", 0, 20), "sent": ("句末等待", 150, 60000),
                     "settle": ("兜底等待", 300, 120000), "batch": ("批量条数", 1, 100),
                     "chars": ("批量字符", 200, 100000), "conc": ("并发数", 1, 8)}
            numbers = {}
            for key, (label, low, high) in specs.items():
                try:
                    numbers[key] = int(ents[key].get())
                    if not low <= numbers[key] <= high:
                        raise ValueError()
                except ValueError:
                    raise ValueError(f"{label}必须是 {low}–{high} 之间的整数")
            gloss_text = get_glossary_text()
            if any(line.strip() and ("=" not in line or not all(x.strip() for x in line.split("=", 1)))
                   for line in gloss_text.splitlines()):
                raise ValueError("译名对照请按每行「原文=译文」填写")
            extra_numbers = {}
            for title, attr, low, high in advanced:
                try:
                    number = int(extras[attr].get())
                    if not low <= number <= high:
                        raise ValueError()
                except ValueError:
                    raise ValueError(f"{title}必须是 {low}–{high} 之间的整数")
                extra_numbers[attr] = number
            snapshot()
            for attr, value in extra_numbers.items():
                setattr(self.tcfg, attr, value)
            self.tcfg.enabled = bool(sw_tr.get())
            self.tcfg.engine = _LABEL_TO_ENGINE[sel_engine.get()]
            self.tcfg.base_url = e_base.get().strip()
            self.tcfg.reasoning = REASONING_LABELS.get(sel_reason.get(), 'auto')
            self.tcfg.model = e_model.get().strip()
            self.tcfg.api_key = e_key.get().strip()
            self.tcfg.azure_region = e_region.get().strip()
            snapshot_profile()
            self.tcfg.engine_profiles = copy.deepcopy(profiles)
            self.tcfg.active_engine_profile = active_profile['name']
            self.tcfg.topic = state["key"] or "general"
            self.tcfg.topics_hidden = list(hidden)
            for key, attr in {"ctx": "context_n", "sent": "sentence_end_grace_ms", "settle": "settle_ms",
                              "batch": "batch_size", "chars": "batch_chars", "conc": "concurrency"}.items():
                setattr(self.tcfg, attr, numbers[key])
            self.tcfg.glossary = ""
            self.settle_sec = self.tcfg.settle_ms / 1000.0
            self.reader.merge_gap = max(0.0, self.tcfg.merge_gap_ms / 1000.0)
            self.tcfg.topics_custom = {}
            self._persist_topics(order, topics)

        test_state = {'seq': 0, 'tested': None}

        def service_values():
            return (active_profile['name'], sel_engine.get(), e_model.get(),
                    e_base.get(), e_key.get(), e_region.get())

        def check_test_result():
            if not lbl_test.winfo_exists():
                return
            if test_state['tested'] is not None and service_values() != test_state['tested']:
                test_state['seq'] += 1
                test_state['tested'] = None
                lbl_test.configure(text='配置已修改，请重新测试', fg=UK.C.SUB)
            self.root.after(250, check_test_result)

        check_test_result()

        def do_test():
            current = self.tcfg
            self.tcfg = copy.deepcopy(current)
            try:
                collect()
                candidate = copy.deepcopy(self.tcfg)
            except ValueError as exc:
                lbl_test.configure(text=str(exc), fg=UK.C.RED)
                return
            finally:
                self.tcfg = current
                self.settle_sec = current.settle_ms / 1000.0
            test_state['seq'] += 1
            seq = test_state['seq']
            tested = service_values()
            test_state['tested'] = tested
            lbl_test.configure(text="测试中…", fg=UK.C.SUB)
            self.root.update_idletasks()

            def run():
                try:
                    svc_tmp = TranslationService(candidate, lambda *a: None)
                    ok, msg = svc_tmp.test()
                except Exception as exc:
                    ok, msg = False, str(exc)

                def show_result(_):
                    if not lbl_test.winfo_exists() or seq != test_state['seq']:
                        return
                    if service_values() != tested:
                        test_state['tested'] = None
                        lbl_test.configure(text='配置已修改，请重新测试', fg=UK.C.SUB)
                    else:
                        lbl_test.configure(text=('✓ ' if ok else '✗ ') + str(msg)[:70],
                                           fg=UK.C.GREEN if ok else UK.C.RED)
                self.q.put(('ui', (show_result, None)))

            threading.Thread(target=run, daemon=True).start()

        def do_save():
            old_mode = (self.tcfg.mode, self.tcfg.pair())
            previous = self.tcfg
            self.tcfg = copy.deepcopy(previous)
            try:
                collect()
                self.tcfg.save()
            except (ValueError, OSError) as exc:
                self.tcfg = previous
                self.settle_sec = previous.settle_ms / 1000.0
                lbl_saved.configure(text=str(exc), fg=UK.C.RED)
                return
            baseline['value'] = draft()
            import frontend_shell
            frontend_shell.refresh_topic(self)
            refresh_topic_state()
            if old_mode != (self.tcfg.mode, self.tcfg.pair()):
                for rec in self.records:
                    self._invalidate_translation(rec)
                    self.autosave(rec)
                self.full_render()
            self.stop_translation()
            started = self.start_translation(silent=True) \
                if self.tcfg.enabled else False
            name = self._translation_topic_name()
            if self.tcfg.enabled and not started:
                lbl_saved.configure(
                    text=f"已保存 · 翻译未启动：请检查引擎与 API Key（当前主题：{name}）",
                    fg=UK.C.RED)
            else:
                lbl_saved.configure(
                    text=f"✓ 已保存 · 当前主题：{name}"
                         f"{'' if self.tcfg.enabled else '（翻译关闭，仅记录原文）'}",
                    fg=UK.C.GREEN)
            btn_save.set_text("✓ 已保存")
            self.flash(f"设置已保存 · 当前主题：{name}")
            self.root.after(1800, _reset_save_ui)

        def _reset_save_ui():
            try:
                btn_save.set_text("保存")
                lbl_saved.configure(text=current_topic_hint(), fg=UK.C.SUB)
            except Exception:
                pass

        btn_save = UK.Pill(bottom, "保存", do_save, kind="primary", icon="check", bg=UK.C.N50, height=38, padx=22)
        btn_save.pack(side="right")
        btn_test = UK.Pill(bottom, "测试", do_test, kind="secondary", icon="translate", tooltip="用当前设置翻译一句试试",
                           bg=UK.C.N50, height=38, padx=18)
        btn_test.pack(side="right", padx=(10, 0))

        def select_page(index):
            for i, p in enumerate(pages):
                p.pack_forget()
                tab_buttons[i].set_kind('selected' if i == index else 'tab')
            pages[index].pack(fill='both', expand=True)
            title = page_titles[index]
            if title in ('用量', '更新'):
                self._sheet_footer.pack_forget()
            else:
                self._sheet_footer.pack(side='bottom', fill='x', padx=24, pady=(8, 12))
                if title == '服务':
                    if not btn_test.winfo_manager():
                        btn_test.pack(side='right', padx=(10, 0))
                    if not lbl_test.winfo_manager():
                        lbl_test.pack(side='left', before=lbl_saved)
                else:
                    btn_test.pack_forget()
                    lbl_test.pack_forget()

        for i, title in enumerate(page_titles):
            tab = UK.Pill(tabs, title, lambda i=i: select_page(i), kind='tab', bg=UK.C.N100, height=36, padx=14)
            tab.pack(side='left', padx=2)
            tab_buttons.append(tab)

        refresh(state["key"])
        select_page(page_titles.index(initial_page) if initial_page in page_titles else 0)

        def draft():
            profile_values = copy.deepcopy(profiles)
            profile_values[active_profile['name']] = {
                'engine': _LABEL_TO_ENGINE[sel_engine.get()],
                'model': e_model.get().strip(), 'base_url': e_base.get().strip(),
                'api_key': e_key.get().strip(), 'azure_region': e_region.get().strip()}
            topic_values = copy.deepcopy(topics)
            if state['key']:
                item = topic_values.setdefault(state['key'], {})
                item.update(name=e_tname.get().strip() or state['key'],
                            brief=t_brief.get().strip(), glossary=get_glossary_text())
            return (bool(sw_tr.get()), sel_reason.get(), active_profile['name'], profile_values,
                    state['key'], tuple(order), tuple(hidden), topic_values,
                    {key: value.get() for key, value in ents.items()},
                    {key: value.get() for key, value in extras.items()},
                    {})

        baseline = {'value': draft()}
        self._sheet_guard = lambda: (
            draft() == baseline['value'] or messagebox.askyesno(
                '放弃未保存的设置？', '设置已修改但尚未保存。确定放弃修改？',
                parent=self.root, default='no'))

    self._show_sheet("设置", build)

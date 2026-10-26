import copy
import threading
import tkinter as tk
from tkinter import messagebox
import ui_kit as UK
from translator import all_topics, TOPICS, TranslationService

def open_settings(self):
    """Tabbed settings, drafts validated before applying to the running service."""

    def build(win):
        W = UK.C.N0
        card = UK.Card(win, padding=20, autosize=False)
        card.pack(fill="both", expand=True)
        tabs = tk.Frame(card.body, bg=UK.C.N100, padx=4, pady=4)
        tabs.pack(anchor="w", pady=(0,16))
        host = tk.Frame(card.body, bg=W)
        host.pack(fill="both",expand=True)
        pages = [tk.Frame(host,bg=W) for _ in range(6)]
        tab_buttons=[]
        def select_page(index):
            for i,page in enumerate(pages):
                page.pack_forget()
                tab_buttons[i].set_kind('selected' if i==index else 'tab')
            pages[index].pack(fill='both',expand=True)
        for i,title in enumerate(['服务','主题','翻译参数','请求参数','用量','备用']):
            tab=UK.Pill(tabs,title,lambda i=i:select_page(i),kind='tab',bg=UK.C.N100,height=36,padx=12)
            tab.pack(side='left',padx=2);tab_buttons.append(tab)

        def lab(parent,text):
            return tk.Label(parent,text=text,bg=W,fg=UK.C.SUB,font=UK.F_SMALL,anchor='w')
        def cell(parent,title,row,col=0,span=1):
            for c in range(3 if parent in (pages[2],pages[3]) else 2):parent.columnconfigure(c,weight=1,uniform='field')
            frame=tk.Frame(parent,bg=W)
            frame.grid(row=row,column=col,columnspan=span,sticky='we',padx=(0,16 if col==0 and span==1 else 0),pady=(0,8))
            lab(frame,title).pack(anchor='w',pady=(0,4))
            return frame
        def field(parent,title,value,row,col=0,span=1,secret=False):
            frame=cell(parent,title,row,col,span)
            w=UK.Field(frame,value=str(value),secret=secret,bg=W,height=36)
            w.pack(fill='x')
            return w

        service=pages[0]
        switchrow=tk.Frame(service,bg=W);switchrow.pack(fill='x',pady=(0,8))
        lab(switchrow,'实时翻译').pack(side='left')
        sw_tr=UK.Switch(switchrow,value=bool(self.tcfg.enabled),bg=W)
        sw_tr.pack(side='left',padx=14)
        form=tk.Frame(service,bg=W);form.pack(fill='x')
        engine_cell=cell(form,'引擎',0)
        sel_engine=UK.Select(engine_cell,['openai 兼容','DeepL','Azure'],bg=W,height=36,
            value={'openai':'openai 兼容','deepl':'DeepL','azure':'Azure'}.get(self.tcfg.engine,'openai 兼容'))
        sel_engine.pack(fill='x')
        e_model=field(form,'模型',self.tcfg.model,0,1)
        e_base=field(form,'Base URL',self.tcfg.base_url,1,span=2)
        e_key=field(form,'API Key',self.tcfg.api_key,2,secret=True)
        e_region=field(form,'Azure 区域',self.tcfg.azure_region,2,1)

        topics=all_topics(self.tcfg);order=list(topics)
        state={'key':self.tcfg.topic if self.tcfg.topic in topics else (order[0] if order else None),'busy':False}
        hidden=list(self.tcfg.topics_hidden or [])
        # 主题页：左边选主题，右边是「会议背景」+「译名对照」——术语跟着主题走。
        #
        # 这里曾经单独占一个「术语」页签，和主题页各写各的：同一个词既可能写在
        # 主题的说明里、又可能写在全局术语表里，两处冲突时没人说得清以哪个为准。
        # 术语本来就是「这个领域的词该怎么译」，属于主题的一部分，所以并进来：
        # 换主题 = 换整套译名对照，不会串味。
        topic_page=pages[1]
        body=tk.Frame(topic_page,bg=W);body.pack(fill='both',expand=True)
        lb=UK.PickList(body,on_pick=lambda key:on_pick(key),width=170,rows=8,bg=W)
        lb.pack(side='left',fill='y',padx=(0,20))
        right=tk.Frame(body,bg=W);right.pack(side='left',fill='both',expand=True)
        e_tname=UK.Field(right,bg=W,height=36);e_tname.pack(fill='x')
        tb=tk.Frame(right,bg=W);tb.pack(anchor='w',pady=(8,10))
        header=tk.Frame(right,bg=W);header.pack(fill='x',pady=(0,4))
        lab(header,'会议背景').pack(side='left')
        t_brief=UK.RoundedText(right,lines=4,bg=W);t_brief.pack(fill='both',expand=True)
        terms_header=tk.Frame(right,bg=W);terms_header.pack(fill='x',pady=(10,4))
        lab(terms_header,'译名对照').pack(side='left')
        t_gloss=UK.RoundedText(right,lines=4,bg=W);t_gloss.pack(fill='both',expand=True)

        pairs=[('上文条数','ctx',self.tcfg.context_n),('句末等待 ms','sent',self.tcfg.sentence_end_grace_ms),
               ('兜底等待 ms','settle',self.tcfg.settle_ms),('批量条数','batch',self.tcfg.batch_size),
               ('批量字符','chars',self.tcfg.batch_chars),('并发数','conc',self.tcfg.concurrency)]
        ents={}
        for title,key,value in pairs:
            positions={'ctx':(2,0,0),'sent':(2,0,1),'settle':(2,0,2),
                       'batch':(3,0,0),'chars':(3,0,1),'conc':(3,0,2)}
            page,row,col=positions[key]
            ents[key]=field(pages[page],title,value,row,col)
        advanced=[('短句上下文阈值','ctx_min_len',0,10000),('上下文最大字符','ctx_max_chars',0,100000),
                  ('新句后等待 ms','supersede_grace_ms',150,60000),('启动补译上限','backlog_max',0,10000),
                  ('合批等待 ms','batch_window_ms',0,60000),('请求间隔 ms','min_interval_ms',0,60000),
                  ('请求超时 s','timeout_s',1,300),('重试次数','max_retry',0,10)]
        positions={'ctx_min_len':(2,1,0),'ctx_max_chars':(2,1,1),'supersede_grace_ms':(2,1,2),
                   'backlog_max':(2,2,0),'batch_window_ms':(3,1,0),'min_interval_ms':(3,1,1),
                   'timeout_s':(3,1,2),'max_retry':(3,2,0)}
        extras={}
        for title,attr,low,high in advanced:
            page,row,col=positions[attr]
            extras[attr]=field(pages[page],title,getattr(self.tcfg,attr),row,col)
        usage_labels = {}
        for i,(title,key) in enumerate([('输入 tokens','input_tokens'),('输出 tokens','output_tokens'),
                                        ('缓存命中 tokens','cache_hit_tokens'),('缓存未命中 tokens','cache_miss_tokens'),
                                        ('API 请求','calls'),('统计范围','scope')]):
            frame = cell(pages[4], title, i//2, i%2)
            label = tk.Label(frame, text='—', bg=W, fg=UK.C.TEXT, font=UK.F_UI, anchor='w')
            label.pack(fill='x', pady=(4,12))
            usage_labels[key] = label
        def refresh_usage():
            if not pages[4].winfo_exists():
                return
            stats = self.svc.stats() if self.svc else {'calls':0}
            calls = stats.get('calls',0)
            for key,label in usage_labels.items():
                value = stats.get(key)
                text = '本次翻译服务' if key=='scope' else ('未提供' if value is None else f'{value:,}')
                if value is not None and key in stats.get('token_reports',{}) and stats['token_reports'][key]<calls:
                    text += '（部分请求）'
                label.configure(text=text)
            self.root.after(1500,refresh_usage)
        refresh_usage()
        fallback={}
        for title,attr,row,col,span in [('引擎（空为关闭）','fallback_engine',0,0,1),('模型','fallback_model',0,1,1),
                                       ('Base URL','fallback_base_url',1,0,2),('API Key','fallback_api_key',2,0,2)]:
            fallback[attr]=field(pages[5],title,getattr(self.tcfg,attr),row,col,span,secret=attr.endswith('key'))

        self._sheet_footer.pack(side='bottom',fill='x',padx=24,pady=(8,12))
        bottom=tk.Frame(self._sheet_footer,bg=UK.C.N50);bottom.pack(fill='x')
        lbl_test=tk.Label(bottom,text='',bg=UK.C.N50,fg=UK.C.SUB,font=UK.F_MINI,wraplength=300,justify='left')
        lbl_test.pack(side='left')
        lbl_saved=tk.Label(bottom,text='',bg=UK.C.N50,fg=UK.C.GREEN,font=UK.F_MINI,wraplength=300,justify='left')
        lbl_saved.pack(side='left',padx=12)

        # ---------------- 主题逻辑 ----------------
        def load_fields(key):
            t = topics.get(key, {})
            e_tname.set(t.get("name", key))
            t_brief.set(t.get("brief", "") or "")
            t_gloss.set(t.get("glossary", "") or "")

        def snapshot(key=None):
            key = key or state["key"]
            if not key:
                return
            t = dict(topics.get(key, {}))
            t["name"] = e_tname.get().strip() or key
            t["brief"] = t_brief.get().strip()
            t["glossary"] = t_gloss.get().strip()
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

        for text, cmd in (("+", do_new), ("复制", do_clone),
                          ("删除", do_delete), ("重置", do_reset)):
            UK.Pill(tb, text, cmd, kind="ghost", height=26, padx=9, bg=W).pack(
                side="left", padx=(0, 4))
        btn_restore = UK.Pill(tb, "恢复内置", do_restore, kind="ghost",
                              height=26, padx=9, bg=W)
        btn_restore.pack(side="left", padx=(0, 4))

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
            if any(line.strip() and ("=" not in line or not all(x.strip() for x in line.split("=", 1)))
                   for line in t_gloss.get().splitlines()):
                raise ValueError("译名对照请按每行「term=译名」填写")
            extra_numbers = {}
            for title, attr, low, high in advanced:
                try:
                    number = int(extras[attr].get())
                    if not low <= number <= high:
                        raise ValueError()
                except ValueError:
                    raise ValueError(f"{title}必须是 {low}–{high} 之间的整数")
                extra_numbers[attr] = number
            if fallback['fallback_engine'].get().strip() not in ('', 'openai', 'deepl', 'azure'):
                raise ValueError("备用引擎请填写 openai / deepl / azure 或留空")
            snapshot()
            for attr, value in extra_numbers.items():
                setattr(self.tcfg, attr, value)
            for attr, field in fallback.items():
                setattr(self.tcfg, attr, field.get().strip())
            self.tcfg.enabled = bool(sw_tr.get())
            self.tcfg.engine = {"openai 兼容": "openai", "DeepL": "deepl",
                                "Azure": "azure"}[sel_engine.get()]
            self.tcfg.base_url = e_base.get().strip()
            self.tcfg.model = e_model.get().strip()
            self.tcfg.api_key = e_key.get().strip()
            self.tcfg.azure_region = e_region.get().strip()
            self.tcfg.topic = state["key"] or "general"
            self.tcfg.topics_hidden = list(hidden)
            for key, attr in {"ctx":"context_n", "sent":"sentence_end_grace_ms", "settle":"settle_ms",
                              "batch":"batch_size", "chars":"batch_chars", "conc":"concurrency"}.items():
                setattr(self.tcfg, attr, numbers[key])
            # 术语已并入主题（t_gloss 随 topic 一起保存），这里清掉旧的全局术语
            # 表：留着它只会在提示词里和新位置重复，且两处冲突时没人知道该信谁。
            # glossary_mode 保留在配置里（旧配置能读、能存），但界面不再提供开关。
            self.tcfg.glossary = ""
            self.settle_sec = self.tcfg.settle_ms / 1000.0
            self.tcfg.topics_custom = {}
            self._persist_topics(order, topics)

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
            lbl_test.configure(text="测试中…", fg=UK.C.SUB)
            self.root.update_idletasks()

            def run():
                svc_tmp = TranslationService(candidate, lambda *a: None)
                ok, msg = svc_tmp.test()
                self.q.put(('test_result', (lbl_test, ok, msg)))

            threading.Thread(target=run, daemon=True).start()

        def do_save():
            old_topic, old_mode = self.tcfg.topic, self.tcfg.mode
            old_ver = self.tcfg.topic_version()
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
            if (old_topic != self.tcfg.topic or old_mode != self.tcfg.mode
                    or old_ver != self.tcfg.topic_version()):
                for rec in self.records:
                    self._invalidate_translation(rec)
                    self.autosave(rec)
                self.full_render()
            # 翻译按开关启停：关闭时只停不开，其余设置照常生效
            self.stop_translation()
            started = self.start_translation(silent=True) \
                if self.tcfg.enabled else False
            # 不立即关面板：先就地明确告知保存结果与当前选中的主题
            topic = all_topics(self.tcfg).get(self.tcfg.topic) or {}
            name = topic.get("name") or self.tcfg.topic
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
            """保存提示复位（面板保持打开，由用户自行关闭）。"""
            try:
                btn_save.set_text("保存")
                lbl_saved.configure(text="")
            except Exception:
                pass            # 面板已被关闭/重建，无需复位

        btn_save = UK.Pill(bottom, "保存", do_save, kind="primary", bg=UK.C.N50, height=38, padx=24)
        btn_save.pack(side="right")
        UK.Pill(bottom, "测试", do_test, kind="secondary", bg=UK.C.N50, height=38, padx=20).pack(side="right",
                                                                 padx=(10, 0))

        refresh(state["key"])
        select_page(0)

    self._show_sheet("设置", build)

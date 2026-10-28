import copy
import threading
import tkinter as tk
from tkinter import messagebox, simpledialog
import ui_kit as UK
from translator import (all_topics, TOPICS, TranslationService,
                        REASONING_LABELS, REASONING_TITLES)
import translator as TR

def open_settings(self, page="服务"):
    """Tabbed settings, drafts validated before applying to the running service."""
    initial_page = page

    def build(win):
        W = UK.C.N0
        card = UK.Card(win, padding=20, autosize=False)
        card.pack(fill="both", expand=True)
        tabs = tk.Frame(card.body, bg=UK.C.N100, padx=4, pady=4)
        tabs.pack(anchor='w', pady=(0,16))
        host = tk.Frame(card.body, bg=W)
        host.pack(fill="both",expand=True)
        page_titles=['服务','主题','翻译参数','请求参数','用量','更新']
        pages = [tk.Frame(host,bg=W) for _ in page_titles]
        tab_buttons=[]
        def select_page(index):
            for i,page in enumerate(pages):
                page.pack_forget()
                tab_buttons[i].set_kind('selected' if i==index else 'tab')
            pages[index].pack(fill='both',expand=True)
            if page_titles[index]=='更新':
                self._sheet_footer.pack_forget()
            else:
                self._sheet_footer.pack(side='bottom',fill='x',padx=24,pady=(8,12))
        for i,title in enumerate(page_titles):
            tab=UK.Pill(tabs,title,lambda i=i:select_page(i),kind='tab',bg=UK.C.N100,height=36,padx=12)
            tab.pack(side='left',padx=2);tab_buttons.append(tab)

        def lab(parent,text):
            return tk.Label(parent,text=text,bg=W,fg=UK.C.SUB,font=UK.F_SMALL,anchor='w')
        def cell(parent,title,row,col=0,span=1):
            wide = parent in (pages[0], pages[2], pages[3])
            for c in range(3 if wide else 2):parent.columnconfigure(c,weight=1,uniform='field')
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
        switchrow=tk.Frame(service,bg=W);switchrow.pack(fill='x',pady=(0,4))
        lab(switchrow,'实时翻译').pack(side='left')
        sw_tr=UK.Switch(switchrow,value=bool(self.tcfg.enabled),bg=W)
        sw_tr.pack(side='left',padx=14)
        profile_fields = ('engine', 'model', 'base_url', 'api_key', 'azure_region')
        profiles = copy.deepcopy(self.tcfg.engine_profiles)
        if not profiles:
            profiles = {'当前配置': {key: getattr(self.tcfg, key) for key in profile_fields}}
        active_profile = {'name': self.tcfg.active_engine_profile
                          if self.tcfg.active_engine_profile in profiles else next(iter(profiles))}
        profile_row = tk.Frame(switchrow, bg=W)
        profile_row.pack(side='right', fill='x', expand=True, padx=(20,0))
        lab(profile_row, '已存配置').pack(side='left', padx=(0,8))
        profile_select = UK.Select(profile_row, list(profiles), value=active_profile['name'],
                                   command=lambda name: switch_profile(name), bg=W, height=34, width=180)
        profile_select.pack(side='left', fill='x', expand=True)
        UK.Pill(profile_row, '新建', lambda: new_profile(), kind='secondary',
                bg=W, height=34, padx=12).pack(side='left', padx=(8,0))
        UK.Pill(profile_row, '重命名', lambda: rename_profile(), kind='ghost',
                bg=W, height=34, padx=10).pack(side='left', padx=(4,0))
        UK.Pill(profile_row, '删除', lambda: delete_profile(), kind='ghost',
                bg=W, height=34, padx=12).pack(side='left', padx=(4,0))
        form=tk.Frame(service,bg=W);form.pack(fill='x')
        engine_cell=cell(form,'引擎',0)
        sel_engine=UK.Select(engine_cell,['openai 兼容','DeepL','Azure'],bg=W,height=36,
            value={'openai':'openai 兼容','deepl':'DeepL','azure':'Azure'}.get(self.tcfg.engine,'openai 兼容'))
        sel_engine.pack(fill='x')
        e_model=field(form,'模型',self.tcfg.model,0,1,span=2)
        e_base=field(form,'Base URL',self.tcfg.base_url,1,span=3)
        e_key=field(form,'API Key',self.tcfg.api_key,2,secret=True)
        e_region=field(form,'Azure 区域',self.tcfg.azure_region,2,1)

        # 思考强度：不是每家上游都认这个字段，旁边配一个探测，结果用底部提示条回话
        reason_cell=cell(form,'思考强度',2,2)
        reason_row=tk.Frame(reason_cell,bg=W)
        reason_row.pack(fill='x')
        sel_reason=UK.Select(reason_row,list(REASONING_LABELS),
                             value=REASONING_TITLES.get(self.tcfg.reasoning,'自动'),
                             bg=W,height=36)
        sel_reason.pack(side='left',fill='x',expand=True)
        probing={'on':False}

        def do_probe():
            if probing['on']:
                return
            probing['on']=True
            level=REASONING_LABELS.get(sel_reason.get(),'auto')
            self.flash('正在检测上游是否支持思考参数…')

            def apply(text):
                probing['on']=False
                self.flash(text)

            def work():
                try:
                    field_name, message = TR.probe_reasoning(self.tcfg, level)
                except Exception as exc:
                    field_name, message = '', f'检测失败：{exc}'
                self.q.put(('ui',(apply,('✓ ' if field_name else '✗ ')+message)))

            threading.Thread(target=work,daemon=True).start()

        UK.Pill(reason_row,'检测',do_probe,kind='ghost',bg=W,height=36,padx=10).pack(
            side='left',padx=(6,0))

        def snapshot_profile():
            profiles[active_profile['name']] = {
                'engine': {'openai 兼容':'openai', 'DeepL':'deepl', 'Azure':'azure'}[sel_engine.get()],
                'model': e_model.get().strip(), 'base_url': e_base.get().strip(),
                'api_key': e_key.get().strip(), 'azure_region': e_region.get().strip()}

        def show_profile(name):
            p = profiles[name]
            sel_engine.set({'openai':'openai 兼容', 'deepl':'DeepL', 'azure':'Azure'}.get(p.get('engine'), 'openai 兼容'))
            for widget, key in ((e_model,'model'), (e_base,'base_url'), (e_key,'api_key'), (e_region,'azure_region')):
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
            name = simpledialog.askstring('新建引擎配置', '输入配置名称：', parent=self.root)
            if name is None:
                return
            name = name.strip()
            if not name or name in profiles:
                messagebox.showerror('新建引擎配置', '名称不能为空或与已有配置重复。', parent=self.root)
                return
            snapshot_profile()
            profiles[name] = {'engine':'openai', 'model':'', 'base_url':'',
                              'api_key':'', 'azure_region':''}
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
            name = simpledialog.askstring('重命名引擎配置', '输入新名称：',
                                          initialvalue=old, parent=self.root)
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

        topics=all_topics(self.tcfg);order=list(topics)
        state={'key':self.tcfg.topic if self.tcfg.topic in topics else
               ('general' if 'general' in topics else (order[0] if order else None)), 'busy':False}
        hidden=list(self.tcfg.topics_hidden or [])
        # 主题页：左边选主题，右边是「会议背景」+「译名对照」——术语跟着主题走。
        #
        # 这里曾经单独占一个「术语」页签，和主题页各写各的：同一个词既可能写在
        # 主题的说明里、又可能写在全局术语表里，两处冲突时没人说得清以哪个为准。
        # 术语本来就是「这个领域的词该怎么译」，属于主题的一部分，所以并进来：
        # 换主题 = 换整套译名对照，不会串味。
        topic_page=pages[1]
        body=tk.Frame(topic_page,bg=W);body.pack(fill='both',expand=True)
        # rows 只是初始值：PickList 会按实际高度调整可见行数，窗口缩小时不会
        # 把下面那半个输入框挤出可视区。
        lb=UK.PickList(body,on_pick=lambda key:on_pick(key),width=170,rows=6,bg=W)
        lb.pack(side='left',fill='y',padx=(0,20))
        right=tk.Frame(body,bg=W);right.pack(side='left',fill='both',expand=True)
        e_tname=UK.Field(right,bg=W,height=34);e_tname.pack(fill='x')
        tb=tk.Frame(right,bg=W);tb.pack(anchor='w',pady=(4,4))
        header=tk.Frame(right,bg=W);header.pack(fill='x',pady=(0,4))
        lab(header,'会议背景').pack(side='left')
        lbl_topic_state=lab(header,'')
        lbl_topic_state.pack(side='right')
        # 两个输入框用 grid 分成等高的两行（weight=1），让 Tk 自己保证「一样高」。
        #
        # 之前用 pack + fill='both' + expand：RoundedText 内部关掉了尺寸传播，
        # expand 拉不动它，容器一紧张就把后一个压扁（实测 66 / 57 一高一矮），
        # 靠回调手算像素去补也补不稳。grid 的 rowconfigure 权重是布局层的承诺，
        # 不依赖回调时机，首绘就对称。
        #
        # 两个标题行用 weight=0，只占各自需要的高度。
        edits=tk.Frame(right,bg=W);edits.pack(fill='both',expand=True)
        edits.columnconfigure(0,weight=1)
        edits.rowconfigure(0,weight=1,uniform='ed')
        edits.rowconfigure(2,weight=1,uniform='ed')
        t_brief=UK.RoundedText(edits,lines=2,bg=W)
        t_brief.grid(row=0,column=0,sticky='nsew')
        terms_header=tk.Frame(edits,bg=W)
        terms_header.grid(row=1,column=0,sticky='we',pady=(6,2))
        lab(terms_header,'译名对照').pack(side='left')
        tk.Label(terms_header,text='每行 原文=译文 · 适用于 OpenAI 兼容引擎',
                 bg=W,fg=UK.C.T3,font=UK.F_MINI).pack(side='right')
        t_gloss=UK.RoundedText(edits,lines=2,bg=W)
        t_gloss.grid(row=2,column=0,sticky='nsew')

        # 等高分配由 grid 的 uniform 行完成，无需定时校准或回写请求高度。

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
                  ('请求超时 s','timeout_s',1,300),('重试次数','max_retry',0,10),
                  ('碎句合并 ms','merge_gap_ms',0,10000)]
        positions={'ctx_min_len':(2,1,0),'ctx_max_chars':(2,1,1),'supersede_grace_ms':(2,1,2),
                   'backlog_max':(2,2,0),'batch_window_ms':(3,1,0),'min_interval_ms':(3,1,1),
                   'timeout_s':(3,1,2),'max_retry':(3,2,0),'merge_gap_ms':(2,2,1)}
        extras={}
        for title,attr,low,high in advanced:
            page,row,col=positions[attr]
            extras[attr]=field(pages[page],title,getattr(self.tcfg,attr),row,col)

        usage_labels = {}
        for i,(title,key) in enumerate([('输入 tokens','input_tokens'),('输出 tokens','output_tokens'),
                                        ('缓存命中 tokens','cache_hit_tokens'),('缓存未命中 tokens','cache_miss_tokens'),
                                        ('API 请求','calls'),('统计范围','scope')]):
            frame = cell(pages[4], title, i//2, i%2)
            label = tk.Label(frame, text='—', bg=W, fg=UK.C.TEXT,
                              font=UK.F_UI if key == 'scope' else ('Consolas', 11), anchor='w')
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

        import sys
        import app_updates
        import update_workflow as UW
        from teamsub import APP_VERSION
        update_page=pages[page_titles.index('更新')]
        tk.Label(update_page,text=f'TeamsSub {APP_VERSION}',bg=W,fg=UK.C.TEXT,
                 font=UK.F_HEAD).pack(anchor='w',pady=(0,20))
        state_update=UW.state_for(self)
        update_status=lab(update_page,'尚未检查')
        update_status.configure(wraplength=680,justify='left')
        update_status.pack(anchor='w',pady=(0,12))
        update_bar=tk.Canvas(update_page,height=8,bg=W,bd=0,highlightthickness=0)
        update_actions=tk.Frame(update_page,bg=W);update_actions.pack(anchor='w')

        def open_release():
            import webbrowser
            release=state_update['release']
            if release:
                try:
                    opened=webbrowser.open(release.url)
                except OSError:
                    opened=False
                if not opened:
                    state_update['error']='无法打开浏览器，请稍后重试'
                    render_update()

        def check_updates():
            if state_update['phase'] in UW.BUSY_PHASES:
                return
            state_update.update(phase='checking',error='',release=None,path=None)
            render_update()
            def run():
                try:
                    release=app_updates.check_latest_release()
                    error=''
                except Exception as exc:
                    release=None
                    error=str(exc) if isinstance(exc,app_updates.UpdateCheckError) else '检查失败，请稍后重试'
                def show_result(_):
                    state_update.update(phase='available',release=release,error=error,checked=True)
                    if update_status.winfo_exists():
                        render_update()
                self.q.put(('ui',(show_result,None)))
            threading.Thread(target=run,daemon=True).start()

        def download_update():
            if state_update['path']:
                UW.begin_install(self)
            else:
                UW.start_download(self,state_update['release'])
            render_update()

        check_button=UK.Pill(update_actions,'检查更新',check_updates,kind='primary',bg=W,height=36)
        check_button.pack(side='left')
        release_button=UK.Pill(update_actions,'前往下载',open_release,kind='secondary',bg=W,height=36)
        manual_button=UK.Pill(update_actions,'手动下载',open_release,kind='ghost',bg=W,height=36)
        cancel_button=UK.Pill(update_actions,'取消下载',lambda: UW.cancel_download(self),kind='ghost',bg=W,height=36)

        def render_update():
            phase=state_update['phase']
            busy=phase in UW.BUSY_PHASES
            check_button.command=None if busy else check_updates
            check_button.set_text('检查中…' if phase=='checking' else
                                  ('重新检查' if state_update.get('checked') else '检查更新'))
            release_button.pack_forget();manual_button.pack_forget();cancel_button.pack_forget()
            update_bar.pack_forget()
            color=UK.C.SUB
            release=state_update['release']
            if phase=='checking':
                text='正在检查…'
            elif phase in ('downloading','cancelling'):
                received,total=state_update['received'],max(1,state_update['total'])
                text=(f'下载中 {received*100//total}% · {received/1048576:.1f} / {total/1048576:.1f} MB'
                      if phase=='downloading' else '正在取消…')
                update_bar.pack(fill='x',before=update_actions,pady=(0,16))
                width=max(20,update_page.winfo_width())
                update_bar.delete('all')
                UK.round_rect(update_bar,0,0,width-1,7,4,fill=UK.C.N100)
                if received:
                    UK.round_rect(update_bar,0,0,max(8,(width-1)*min(1,received/total)),7,4,fill=UK.C.A500)
                cancel_button.set_text('取消下载')
                if phase=='downloading':cancel_button.pack(side='left',padx=(12,0))
            elif phase=='saving':
                text='正在保存会议…'
                cancel_button.set_text('取消更新');cancel_button.pack(side='left',padx=(12,0))
            elif phase=='recovering':
                text='正在恢复会议记录…'
            elif phase=='installing':
                text='正在安装，即将重启…'
            elif state_update['error']:
                text=state_update['error'];color=UK.C.RED
            elif release and release.newer_than(APP_VERSION):
                text=f'发现新版本 {release.version} · 当前版本 {APP_VERSION}';color=UK.C.A600
            elif release:
                text=f'已是最新正式版本 · 当前版本 {APP_VERSION}';color=UK.C.GREEN
            else:
                text='尚未检查'
            update_status.configure(text=text,fg=color)
            if not busy and release and release.newer_than(APP_VERSION):
                automatic=bool(getattr(sys,'frozen',False) and release.installer)
                release_button.set_text(('安装并重启' if state_update['path'] else '下载并更新')
                                        if automatic else '前往下载')
                release_button.command=download_update if automatic else open_release
                release_button.pack(side='left',padx=(12,0))
                if automatic:manual_button.pack(side='left',padx=(8,0))

        def poll_update():
            if update_status.winfo_exists():
                render_update()
                self.root.after(200,poll_update)
        poll_update()

        self._sheet_footer.pack(side='bottom',fill='x',padx=24,pady=(8,12))
        bottom=tk.Frame(self._sheet_footer,bg=UK.C.N50);bottom.pack(fill='x')
        lbl_test=tk.Label(bottom,text='',bg=UK.C.N50,fg=UK.C.SUB,font=UK.F_MINI,wraplength=300,justify='left')
        lbl_test.pack(side='left')
        def current_topic_hint():
            name=UK.fit_text(self._translation_topic_name(), UK.F_MINI, 220)
            return f'当前主题：{name}'
        lbl_saved=tk.Label(bottom,text=current_topic_hint(),bg=UK.C.N50,fg=UK.C.SUB,font=UK.F_MINI,wraplength=300,justify='left')
        lbl_saved.pack(side='left',padx=12)

        # ---------------- 主题逻辑 ----------------
        def refresh_topic_state():
            current=state['key'] == self.tcfg.topic and state['key'] in topics
            lbl_topic_state.configure(
                text='当前主题 · 修改后保存生效' if current else '待切换 · 保存后生效',
                fg=UK.C.SUB if current else UK.C.A600)

        def load_fields(key):
            t = topics.get(key, {})
            e_tname.set(t.get("name", key))
            t_brief.set(t.get("brief", "") or "")
            t_gloss.set(t.get("glossary", "") or "")
            refresh_topic_state()

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
            snapshot()
            for attr, value in extra_numbers.items():
                setattr(self.tcfg, attr, value)
            self.tcfg.enabled = bool(sw_tr.get())
            self.tcfg.engine = {"openai 兼容": "openai", "DeepL": "deepl",
                                "Azure": "azure"}[sel_engine.get()]
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
            for key, attr in {"ctx":"context_n", "sent":"sentence_end_grace_ms", "settle":"settle_ms",
                              "batch":"batch_size", "chars":"batch_chars", "conc":"concurrency"}.items():
                setattr(self.tcfg, attr, numbers[key])
            # 术语已并入主题（t_gloss 随 topic 一起保存），这里清掉旧的全局术语
            # 表：留着它只会在提示词里和新位置重复，且两处冲突时没人知道该信谁。
            # glossary_mode 保留在配置里（旧配置能读、能存），但界面不再提供开关。
            self.tcfg.glossary = ""
            self.settle_sec = self.tcfg.settle_ms / 1000.0
            # 采集线程在跑，合并窗口立即生效，不必重启
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
            baseline['value'] = draft()
            import frontend_shell
            frontend_shell.refresh_topic(self)
            refresh_topic_state()
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
            """保存提示复位（面板保持打开，由用户自行关闭）。"""
            try:
                btn_save.set_text("保存")
                lbl_saved.configure(text=current_topic_hint(), fg=UK.C.SUB)
            except Exception:
                pass            # 面板已被关闭/重建，无需复位

        btn_save = UK.Pill(bottom, "保存", do_save, kind="primary", bg=UK.C.N50, height=38, padx=24)
        btn_save.pack(side="right")
        UK.Pill(bottom, "测试", do_test, kind="secondary", bg=UK.C.N50, height=38, padx=20).pack(side="right",
                                                                 padx=(10, 0))

        refresh(state["key"])
        select_page(page_titles.index(initial_page) if initial_page in page_titles else 0)

        def draft():
            profile_values = copy.deepcopy(profiles)
            profile_values[active_profile['name']] = {
                'engine': {'openai 兼容':'openai', 'DeepL':'deepl', 'Azure':'azure'}[sel_engine.get()],
                'model': e_model.get().strip(), 'base_url': e_base.get().strip(),
                'api_key': e_key.get().strip(), 'azure_region': e_region.get().strip()}
            topic_values = copy.deepcopy(topics)
            if state['key']:
                item = topic_values.setdefault(state['key'], {})
                item.update(name=e_tname.get().strip() or state['key'],
                            brief=t_brief.get().strip(), glossary=t_gloss.get().strip())
            return (bool(sw_tr.get()), active_profile['name'], profile_values,
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

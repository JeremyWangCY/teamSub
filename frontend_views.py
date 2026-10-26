"""History, reading preferences and export flows, independent from live capture."""
import copy
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import ui_kit as UK
import meeting_export as ME
import meeting_repository as MR


def label(parent, text, *, small=False):
    w = tk.Label(parent, text=text, bg=parent.cget('bg'), fg=UK.C.SUB if small else UK.C.TEXT,
                 font=UK.F_SMALL if small else UK.F_HEAD, justify='left', anchor='w', wraplength=720)
    w.pack(anchor='w', pady=(8, 8))
    return w


def card(parent):
    c = UK.Card(parent, padding=20, autosize=False)
    c.pack(fill='both', expand=True)
    return c.body


def open_export(app, records=None, title=None, initial_format='md'):
    live = records is None
    frozen = ME.snapshot(app.records if live else records)
    title = title or app.reader.meeting_title or '会议记录'
    current = ME.snapshot(ME.filtered(app.records, app.speaker_filter, app.search.value(),
                                     getattr(app, 'display_start', 0))) if live else frozen
    if not frozen:
        app._notice('还没有可导出的记录。')
        return

    def build(parent):
        b = card(parent)
        label(b, title, small=True)
        row = tk.Frame(b, bg=UK.C.N0)
        row.pack(fill='x', pady=8)
        scope = UK.Select(row, ['整场会议', '当前筛选结果'] if live else ['所选历史记录'],
                          value='整场会议' if live else '所选历史记录', width=170, bg=UK.C.N0)
        scope.pack(side='left')
        fmt = UK.Select(row, ['Markdown', 'TXT 纯文本', 'SRT 双语字幕'],
                        value={'md':'Markdown', 'txt':'TXT 纯文本', 'srt':'SRT 双语字幕'}[initial_format], width=160, bg=UK.C.N0)
        fmt.pack(side='left', padx=12)
        content = UK.Select(row, ['原文 + 译文', '仅原文', '仅译文'], value='原文 + 译文', width=150, bg=UK.C.N0)
        content.pack(side='left')
        choices = tk.Frame(b, bg=UK.C.N0)
        choices.pack(fill='x', pady=8)
        name = tk.BooleanVar(value=True)
        stamp = tk.BooleanVar(value=True)
        synthetic = tk.BooleanVar(value=False)
        def check(text, var):
            tk.Checkbutton(choices, text=text, variable=var, bg=UK.C.N0, fg=UK.C.SUB,
                           selectcolor=UK.C.N0, activebackground=UK.C.N0, activeforeground=UK.C.TEXT,
                           font=UK.F_SMALL).pack(side='left', padx=(0, 12))
        check('包含姓名', name)
        check('正文时间', stamp)
        check('SRT 阅读时间轴（每条 3 秒）', synthetic)
        note = label(b, '', small=True)
        preview = tk.Text(b, height=3, wrap='word', bg=UK.C.N100, fg=UK.C.TEXT,
                          font=UK.F_UI, padx=16, pady=12, relief='flat', bd=0)
        preview.pack(fill='both', expand=True, pady=(4,0))
        UK.attach_text_menu(preview)      # 预览稿也允许右键复制
        app._sheet_footer.pack(side='bottom',fill='x',padx=36,pady=(6,14))
        result = label(app._sheet_footer, '', small=True)
        actions = tk.Frame(app._sheet_footer, bg=UK.C.N50)
        actions.pack(fill='x')
        open_row = tk.Frame(actions, bg=UK.C.N50)
        open_row.pack(side='left')
        state = {'signature': None, 'busy':False}
        cancel = threading.Event()

        def selected():
            return current if live and scope.get() == '当前筛选结果' else frozen

        def options():
            return ME.ExportOptions({'Markdown':'md', 'TXT 纯文本':'txt', 'SRT 双语字幕':'srt'}[fmt.get()],
                {'原文 + 译文':'both', '仅原文':'source', '仅译文':'translation'}[content.get()],
                name.get(), stamp.get(), synthetic.get())

        def update():
            if not b.winfo_exists():
                return
            recs, opt = selected(), options()
            sig = (scope.get(), opt, ME.missing_translation(recs))
            if sig != state['signature']:
                state['signature'] = sig
                missing = ME.missing_translation(recs)
                note.configure(text=f'{len(recs)} 条 · 未完成译文 {missing} 条' + (' · 采集相对时间' if opt.format == 'srt' and not opt.synthetic_timing else ''))
                preview.configure(state='normal')
                preview.delete('1.0', 'end')
                try:
                    preview.insert('end', ME.format_meeting(title+'（前 3 条预览）', recs[:3], opt)
                                   + ('\n…（预览前 3 条，导出含全部）' if len(recs)>3 else ''))
                except ValueError as exc:
                    preview.insert('end', str(exc))
                preview.configure(state='disabled')
            app.root.after(250, update)

        def save(recs, opt):
            if not recs:
                result.configure(text='当前筛选没有记录，请改选整场会议。', fg=UK.C.RED)
                state['busy'] = False
                return
            try:
                ME.format_meeting(title, recs, opt)
            except ValueError as exc:
                result.configure(text=str(exc), fg=UK.C.RED)
                state['busy'] = False
                return
            path = filedialog.asksaveasfilename(parent=app.root, title='导出会议快照',
                defaultextension='.'+opt.format, initialfile='会议记录_'+time.strftime('%Y%m%d_%H%M%S')+'.'+opt.format,
                filetypes=[(opt.format.upper(), '*.'+opt.format)])
            if not path:
                state['busy'] = False
                return
            # File extension is an explicit contract, including when the user types a different suffix.
            path = str(Path(path).with_suffix('.'+opt.format))
            if Path(path).exists() and not messagebox.askyesno('覆盖文件', '目标文件已存在，确认覆盖？', parent=app.root):
                state['busy'] = False
                return
            result.configure(text='正在写入…', fg=UK.C.SUB)
            def done(output):
                state['busy'] = False
                if not result.winfo_exists():
                    app.flash('导出完成：'+str(output))
                    return
                result.configure(text=f'已导出 {len(recs)} 条 · {output.name[:45]}', fg=UK.C.GREEN)
                for w in open_row.winfo_children():
                    w.destroy()
                UK.Pill(open_row, '打开文件', lambda: os.startfile(str(output)), kind='secondary').pack(side='left')
                UK.Pill(open_row, '打开文件夹', lambda: os.startfile(str(output.parent)), kind='ghost').pack(side='left', padx=12)
            def failed(exc):
                state['busy'] = False
                if result.winfo_exists():
                    result.configure(text='导出失败：'+str(exc), fg=UK.C.RED)
            app.background(lambda: ME.write_export(path, title, recs, opt), done, failed)

        def export_now(translate=False):
            if state['busy']:
                return
            recs, opt = ME.snapshot(selected()), options()
            if not translate or opt.content == 'source' or not ME.missing_translation(recs):
                state['busy'] = True
                save(recs, opt)
                return
            if not app._has_credentials():
                result.configure(text='请先在设置中配置翻译服务。', fg=UK.C.RED)
                return
            if not messagebox.askyesno('补译后导出', f'将为这份快照补译 {ME.missing_translation(recs)} 条。\n使用当前服务与主题，可能产生 API 费用，是否继续？', parent=app.root):
                return
            state['busy'] = True
            cancel_button.pack(side='left',before=open_row,padx=(0,10))
            cancel.clear()
            app._export_jobs.append(cancel)
            cfg = copy.deepcopy(app.tcfg)
            result.configure(text='补译中，可取消；实时采集继续。', fg=UK.C.SUB)
            def translate_snapshot():
                from translator import TranslationService
                replies = queue.Queue()
                svc = TranslationService(cfg, lambda i, text, meta: replies.put((i,text,meta)))
                try:
                    svc.start()
                    todo = [i for i,r in enumerate(recs) if ME.missing_translation([r])]
                    for i in todo:
                        if cancel.is_set():
                            raise RuntimeError('已取消补译；可直接导出已有内容。')
                        svc.submit(i, recs[i]['text'], context=[x['text'] for x in recs[max(0,i-cfg.context_n):i]])
                    remaining, last_reply = set(todo), time.monotonic()
                    while remaining:
                        if cancel.is_set():
                            raise RuntimeError('已取消补译；可直接导出已有内容。')
                        try:
                            i,text,meta = replies.get(timeout=.25)
                        except queue.Empty:
                            if time.monotonic()-last_reply > 180:
                                raise RuntimeError('补译等待超时；可导出已有内容。')
                            continue
                        if i not in remaining:
                            continue
                        remaining.remove(i)
                        last_reply = time.monotonic()
                        recs[i].update(tr=text, tr_status=meta.get('status','fail'))
                    return recs
                finally:
                    svc.stop()
                    for worker in svc.threads:
                        worker.join(2)
            def ready(translated):
                if cancel_button.winfo_exists():
                    cancel_button.pack_forget()
                if cancel in app._export_jobs:
                    app._export_jobs.remove(cancel)
                if not b.winfo_exists():
                    state['busy'] = False
                    return
                failures = ME.missing_translation(translated)
                if failures and not messagebox.askyesno('部分补译未完成', f'仍有 {failures} 条未完成，是否导出当前结果？', parent=app.root):
                    state['busy'] = False
                    return
                save(translated, opt)
            def failed(exc):
                state['busy'] = False
                if cancel_button.winfo_exists():
                    cancel_button.pack_forget()
                if cancel in app._export_jobs:
                    app._export_jobs.remove(cancel)
                if b.winfo_exists():
                    result.configure(text=str(exc), fg=UK.C.RED)
            app.background(translate_snapshot, ready, failed)
        UK.Pill(actions, '导出已有内容', export_now, kind='primary', height=36).pack(side='right')
        UK.Pill(actions, '补译后导出', lambda: export_now(True), kind='tint', height=36).pack(side='right', padx=12)
        cancel_button = UK.Pill(actions, '取消补译', cancel.set, kind='ghost')
        b.bind('<Destroy>', lambda e: cancel.set() if e.widget is b else None, add='+')
        update()
    app._show_sheet('导出会议', build)


def open_history(app):
    from teamsub import CAPTION_DIR
    def build(parent):
        b = card(parent)

        status = label(b, '正在读取本地记录…', small=True)
        search = UK.SearchBox(b, width=400, placeholder='搜索标题、日期或文件名', bg=UK.C.N0)
        search.pack(anchor='w', pady=8)
        frame = tk.Frame(b, bg=UK.C.N0)
        frame.pack(fill='both',expand=True)
        listing = tk.Listbox(frame, height=3, selectmode='extended', exportselection=False,
                             bg=UK.C.N100, fg=UK.C.TEXT, font=UK.F_UI, bd=0, highlightthickness=0,
                             selectbackground=UK.C.A100, selectforeground=UK.C.TEXT, activestyle='none')
        listing.pack(side='left', fill='both', expand=True)
        sb = tk.Scrollbar(frame, command=listing.yview)
        sb.pack(side='right', fill='y')
        listing.configure(yscrollcommand=sb.set)
        state = {'all':[], 'visible':[]}
        def filter_list():
            query = search.value().casefold()
            state['visible'] = [s for s in state['all'] if query in (s.title+s.started+s.path.name).casefold()]
            listing.delete(0,'end')
            for s in state['visible']:
                listing.insert('end', f'{s.started}  ·  {s.title}  ·  {s.count} 条  ·  {s.path.stem[-12:]}'+('  [旧版]' if s.legacy else ''))
        search.on_change = filter_list
        def loaded(value):
            if not b.winfo_exists():
                return
            sessions, errors = value
            state['all'] = sessions
            status.configure(text=f'{len(sessions)} 场 · Ctrl 多选'+(f' {len(errors)} 个文件无法读取：'+ '; '.join(errors)[:240] if errors else ''))
            filter_list()
        def refresh():
            store = app.save_store
            def load():
                if store and hasattr(store, 'flush'):
                    store.flush()
                return MR.list_sessions(CAPTION_DIR)
            app.background(load, loaded)
        def selected():
            return [state['visible'][i] for i in listing.curselection()]
        def read(action):
            sessions = selected()
            if not sessions:
                status.configure(text='请先选择会议记录。')
                return
            if action in ('view','resume') and len(sessions)!=1:
                status.configure(text='查看或续接时请选择一场会议；合并导出支持多选。')
                return
            paths = [s.path for s in sessions]
            if action == 'resume':
                app.resume_meeting(sessions[0])
            else:
                def done(records):
                    title = sessions[0].title if len(sessions)==1 else '合并会议（所选列表顺序）'
                    if action == 'export':
                        open_export(app, records, title)
                    else:
                        view_history(app, sessions[0], records)
                app.background(lambda: MR.merge_sessions(paths), done)
        def do_delete():
            """删除所选会议：移入回收站可还原；正在记录的这场会被拒绝。"""
            sessions = selected()
            if not sessions:
                status.configure(text='请先选择要删除的会议记录。')
                return
            names = '、'.join(s.title for s in sessions[:3])
            if len(sessions) > 3:
                names += f' 等 {len(sessions)} 场'
            # 用系统确认框而不是 _confirm()：后者会推一层界面，删完还要弹回来，
            # 而且它的回调是异步的，容易在列表已重建的情况下操作旧控件。
            if not messagebox.askyesno(
                    '删除会议记录',
                    f'删除「{names}」？\n\n'
                    '记录会移入系统回收站，可以在资源管理器里还原。',
                    parent=app.root, default='no'):
                return
            paths = [s.path for s in sessions]
            active = app.save_path

            def work():
                return MR.delete_sessions(paths, active_path=active)

            def done(value):
                if not b.winfo_exists():
                    return
                removed, refused = value
                if refused:
                    status.configure(text='部分未删除：' + '；'.join(refused)[:300])
                elif removed:
                    status.configure(text=f'已移入回收站 {len(removed)} 个文件')
                refresh()          # 无论成败都重读列表，界面反映真实磁盘状态

            status.configure(text='正在删除…')
            app.background(work, done)

        app._sheet_footer.pack(side='bottom',fill='x',padx=24,pady=(8,12))
        row = tk.Frame(app._sheet_footer,bg=UK.C.N50)
        row.pack(fill='x')
        for text, action, kind in [('打开记录','view','tint'), ('导出 / 合并所选','export','primary'), ('续接所选会议','resume','ghost')]:
            UK.Pill(row,text,lambda a=action: read(a),kind=kind).pack(side='left',padx=(0,12))
        UK.Pill(row,'删除',do_delete,kind='ghost').pack(side='left',padx=(0,12))
        UK.Pill(row,'刷新',refresh,kind='ghost').pack(side='right')
        listing.bind('<Double-Button-1>',lambda e: read('view'))
        refresh()
    app._show_sheet('历史会议', build)


def record_at(txt, x, y):
    """点在哪条记录的「编辑这条」上？没有就返回 None。

    用「坐标 → 字符索引 → 标签名」反查，而不是给每个标签单独 tag_bind：
    正文是只读的 Text，逐条 bind 在重排后容易失效，反查则永远跟着当前内容走
    （实时界面的右键「修正译文」也是同一套路）。
    """
    try:
        index = txt.index(f"@{x},{y}")
    except tk.TclError:
        return None
    for tag in txt.tag_names(index):
        if tag.startswith('ed') and tag[2:].isdigit():
            return int(tag[2:])
    return None


def view_history(app, session, records):
    """历史详情：正文画法与实时会议完全一致，默认只读，「编辑」才可改。

    旧实现把整场记录塞进一段 ``state='disabled'`` 的 Text，内容来自纯文本
    导出：没有序号圆点、没有译文缩进、说话人不带颜色，和实时界面像两个
    产品。现在统一走 transcript_view，同一个 render_block 画两边。
    """
    import transcript_view as TV

    page_size = 200
    # 状态放在 build 外面：推层/弹层都会重跑 build，滚动位置、页码、编辑开关
    # 都得从这里恢复，不能活在 build 的局部变量里
    state = {'records': records, 'page': 0, 'query': '', 'editing': False,
             'display': getattr(app, 'display_mode', 'both'), 'dirty': 0}

    def shown():
        return ME.filtered(state['records'], None, state['query'])

    def build(parent):
        b = card(parent)

        # ---------------- 标题行 ----------------
        head = tk.Frame(b, bg=UK.C.N0)
        head.pack(fill='x')
        title_box = tk.Frame(head, bg=UK.C.N0)
        title_box.pack(side='left', fill='x', expand=True)
        tk.Label(title_box, text=session.title, bg=UK.C.N0, fg=UK.C.TEXT,
                 font=UK.F_HEAD, anchor='w', justify='left',
                 wraplength=520).pack(anchor='w')
        meta = tk.Label(title_box, text='', bg=UK.C.N0, fg=UK.C.T3,
                        font=UK.F_MINI, anchor='w')
        meta.pack(anchor='w', pady=(4, 0))

        actions = tk.Frame(head, bg=UK.C.N0)
        actions.pack(side='right')

        # ---------------- 检索 + 显示模式 ----------------
        bar = tk.Frame(b, bg=UK.C.N0)
        bar.pack(fill='x', pady=(12, 8))
        search = UK.SearchBox(bar, width=300, placeholder='搜索这场会议',
                              bg=UK.C.N0)
        search.pack(side='left')
        seg = UK.Segmented(bar, ['双语', '原文', '译文'],
                           command=lambda label: pick_display(label), bg=UK.C.N0)
        seg.pack(side='left', padx=12)
        seg.set({'both': 0, 'source': 1, 'translation': 2}.get(state['display'], 0))
        readout = tk.Label(bar, text='', bg=UK.C.N0, fg=UK.C.T3, font=UK.F_MINI)
        readout.pack(side='right')

        # ---------------- 正文 ----------------
        # 外框用卡片，正文 Text 在卡片里，和实时会议的层次一致
        holder = UK.Card(b, radius=UK.R_CARD, padding=14, bg=UK.C.N0)
        holder.pack(fill='both', expand=True)
        txt = tk.Text(holder.body, wrap='word', bg=UK.C.N0, fg=UK.C.TEXT,
                      font=UK.F_BODY, bd=0, highlightthickness=0,
                      padx=6, pady=0, spacing1=2, spacing3=6,
                      selectbackground=UK.C.A100, selectforeground=UK.C.TEXT,
                      insertbackground=UK.C.TEXT, undo=False)
        scroll = UK.ThinScroll(holder.body, txt, bg=UK.C.N0)
        scroll.pack(side='right', fill='y', padx=(6, 0))
        txt.pack(side='left', fill='both', expand=True)
        TV.configure_tags(txt, reading_size=app.reading_size,
                          density=getattr(app, 'density', 'comfort'))

        def readonly(on):
            """只读态：不能输入、光标不用 I 形，但选中复制仍然可用。"""
            txt.configure(state='disabled' if on else 'normal',
                          cursor='arrow' if on else 'xterm',
                          takefocus=0 if on else 1)

        def paint(reset_scroll=False):
            chosen = shown()
            state['chosen'] = chosen          # 编辑回调按「过滤后的序号」定位
            total = max(1, (len(chosen) + page_size - 1) // page_size)
            state['page'] = max(0, min(state['page'], total - 1))
            start = state['page'] * page_size
            batch = chosen[start:start + page_size]
            keep = 0.0 if reset_scroll else state.get('scroll', 0.0)
            txt.configure(state='normal')
            txt.delete('1.0', 'end')
            for i, rec in enumerate(batch):
                end = TV.render_block(txt, i, rec, app, state['display'],
                                      retry=False)
                if state['editing'] and MR.editable(session.path):
                    tag = f"ed{start + i}"
                    txt.insert(end, "  ✎ 编辑这条\n", ("editmark", tag))
                    txt.tag_bind(tag, "<Enter>",
                                 lambda e: txt.configure(cursor="hand2"))
                    txt.tag_bind(tag, "<Leave>",
                                 lambda e: txt.configure(cursor="arrow"))
            # 正文始终只读：编辑是「选中某一条进编辑页」，而不是让整篇变成
            # 一个大输入框——那样一按错键就改到别人的发言，还没法撤销
            readonly(True)
            if keep:
                # 从编辑页回来时停在原位：长会议里每次保存都跳回顶部很难用
                txt.yview_moveto(keep)
                state['scroll'] = keep
            readout.configure(
                text=f'{len(chosen)} 条 · 第 {state["page"] + 1}/{total} 页'
                     + (f' · 已改 {state["dirty"]} 条' if state['dirty'] else ''))
            meta.configure(text=f'{len(state["records"])} 条 · {session.path.name}'
                               + (' · 旧版 Markdown（只读）' if session.legacy else ''))

        def pick_display(label):
            state['display'] = {'双语': 'both', '原文': 'source',
                                '译文': 'translation'}[label]
            paint(reset_scroll=True)

        def find():
            state['query'] = search.value()
            state['page'] = 0
            paint(reset_scroll=True)

        search.on_change = find

        def turn(delta):
            state['page'] += delta
            paint(reset_scroll=True)

        def toggle_edit():
            if session.legacy or not MR.editable(session.path):
                app._notice('这份记录是旧版 Markdown 文件，没有数据库可比对，暂时只读。\n'
                            '可以在实时会议里用「续接该会议」另存为新版后再编辑。')
                return
            state['editing'] = not state['editing']
            rebuild_actions()
            paint()

        def rebuild_actions():
            for w in actions.winfo_children():
                w.destroy()
            UK.Pill(actions, '导出整场记录',
                    lambda: open_export(app, state['records'], session.title),
                    kind='primary').pack(side='right')
            UK.Pill(actions, '完成' if state['editing'] else '编辑',
                    toggle_edit,
                    kind='tint' if state['editing'] else 'ghost').pack(
                        side='right', padx=(0, 10))

        def open_editor(index):
            chosen = state.get('chosen') or state['records']
            if not (0 <= index < len(chosen)):
                return
            try:
                state['scroll'] = txt.yview()[0]     # 回来时停在原地
            except tk.TclError:
                pass
            edit_record(app, session, chosen[index], state)

        def on_text_click(event):
            """只读正文里唯一可点的东西就是「编辑这条」。"""
            index = record_at(txt, event.x, event.y)
            if index is None:
                return None
            open_editor(index)
            return 'break'

        # 挂在控件上而不是逐标签 bind：命中逻辑与鼠标共用同一条路径，
        # 也方便回归测试直接调用（event_generate 造不出 Text 的标签点击）
        txt.marker_click = on_text_click
        txt.bind('<Button-1>', on_text_click, add='+')
        # 只读正文也要能右键复制：Ctrl+C 一直可用，但右键是用户的第一反应，
        # 右键没菜单等于「不能复制」。
        UK.attach_text_menu(txt)

        # ---------------- 底部 ----------------
        app._sheet_footer.pack(side='bottom', fill='x', padx=24, pady=(8, 12))
        row = tk.Frame(app._sheet_footer, bg=UK.C.N50)
        row.pack(fill='x')
        UK.Pill(row, '上一页', lambda: turn(-1), kind='ghost').pack(side='left')
        UK.Pill(row, '下一页', lambda: turn(1), kind='ghost').pack(side='left', padx=12)
        # 真正的「返回上一层」，不是再开一个历史列表（旧实现会叠层，
        # 还把主窗口一起带走了）
        UK.Pill(row, '返回历史列表', app._close_sheet,
                kind='tint').pack(side='right')

        rebuild_actions()
        readonly(True)
        paint()

    app._show_sheet('会议记录', build, push=True)


def edit_record(app, session, rec, state):
    """编辑单条记录：改完写回数据库并重投影 Markdown。"""
    def build(parent):
        box = UK.Card(parent, padding=20, autosize=False)
        box.pack(fill='both', expand=True)
        b = box.body
        b.columnconfigure(0, weight=1, uniform='edit')
        b.columnconfigure(1, weight=1, uniform='edit')
        b.rowconfigure(1, weight=1)

        def caption(text, row, column, **kw):
            tk.Label(b, text=text, bg=UK.C.N0, fg=UK.C.SUB,
                     font=UK.F_SMALL).grid(row=row, column=column, sticky='w',
                                           **kw)

        caption('发言人', 0, 0, pady=(0, 8))
        caption('原文', 1, 0, pady=(0, 10))
        caption('译文', 1, 1, padx=(16, 0), pady=(0, 10))

        name = UK.Field(b, value=rec.get('name', ''), bg=UK.C.N0)
        name.grid(row=0, column=0, sticky='we', padx=(0, 16))

        source = tk.Text(b, height=3, wrap='word', font=UK.F_UI, bg=UK.C.N100,
                         fg=UK.C.TEXT, bd=0, padx=12, pady=10,
                         insertbackground=UK.C.TEXT)
        source.grid(row=2, column=0, sticky='nsew', padx=(0, 16))
        source.insert('1.0', rec.get('text', ''))

        trans = tk.Text(b, height=3, wrap='word', font=UK.F_UI, bg=UK.C.N100,
                        fg=UK.C.TEXT, bd=0, padx=12, pady=10,
                        insertbackground=UK.C.TEXT)
        trans.grid(row=2, column=1, sticky='nsew')
        trans.insert('1.0', rec.get('tr', '') or '')

        status = tk.Label(b, text='', bg=UK.C.N0, fg=UK.C.T3, font=UK.F_MINI,
                          justify='left', anchor='w', wraplength=620)
        status.grid(row=3, column=0, columnspan=2, sticky='we', pady=(10, 0))

        app._sheet_footer.pack(side='bottom', fill='x', padx=24, pady=(8, 12))
        row = tk.Frame(app._sheet_footer, bg=UK.C.N50)
        row.pack(fill='x')
        busy = {'on': False}

        def save():
            if busy['on']:
                return
            text = source.get('1.0', 'end').strip()
            if not text:
                status.configure(text='原文不能为空。', fg=UK.C.RED)
                return
            tr = trans.get('1.0', 'end').strip()
            fields = {'text': text, 'tr': tr,
                      'tr_status': 'ok' if tr else 'none',
                      'name': name.get().strip() or rec.get('name', ''),
                      'raw': name.get().strip() or rec.get('raw', '')}
            busy['on'] = True
            status.configure(text='正在写回记录…', fg=UK.C.SUB)

            def done(written):
                # 先改内存里的记录再关层：关层会按父层 builder 重画整个详情页，
                # 它读的就是这份记录，顺序反了会先画出旧文本再被覆盖
                rec.update(fields)
                state['dirty'] = state.get('dirty', 0) + 1
                app.flash(f'已保存 {written} 条修改')
                app._close_sheet()

            def failed(exc):
                busy['on'] = False
                if status.winfo_exists():
                    status.configure(text='保存失败：' + str(exc), fg=UK.C.RED)

            app.background(lambda: MR.save_edits(session.path, {rec['id']: fields}),
                           done, failed)

        UK.Pill(row, '保存', save, kind='primary').pack(side='right')
        UK.Pill(row, '取消', app._close_sheet, kind='ghost').pack(side='right', padx=(0, 8))

    app._show_sheet('编辑记录', build, push=True)


def open_reading(app):
    def build(parent):
        b=card(parent)
        label(b,'字号',small=True)
        size=UK.Select(b,['11','12','14','16','18'],value=str(app.reading_size),width=160,bg=UK.C.N0)
        size.pack(anchor='w',pady=12)
        label(b,'间距',small=True)
        density=UK.Select(b,['舒适间距','紧凑间距'],value='舒适间距' if app.density=='comfort' else '紧凑间距',width=160,bg=UK.C.N0)
        density.pack(anchor='w',pady=12)
        def apply():
            import transcript_view as TV
            app.reading_size=int(size.get());app.density='comfort' if density.get()=='舒适间距' else 'compact'
            TV.configure_tags(app.txt, reading_size=app.reading_size,
                              density=app.density)
            app.tcfg.reading_size=app.reading_size;app.tcfg.reading_density=app.density;app.tcfg.save()
            if app.float_bar is not None:
                app.float_bar.repaint()
            app._close_sheet()
        app._sheet_footer.pack(side='bottom',fill='x',padx=24,pady=(8,12))
        UK.Pill(app._sheet_footer,'应用',apply,kind='primary').pack(side='right')
    app._show_sheet('阅读偏好',build)

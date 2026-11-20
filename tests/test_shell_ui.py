"""Shell layout, icons, empty states and crash-recovery prompt."""
import update_test_support as support
import json
import time
import tkinter as tk
import unittest
import icons
import ui_kit as UK
import teamsub as A


class ShellTestCase(unittest.TestCase):
    def setUp(self):
        A.session_stamp_path().unlink(missing_ok=True)
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *exc: self.errors.append(exc)

    def start(self):
        self.app = A.App(self.root)
        self.root.geometry('1000x640')
        self.pump()

    def tearDown(self):
        app = getattr(self, 'app', None)
        if app and app.save_store:
            app.save_store.close()
        try:
            for timer in self.root.tk.call('after', 'info'):
                self.root.tk.call('after', 'cancel', timer)
            self.root.destroy()
        except tk.TclError:
            pass
        A.session_stamp_path().unlink(missing_ok=True)
        support.T.Config(enabled=False).save()
        self.assertEqual(self.errors, [])

    def pump(self, seconds=.12):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.root.update()
            time.sleep(.01)

    def walk(self, w):
        yield w
        for child in w.winfo_children():
            yield from self.walk(child)

    def sheet_labels(self):
        return [w.cget('text') for w in self.walk(self.app.sheet_layer) if isinstance(w, tk.Label)]

    def sheet_pills(self):
        return [w.text for w in self.walk(self.app.sheet_layer) if isinstance(w, UK.Pill)]


class IconTests(unittest.TestCase):
    def test_every_icon_renders_non_empty(self):
        for name in icons.names():
            img = icons.render(name, 16, '#000000')
            self.assertEqual(img.size, (16, 16))
            self.assertIsNotNone(img.getbbox(), name)

    def test_photo_cache_is_per_root(self):
        first = tk.Tk()
        a = icons.photo('search', 16, '#123456', master=first)
        self.assertIs(a, icons.photo('search', 16, '#123456', master=first))
        first.destroy()
        second = tk.Tk()
        try:
            b = icons.photo('search', 16, '#123456', master=second)
            self.assertIsNot(a, b)
            tk.Label(second, image=b)     # 旧解释器的图片在这里会抛 TclError
        finally:
            second.destroy()


class RowListTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('400x300')
        self.rows = UK.RowList(self.root)
        self.rows.pack(fill='both', expand=True)
        for i in range(5):
            self.rows.insert('end', f'row {i}', title=f'标题 {i}', meta='meta')
        self.events = []
        self.rows.bind('<<ListboxSelect>>', lambda e: self.events.append(self.rows.curselection()))
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def click(self, row, state=0):
        y = row * UK.RowList.ROW_H + 10
        self.rows._click(type('E', (), {'x': 20, 'y': y, 'state': state})())
        self.root.update()

    def test_listbox_compatible_api(self):
        self.assertEqual(self.rows.size(), 5)
        self.assertEqual(self.rows.get(2), 'row 2')
        self.rows.selection_set(1, 'end')
        self.assertEqual(self.rows.curselection(), (1, 2, 3, 4))
        self.rows.selection_clear(0, 'end')
        self.assertEqual(self.rows.curselection(), ())
        self.rows.delete(0, 'end')
        self.assertEqual(self.rows.size(), 0)
        # 程序改选择不发事件（与 Listbox 一致），刷新列表不会误清提示
        self.assertEqual(self.events, [])

    def test_click_ctrl_and_shift_selection(self):
        self.click(1)
        self.click(3, state=0x0004)            # Ctrl 增选
        self.assertEqual(self.rows.curselection(), (1, 3))
        self.click(1, state=0x0004)            # Ctrl 取消
        self.assertEqual(self.rows.curselection(), (3,))
        self.click(0, state=0x0001)            # Shift 连选到锚点（最后点的那行）
        self.assertEqual(self.rows.curselection(), (0, 1))
        self.click(3)
        self.click(1, state=0x0001)
        self.assertEqual(self.rows.curselection(), (1, 2, 3))
        self.assertEqual(len(self.events), 6)

    def test_keyboard_moves_selection(self):
        self.click(0)
        self.rows._step(1, None)
        self.rows._step(1, None)
        self.assertEqual(self.rows.curselection(), (2,))
        self.rows._select_all()
        self.assertEqual(self.rows.curselection(), (0, 1, 2, 3, 4))


class ShellLayoutTests(ShellTestCase):
    def test_nav_pages_cover_live_header_and_have_no_back(self):
        self.start()
        for open_page, title in ((self.app.open_history, '历史会议'),
                                 (self.app.open_settings, '设置'),
                                 (self.app.show_help, '帮助')):
            open_page()
            self.pump()
            # 位置由导航高亮表明，页面不再重复标题
            self.assertNotIn(title, self.sheet_labels())
            self.assertNotIn('返回', self.sheet_pills())
            self.assertEqual(self.app.nav_buttons[title].kind, 'selected')
            # 内推层盖住整个主区，实时页的标题不再露出来
            self.assertEqual(self.app.sheet_layer.winfo_height(),
                             self.app.main_area.winfo_height())
        self.app.show_live()
        self.pump()
        self.assertEqual(self.app.nav_buttons['实时会议'].kind, 'selected')

    def test_pushed_pages_offer_back_and_keep_nav(self):
        self.start()
        self.app._confirm('确认一下', lambda: None, push=False)
        self.pump()
        self.assertIn('返回', self.sheet_pills())
        self.assertEqual(self.app.nav_buttons['实时会议'].kind, 'selected')

    def test_live_shortcuts_ignored_while_sheet_open(self):
        self.start()
        cleared = []
        self.app.clear = lambda: cleared.append(True)
        self.app.open_settings()
        self.pump()
        self.root.event_generate('<Control-l>')
        self.pump()
        self.assertEqual(cleared, [])

    def test_empty_state_reflects_reason(self):
        self.start()
        self.assertEqual(self.app.empty_title.cget('text'), '等待字幕')
        self.assertIn('Teams', self.app.empty_hint.cget('text'))
        self.app.records.append({'name': 'A', 'raw': 'A', 'text': 'hi', 'id': 'x'})
        self.app.show_placeholder()
        self.assertEqual(self.app.empty_title.cget('text'), '已清屏')
        self.app.speaker_filter = 'B'
        self.app.show_placeholder()
        self.assertEqual(self.app.empty_title.cget('text'), '没有匹配的字幕')


class RecoveryPromptTests(ShellTestCase):
    def write_stamp(self, path):
        stamp = A.session_stamp_path()
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(json.dumps({'path': str(path), 'title': '旧会议', 'ts': time.time()},
                                    ensure_ascii=False), encoding='utf-8')

    def test_current_session_is_not_reported_as_crashed(self):
        """启动后很快写下的本次登记，不能被当成上次异常退出。"""
        self.start()
        current = A.session_stamp_path().parent / 'current.md'
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text('x', encoding='utf-8')
        self.app.save_path = current
        self.app._stamp_session()
        self.app._check_recovery()
        self.pump()
        self.assertFalse(self.app._sheet_open)
        self.assertTrue(A.session_stamp_path().exists(), '本次会话的登记必须保留')
        current.unlink()

    def test_previous_unfinished_session_is_reported_once(self):
        previous = A.session_stamp_path().parent / 'previous.md'
        previous.parent.mkdir(parents=True, exist_ok=True)
        previous.write_text('x', encoding='utf-8')
        self.write_stamp(previous)
        self.start()
        self.app._check_recovery()
        self.pump()
        self.assertTrue(self.app._sheet_open)
        self.assertTrue(any('旧会议' in t for t in self.sheet_labels()))
        self.assertFalse(A.session_stamp_path().exists())
        self.app._close_sheet()
        self.app._check_recovery()
        self.pump()
        self.assertFalse(self.app._sheet_open)
        previous.unlink()

    def test_recovery_resumes_previous_session_in_app_instead_of_opening_md(self):
        from datetime import datetime
        from unittest.mock import patch
        import ui_kit as UK
        from caption_store import CaptionStore
        cap_dir = A.session_stamp_path().parent
        store = CaptionStore(cap_dir, '上次未完会议')
        store.upsert({'id': 'rec-1', 'name': 'Jeremy Wang', 'raw': 'Jeremy Wang',
                      'text': 'Welcome back.', 'ts': datetime(2026, 11, 20, 10, 0, 0),
                      'backlog': False}, 'Jeremy Wang', '10:00:00')
        prev_md = store.path
        store.close()
        self.write_stamp(prev_md)
        self.start()
        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)
        with patch.object(A.os, 'startfile') as startfile:
            self.app._check_recovery()
            self.pump()
            btn = next(w for w in walk(self.app.sheet_layer)
                       if isinstance(w, UK.Pill) and w.text == '续接上次会议')
            btn.command()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not self.app.records:
                self.pump(0.05)
            startfile.assert_not_called()
            self.assertEqual(len(self.app.records), 1)
            self.assertEqual(self.app.records[0]['text'], 'Welcome back.')
            self.assertEqual(len(self.app.reader.open_prefix), 1)
            self.assertIn('续接：上次未完会议', self.app._meeting_txt)


class RestoreVeilTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('300x200')
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def test_hidden_until_reveal_then_opaque(self):
        veil = A.RestoreVeil(self.root)
        self.assertIsNotNone(veil.hwnd)
        alphas = []
        veil._alpha = alphas.append
        veil.reveal_soon()                      # 没隐藏过：不排队
        self.assertIsNone(veil._pending)
        veil.hide()
        self.assertTrue(veil.veiled)
        veil.reveal_soon()
        veil.reveal_soon()                      # 重复的 <Map> 只排一次
        self.assertIsNotNone(veil._pending)
        veil.hide()                             # 还没露脸又被最小化：撤掉旧的定时
        self.assertIsNone(veil._pending)
        veil.reveal_soon()
        deadline = time.monotonic() + 2
        while veil.veiled and time.monotonic() < deadline:
            self.root.update()
        self.assertFalse(veil.veiled)
        self.assertEqual(alphas, [0, 0, 255])


if __name__ == '__main__':
    unittest.main()


class LiveRewriteTests(ShellTestCase):
    def test_rewrite_keeps_old_translation_until_new_one(self):
        """字幕改写时旧译文留到新译文到达，块高度不来回跳。"""
        from datetime import datetime
        self.start()
        app = self.app
        app.trans_on = True
        app.pump_translation = lambda: None
        rec = {'name': 'Alice', 'raw': 'Alice', 'text': 'What kind of nut',
               'ts': datetime.now(), 'backlog': False, 'id': 'r1', 'revision': 0,
               'tr': '什么螺母', 'tr_status': 'ok'}
        app.q.put(('new', rec))
        app.pump()
        app.q.put(('update', {'id': 'r1', 'text': 'What kind of nut are you using'}))
        app.pump()
        live = app.records[-1]
        self.assertNotIn('tr', live)
        self.assertEqual(live['_tr_stale'], '什么螺母')
        self.assertIn('什么螺母', app.txt.get('1.0', 'end'))
        token = 'tok'
        app.tr_requests[token] = (live, live['text'])
        live['_tr_request'] = token
        app.q.put(('tr', (token, '你们用的是什么螺母', {'status': 'ok'})))
        app.pump()
        self.assertNotIn('_tr_stale', live)
        body = app.txt.get('1.0', 'end')
        self.assertIn('你们用的是什么螺母', body)
        self.assertNotIn('→  什么螺母\n', body)


class WatchdogAndSettingsGuardTests(ShellTestCase):
    def test_watchdog_recovers_withdrawn_window_once_and_does_not_loop(self):
        """看门狗在零窗口时唤出主窗口后，主窗口恢复可见且不再重复触发看门狗。"""
        self.start()
        app = self.app
        app.root.withdraw()
        app._tray_hidden = False
        app._was_iconic = True
        if app._veil is not None:
            app._veil.hide()
        app._hidden_since = time.monotonic() - 3.0
        notes = []
        orig_log = A.log_note
        A.log_note = notes.append
        try:
            app._watchdog()
            self.pump(.2)
            self.assertEqual(len(notes), 1)
            self.assertEqual(app._win_state(app.root), 'normal')
            self.assertFalse(app._was_iconic)
            if app._veil is not None:
                self.assertFalse(app._veil.veiled)
            # 再跑一轮看门狗不应再次触发
            app._hidden_since = time.monotonic() - 3.0
            app._watchdog()
            self.assertEqual(len(notes), 1)
        finally:
            A.log_note = orig_log

    def test_settings_reasoning_change_triggers_unsaved_guard(self):
        """设置页修改思考强度应触发未保存草稿保护。"""
        self.start()
        self.app.open_settings()
        self.pump()
        selects = [w for w in self.walk(self.app.sheet_layer)
                   if isinstance(w, UK.Select) and '高' in getattr(w, 'options', ())]
        self.assertEqual(len(selects), 1)
        sel_reason = selects[0]
        asked = []
        orig_ask = A.messagebox.askyesno
        A.messagebox.askyesno = lambda *a, **k: (asked.append(True), False)[1]
        try:
            sel_reason.set('高', notify=True)
            self.assertFalse(self.app._allow_leave_sheet())
            self.assertEqual(len(asked), 1)
        finally:
            A.messagebox.askyesno = orig_ask

    def test_topic_toolbar_export_and_import_ui(self):
        """设置 → 主题页支持将当前主题导出为 JSON 并从 JSON 导入新主题。"""
        import tempfile
        from pathlib import Path
        import settings_view as SV
        self.start()
        self.app.open_settings(page='主题')
        self.pump()
        pills = {w.text: w for w in self.walk(self.app.sheet_layer) if isinstance(w, UK.Pill)}
        self.assertIn('导入', pills)
        self.assertIn('导出', pills)
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / 'topic.json'
            orig_save = SV.filedialog.asksaveasfilename
            orig_open = SV.filedialog.askopenfilename
            SV.filedialog.asksaveasfilename = lambda **k: str(out_path)
            try:
                pills['导出'].command()
                self.assertTrue(out_path.is_file())
                data = json.loads(out_path.read_text(encoding='utf-8'))
                self.assertIn('name', data)
                self.assertIn('brief', data)
                self.assertIsInstance(data['glossary'], list)
                # 修改名称后导入为新主题
                data['name'] = 'AI 生成的测试主题'
                data['glossary'] = ['test term=测试术语']
                out_path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
                SV.filedialog.askopenfilename = lambda **k: str(out_path)
                pills['导入'].command()
                self.pump()
                pick_lists = [w for w in self.walk(self.app.sheet_layer) if isinstance(w, UK.PickList)]
                self.assertEqual(len(pick_lists), 1)
                labels = [item[1] for item in pick_lists[0].items]
                self.assertIn('AI 生成的测试主题', labels)
            finally:
                SV.filedialog.asksaveasfilename = orig_save
                SV.filedialog.askopenfilename = orig_open


class FloatBarLifecycleTests(ShellTestCase):
    def test_minimize_auto_float_and_restore_resets_float_state(self):
        """最小化自动切悬浮窗不污染磁盘配置，还原后立即把开关复位，再点一次即可打开。"""
        self.start()
        app = self.app
        app.tcfg.float_on_min = True
        app.tcfg.float_bar = False
        app.tcfg.save()
        app._on_minimize()
        self.pump()
        self.assertTrue(app._float_visible())
        self.assertFalse(support.T.Config.load().float_bar)
        app._on_restore()
        self.pump()
        self.assertFalse(app._float_visible())
        self.assertFalse(app.float_on)
        app.toggle_float()
        self.pump()
        self.assertTrue(app._float_visible())
        self.assertTrue(app.float_on)

    def test_closing_float_bar_keeps_minimized_or_tray_hidden_and_recovers_orphan(self):
        """点悬浮窗 ✕ 只关悬浮窗：最小化或收进托盘时保持不弹主窗口；仅无入口孤儿态才兜底唤回并解开遮罩。"""
        self.start()
        app = self.app
        # 1) 收进托盘时点 ✕：主窗口继续留在托盘，不弹出来
        app._tray_hidden = True
        app._was_iconic = True
        app.root.withdraw()
        app.set_float(True)
        self.pump()
        app.set_float(False)
        self.pump()
        self.assertEqual(app._win_state(app.root), 'withdrawn')
        self.assertTrue(app._tray_hidden)
        # 2) 无托盘且 withdrawn 的异常隐藏态点 ✕：兜底唤回主窗口并解除透明遮罩
        app._tray_hidden = False
        if app._veil is not None:
            app._veil.hide()
        app.set_float(True)
        self.pump()
        app.set_float(False)
        self.pump(.25)
        self.assertEqual(app._win_state(app.root), 'normal')
        self.assertFalse(app._was_iconic)
        if app._veil is not None:
            self.assertFalse(app._veil.veiled)

    def test_float_bar_repaint_preserves_dragged_position(self):
        """切换主题或重绘悬浮窗时保留拖拽后的屏幕坐标。"""
        self.start()
        app = self.app
        app.set_float(True)
        self.pump()
        app.float_bar.win.geometry('+123+234')
        self.pump()
        app.float_bar.repaint()
        self.pump()
        self.assertEqual(app.float_bar.win.winfo_x(), 123)
        self.assertEqual(app.float_bar.win.winfo_y(), 234)

    def test_translation_pending_stays_steady_on_asr_update_and_backlog_shows_timestamp(self):
        """翻译开启时，ASR 连续改写不会让『翻译中…』反复消失又出现；带采集时间的首帧记录不再显示时间未知。"""
        from datetime import datetime
        import meeting_export as ME
        self.start()
        app = self.app
        app.trans_on = True
        app.pump_translation = lambda: None
        ts = datetime(2026, 11, 19, 14, 50, 2)
        rec = {'name': 'Jeremy Wang', 'raw': 'Jeremy Wang', 'text': 'We need',
               'ts': ts, 'backlog': True, 'id': 'r-live-1', 'revision': 0}
        app.q.put(('new', dict(rec)))
        self.pump()
        self.assertEqual(app.records[0].get('tr_status'), 'pending')
        self.assertIn('翻译中…', app.txt.get('1.0', 'end'))
        self.assertEqual(app.ts_of(app.records[0]), '14:50:02')
        self.assertIn('[14:50:02]', ME.format_meeting('测试', app.records))

        # 模拟 ASR 连续改写同一句：翻译中… 必须稳定保持，不能被 _invalidate_translation 清掉
        updated = dict(rec, text='We need to check the tracker.', revision=1, backlog=False)
        app.q.put(('update', updated))
        self.pump()
        self.assertEqual(app.records[0].get('tr_status'), 'pending')
        self.assertIn('翻译中…', app.txt.get('1.0', 'end'))




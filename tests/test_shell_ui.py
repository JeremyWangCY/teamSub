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


if __name__ == '__main__':
    unittest.main()

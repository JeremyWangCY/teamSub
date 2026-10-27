import update_test_support as support
from datetime import datetime
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from PIL import ImageTk, ImageChops
import frontend_views as V
import meeting_export as E
import meeting_repository as R
import ui_kit as UK
from caption_store import CaptionStore
from test_update_settings import GuiTestCase


class MeetingDetailsTests(GuiTestCase):
    def setUp(self):
        super().setUp()
        self.app.show_live()
        self.temporary=tempfile.TemporaryDirectory()
        store=CaptionStore(self.temporary.name,'原会议')
        store.upsert(dict(id='edit-one',name='Alex',raw='Alex',text='Original',
                          tr='原译文',tr_status='ok',ts=datetime(2026,10,27,9)),
                     'Alex','09:00')
        self.path=store.path.with_suffix('.sqlite3')
        store.close()
        self.session=R.list_sessions(self.temporary.name)[0][0]

    def tearDown(self):
        super().tearDown()
        self.temporary.cleanup()

    def test_rename_preserves_identity_content_and_export(self):
        original=self.path.read_bytes()
        before=R.load_session(self.path)[1]
        R.rename_session(self.path,'  项目复盘  ')
        current=R.list_sessions(self.temporary.name)[0][0]
        self.assertEqual((current.title,current.path,current.started,current.count),
                         ('项目复盘',self.path,self.session.started,1))
        title,records=R.load_session(self.path)
        self.assertEqual(records,before)
        self.assertIn('项目复盘',E.format_meeting(title,records))
        self.assertTrue(self.path.with_suffix('.md').read_text(encoding='utf-8').startswith('# 项目复盘'))
        self.assertNotEqual(original,self.path.read_bytes())
        for invalid in ('  ','a\nb','x'*121):
            with self.assertRaises(ValueError):R.rename_session(self.path,invalid)
        self.assertEqual(R.load_session(self.path)[0],'项目复盘')

    def test_active_writer_keeps_renamed_title(self):
        writer=CaptionStore(self.temporary.name,'原会议',path=self.path)
        try:
            R.rename_session(self.path,'新名称')
            writer.upsert(dict(id='another',name='Bo',text='Later',ts=None),'Bo','09:01')
            self.assertTrue(writer.path.read_text(encoding='utf-8').startswith('# 新名称'))
            self.assertEqual(R.load_session(self.path)[0],'新名称')
        finally:
            writer.close()

    def test_history_list_and_detail_rename(self):
        with patch.object(support.A,'CAPTION_DIR',Path(self.temporary.name)):
            self.app.open_history()
            self.wait_for(lambda:any(isinstance(w,tk.Listbox) and w.size()==1 for w in self.walk(self.app.sheet_layer)))
            listing=next(w for w in self.walk(self.app.sheet_layer) if isinstance(w,tk.Listbox))
            self.button('重命名').command()
            self.assertTrue(any('请选择一场会议' in s for s in self.texts()))
            listing.selection_set(0)
            with patch.object(V.simpledialog,'askstring',return_value='列表新名称'):
                self.button('重命名').command()
                self.wait_for(lambda:'列表新名称' in listing.get(0))
            self.button('打开记录').command()
            self.wait_for(lambda:self.app._sheet_title=='会议记录')
            with patch.object(V.simpledialog,'askstring',return_value='详情新名称'):
                self.button('重命名').command()
                self.wait_for(lambda:'详情新名称' in self.texts())
            self.app._close_sheet()
            self.wait_for(lambda:any(isinstance(w,tk.Listbox) and w.size() and '详情新名称' in w.get(0)
                                     for w in self.walk(self.app.sheet_layer)))

    def test_edit_record_persists_and_reopens(self):
        records=R.load_session(self.path)[1]
        long_session=R.Session(self.path,'项目复盘'*24,self.session.started,1)
        V.view_history(self.app,long_session,records)
        self.pump()
        for text in ('补译','编辑记录','导出整场记录'):
            button=self.button(text)
            self.assertTrue(button.winfo_ismapped())
            self.assertEqual(button.winfo_width(),button.winfo_reqwidth())
            self.assertLessEqual(button.winfo_rootx()+button.winfo_width(),self.root.winfo_rootx()+1000)
        self.button('编辑记录').command()
        detail=next(w for w in self.walk(self.app.sheet_layer) if isinstance(w,tk.Text))
        self.assertIn('编辑这条',detail.get('1.0','end'))
        V.edit_record(self.app,self.session,records[0],{'dirty':0})
        self.pump()
        fields=[w for w in self.walk(self.app.sheet_layer) if isinstance(w,tk.Text)]
        for field,text in zip(fields,('修正原文','修正译文')):
            field.delete('1.0','end');field.insert('1.0',text)
        self.button('保存').command()
        self.wait_for(lambda:self.app._sheet_title=='会议记录')
        saved=R.load_session(self.path)[1][0]
        self.assertEqual((saved['text'],saved['tr']),('修正原文','修正译文'))
        markdown=self.path.with_suffix('.md').read_text(encoding='utf-8')
        self.assertIn('修正原文',markdown);self.assertIn('修正译文',markdown)
        self.assertIn('修正译文',E.format_meeting(self.session.title,[saved]))

    def test_float_dot_is_symmetric_and_has_clearance(self):
        from floatbar import FloatBar
        bar=FloatBar(self.app)
        try:
            bar.sync(None)
            alpha=ImageTk.getimage(bar._dot_image).getchannel('A')
            self.assertIsNone(ImageChops.difference(alpha,alpha.transpose(0)).getbbox())
            self.assertIsNone(ImageChops.difference(alpha,alpha.transpose(1)).getbbox())
            self.assertLess(alpha.getpixel((7,0)),10)
            self.assertGreater(alpha.getpixel((7,7)),250)
            bar.sync(dict(name='Alex',text='Sample',tr='示例',ts=None))
            self.assertEqual(bar._dot_color,self.app.speaker_color('Alex'))
        finally:
            bar.win.destroy()

    def test_active_edit_invalidates_pending_machine_translation(self):
        from caption_store import AsyncCaptionStore
        records=R.load_session(self.path)[1]
        self.app.records=records
        self.app.save_path=self.path.with_suffix('.md')
        self.app.save_store=AsyncCaptionStore(self.temporary.name,'原会议',path=self.path)
        live=records[0]
        live['_tr_request']='old-request'
        self.app.tr_requests['old-request']=(live,live['text'])
        self.app.pending_tr[0]=0
        self.app.full_render()
        V.view_history(self.app,self.session,R.load_session(self.path)[1])
        V.edit_record(self.app,self.session,R.load_session(self.path)[1][0],{'dirty':0})
        self.pump()
        fields=[w for w in self.walk(self.app.sheet_layer) if isinstance(w,tk.Text)]
        fields[1].delete('1.0','end');fields[1].insert('1.0','人工修正优先')
        self.button('保存').command()
        self.wait_for(lambda:self.app._sheet_title=='会议记录')
        self.app.q.put(('tr','old-request','过时机器译文',{'status':'ok'}))
        self.pump()
        self.assertEqual(live['tr'],'人工修正优先')
        self.assertNotIn('old-request',self.app.tr_requests)
        self.assertNotIn(0,self.app.pending_tr)
        self.assertEqual(R.load_session(self.path)[1][0]['tr'],'人工修正优先')


if __name__=='__main__':unittest.main(verbosity=2)

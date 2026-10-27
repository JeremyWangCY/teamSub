import update_test_support as support
from datetime import datetime
from pathlib import Path
import queue
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import app_updates as U
import update_workflow as W
from caption_store import AsyncCaptionStore, CaptionStore
import meeting_repository as R
from test_update_settings import GuiTestCase


class UpdateWorkflowTests(GuiTestCase):
    def release(self):
        return U.Release('9.0.0', U.RELEASES_URL+'/tag/v9.0.0',
            U.InstallerAsset('TeamsSub-Setup-9.0.0.exe',
                U.RELEASES_URL+'/download/v9.0.0/TeamsSub-Setup-9.0.0.exe',100,'a'*64))

    def ready(self):
        state=W.state_for(self.app)
        state.update(phase='ready',release=self.release(),path=Path('unused.exe'))
        return state

    def test_download_progress_survives_page_rebuild_and_cancel_retries(self):
        entered=threading.Event()
        calls=[]
        def download(release,directory,progress,cancel):
            calls.append(directory)
            progress(50,100);entered.set()
            if not cancel.wait(4): raise AssertionError('Download was not cancelled')
            raise U.UpdateCancelled('cancelled')
        with patch.object(U,'download_installer',side_effect=download):
            W.start_download(self.app,self.release())
            self.assertTrue(entered.wait(1))
            self.wait_for(lambda:W.state_for(self.app)['received']==50)
            self.app.show_live();self.app.open_settings(page='更新');self.pump()
            self.assertTrue(any('50%' in s for s in self.texts()))
            self.button('取消下载').command()
            self.wait_for(lambda:W.state_for(self.app)['phase']=='available')
            self.assertIn('取消',W.state_for(self.app)['error'])
            W.start_download(self.app,self.release())
            self.wait_for(lambda:len(calls)==2)
            W.cancel_download(self.app)
            self.wait_for(lambda:W.state_for(self.app)['phase']=='available')
        self.assertNotEqual(calls[0],calls[1])
        self.assertFalse(self.errors)

    def test_success_saves_then_closes_then_starts_installer(self):
        state=self.ready();events=[]
        self.app.show_live()
        directory=Path(support.temporary.name)/('workflow-'+str(time.time_ns()))
        store=AsyncCaptionStore(directory,'更新前会议')
        self.app.save_store=store;self.app.save_path=store.path
        rec=dict(id='saved-before-install',name='Alice',text='保存这句话',
                 tr='Keep this',tr_status='ok',ts=datetime.now())
        self.app.records=[rec];store.upsert(rec,'Alice','12:00')
        def prepare(*args):
            self.assertEqual(R.load_session(store.path.with_suffix('.sqlite3'))[1][0]['text'],'保存这句话')
            events.append('saved')
            def start():
                self.assertFalse(store.thread.is_alive())
                events.append('installer-start')
            return SimpleNamespace(start=start)
        with patch.object(U,'prepare_install',side_effect=prepare):
            W.begin_install(self.app)
            deadline=time.monotonic()+5
            while not self.app._closing and time.monotonic()<deadline:
                self.root.update();time.sleep(.01)
        self.assertTrue(self.app._closing)
        self.assertEqual(events,['saved','installer-start'])
        self.assertEqual(state['phase'],'installing')

    def test_unsaved_edit_guard_prevents_install(self):
        state=self.ready()
        with patch.object(self.app,'_allow_leave_sheet',return_value=False),patch.object(U,'prepare_install') as prepare:
            W.begin_install(self.app);self.pump()
            prepare.assert_not_called()
        self.assertEqual(state['phase'],'ready')
        self.assertFalse(self.app.reader.paused)

    def test_failed_save_keeps_window_and_capture_alive(self):
        state=self.ready();original=self.app.save_store
        self.app.save_store=SimpleNamespace(flush=Mock(side_effect=OSError('disk full')))
        try:
            with patch.object(U,'prepare_install') as prepare:
                W.begin_install(self.app)
                self.wait_for(lambda:state['phase']=='ready' and bool(state['error']))
                prepare.assert_not_called()
            self.assertTrue(self.root.winfo_exists())
            self.assertFalse(self.app.reader.paused)
            self.assertFalse(getattr(self.app,'_quit_pending',False))
        finally:self.app.save_store=original

    def test_real_write_failure_then_recovery_updates_only_after_all_records_saved(self):
        state=self.ready();self.app.show_live()
        directory=Path(support.temporary.name)/('real-recovery-'+str(time.time_ns()))
        blocked=threading.Event();blocked.set()
        write=CaptionStore.upsert_many
        def persist(store,items):
            if blocked.is_set():raise OSError('simulated temporary disk error')
            return write(store,items)
        with patch.object(CaptionStore,'upsert_many',persist):
            store=AsyncCaptionStore(directory,'恢复后更新')
            self.app.save_store=store;self.app.save_path=store.path
            first=dict(id='before-failure',name='Alice',text='不能遗漏的原文',ts=datetime.now())
            self.app.records=[first];store.upsert(first,'Alice','12:00')
            self.wait_for(lambda:store._db_error is not None)
            with patch.object(U,'prepare_install') as prepare:
                W.begin_install(self.app)
                self.wait_for(lambda:state['phase']=='ready' and bool(state['error']))
                prepare.assert_not_called()
            self.assertFalse(self.app._closing)
            self.assertFalse(self.app.reader.paused)
            blocked.clear()
            second=dict(id='after-recovery',name='Bob',text='恢复后的原文',ts=datetime.now())
            self.app.records.append(second);store.upsert(second,'Bob','12:01')
            store.flush()
            self.assertIsNone(store._db_error)
            def prepare(*_):
                records=R.load_session(store.path.with_suffix('.sqlite3'))[1]
                self.assertEqual([r['id'] for r in records],['before-failure','after-recovery'])
                def launch():
                    self.assertFalse(store.thread.is_alive())
                    self.assertIn('不能遗漏的原文',store.path.read_text(encoding='utf-8'))
                return SimpleNamespace(start=launch)
            with patch.object(U,'prepare_install',side_effect=prepare) as prepared:
                W.begin_install(self.app)
                deadline=time.monotonic()+5
                while not self.app._closing and time.monotonic()<deadline:
                    self.root.update();time.sleep(.01)
                self.assertTrue(self.app._closing)
                prepared.assert_called_once()

    def test_cancel_while_preparing_never_starts(self):
        state=self.ready();entered=threading.Event();release=threading.Event();plan=Mock()
        def prepare(*args):
            entered.set();release.wait(3);return plan
        with patch.object(U,'prepare_install',side_effect=prepare):
            W.begin_install(self.app)
            self.wait_for(entered.is_set)
            W.cancel_download(self.app);release.set()
            self.wait_for(lambda:state['phase']=='ready')
        plan.start.assert_not_called()
        self.assertFalse(self.app.reader.paused)
        self.assertTrue(self.root.winfo_exists())

    def test_new_unsaved_edits_during_preflight_are_guarded_again(self):
        state=self.ready();plan=Mock()
        with patch.object(self.app,'_allow_leave_sheet',side_effect=[True,False]),patch.object(U,'prepare_install',return_value=plan):
            W.begin_install(self.app)
            self.wait_for(lambda:state['phase']=='ready')
        plan.start.assert_not_called()
        self.assertFalse(self.app.reader.paused)
        self.assertIsNone(self.app._update_plan)
        self.assertTrue(self.root.winfo_exists())

    def test_start_failure_restores_closed_writer_and_existing_records(self):
        state=self.ready();self.app.show_live()
        directory=Path(support.temporary.name)/('recovery-'+str(time.time_ns()))
        store=AsyncCaptionStore(directory,'继续记录')
        self.app.save_store=store;self.app.save_path=store.path
        rec=dict(id='before',name='Alice',text='before',ts=datetime.now())
        self.app.records=[rec];store.upsert(rec,'Alice','12:00')
        plan=Mock();plan.start.side_effect=OSError('blocked')
        with patch.object(U,'prepare_install',return_value=plan):
            W.begin_install(self.app)
            self.wait_for(lambda:'更新未启动' in state['error'])
            self.wait_for(lambda:self.app.save_store is not store)
        self.assertFalse(self.app._closing)
        self.assertFalse(self.app.reader.paused)
        self.app.save_store.upsert(dict(id='after',name='Bob',text='after',ts=datetime.now()),'Bob','12:01')
        self.app.save_store.flush()
        self.assertEqual([r['text'] for r in R.load_session(store.path.with_suffix('.sqlite3'))[1]],['before','after'])
        self.assertIsNone(self.app._update_plan)

    def test_live_writer_cannot_be_overridden_at_exit_barrier(self):
        state=self.ready();plan=Mock()
        self.app._update_plan=plan
        with patch.object(self.app,'_store_closed',return_value=False),patch.object(self.app,'_cancel_quit') as cancel:
            self.assertFalse(W.launch_pending(self.app))
            cancel.assert_called_once()
        plan.start.assert_not_called()
        self.app._update_plan=None


if __name__=='__main__':unittest.main(verbosity=2)

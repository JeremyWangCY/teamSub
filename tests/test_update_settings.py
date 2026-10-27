import update_test_support as support
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import app_updates as U
import ui_kit as UK


class GuiTestCase(unittest.TestCase):
    def setUp(self):
        self.root=tk.Tk()
        self.errors=[]
        self.root.report_callback_exception=lambda *exc:self.errors.append(exc)
        self.app=support.A.App(self.root)
        self.root.geometry('1000x640')
        self.app.open_settings(page='更新')
        self.pump()

    def tearDown(self):
        if self.app.svc:self.app.svc.stop()
        if self.app.save_store:self.app.save_store.close()
        try:
            for timer in self.root.tk.call('after','info'):
                self.root.tk.call('after','cancel',timer)
            self.root.destroy()
        except tk.TclError:
            pass

    def pump(self,seconds=.12):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            self.root.update();time.sleep(.01)

    def walk(self,w):
        yield w
        for child in w.winfo_children():yield from self.walk(child)

    def button(self,text):
        return next(w for w in self.walk(self.app.sheet_layer) if isinstance(w,UK.Pill) and w.text==text)

    def texts(self):
        return [w.cget('text') for w in self.walk(self.app.sheet_layer) if isinstance(w,tk.Label)]

    def wait_for(self,check):
        end=time.monotonic()+4
        while not check() and time.monotonic()<end:self.pump(.03)
        self.assertTrue(check())


class UpdateSettingsTests(GuiTestCase):
    def test_manual_check_and_download(self):
        self.assertFalse(self.app._sheet_footer.winfo_ismapped())
        release=U.Release('9.0.0',f'{U.RELEASES_URL}/tag/v9.0.0')
        with patch.object(U,'check_latest_release',return_value=release) as fetch:
            self.assertEqual(fetch.call_count,0)
            self.button('检查更新').command()
            self.wait_for(lambda:any('发现新版本 9.0.0' in s for s in self.texts()))
            self.assertEqual(fetch.call_count,1)
        with patch('webbrowser.open',return_value=True) as browser:
            self.button('前往下载').command()
            browser.assert_called_once_with(release.url)
        page=self.button('重新检查').master.master
        self.assertLessEqual(page.winfo_reqheight(),page.winfo_height()+1)
        self.assertFalse(self.errors)
        self.button('服务').command();self.pump()
        self.assertTrue(self.app._sheet_footer.winfo_ismapped())

    def test_update_button_only_on_update_page_and_no_fallback_tab(self):
        self.app.open_settings()
        self.pump()
        self.assertFalse(self.button('检查更新').winfo_ismapped())
        self.assertFalse(any(isinstance(w,UK.Pill) and w.text=='备用'
                             for w in self.walk(self.app.sheet_layer)))
        self.button('更新').command()
        self.pump()
        self.assertTrue(self.button('检查更新').winfo_ismapped())
        with patch.object(U,'check_latest_release',return_value=U.Release(support.A.APP_VERSION,U.RELEASES_URL)):
            self.button('检查更新').command()
            self.wait_for(lambda:any('已是最新正式版本' in s for s in self.texts()))
        self.assertFalse(self.app._sheet_footer.winfo_ismapped())

    def test_latest_and_error_can_retry(self):
        with patch.object(U,'check_latest_release',return_value=U.Release(support.A.APP_VERSION,U.RELEASES_URL)):
            self.button('检查更新').command()
            self.wait_for(lambda:any('已是最新正式版本' in s for s in self.texts()))
        with patch.object(U,'check_latest_release',side_effect=U.UpdateCheckError('无法连接 GitHub，请重试')):
            self.button('重新检查').command()
            self.wait_for(lambda:'无法连接 GitHub，请重试' in self.texts())
        self.assertFalse(self.button('前往下载').winfo_ismapped())
        self.assertTrue(callable(self.button('重新检查').command))

    def test_closed_page_ignores_result(self):
        started=threading.Event();release=threading.Event()
        def check():
            started.set();release.wait(3)
            return U.Release('9.0.0',U.RELEASES_URL)
        with patch.object(U,'check_latest_release',side_effect=check):
            self.button('检查更新').command()
            self.assertTrue(started.wait(1))
            self.app._close_sheet()
            release.set();self.pump(.3)
        self.assertFalse(self.errors)

    def test_exit_with_widget_owned_timer(self):
        widget=tk.Frame(self.root)
        widget.after(60000,lambda:None)
        self.app.on_close()
        try:
            exists=self.root.winfo_exists()
        except tk.TclError:
            exists=False
        self.assertFalse(exists)


if __name__=='__main__':unittest.main(verbosity=2)

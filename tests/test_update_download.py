import hashlib
import http.client
import io
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app_updates as U


PACKAGE = b'MZ' + b'valid installer data' * 15000


def release_for(data=PACKAGE):
    filename = 'TeamSub-Setup-9.1.0.exe'
    asset = U.InstallerAsset(filename, f'{U.RELEASES_URL}/download/v9.1.0/{filename}',
                             len(data), hashlib.sha256(data).hexdigest())
    return U.Release('9.1.0', f'{U.RELEASES_URL}/tag/v9.1.0', asset)


class Response(io.BytesIO):
    def __init__(self, data=PACKAGE, *, url=None, length=None, fail_after=None):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data) if length is None else length)}
        self.url = url or release_for().installer.url
        self.fail_after = fail_after

    def geturl(self):
        return self.url

    def read(self, size=-1):
        if self.fail_after is not None and self.tell() >= self.fail_after:
            raise http.client.IncompleteRead(b'interrupted')
        return super().read(size)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / '中文 更新包'
        self.release = release_for()
        self.disk = patch.object(U.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**10))
        self.disk.start()
        self.addCleanup(self.disk.stop)

    def test_verified_download_unicode_path_and_public_request(self):
        progress = []
        with patch.object(U, '_open_download', return_value=Response()) as fetch:
            path = U.download_installer(self.release, self.directory,
                                        lambda got, total: progress.append((got, total)))
        self.assertEqual(path.read_bytes(), PACKAGE)
        self.assertEqual(progress[0], (0, len(PACKAGE)))
        self.assertEqual(progress[-1], (len(PACKAGE), len(PACKAGE)))
        self.assertGreater(len(progress), 2)
        self.assertEqual(list(self.directory.iterdir()), [path])
        request = fetch.call_args.args[0]
        self.assertEqual(request.full_url, self.release.installer.url)
        self.assertEqual(request.get_method(), 'GET')
        self.assertIsNone(request.data)
        self.assertEqual({key.lower() for key, _ in request.header_items()}, {'accept', 'user-agent'})

    def test_corruption_size_format_and_empty_response_never_publish_package(self):
        bad_same_size = b'MZ' + b'x' * (len(PACKAGE) - 2)
        for response in (Response(bad_same_size), Response(b'xx' + PACKAGE[2:]),
                         Response(PACKAGE[:-1], length=len(PACKAGE)),
                         Response(PACKAGE + b'overflow', length=len(PACKAGE)),
                         Response(b'', length=len(PACKAGE)), Response(length=len(PACKAGE)+1)):
            with self.subTest(size=response.getbuffer().nbytes), patch.object(U, '_open_download', return_value=response):
                with self.assertRaises(U.UpdateCheckError):
                    U.download_installer(self.release, self.directory)
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_network_interruption_cleans_partial_file(self):
        with patch.object(U, '_open_download', return_value=Response(fail_after=128*1024)):
            with self.assertRaisesRegex(U.UpdateCheckError, '下载失败'):
                U.download_installer(self.release, self.directory)
        self.assertEqual(list(self.directory.iterdir()), [])
        with patch.object(U, '_open_download', side_effect=urllib.error.URLError('offline')):
            with self.assertRaises(U.UpdateCheckError):
                U.download_installer(self.release, self.directory)

    def test_cancel_before_or_during_download_cleans_partial_file(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(U, '_open_download') as fetch:
            with self.assertRaises(U.UpdateCancelled):
                U.download_installer(self.release, self.directory, cancel=cancel)
            fetch.assert_not_called()
        cancel.clear()
        with patch.object(U, '_open_download', return_value=Response()):
            with self.assertRaises(U.UpdateCancelled):
                U.download_installer(self.release, self.directory,
                                      lambda got, total: cancel.set() if got else None, cancel)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_download_cannot_escape_trusted_sources_or_downgrade_https(self):
        request = urllib.request.Request(self.release.installer.url)
        handler = U._GithubRedirects()
        for url in ('http://github.com/package.exe', 'https://example.com/package.exe',
                    'https://github.com.evil.test/package.exe',
                    'https://user:pass@github.com/package.exe', 'https://github.com:8443/package.exe'):
            with self.subTest(url=url):
                with self.assertRaises(U.UpdateCheckError):
                    handler.redirect_request(request, None, 302, '', {}, url)
        official = 'https://release-assets.githubusercontent.com/assets/123?token=public-download'
        self.assertEqual(handler.redirect_request(request, None, 302, '', {}, official).full_url, official)
        with patch.object(U, '_open_download', return_value=Response(url='https://evil.test/package.exe')):
            with self.assertRaises(U.UpdateCheckError):
                U.download_installer(self.release, self.directory)
        self.assertFalse((self.directory / self.release.installer.filename).exists())

    def test_unknown_package_and_insufficient_space_do_not_open_network(self):
        bad = U.Release(self.release.version, self.release.url)
        with patch.object(U, '_open_download') as fetch:
            with self.assertRaises(U.UpdateCheckError):
                U.download_installer(bad, self.directory)
            with patch.object(U.shutil, 'disk_usage', return_value=SimpleNamespace(free=1)):
                with self.assertRaisesRegex(U.UpdateCheckError, '空间不足'):
                    U.download_installer(self.release, self.directory)
            fetch.assert_not_called()

    def test_retry_reuses_only_a_verified_complete_package(self):
        self.directory.mkdir()
        existing = self.directory / self.release.installer.filename
        existing.write_bytes(PACKAGE)
        with patch.object(U, '_open_download') as fetch:
            self.assertEqual(U.download_installer(self.release, self.directory), existing)
            fetch.assert_not_called()
        existing.write_bytes(b'corrupt')
        with patch.object(U, '_open_download', return_value=Response()) as fetch:
            self.assertEqual(U.download_installer(self.release, self.directory), existing)
            fetch.assert_called_once()
        self.assertEqual(existing.read_bytes(), PACKAGE)


@unittest.skipUnless(os.name == 'nt', 'Windows installer integration')
class InstallPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.package_dir = base / '中文 下载 缓存'
        self.package_dir.mkdir()
        self.release = release_for()
        self.package = self.package_dir / self.release.installer.filename
        self.package.write_bytes(PACKAGE)
        self.app = base / '中文 已安装 程序' / 'TeamSub.exe'
        self.app.parent.mkdir()
        self.original = b'MZold app preserving data elsewhere'
        self.app.write_bytes(self.original)
        self.disk = patch.object(U.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**10))
        self.disk.start()
        self.addCleanup(self.disk.stop)

    def prepare(self):
        return U.prepare_install(self.package, self.release, current_exe=self.app)

    def test_prepare_does_not_launch_and_start_preserves_arguments_and_environment(self):
        with patch.object(U.subprocess, 'Popen') as launch, patch.object(U, '_same_image_parent', return_value=765):
            plan = self.prepare()
            launch.assert_not_called()
            self.assertEqual(plan.backup_path.name, 'TeamSub.previous.exe')
            self.assertEqual(plan.backup_path.read_bytes(), self.original)
            self.assertEqual(plan.backup_sha256, hashlib.sha256(self.original).hexdigest())
            self.assertNotEqual(plan.backup_path.parent, self.app.parent)
            plan.start()
        command = launch.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], str(self.package.resolve()))
        for flag in ('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                     f'/DIR={self.app.parent.resolve()}', '/TeamSubUpdate=1',
                     f'/TeamSubWaitPid={os.getpid()}', '/TeamSubWaitParentPid=765',
                     f'/TeamSubBackup={plan.backup_path}', f'/TeamSubBackupSHA256={plan.backup_sha256}'):
            self.assertIn(flag, command)
        self.assertFalse(launch.call_args.kwargs['shell'])
        self.assertEqual(launch.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertEqual(launch.call_args.kwargs['cwd'], str(self.package_dir.resolve()))

    def test_changed_installer_backup_or_app_cancels_before_launch(self):
        for target in ('installer', 'backup', 'app'):
            self.package.write_bytes(PACKAGE)
            self.app.write_bytes(self.original)
            plan = self.prepare()
            path = {'installer': self.package, 'backup': plan.backup_path, 'app': self.app}[target]
            path.write_bytes(b'changed')
            with self.subTest(target=target), patch.object(U.subprocess, 'Popen') as launch:
                with self.assertRaises(U.UpdateCheckError):
                    plan.start()
                launch.assert_not_called()

    def test_write_permission_and_copy_errors_preserve_app_and_remove_partial_backup(self):
        for operation in ('permission', 'copy'):
            mocked = (patch.object(U.tempfile, 'TemporaryFile', side_effect=PermissionError())
                      if operation == 'permission' else patch.object(U.shutil, 'copyfileobj', side_effect=OSError()))
            with self.subTest(operation=operation), mocked, patch.object(U.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(U.UpdateCheckError, '不可写'):
                    self.prepare()
                launch.assert_not_called()
            self.assertEqual(self.app.read_bytes(), self.original)
            self.assertEqual(list(self.package_dir.iterdir()), [self.package])

    def test_source_mode_and_noncanonical_exe_use_manual_install(self):
        with patch.object(U.sys, 'frozen', False, create=True):
            with self.assertRaisesRegex(U.UpdateCheckError, '手动安装'):
                U.prepare_install(self.package, self.release)
        renamed = self.app.with_name('renamed.exe')
        renamed.write_bytes(self.original)
        with self.assertRaises(U.UpdateCheckError):
            U.prepare_install(self.package, self.release, current_exe=renamed)
        self.assertEqual(U._same_image_parent(self.app.resolve()), 0)

    def test_no_space_and_failed_launcher_leave_old_app_untouched(self):
        with patch.object(U.shutil, 'disk_usage', return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(U.UpdateCheckError, '空间不足'):
                self.prepare()
        plan = self.prepare()
        with patch.object(U.subprocess, 'Popen', side_effect=OSError()):
            with self.assertRaisesRegex(U.UpdateCheckError, '无法启动'):
                plan.start()
        self.assertEqual(self.app.read_bytes(), self.original)
        self.assertEqual(plan.backup_path.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main(verbosity=2)

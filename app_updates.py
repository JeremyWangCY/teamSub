"""Verified public updates. No translation configuration or credentials are read."""
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = "JeremyWangCY/teamsSub"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
MIN_FREE_BYTES = 128 * 1024 * 1024
DOWNLOAD_HOSTS = frozenset(("github.com", "release-assets.githubusercontent.com",
                            "objects.githubusercontent.com", "github-releases.githubusercontent.com"))


class UpdateCheckError(RuntimeError):
    pass


class UpdateCancelled(UpdateCheckError):
    pass


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value or "")
    if not match:
        raise ValueError("版本号格式无效")
    return tuple(int(part) for part in match.groups())


@dataclass(frozen=True)
class InstallerAsset:
    filename: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    installer: InstallerAsset | None = None

    def newer_than(self, current):
        return version_tuple(self.version) > version_tuple(current)


def _validate_asset(release):
    asset = release.installer
    if asset is None:
        raise UpdateCheckError("此版本未提供可校验的安装包，请手动下载")
    prefix = RELEASES_URL + "/tag/"
    try:
        tag = release.url.removeprefix(prefix)
        if (not release.url.startswith(prefix) or tag.removeprefix('v') != release.version
                or version_tuple(tag) != version_tuple(release.version)
                or asset.filename != f"TeamsSub-Setup-{release.version}.exe"
                or asset.url != f"{RELEASES_URL}/download/{tag}/{asset.filename}"
                or type(asset.size) is not int or not 2 <= asset.size <= MAX_INSTALLER_BYTES
                or not re.fullmatch(r"[a-fA-F0-9]{64}", asset.sha256)):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise UpdateCheckError("安装包信息无效，请重新检查更新") from None
    return asset


def _release_asset(data, version, tag):
    assets = data.get('assets', [])
    if not isinstance(assets, list):
        raise ValueError('invalid assets')
    matches = [item for item in assets if isinstance(item, dict)
               and item.get('name') == f'TeamsSub-Setup-{version}.exe']
    if len(matches) > 1:
        raise ValueError('ambiguous installer')
    if not matches:
        return None
    item = matches[0]
    digest = item.get('digest')
    # 老版 Release 仍可手动下载，但没有可信摘要时不能自动执行。
    if not isinstance(digest, str) or not re.fullmatch(r'sha256:[a-fA-F0-9]{64}', digest):
        return None
    asset = InstallerAsset(item['name'], item.get('browser_download_url'),
                           item.get('size'), digest[7:].lower())
    _validate_asset(Release(version, f'{RELEASES_URL}/tag/{tag}', asset))
    return asset


def check_latest_release():
    request = urllib.request.Request(LATEST_API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "TeamsSub-update-check",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        message = {404: "尚未找到正式发布版本", 403: "GitHub 请求受限，请稍后重试",
                   429: "GitHub 请求受限，请稍后重试"}.get(exc.code, "GitHub 暂时不可用，请稍后重试")
        raise UpdateCheckError(message) from None
    except (OSError, urllib.error.URLError, http.client.HTTPException):
        raise UpdateCheckError("无法连接 GitHub，请检查网络后重试") from None
    try:
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("response too large")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            raise ValueError("not a stable release")
        tag = data["tag_name"]
        version_tuple(tag)
        expected = f"{RELEASES_URL}/tag/{tag}"
        if data.get("html_url") != expected:
            raise ValueError("unexpected release URL")
        version = tag.removeprefix("v")
        return Release(version, expected, _release_asset(data, version, tag))
    except (ValueError, KeyError, TypeError):
        raise UpdateCheckError("GitHub 返回的版本信息无效，请稍后重试") from None


def _trusted_download_url(url):
    value = urllib.parse.urlsplit(url)
    return (value.scheme == 'https' and value.hostname in DOWNLOAD_HOSTS
            and value.port in (None, 443) and value.username is None and value.password is None)


class _GithubRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_download_url(newurl):
            raise UpdateCheckError('下载地址不可信，已停止更新')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_download(request):
    # 默认 ProxyHandler 保留系统/环境代理；不安装 Cookie 或认证处理器。
    return urllib.request.build_opener(_GithubRedirects()).open(request, timeout=20)


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


def _check_cancel(cancel):
    if _cancelled(cancel):
        raise UpdateCancelled('下载已取消')


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(128 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_installer(path, asset):
    path = Path(path)
    try:
        if not path.is_file() or path.stat().st_size != asset.size:
            raise UpdateCheckError('安装包大小不符，请重新下载')
        with path.open('rb') as stream:
            if stream.read(2) != b'MZ':
                raise UpdateCheckError('安装包格式无效，请重新下载')
        if _sha256(path) != asset.sha256.lower():
            raise UpdateCheckError('安装包校验失败，请重新下载')
    except OSError:
        raise UpdateCheckError('无法读取安装包，请重新下载') from None
    return path


def download_installer(release, directory, progress=None, cancel=None):
    """Download into a private staging file; only publish it after full verification."""
    asset = _validate_asset(release)
    _check_cancel(cancel)
    directory = Path(directory)
    partial = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(directory).free < asset.size + MIN_FREE_BYTES:
            raise UpdateCheckError('磁盘空间不足，请清理后重试')
        final = directory / asset.filename
        if final.exists():
            try:
                verify_installer(final, asset)
            except UpdateCheckError:
                # 缓存被截断/修改后重试应重新下载，不能一直卡在坏文件上。
                final.unlink()
            else:
                if progress:
                    progress(asset.size, asset.size)
                return final
        request = urllib.request.Request(asset.url, headers={
            'Accept': 'application/octet-stream', 'User-Agent': 'TeamsSub-update-download'})
        received = 0
        if progress:
            progress(0, asset.size)
        with _open_download(request) as response:
            if not _trusted_download_url(response.geturl()):
                raise UpdateCheckError('下载地址不可信，已停止更新')
            declared = response.headers.get('Content-Length')
            if declared is not None and (not declared.isdigit() or int(declared) != asset.size):
                raise UpdateCheckError('安装包大小不符，请重新下载')
            with tempfile.NamedTemporaryFile(prefix=asset.filename + '.', suffix='.part',
                                              dir=directory, delete=False) as stream:
                partial = Path(stream.name)
                while True:
                    _check_cancel(cancel)
                    chunk = response.read(128 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > asset.size:
                        raise UpdateCheckError('安装包大小超出预期，已停止下载')
                    stream.write(chunk)
                    if progress:
                        progress(received, asset.size)
                stream.flush()
                os.fsync(stream.fileno())
        _check_cancel(cancel)
        verify_installer(partial, asset)
        os.replace(partial, final)
        partial = None
        return final
    except UpdateCheckError:
        raise
    except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError):
        _check_cancel(cancel)
        raise UpdateCheckError('下载失败，请检查网络或目录权限后重试') from None
    finally:
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass


def _same_image_parent(executable):
    if os.name != 'nt':
        return 0
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD,
                                                     wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
        kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        parent = os.getppid()
        handle = kernel.OpenProcess(0x1000, False, parent)
        if not handle:
            return 0
        try:
            size = wintypes.DWORD(32768)
            name = ctypes.create_unicode_buffer(size.value)
            if not kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                return 0
            return parent if Path(name.value).resolve() == executable else 0
        finally:
            kernel.CloseHandle(handle)
    except (OSError, ValueError):
        return 0


@dataclass(frozen=True)
class InstallPlan:
    installer_path: Path
    asset: InstallerAsset
    current_exe: Path
    backup_path: Path
    backup_sha256: str
    arguments: tuple[str, ...]

    def start(self):
        # 保存/关闭需要时间；执行前再校验，不能使用准备阶段之后被替换的文件。
        verify_installer(self.installer_path, self.asset)
        try:
            if (_sha256(self.backup_path) != self.backup_sha256
                    or _sha256(self.current_exe) != self.backup_sha256):
                raise UpdateCheckError('程序备份校验失败，更新已取消')
            env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1')
            return subprocess.Popen([str(self.installer_path), *self.arguments],
                                    cwd=str(self.installer_path.parent), env=env,
                                    shell=False, close_fds=True)
        except OSError:
            raise UpdateCheckError('无法启动安装程序，请重试或手动安装') from None


def prepare_install(installer, release, current_exe=None):
    """Prepare recovery before exit; no installer process is started here."""
    if os.name != 'nt' or (current_exe is None and not getattr(sys, 'frozen', False)):
        raise UpdateCheckError('当前运行方式请使用手动安装')
    asset = _validate_asset(release)
    installer = Path(installer).resolve()
    verify_installer(installer, asset)
    executable = Path(current_exe or sys.executable).resolve()
    if executable.name.casefold() != 'teamssub.exe' or installer.parent == executable.parent:
        raise UpdateCheckError('请从正式安装目录运行 TeamsSub 后重试')
    backup = installer.parent / 'TeamsSub.previous.exe'
    temporary = None
    try:
        if not executable.is_file():
            raise UpdateCheckError('找不到当前程序，更新已取消')
        needed = MIN_FREE_BYTES + executable.stat().st_size * 2
        for directory in (executable.parent, installer.parent):
            if shutil.disk_usage(directory).free < needed:
                raise UpdateCheckError('磁盘空间不足，请清理后重试')
        # os.access 在 Windows ACL 下不可靠，实际创建后立即删除测试文件。
        with tempfile.TemporaryFile(prefix='.TeamsSub-write-check-', dir=executable.parent):
            pass
        with tempfile.NamedTemporaryFile(prefix='TeamsSub.previous.', suffix='.part',
                                          dir=installer.parent, delete=False) as stream:
            temporary = Path(stream.name)
            with executable.open('rb') as current:
                shutil.copyfileobj(current, stream)
            stream.flush()
            os.fsync(stream.fileno())
        digest = _sha256(executable)
        if _sha256(temporary) != digest:
            raise UpdateCheckError('程序备份校验失败，更新已取消')
        os.replace(temporary, backup)
        temporary = None
        arguments = ('/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                     f'/DIR={executable.parent}', '/TeamsSubUpdate=1',
                     f'/TeamsSubWaitPid={os.getpid()}',
                     f'/TeamsSubWaitParentPid={_same_image_parent(executable)}',
                     f'/TeamsSubBackup={backup}', f'/TeamsSubBackupSHA256={digest}',
                     f'/LOG={installer.parent / "install.log"}')
        return InstallPlan(installer, asset, executable, backup, digest, arguments)
    except OSError:
        raise UpdateCheckError('安装目录不可写，请重试或手动安装') from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

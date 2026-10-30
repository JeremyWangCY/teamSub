"""Opt-in, offline runtime probes; never load application settings or user records.

The entry point must call this before acquiring the application mutex or loading
the UI. All fixtures live under an owned TemporaryDirectory. The only retained
file is the explicitly requested JSON report, which contains no personal paths.
"""
import ctypes
from datetime import datetime
import json
import os
from pathlib import Path
import platform
import re
import secrets
import struct
import sys
import tempfile


def _check_tk(_directory):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    try:
        root.withdraw()
        frame = ttk.Frame(root)
        entry = ttk.Entry(frame)
        entry.insert(0, '中文 Unicode ✓')
        canvas = tk.Canvas(frame, width=24, height=24)
        canvas.create_oval(2, 2, 22, 22)
        image = tk.PhotoImage(master=root, width=2, height=2)
        image.put('#f5f5f3', to=(0, 0, 2, 2))
        root.update_idletasks()
        return entry.get() == '中文 Unicode ✓' and image.width() == 2
    finally:
        root.destroy()


def _check_pillow(_directory):
    import tkinter as tk
    from PIL import Image, ImageTk

    bitmap = Image.new('RGBA', (16, 16), (20, 40, 60, 255))
    root = tk.Tk()
    try:
        root.withdraw()
        image = ImageTk.PhotoImage(bitmap, master=root)
        root.update_idletasks()
        return bitmap.getpixel((8, 8)) == (20, 40, 60, 255) and image.width() == 16
    finally:
        root.destroy()
        bitmap.close()


def _check_uiautomation(_directory):
    import uiautomation as auto

    # Initialize only the API. Never enumerate windows or read their contents.
    with auto.UIAutomationInitializerInThread():
        return callable(auto.Control) and callable(auto.GetRootControl)


def _check_dpapi(_directory):
    import win32crypt

    fixture = secrets.token_bytes(32)
    encrypted = win32crypt.CryptProtectData(fixture, 'TeamSub self-test', None, None, None, 0)
    _, restored = win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)
    return restored == fixture


def _fixture_record():
    return dict(id='runtime-fixture', name='测试发言人', raw='测试发言人',
                text='Source English café ✓', tr='译文中文 测试 ✓', tr_status='ok',
                ts=datetime(2026, 1, 2, 3, 4, 5), backlog=False)


def _check_persistence(directory):
    from caption_store import CaptionStore
    from meeting_repository import load_session, rename_session, save_edits

    record = _fixture_record()
    store = CaptionStore(directory, '诊断 临时会议')
    try:
        path = store.path
        store.upsert(record, record['name'], '03:04:05')
    finally:
        store.close()
    database = path.with_suffix('.sqlite3')
    title, records = load_session(database)
    if title != '诊断 临时会议' or len(records) != 1 or records[0]['tr'] != record['tr']:
        return False
    save_edits(database, {record['id']: {'text': 'Edited café ✓', 'tr': '编辑后译文 ✓'}})
    rename_session(database, '已重命名 中文会议')
    title, records = load_session(database)
    projection = path.read_text(encoding='utf-8')
    return (title == '已重命名 中文会议' and records[0]['text'] == 'Edited café ✓'
            and records[0]['tr'] == '编辑后译文 ✓' and title in projection
            and '编辑后译文 ✓' in projection)


def _check_exports(directory):
    from meeting_export import ExportOptions, write_export

    record = _fixture_record()
    for format_name in ('md', 'txt', 'srt'):
        for content in ('source', 'translation', 'both'):
            path = directory / f'导出 {content}.{format_name}'
            write_export(path, '临时导出', [record], ExportOptions(format=format_name, content=content))
            text = path.read_text(encoding='utf-8')
            if (record['text'] in text) != (content in ('source', 'both')):
                return False
            if (record['tr'] in text) != (content in ('translation', 'both')):
                return False
    return True


def _resource_directory():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))


def _check_icon(_directory):
    from PIL import Image

    with Image.open(_resource_directory() / 'teamsub.ico') as icon:
        icon.load()
        return icon.width > 0 and icon.height > 0


def _check_version(_directory):
    if getattr(sys, 'frozen', False):
        import win32api

        info = win32api.GetFileVersionInfo(sys.executable, '\\')
        return info['FileVersionMS'] != 0 or info['FileVersionLS'] != 0
    content = (_resource_directory() / 'version_info.txt').read_text(encoding='utf-8')
    return bool(re.search(r'filevers=\(\d+,\s*\d+,\s*\d+,\s*\d+\)', content))


def _checks():
    return (
        ('tk_widgets', _check_tk), ('pillow', _check_pillow),
        ('uiautomation_com', _check_uiautomation), ('dpapi_roundtrip', _check_dpapi),
        ('sqlite_unicode_persistence_and_edits', _check_persistence),
        ('unicode_exports_all_modes', _check_exports), ('icon_resource', _check_icon),
        ('version_metadata', _check_version),
    )


def _metadata():
    version = sys.getwindowsversion() if sys.platform == 'win32' else None
    return dict(
        os=platform.system(),
        os_version=('.'.join(str(part) for part in (version.major, version.minor, version.build))
                    if version else platform.release()),
        machine_architecture=platform.machine(), process_bits=struct.calcsize('P') * 8,
        python_version=platform.python_version(), frozen=bool(getattr(sys, 'frozen', False)),
        elevated=bool(ctypes.windll.shell32.IsUserAnAdmin()) if version else None,
    )


def run_self_test(report_path):
    """Write a privacy-preserving JSON report to an absolute path; return success.

    Failed probes are retained as false and a stable exception type, never the
    exception message or traceback (both could contain private paths or data).
    An invalid/unwritable report destination returns False without opening UI.
    """
    temporary_report = None
    try:
        report_path = Path(report_path)
        if not report_path.is_absolute():
            return False
        report = dict(schema_version=1, runtime=_metadata(), checks={}, errors={})
        with tempfile.TemporaryDirectory(prefix='teamsub-self-test-') as owned:
            directory = Path(owned) / '中文 空格 自检'
            directory.mkdir()
            for name, probe in _checks():
                try:
                    passed = probe(directory) is True
                    report['checks'][name] = passed
                    if not passed:
                        report['errors'][name] = 'CheckFailed'
                except Exception as exc:
                    report['checks'][name] = False
                    report['errors'][name] = re.sub(r'[^A-Za-z0-9_]', '', type(exc).__name__)[:64]
        report['passed'] = bool(report['checks']) and all(report['checks'].values())
        # Do not create arbitrary destination trees; the caller owns that path.
        fd, temporary_report = tempfile.mkstemp(prefix='.teamsub-report-', dir=report_path.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_report, report_path)
        temporary_report = None
        return report['passed']
    except Exception:
        return False
    finally:
        if temporary_report is not None:
            try:
                os.unlink(temporary_report)
            except OSError:
                pass

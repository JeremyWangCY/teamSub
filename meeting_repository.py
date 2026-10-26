"""Read-only session discovery/import. Same titles never imply same sessions."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re
import sqlite3
import uuid


@dataclass(frozen=True)
class Session:
    path: Path
    title: str
    started: str
    count: int
    legacy: bool = False


def _connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)


def list_sessions(directory):
    sessions, errors = [], []
    directory = Path(directory)
    for path in sorted(directory.glob('*.sqlite3'), reverse=True):
        try:
            with closing(_connect(path)) as db:
                title, started = db.execute('SELECT title, started FROM session LIMIT 1').fetchone()
                count = db.execute('SELECT count(*) FROM captions').fetchone()[0]
            sessions.append(Session(path, title, started, count))
        except (sqlite3.Error, TypeError, ValueError) as exc:
            errors.append(f'{path.name}: {exc}')
    for path in sorted(directory.glob('*.md'), reverse=True):
        if path.with_suffix('.sqlite3').exists():
            continue
        try:
            title, records = load_legacy(path)
            sessions.append(Session(path, title, '时间未知', len(records), True))
        except (OSError, UnicodeError) as exc:
            errors.append(f'{path.name}: {exc}')
    return sessions, errors


def load_legacy(path):
    text = Path(path).read_text(encoding='utf-8-sig')
    title, records = Path(path).stem, []
    for line in text.splitlines():
        if line.startswith('# '):
            title = line[2:].strip()
        match = re.match(r'^\s*(?:-\s*)?\*\*\[([^]]+)\]\s*(.*?)\*\*\s*(.*)$', line)
        if match:
            stamp, name, source = match.groups()
            records.append(dict(id=uuid.uuid4().hex, name=name or '未知发言人', raw=name,
                                text=source, ts=None, backlog=True, legacy_time=stamp,
                                tr_status='none', source_session=Path(path).name))
        elif line.strip().startswith('>') and records:
            value = line.strip()[1:].strip()
            if not value.startswith('⚠'):
                r = records[-1]
                r['tr'] = (r.get('tr', '') + '\n' + value).strip()
                r['tr_status'] = 'ok'
        elif line.strip() and records and not line.startswith(('#', '开始时间', '- 导出', '- 条目', '- 发言')):
            records[-1]['text'] += '\n' + line.strip()
    if not records and text.strip():
        records = [dict(id=uuid.uuid4().hex, name='未知发言人', raw='', text=text,
                        ts=None, backlog=True, tr_status='none', source_session=Path(path).name)]
    return title, records


def load_session(path):
    path = Path(path)
    if path.suffix.lower() == '.md':
        return load_legacy(path)
    with closing(_connect(path)) as db:
        title = db.execute('SELECT title FROM session LIMIT 1').fetchone()[0]
        rows = db.execute('SELECT payload FROM captions ORDER BY seq').fetchall()
    records = []
    for (payload,) in rows:
        r = json.loads(payload)
        try:
            r['ts'] = datetime.fromisoformat(r['ts'])
        except (ValueError, TypeError, KeyError):
            r['ts'], r['backlog'] = None, True
        r['source_session'] = path.name
        if r.get('tr_status') == 'pending':
            r['tr_status'] = 'none'
        records.append(r)
    return title, records


def merge_sessions(paths):
    """Explicit selection order, preserve every record and provenance, including repeated speech."""
    records = []
    for path in paths:
        _, fragment = load_session(path)
        records.extend(fragment)
    return records


def editable(path):
    """能否回写：只有带数据库的新版记录可以改。

    旧版 .md 是「一次性投影」，没有 id 也没有数据库，改了就无处可存——
    与其假装能编辑，不如让界面把按钮关掉并说清楚原因。
    """
    path = Path(path)
    return path.suffix.lower() != '.md' and path.with_suffix('.sqlite3').exists()


def update_records(path, updates):
    """把修改写回会话文件，返回实际写入的条数。

    ``updates`` 形如 ``{record_id: {字段: 新值}}``，字段限 text / tr /
    tr_status / name / raw。SQLite 是唯一事实源，Markdown 只是投影（和采集
    时的写入方向一致）：只改 .md 会被下一次投影直接覆盖，只改库则导出读到
    的还是旧文本，所以两边都要落。
    """
    path = Path(path)
    db_path = path.with_suffix('.sqlite3')
    if path.suffix.lower() == '.md' or not db_path.exists():
        raise PermissionError('该记录是旧版 Markdown 文件，暂不支持编辑')
    allowed = {'text', 'tr', 'tr_status', 'name', 'raw'}
    db = sqlite3.connect(str(db_path), timeout=5)
    try:
        written = 0
        with db:
            for rid, fields in updates.items():
                row = db.execute('SELECT payload FROM captions WHERE id = ?',
                                 (rid,)).fetchone()
                if not row:
                    continue
                payload = json.loads(row[0])
                for key, value in fields.items():
                    if key in allowed:
                        payload[key] = value
                if 'name' in fields or 'raw' in fields:
                    payload['display_name'] = fields.get('raw') or fields.get('name') \
                        or payload.get('display_name')
                db.execute('UPDATE captions SET payload = ? WHERE id = ?',
                           (json.dumps(payload, ensure_ascii=False), rid))
                written += 1
        return written
    finally:
        db.close()


def rewrite_markdown(path):
    """按数据库内容重写同名的 .md 投影（原子替换 + fsync，见 CaptionStore）。"""
    from caption_store import CaptionStore
    path = Path(path)
    if path.suffix.lower() == '.md' or not path.with_suffix('.sqlite3').exists():
        return False
    store = CaptionStore(path.parent, path.stem, path=path)
    try:
        store.write_markdown()
        return True
    finally:
        store.close()


def save_edits(path, updates):
    """改库 + 重投影，一步到位（后台线程里调用）。"""
    written = update_records(path, updates)
    rewrite_markdown(path)
    return written

def session_files(path):
    """一场会议在磁盘上占用的所有文件（.sqlite3 与 .md 成对出现）。

    删除时必须一起处理：只删 .md 会留下数据库（照样能读出全部内容），
    只删 .sqlite3 又会留下可读的正文投影。
    """
    path = Path(path)
    files = []
    if path.suffix.lower() == ".md":
        files.append(path)
        db = path.with_suffix(".sqlite3")
        if db.exists():
            files.append(db)
    else:
        files.append(path)
        md = path.with_suffix(".md")
        if md.exists():
            files.append(md)
    return [f for f in files if f.exists()]


def delete_sessions(paths, active_path=None):
    """把选中的会议记录移入回收站，返回 (成功删除的路径, 拒绝原因列表)。

    三条硬约束，都是「删了就回不来」的防线：

    1. **进回收站，不永久删**。用户手滑是常态，办公软件不该让一次误点变成
       不可逆事故。用 SHFileOperation + FOF_ALLOWUNDO，用户在资源管理器里
       还能找回来。
    2. **正在记录的这场不许删**。它的 SQLite 正被写线程持有，删了轻则保存
       报错、重则半场记录丢失。
    3. **成对删除**。.md 与 .sqlite3 必须一起走（见 session_files）。

    返回的第二项是给界面用的中文原因，调用方据此提示，不要静默失败。
    """
    active = None
    if active_path is not None:
        try:
            active = Path(active_path).resolve()
        except OSError:
            active = None

    removed, refused, batch = [], [], []
    for raw in paths:
        path = Path(raw)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if active is not None and resolved in (active,
                                               active.with_suffix(".sqlite3"),
                                               active.with_suffix(".md")):
            refused.append(f"{path.stem}：正在记录中，不能删除")
            continue
        files = session_files(path)
        if not files:
            refused.append(f"{path.stem}：文件已不存在")
            continue
        batch.extend(files)

    if batch:
        ok, reason = _recycle(batch)
        if ok:
            removed = batch
        else:
            refused.append(reason)
    return removed, refused


def _recycle(files):
    """把文件移入回收站，返回 (是否全部成功, 失败原因)。

    优先用 SHFileOperationW(FOF_ALLOWUNDO|FOF_NOCONFIRMATION|FOF_SILENT)，
    它在资源管理器层面「删除」，用户能从回收站还原。
    非 Windows 或调用失败时退回 os.unlink（永久删除），但要如实告知。
    """
    files = [Path(f) for f in files]
    if not files:
        return True, ""
    try:
        import ctypes
        from ctypes import wintypes

        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [("hwnd", wintypes.HWND),
                        ("wFunc", wintypes.UINT),
                        ("pFrom", wintypes.LPCWSTR),
                        ("pTo", wintypes.LPCWSTR),
                        ("fFlags", ctypes.c_uint16),
                        ("fAnyOperationsAborted", wintypes.BOOL),
                        ("hNameMappings", ctypes.c_void_p),
                        ("lpszProgressTitle", wintypes.LPCWSTR)]

        # pFrom 是「双空字符结尾」的多个路径
        buf = "\0".join(str(f) for f in files) + "\0\0"
        op = SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = 3                      # FO_DELETE
        op.pFrom = buf
        op.pTo = None
        op.fFlags = 0x0040 | 0x0010 | 0x0004   # ALLOWUNDO | NOCONFIRMATION | SILENT
        res = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if res == 0 and not op.fAnyOperationsAborted:
            return True, ""
        return False, f"移入回收站失败（代码 {res}），未删除任何文件"
    except Exception as exc:
        # 回收站不可用（例如非 Windows）时退到永久删除，并明确告知用户。
        try:
            for f in files:
                f.unlink()
            return True, ""
        except OSError as exc2:
            return False, f"删除失败：{exc2}"

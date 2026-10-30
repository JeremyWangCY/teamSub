"""Session discovery, import and edits. Same titles never imply same sessions."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import re
import sqlite3
import uuid


@dataclass(frozen=True)
class DeleteResult:
    """删除结果：进回收站的文件 + 没删掉的原因（中文，直接给界面看）。"""
    removed: list
    refused: list

    def __iter__(self):
        # 老调用方按 (removed, refused) 解包，保持兼容。
        return iter((self.removed, self.refused))

    @property
    def ok(self):
        return not self.refused


@dataclass(frozen=True)
class Session:
    path: Path
    title: str
    started: str
    count: int
    legacy: bool = False
    ended: str = ''          # 最后一条字幕的时间，只用来算时长；读不到就是空串

    @property
    def started_label(self):
        """开始时间的显示形式。

        列表和删除确认框过去直接把 ISO 串（2026-10-30T13:12:24）塞进界面，
        而自动命名的会议标题永远是「飞书会议」——几场同名的会议在确认框里
        长得一模一样，用户根本不知道自己删的是哪一场。日期才是那个锚点，
        所以统一在这里格式化，不要再各自拼字符串。
        """
        return friendly_time(self.started)

    @property
    def duration_label(self):
        """会议持续多久；算不出来返回空串（旧版 Markdown、时间缺失）。"""
        return duration_text(self.started, self.ended)

    def summary(self):
        """列表行与删除确认共用的一句话：时间 · 标题 · 条数 · 时长。

        两处必须同源：确认框里写「2026-10-30 13:12 · 飞书会议 · 5 条」，
        列表里是同一串字，用户才能把确认框和自己的选择对上。
        """
        parts = [self.started_label, self.title or '未命名会议', f'{self.count} 条']
        duration = self.duration_label
        if duration:
            parts.append(duration)
        if self.legacy:
            parts.append('旧版')
        return ' · '.join(parts)


def friendly_time(value):
    """把 ISO 时间显示成「2026-10-30 13:12」；认不出的原样返回。"""
    text = str(value or '').strip()
    try:
        return f'{datetime.fromisoformat(text):%Y-%m-%d %H:%M}'
    except (TypeError, ValueError):
        return text or '时间未知'


def duration_text(started, ended):
    """两个 ISO 时间之间的时长，返回「1 小时 12 分」/「8 分钟」/「不到 1 分钟」。"""
    try:
        first, last = datetime.fromisoformat(str(started)), datetime.fromisoformat(str(ended))
    except (TypeError, ValueError):
        return ''
    seconds = int((last - first).total_seconds())
    if seconds < 0:
        return ''
    if seconds < 60:
        return '不到 1 分钟'
    minutes = seconds // 60
    if minutes < 60:
        return f'{minutes} 分钟'
    hours, minutes = divmod(minutes, 60)
    return f'{hours} 小时 {minutes} 分' if minutes else f'{hours} 小时'


def _connect(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=3)


def _last_stamp(db):
    """库里最后一条字幕的时间，用来算会议时长；取不到就是空串。

    只反序列化 seq 最大的那一行：列表要反复重读，把几百条 payload 全解析
    一遍只是为了拿最后一行太亏。老记录里 ts 可能是 null 或格式不对，那就
    当作「不知道」，界面自然不会显示时长——而不是编一个 0 分钟出来。
    """
    row = db.execute('SELECT payload FROM captions ORDER BY seq DESC LIMIT 1').fetchone()
    if not row:
        return ''
    try:
        return json.loads(row[0]).get('ts') or ''
    except (ValueError, TypeError):
        return ''


def list_sessions(directory):
    sessions, errors = [], []
    directory = Path(directory)
    for path in sorted(directory.glob('*.sqlite3'), reverse=True):
        try:
            with closing(_connect(path)) as db:
                title, started = db.execute('SELECT title, started FROM session LIMIT 1').fetchone()
                count = db.execute('SELECT count(*) FROM captions').fetchone()[0]
                ended = _last_stamp(db)
            sessions.append(Session(path, title, started, count, ended=ended))
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
    allowed = {'text', 'tr', 'tr_status', 'name', 'raw', 'qa'}
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


def validate_title(title):
    title = title.strip()
    if not title or len(title) > 120 or any(ord(c) < 32 for c in title):
        raise ValueError('会议名称须为 1–120 个字符，不能包含换行或控制字符。')
    return title


def rename_session(path, title):
    """只修改显示名称，保留路径、开始时间、记录 ID 和会议内容。"""
    title = validate_title(title)
    if not editable(path):
        raise PermissionError('旧版 Markdown 请先续接并另存为新版会议，再重命名。')
    db_path = Path(path).with_suffix('.sqlite3').resolve()
    with closing(sqlite3.connect(db_path.as_uri() + '?mode=rw', uri=True, timeout=5)) as db:
        with db:
            if db.execute('UPDATE session SET title = ?', (title,)).rowcount != 1:
                raise ValueError('会议信息已变更，请刷新后重试。')
    rewrite_markdown(path)
    return title

def translation_unchanged(record, expected):
    def status(rec):
        value = rec.get('tr_status')
        return 'none' if value == 'pending' else value
    return (all(record.get(key) == expected.get(key) for key in ('text', 'tr'))
            and status(record) == status(expected))


def save_translations(path, results):
    """Only fill unchanged, untranslated records; never replace a later edit."""
    if not editable(path):
        raise PermissionError('旧版 Markdown 记录请先续接并另存为新版会议')
    applied = {}
    db = sqlite3.connect(str(Path(path).with_suffix('.sqlite3')), timeout=5)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            for rid, (expected, fields) in results.items():
                row = db.execute('SELECT payload FROM captions WHERE id = ?', (rid,)).fetchone()
                if row is None:
                    continue
                record = json.loads(row[0])
                if not translation_unchanged(record, expected):
                    continue
                if record.get('tr_status') == 'skip' or (
                        record.get('tr_status') == 'ok' and record.get('tr')):
                    continue
                record.update(fields)
                db.execute('UPDATE captions SET payload = ? WHERE id = ?',
                           (json.dumps(record, ensure_ascii=False), rid))
                applied[rid] = fields
        return applied
    finally:
        db.close()


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
    """把选中的会议记录移入回收站，返回 DeleteResult(removed, refused)。

    三条硬约束，都是「删了就回不来」的防线：

    1. **进回收站，不永久删**。用户手滑是常态，办公软件不该让一次误点变成
       不可逆事故。用 SHFileOperation + FOF_ALLOWUNDO，用户在资源管理器里
       还能找回来。
    2. **正在记录的这场默认不许删**。它的 SQLite 正被写线程持有，删了轻则
       保存报错、重则半场记录丢失。要删它，界面必须先把写线程停掉、清空会
       话，再把路径从 active_path 里挪走（见 app.detach_session）。
    3. **成对删除**。.md 与 .sqlite3 必须一起走（见 session_files）。

    **逐场删除、逐场报告**。早先是把整批文件塞进一次 SHFileOperation：只要
    其中一个被占用或权限不足，整批就返回非 0，于是「三场能删、一场不能删」
    表现为四条记录全都不动、界面只闪一句错误码——用户看到的就是「删除没反
    应」。现在一场失败不影响其余各场，失败原因也写清是哪一场。
    """
    active = None
    if active_path is not None:
        try:
            active = Path(active_path).resolve()
        except OSError:
            active = None

    removed, refused = [], []
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
        ok, reason = _recycle(files)
        if ok:
            removed.extend(files)
        else:
            refused.append(f"{path.stem}：{reason}")
    return DeleteResult(removed, refused)


def _recycle(files):
    """把文件移入回收站，返回 (是否成功, 失败原因)。

    优先用 SHFileOperationW(FOF_ALLOWUNDO|FOF_NOCONFIRMATION|FOF_SILENT)，
    它在资源管理器层面「删除」，用户能从回收站还原。
    回收站不可用时报告失败，绝不退回永久删除。

    **不看返回码，看磁盘**。SHFileOperation 的返回值在「部分成功」时是最后
    一个失败项的 Win32 码，光凭它判断不了谁删掉了谁没删；而这里唯一重要的
    事实是「文件还在不在」。所以调用后逐个 stat，没走的再单独重试一次（常
    见原因是杀软短暂锁住刚写完的 .md），仍失败才报原因。
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

        def attempt(targets):
            # pFrom 是「双空字符结尾」的多个路径
            buf = "\0".join(str(f) for f in targets) + "\0\0"
            op = SHFILEOPSTRUCTW()
            op.hwnd = None
            op.wFunc = 3                      # FO_DELETE
            op.pFrom = buf
            op.pTo = None
            op.fFlags = 0x0040 | 0x0010 | 0x0004   # ALLOWUNDO | NOCONFIRMATION | SILENT
            code = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            return code, bool(op.fAnyOperationsAborted)

        code, aborted = attempt(files)
        left = [f for f in files if f.exists()]
        if not left:
            return True, ""
        # 有文件没走：单独再试一次，避免一个被占用的文件拖累同场的其余文件。
        code, aborted = attempt(left)
        left = [f for f in left if f.exists()]
        if not left:
            return True, ""
        return False, _recycle_reason(code, aborted, left)
    except Exception as exc:
        # 可恢复删除失败时必须停下，不能把用户同意的操作升级为永久删除。
        return False, f"无法移入回收站，已停止删除：{exc}"


# SHFileOperation 的返回值是 Win32 错误码，其中几个是我们真能给出建议的。
# 说「删除失败（代码 32）」等于让用户自己猜；说「文件被其它程序占用」才有用。
_RECYCLE_CODES = {
    2: '找不到文件（可能已被移动或删除）',
    5: '拒绝访问，请检查文件是否只读或被其它程序占用',
    19: '磁盘写保护，无法移入回收站',
    32: '文件正被其它程序占用，请关闭后重试',
    33: '文件被锁定，无法移入回收站',
    80: '文件正被其它程序占用，请关闭后重试',
    87: '路径无效，回收站可能已损坏',
    1223: '操作被取消',
}


def _recycle_reason(code, aborted, left):
    """把失败码翻译成用户能照着做的中文，并点名是哪几个文件。"""
    names = '、'.join(f.name for f in left[:2])
    if len(left) > 2:
        names += f' 等 {len(left)} 个文件'
    if aborted:
        return f'删除被中止，{names} 仍在原处'
    detail = _RECYCLE_CODES.get(code)
    if detail is None:
        detail = f'系统拒绝删除（代码 {code}）'
    return f'{detail}：{names}'

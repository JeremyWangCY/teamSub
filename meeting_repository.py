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

"""Immutable meeting snapshots and formatters; no UI or translation API calls."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import copy
import os
import tempfile


@dataclass(frozen=True)
class ExportOptions:
    format: str = 'md'
    content: str = 'both'
    names: bool = True
    timestamps: bool = True
    synthetic_timing: bool = False


def snapshot(records):
    return copy.deepcopy([{k: v for k, v in r.items() if not k.startswith('_')}
                          for r in records])


def filtered(records, speaker=None, query='', start=0):
    query = query.casefold().strip()
    return [r for r in records[start:] if (not speaker or r['name'] == speaker)
            and (not query or query in (r.get('text', '') + '\n' + r.get('tr', '')
                                       + '\n' + r.get('name', '')).casefold())]


def missing_translation(records):
    return sum(1 for r in records if r.get('tr_status') != 'skip'
               and not (r.get('tr_status') == 'ok' and r.get('tr')))


def _stamp(r):
    ts = r.get('ts')
    return ts.strftime('%H:%M:%S') if isinstance(ts, datetime) and not r.get('backlog') else '时间未知'


def _lines(r, opt):
    source = r.get('text', '').strip()
    translation = r.get('tr', '').strip() if r.get('tr_status') == 'ok' else ''
    if opt.content == 'source':
        return [source]
    if opt.content == 'translation':
        return [translation or (source if r.get('tr_status') == 'skip' else '[译文未完成]')]
    return [source] + ([translation] if translation else
                       ([] if r.get('tr_status') == 'skip' else ['[译文未完成]']))


def _srt_time(seconds):
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def format_meeting(title, records, opt=ExportOptions()):
    if opt.format not in ('md', 'txt', 'srt') or opt.content not in ('both', 'source', 'translation'):
        raise ValueError('不支持的导出选项')
    if opt.format == 'srt':
        sources = {r.get('source_session', '') for r in records}
        known = all(isinstance(r.get('ts'), datetime) and not r.get('backlog') for r in records)
        if (not known or len(sources) > 1) and not opt.synthetic_timing:
            raise ValueError('包含未知时间或多个会议片段，请明确选择「阅读时间轴」或使用 Markdown / TXT。')
        base = records[0]['ts'] if records and known else None
        starts = [i * 3.0 if opt.synthetic_timing else max(0, (r['ts'] - base).total_seconds())
                  for i, r in enumerate(records)]
        out = []
        for i, r in enumerate(records):
            end = min(starts[i] + 6, starts[i+1]) if i+1 < len(starts) else starts[i]+3
            end = max(starts[i]+0.1, end)
            lines = _lines(r, opt)
            if opt.names:
                lines[0] = f"{r.get('display_name') or r['name']}: {lines[0]}"
            out += [str(i+1), f'{_srt_time(starts[i])} --> {_srt_time(end)}', *lines, '']
        return '\n'.join(out)
    md = opt.format == 'md'
    out = [('# ' if md else '') + title, '', f'条目：{len(records)} · 未完成译文：{missing_translation(records)}', '']
    previous = None
    for r in records:
        origin = r.get('source_session')
        if origin and origin != previous:
            out += [('## ' if md else '') + '来源：' + origin, '']
            previous = origin
        meta = []
        if opt.timestamps:
            meta.append('[' + _stamp(r) + ']')
        if opt.names:
            meta.append(r.get('display_name') or r['name'])
        if meta:
            label = ' '.join(meta)
            out.append('**' + label + '**' if md else label)
        lines = _lines(r, opt)
        out.append(lines[0])
        out.extend(('> ' if md else '') + part for line in lines[1:] for part in line.splitlines())
        if md and r.get('qa'):
            out.append('> ⚠ ' + '；'.join(x.get('detail', '') for x in r['qa']))
        out.append('')
    return '\n'.join(out)


def write_export(path, title, records, options):
    path = Path(path)
    content = format_meeting(title, records, options)
    fd, temp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    if options.format == 'srt':
        # SRT cannot carry metadata without creating bogus cues.
        note = ('阅读时间轴：按每条 3 秒生成，不对应会议录像。' if options.synthetic_timing else
                '采集相对时间轴：以首条采集记录为零点，不保证与会议录像同步。')
        path.with_suffix(path.suffix + '.说明.txt').write_text(note, encoding='utf-8')
    return path

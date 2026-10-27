"""Persist caption records by ID; Markdown is an atomic, ordered projection."""
import copy
import json
import os
from pathlib import Path
import queue
import re
import sqlite3
import tempfile
import threading
import time
from datetime import datetime
import uuid

# 投影节流：SQLite 才是事实源，Markdown 只是给人看的快照，没必要每条都重写。
# 收尾时的"最后一次投影"也走同一个判据——没有未投影的记录就跳过，省掉一整轮
# fsync + replace；有未投影的记录则照写不误，否则用户打开 .md 会永久少几条。
_PROJECT_INTERVAL = 1.0


class CaptionStore:
    def __init__(self, directory, title, *, path=None, project_each=True):
        self.project_each = project_each
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r'[\\/:*?"<>|]', '_', title).strip(' .')[:60] or 'meeting'
        self.started = datetime.now().isoformat(timespec='seconds')
        self.title = title
        stem = f"{datetime.now():%Y-%m-%d_%H%M%S}_{safe}_{uuid.uuid4().hex[:12]}"
        self.path = Path(path).with_suffix('.md') if path else directory / (stem + '.md')
        db_path = self.path.with_suffix('.sqlite3')
        if path and db_path.exists():
            self.db = sqlite3.connect(db_path)
            self.title, self.started = self.db.execute('SELECT title, started FROM session LIMIT 1').fetchone()
            return
        db_path.touch(exist_ok=False)
        self.db = sqlite3.connect(db_path)
        self.db.executescript('''
            CREATE TABLE session(title TEXT, started TEXT);
            CREATE TABLE captions(seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL);
        ''')
        self.db.execute('INSERT INTO session VALUES (?, ?)', (title, self.started))
        self.db.commit()

    @staticmethod
    def _payload(record, name, timestamp):
        record.setdefault('id', uuid.uuid4().hex)
        payload = {k: record[k] for k in ('id', 'name', 'raw', 'text', 'backlog',
                   'tr', 'tr_status', 'qa') if k in record}
        payload.update(ts=record['ts'].isoformat() if record.get('ts') else None,
                       display_name=name, display_time=timestamp)
        return record['id'], json.dumps(payload, ensure_ascii=False)

    def upsert(self, record, name, timestamp):
        self.upsert_many([(record, name, timestamp)])

    def upsert_many(self, items):
        rows = [self._payload(*item) for item in items]
        with self.db:
            self.db.executemany("""INSERT INTO captions(id,payload) VALUES (?,?)
                ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""", rows)
        if self.project_each:
            self.write_markdown()

    def write_markdown(self):
        # 历史列表可修改正在记录的会议名称，投影始终读取数据库中的最新名称。
        self.title, self.started = self.db.execute('SELECT title, started FROM session LIMIT 1').fetchone()
        lines = [f'# {self.title} · 自动保存', '', f'开始时间：{self.started}', '']
        for (raw,) in self.db.execute('SELECT payload FROM captions ORDER BY seq'):
            rec = json.loads(raw)
            lines.append(f"- **[{rec['display_time']}] {rec['display_name']}**　{rec['text']}")
            if rec.get('tr_status') == 'ok' and rec.get('tr'):
                lines.extend('  > ' + line for line in rec['tr'].splitlines())
            if rec.get('qa'):
                lines.append('  > ⚠ 质检：' + '；'.join(i['detail'] for i in rec['qa']))
        fd, temporary = tempfile.mkstemp(prefix=self.path.stem, suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write('\n'.join(lines) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def close(self):
        self.db.close()


class AsyncCaptionStore:
    """Single writer owns SQLite; snapshots enter a queue, Markdown is coalesced.

    关闭拆成两步：close_async() 只投递请求，wait_closed() 分片等待。关窗口的
    主线程因此不会被写线程里的 fsync/replace 拖住——用户看到的「窗口假死 30
    秒再弹保存未完成」正是「主线程 join 卡在磁盘上」的后果。
    """

    def __init__(self, directory, title, *, path=None, on_error=None):
        self.queue = queue.Queue()
        self.on_error = on_error or (lambda exc: None)
        self.error = None          # 致命错误：写线程已退出，之后的记录不再落盘
        self.last_error = None     # 最近一次可恢复失败（如投影写不出去），界面可提示
        self.closed = False
        self._close_sent = False
        self._db_error = None      # SQLite 本身写不进去：这才是真的丢数据
        self.path = Path(path).with_suffix('.md') if path else Path(directory) / (
            f"{datetime.now():%Y-%m-%d_%H%M%S}_{uuid.uuid4().hex[:12]}.md")
        self.thread = threading.Thread(target=self._run, args=(directory, title, path),
                                       daemon=True, name='caption-store-writer')
        self.thread.start()

    def upsert(self, record, name, timestamp):
        if self.closed:
            raise RuntimeError('记录文件已关闭')
        if self.error:
            raise RuntimeError(str(self.error))
        record.setdefault('id', uuid.uuid4().hex)
        self.queue.put(('record', (copy.deepcopy(record), name, timestamp)))

    # ---------------- 写线程 ----------------
    def _report(self, exc):
        """记下失败并通知界面；回调本身出错不能反过来打断写线程。"""
        self.last_error = exc
        try:
            self.on_error(exc)
        except Exception:
            pass

    def _project(self, store):
        """投影一次 Markdown，返回是否成功；失败只记账，绝不上抛。

        SQLite 已经提交，投影只是一份给人看的快照：下一次写入会整份重写，
        所以这里失败不该弄死写线程——一抛就永久停摆，之后所有字幕都存不进去。
        """
        try:
            store.write_markdown()
            # 一份旧 SQLite 快照能投影成功，不代表尚未提交的记录已保存。
            if self._db_error is None:
                self.last_error = None
            return True
        except Exception as exc:
            self._report(exc)
            return False

    def _persist(self, store, batch):
        """提交全部待保存快照；调用方须保留失败批次，成功后才能清空它。"""
        try:
            store.upsert_many(batch)
            self._db_error = None
            return True
        except Exception as exc:
            self._db_error = exc
            self._report(exc)
            return False

    def _run(self, directory, title, existing):
        store = None
        pending = None
        try:
            # A new path must be exclusively created, even when two sessions share a title.
            if existing is None:
                Path(directory).mkdir(parents=True, exist_ok=True)
                store = CaptionStore(directory, title, path=self.path, project_each=False)
                # Expose the actual unique filename before the first write.
                self.path = store.path
            else:
                store = CaptionStore(directory, title, path=existing, project_each=False)
            dirty, last = False, time.monotonic()
            # dict 保留首次入队顺序，同 ID 则以最新快照替换。失败时不能把旧
            # batch 放回队尾，否则它可能晚于人工编辑写入，反而覆盖新内容。
            unwritten = {}
            retry_after = 0.0
            closing = False
            while True:
                try:
                    if pending is not None:
                        kind, payload = pending
                        pending = None
                    else:
                        kind, payload = self.queue.get(timeout=.25)
                except queue.Empty:
                    kind, payload = 'tick', None
                if kind == 'record':
                    batch = [payload]
                    for _ in range(99):
                        try:
                            next_kind, next_payload = self.queue.get_nowait()
                        except queue.Empty:
                            break
                        if next_kind != 'record':
                            pending = (next_kind, next_payload)
                            break
                        batch.append(next_payload)
                    for item in batch:
                        unwritten[item[0]['id']] = item
                closing = closing or kind == 'close'
                if unwritten and (kind in ('flush', 'close') or time.monotonic() >= retry_after):
                    if self._persist(store, list(unwritten.values())):
                        unwritten.clear()
                        dirty = True
                    else:
                        # 连续来字幕时也有重试间隔，避免坏盘上无限紧循环。
                        retry_after = time.monotonic() + .25
                if dirty and (time.monotonic()-last > _PROJECT_INTERVAL or kind == 'flush' or closing):
                    # 只有投影成功才清脏：失败后下一轮（节流期一过）会自动整份重投，
                    # 于是「磁盘恢复后 .md 自己补回来」是自愈的，不用人工干预。
                    # 没有未投影记录时 dirty 为假，收尾那次多余的 fsync 自然就省掉了。
                    dirty, last = not self._project(store), time.monotonic()
                if kind in ('flush', 'close'):
                    payload.set()
                if closing and not unwritten:
                    break
                # 关闭请求遇到数据库故障时仍保留线程和 unwritten，恢复后继续
                # 写完再退出。wait_closed/close 可超时，但不能把未保存伪报成功。
        except Exception as exc:      # 兜底：连建库/取队列都炸了，写线程才真的停
            self.error = exc
            self._report(exc)
        finally:
            if pending is not None and pending[0] in ('flush', 'close'):
                pending[1].set()
            try:
                if store:
                    store.close()
            except Exception as exc:
                self._report(exc)
            while not self.queue.empty():
                kind, payload = self.queue.get_nowait()
                if kind in ('flush', 'close'):
                    payload.set()

    # ---------------- 关闭 ----------------
    def close_async(self):
        """投递关闭请求后立刻返回。任意线程可调，重复调用无副作用。"""
        if self._close_sent:
            return
        self._close_sent = True
        self.closed = True            # 先置位：之后来的 upsert 立刻被拒绝
        self.queue.put(('close', threading.Event()))

    def wait_closed(self, timeout=30.0):
        """最多等 timeout 秒，返回写线程是否已经结束。绝不抛异常、绝不超时。

        界面侧这样用：close_async() 之后，用很小的 timeout 反复调它，两次之间
        照常 update 界面——于是关窗口既不假死，也不丢尚未写完的内容。
        """
        try:
            timeout = max(0.0, float(timeout))
        except (TypeError, ValueError):
            timeout = 0.0
        try:
            if self.thread.is_alive():
                self.thread.join(timeout)
            return not self.thread.is_alive()
        except Exception:             # 例如从写线程自己的回调里调进来，join 会拒绝
            return not self.thread.is_alive()

    def close(self, timeout=30.0):
        """旧语义：等写线程收尾，等不到就抛 TimeoutError。"""
        self.close_async()
        if not self.wait_closed(timeout):
            if self._db_error:
                raise RuntimeError(str(self._db_error))
            raise TimeoutError('等待自动保存结束超时')
        if self.error:
            raise RuntimeError(str(self.error))
        if self._db_error:
            raise RuntimeError(str(self._db_error))

    # ---------------- 写入侧等待 ----------------
    def flush(self, timeout=30):
        """等队列排空并投影一次。

        只有「SQLite 写不进去」才算失败：Markdown 投影失败时数据仍在库里，
        此时抛异常会让「切换会议」这类流程无谓地中断。
        """
        if self.error:
            raise RuntimeError(str(self.error))
        if self._close_sent:
            if not self.wait_closed(timeout):
                if self._db_error:
                    raise RuntimeError(str(self._db_error))
                raise TimeoutError('等待自动保存结束超时')
            if self.error:
                raise RuntimeError(str(self.error))
            if self._db_error:
                raise RuntimeError(str(self._db_error))
            return
        if not self.thread.is_alive():
            if self._db_error:
                raise RuntimeError(str(self._db_error))
            return
        done = threading.Event()
        self.queue.put(('flush', done))
        if not done.wait(timeout):
            raise TimeoutError('等待自动保存超时')
        if self.error:
            raise RuntimeError(str(self.error))
        if self._db_error:
            raise RuntimeError(str(self._db_error))

"""Backfill saved meetings without tying translation lifetime to a Tk page."""
import copy
import queue
import threading
import time

import meeting_export as ME
import meeting_repository as MR


def job_key(path):
    return str(path.resolve()).casefold()


def active_job(app, path):
    job = getattr(app, '_translation_jobs', {}).get(job_key(path))
    return job if job is not None and not job.done.is_set() else None


def persist_for_app(app, path, results, cancel):
    """Route active-session writes through its existing single writer."""
    ready = threading.Event()
    outcome = {}

    def apply(_):
        try:
            if cancel.is_set() or getattr(app, '_quit_pending', False):
                outcome['applied'] = {}
                return
            if not app.save_path or job_key(app.save_path.with_suffix('.sqlite3')) != job_key(path):
                return  # Archived session: the worker can update SQLite directly.
            store = app.save_store
            applied = {}
            outcome.update(applied=applied, store=store)
            for i, rec in enumerate(app.records):
                result = results.get(rec.get('id'))
                if result is None:
                    continue
                expected, fields = result
                if not MR.translation_unchanged(rec, expected) or not ME.missing_translation([rec]):
                    continue
                updated = dict(rec, **fields)
                store.upsert(updated, app.name_of(updated), app.ts_of(updated))
                rec.update(fields)
                rec.pop('_tr_request', None)
                app.pending_tr.pop(i, None)
                applied[rec['id']] = fields
                app.draw_translation(i, rec)
        except Exception as exc:
            outcome['error'] = exc
        finally:
            ready.set()

    app.q.put(('ui', (apply, None)))
    while not ready.wait(.2):
        if getattr(app, '_closing', False):
            raise RuntimeError('程序正在退出，补译已停止。')
    if 'error' in outcome:
        raise outcome['error']
    if 'applied' not in outcome:
        return MR.save_translations(path, results), False
    store = outcome.get('store')
    if store is not None and hasattr(store, 'flush'):
        store.flush()
        if getattr(store, 'last_error', None):
            raise RuntimeError('译文已写入记录，Markdown 同步失败：'+str(store.last_error))
    return outcome['applied'], store is not None


class TranslationJob:
    def __init__(self, path, records, cfg, persist=None):
        self.path = path
        self.records = ME.snapshot(records)
        self.cfg = copy.deepcopy(cfg)
        self.persist = persist
        self.todo = [i for i, rec in enumerate(self.records)
                     if ME.missing_translation([rec])]
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.lock = threading.Lock()
        self.completed = self.saved = self.failed = self.changed = 0
        self.error = self.last_error = ''
        self.updates = []
        self.thread = threading.Thread(target=self._run, daemon=True,
                                       name='meeting-backfill')

    def start(self):
        self.thread.start()

    def progress(self, since=0):
        with self.lock:
            return dict(total=len(self.todo), completed=self.completed,
                        saved=self.saved, failed=self.failed, changed=self.changed,
                        error=self.error, last_error=self.last_error,
                        done=self.done.is_set(), cancelled=self.cancel.is_set(),
                        updates=self.updates[since:])

    def _run(self):
        from translator import TranslationService
        replies = queue.Queue()
        svc = None
        dirty = False
        try:
            svc = TranslationService(self.cfg,
                lambda i, text, meta: replies.put((i, text, meta)))
            svc.start()
            for i in self.todo:
                if self.cancel.is_set():
                    break
                svc.submit(i, self.records[i]['text'], context=[r['text'] for r in
                    self.records[max(0, i-self.cfg.context_n):i]])
            remaining = set(self.todo)
            last_reply = last_project = time.monotonic()
            while remaining and not self.cancel.is_set():
                try:
                    first = replies.get(timeout=.2)
                except queue.Empty:
                    if time.monotonic()-last_reply > 180:
                        raise TimeoutError('等待翻译超时，可重试未完成的条目。')
                    continue
                batch = [first]
                for _ in range(63):
                    try:
                        batch.append(replies.get_nowait())
                    except queue.Empty:
                        break
                results = {}
                failures = processed = 0
                error = ''
                for i, text, meta in batch:
                    if i not in remaining:
                        continue
                    remaining.remove(i)
                    processed += 1
                    status = meta.get('status', 'fail')
                    if status == 'skip' or (status == 'ok' and text.strip()):
                        rec = self.records[i]
                        results[rec['id']] = (rec, dict(tr=text if status == 'ok' else '',
                                                     tr_status=status, qa=[]))
                    else:
                        failures += 1
                        error = meta.get('error') or '翻译服务未返回译文'
                last_reply = time.monotonic()
                applied, projected = {}, False
                if results:
                    if self.persist is not None:
                        applied, projected = self.persist(results, self.cancel)
                    else:
                        applied = MR.save_translations(self.path, results)
                dirty = dirty or (bool(applied) and not projected)
                with self.lock:
                    self.completed += processed
                    self.saved += len(applied)
                    self.failed += failures
                    self.changed += len(results)-len(applied)
                    self.last_error = error or self.last_error
                    self.updates.extend(applied.items())
                if dirty and time.monotonic()-last_project >= 1:
                    MR.rewrite_markdown(self.path)
                    dirty = False
                    last_project = time.monotonic()
        except Exception as exc:
            with self.lock:
                self.error = str(exc)
        finally:
            if svc is not None:
                svc.stop()
            if dirty:
                try:
                    MR.rewrite_markdown(self.path)
                except Exception as exc:
                    with self.lock:
                        self.error = '译文已写入记录，Markdown 同步失败：' + str(exc)
            self.done.set()

"""Own the update job independently of settings widgets and the Tk view lifecycle."""
from pathlib import Path
import threading
import time
import uuid

import app_updates as U


BUSY_PHASES = {'checking', 'downloading', 'cancelling', 'saving', 'installing'}


def state_for(app):
    if not hasattr(app, '_update_state'):
        app._update_state = dict(phase='idle', release=None, path=None,
                                 received=0, total=0, error='', token=None, cancel=None)
    return app._update_state


def cancel_download(app):
    state = state_for(app)
    if state['cancel'] is not None and state['phase'] in {'downloading', 'saving'}:
        state['cancel'].set()
        if state['phase'] == 'downloading':
            state['phase'] = 'cancelling'


def start_download(app, release):
    state = state_for(app)
    if state['phase'] in BUSY_PHASES:
        return
    from teamsub import data_dir
    token = uuid.uuid4().hex
    cancel = threading.Event()
    directory = data_dir() / 'cache' / 'updates' / token
    state.update(phase='downloading', release=release, path=None, received=0,
                 total=release.installer.size, error='', token=token, cancel=cancel)
    last_progress = [0.0]

    def progress(received, total):
        now = time.monotonic()
        if received != total and now - last_progress[0] < .15:
            return
        last_progress[0] = now
        def show(_):
            if state['token'] == token and state['phase'] == 'downloading':
                state.update(received=received, total=total)
        app.q.put(('ui', (show, None)))

    def done(path):
        if state['token'] != token or getattr(app, '_closing', False):
            return
        state['path'] = Path(path)
        state['phase'] = 'ready'
        if cancel.is_set():
            state['error'] = '已取消更新'
            return
        begin_install(app)

    def failed(exc):
        if state['token'] != token or getattr(app, '_closing', False):
            return
        state.update(phase='available', error='已取消下载' if cancel.is_set() else str(exc))

    app.background(lambda: U.download_installer(release, directory, progress=progress, cancel=cancel),
                   done, failed)


def begin_install(app):
    state = state_for(app)
    if state['phase'] != 'ready' or not state['path']:
        return
    if getattr(app, '_closing', False) or getattr(app, '_quit_pending', False):
        return
    if not app._allow_leave_sheet():
        return
    reader, store = app.reader, app.save_store
    context = dict(reader=reader, store=store, path=app.save_path,
                   paused=reader.paused, translating=app.trans_on)
    app._update_context = context
    cancel = state['cancel'] = threading.Event()
    state.update(phase='saving', error='')
    reader.paused = True
    app.stop_translation()
    jobs = [job for job in app._translation_jobs.values() if not job.done.is_set()]
    for job in jobs:
        job.cancel.set()

    def failed(exc):
        state.update(phase='ready', error=str(exc))
        _restore_session(app, context)
        app._update_context = None

    def ready(plan):
        if getattr(app, '_closing', False):
            return
        if cancel.is_set():
            failed(RuntimeError('已取消更新'))
            return
        if app.reader is not reader or app.save_store is not store:
            failed(RuntimeError('会议已切换，请重试更新'))
            return
        app._update_plan = plan
        app.on_close()
        # Guard rejection or a cancelled close must not install on a later unrelated exit.
        if not app._closing and not app._quit_pending:
            cancel_pending(app)

    def prepare():
        deadline = time.monotonic() + 90
        for job in jobs:
            while not job.done.wait(.1):
                if cancel.is_set() or time.monotonic() >= deadline:
                    raise RuntimeError('会议补译尚未保存，请稍后重试更新')
        if cancel.is_set():
            raise RuntimeError('已取消更新')
        if store and hasattr(store, 'flush'):
            store.flush()
        if store and (getattr(store, 'error', None) or getattr(store, '_db_error', None)
                      or getattr(store, 'last_error', None)):
            raise RuntimeError('会议保存失败，更新已停止')
        return U.prepare_install(state['path'], state['release'])

    def drained():
        if getattr(app, '_closing', False):
            return
        if cancel.is_set():
            failed(RuntimeError('已取消更新'))
        elif app.reader is not reader or app.save_store is not store:
            failed(RuntimeError('会议已切换，请重试更新'))
        elif time.monotonic() >= drain_deadline:
            failed(RuntimeError('会议正在处理，请稍后重试更新'))
        elif not app.q.empty():
            app.root.after(25, drained)
        else:
            app.background(prepare, ready, failed)
    drain_deadline = time.monotonic() + 30
    app.root.after(1, drained)


def _restore_session(app, context):
    if not context or app.reader is not context['reader']:
        return
    app.reader.paused = context['paused']
    if context['translating'] and not app.trans_on:
        app.start_translation(silent=True)


def launch_pending(app):
    """Last shutdown barrier: the writer has closed, the old process still owns its mutex."""
    plan = getattr(app, '_update_plan', None)
    if plan is None:
        return True
    store = app.save_store
    try:
        if not app._store_closed(store):
            raise RuntimeError('会议尚未保存完成，更新已停止')
        if store and (getattr(store, 'error', None) or getattr(store, '_db_error', None)
                      or getattr(store, 'last_error', None)):
            raise RuntimeError('会议保存失败，更新已停止')
        plan.start()
    except Exception as exc:
        state_for(app).update(phase='ready', error='更新未启动：' + str(exc))
        app._cancel_quit()
        return False
    state_for(app)['phase'] = 'installing'
    app._update_plan = None
    return True


def cancel_pending(app):
    """Cancel only a pending plan; no installer has been launched at this point."""
    context = getattr(app, '_update_context', None)
    if context is None:
        return
    app._update_plan = None
    app._update_context = None
    state = state_for(app)
    state.update(phase='ready', error=state['error'] or '已取消更新')
    if state['cancel']:
        state['cancel'].set()
    original = context['store']

    def restore_writer():
        if getattr(app, '_closing', False) or app.reader is not context['reader']:
            return
        if original is not None and getattr(original, 'closed', False):
            thread = getattr(original, 'thread', None)
            if thread is not None and thread.is_alive():
                app.reader.paused = True
                app.root.after(100, restore_writer)
                return
            if app.save_store is original and context['path']:
                from caption_store import AsyncCaptionStore
                app.save_store = AsyncCaptionStore(Path(context['path']).parent,
                    app.reader.meeting_title or '会议', path=context['path'],
                    on_error=lambda exc: app.q.put(('save_error', str(exc))))
                for rec in app.records:
                    app.save_store.upsert(rec, app.name_of(rec), app.ts_of(rec))
        _restore_session(app, context)
    restore_writer()

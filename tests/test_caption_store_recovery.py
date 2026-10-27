"""Real SQLite recovery after transient and persistent write failures."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from caption_store import AsyncCaptionStore, CaptionStore


class CaptionStoreRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='caption-recovery-')
        self.directory = Path(self.temporary.name) / '中文 空格 会议'
        self.allowed = threading.Event()
        self.failed = threading.Event()
        self.committed = threading.Event()
        self.original_persist = CaptionStore.upsert_many

        def persist(store, items):
            if not self.allowed.is_set():
                self.failed.set()
                raise OSError('isolated write failure')
            self.original_persist(store, items)
            self.committed.set()

        self.patch = patch.object(CaptionStore, 'upsert_many', new=persist)
        self.patch.start()
        self.writer = AsyncCaptionStore(self.directory, '恢复验证')

    def tearDown(self):
        # Faults belong only to this fixture. Allow its retained queue to drain.
        self.allowed.set()
        try:
            self.writer.close(timeout=5)
        finally:
            self.patch.stop()
            self.temporary.cleanup()

    def put(self, key, text):
        self.writer.upsert(dict(id=key, name='测试发言人', text=text,
                               tr='译文 '+text, tr_status='ok', ts=None),
                           '测试发言人', '09:00')

    def fail_first(self):
        self.put('before', '故障前待保存')
        self.assertTrue(self.failed.wait(2))
        with self.assertRaisesRegex(RuntimeError, 'isolated write failure'):
            self.writer.flush(timeout=2)
        self.assertIsNotNone(self.writer._db_error)
        self.assertTrue(self.writer.thread.is_alive())

    def rows(self):
        with closing(sqlite3.connect(self.writer.path.with_suffix('.sqlite3'))) as connection:
            return [json.loads(row[0]) for row in connection.execute(
                'SELECT payload FROM captions ORDER BY seq')]

    def assert_complete(self, expected):
        records = self.rows()
        self.assertEqual([(r['id'], r['text']) for r in records], expected)
        markdown = self.writer.path.read_text(encoding='utf-8')
        for _, text in expected:
            self.assertIn(text, markdown)
            self.assertIn('译文 '+text, markdown)
        self.assertIsNone(self.writer.error)
        self.assertIsNone(self.writer._db_error)
        self.assertIsNone(self.writer.last_error)

    def test_new_record_after_recovery_preserves_the_failed_batch(self):
        self.fail_first()
        self.allowed.set()
        self.put('after', '恢复后新记录')
        self.writer.flush(timeout=2)
        self.assert_complete([('before', '故障前待保存'), ('after', '恢复后新记录')])

    def test_flush_without_new_records_retries_the_retained_batch(self):
        self.fail_first()
        self.allowed.set()
        self.writer.flush(timeout=2)
        self.assert_complete([('before', '故障前待保存')])

    def test_idle_writer_retries_after_recovery_without_a_new_request(self):
        self.fail_first()
        self.allowed.set()
        self.assertTrue(self.committed.wait(2))
        self.writer.flush(timeout=2)
        self.assert_complete([('before', '故障前待保存')])

    def test_same_id_uses_latest_edit_without_duplicates_or_reordering(self):
        self.fail_first()
        self.put('after', '第二条原文')
        self.put('before', '人工修正后的新原文')
        self.allowed.set()
        self.writer.flush(timeout=2)
        self.assert_complete([('before', '人工修正后的新原文'), ('after', '第二条原文')])
        self.assertNotIn('故障前待保存', self.writer.path.read_text(encoding='utf-8'))

    def test_close_retains_pending_records_until_storage_recovers(self):
        self.fail_first()
        self.writer.close_async()
        self.assertFalse(self.writer.wait_closed(.1))
        self.assertTrue(self.writer.thread.is_alive())
        self.allowed.set()
        self.assertTrue(self.writer.wait_closed(3))
        self.writer.close(timeout=1)
        self.assert_complete([('before', '故障前待保存')])

    def test_persistent_failure_never_reports_success_from_close_or_flush(self):
        self.fail_first()
        with self.assertRaisesRegex(RuntimeError, 'isolated write failure'):
            self.writer.close(timeout=.1)
        self.assertFalse(self.writer.wait_closed(0))
        with self.assertRaisesRegex(RuntimeError, 'isolated write failure'):
            self.writer.flush(timeout=.1)
        self.assertEqual(self.rows(), [])
        self.assertIsNotNone(self.writer._db_error)
        self.allowed.set()
        self.writer.flush(timeout=3)
        self.assert_complete([('before', '故障前待保存')])

    def test_additional_failed_batches_keep_error_until_all_records_commit(self):
        self.fail_first()
        self.put('two', '第二条')
        self.put('three', '第三条')
        with self.assertRaisesRegex(RuntimeError, 'isolated write failure'):
            self.writer.flush(timeout=2)
        self.assertIsNotNone(self.writer._db_error)
        self.assertIsNotNone(self.writer.last_error)
        self.assertEqual(self.rows(), [])
        self.allowed.set()
        self.writer.flush(timeout=2)
        self.assert_complete([('before', '故障前待保存'), ('two', '第二条'), ('three', '第三条')])

    def test_projection_of_old_rows_cannot_clear_a_database_write_error(self):
        self.allowed.set()
        self.put('good', '已提交但尚未投影')
        self.assertTrue(self.committed.wait(2))
        self.allowed.clear()
        self.put('pending', '仍在等待写入')
        with self.assertRaisesRegex(RuntimeError, 'isolated write failure'):
            self.writer.flush(timeout=2)
        self.assertIsNotNone(self.writer.last_error)
        self.assertIsNotNone(self.writer._db_error)
        self.allowed.set()
        self.writer.flush(timeout=2)
        self.assert_complete([('good', '已提交但尚未投影'), ('pending', '仍在等待写入')])

    def test_projection_only_failure_still_closes_after_sqlite_is_committed(self):
        self.allowed.set()
        with patch.object(CaptionStore, 'write_markdown', side_effect=OSError('projection unavailable')):
            self.put('good', 'SQLite完整保存')
            self.writer.close(timeout=2)
        self.assertFalse(self.writer.thread.is_alive())
        self.assertEqual([(row['id'], row['text']) for row in self.rows()], [('good', 'SQLite完整保存')])
        self.assertIsNone(self.writer._db_error)
        self.assertIn('projection unavailable', str(self.writer.last_error))


if __name__ == '__main__':
    unittest.main(verbosity=2)

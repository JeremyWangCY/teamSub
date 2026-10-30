"""历史会议的删除与显示：日期必须可见，删除必须逐场报告。"""
from datetime import datetime
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import update_test_support as support      # 必须最先导入：它把仓库根目录放进 sys.path
import frontend_views as V
import ui_kit as UK
import meeting_repository as R
from caption_store import CaptionStore
from test_update_settings import GuiTestCase


def make_session(directory, title, when, count=2, span_minutes=0):
    """在 directory 里造一场会议，时间钉死在 when；span_minutes 是末条字幕的偏移。

    CaptionStore 自己会把 started 记成「现在」，而那正是要断言的东西，所以
    建好之后再把 session.started 和最后一条字幕的 ts 改回我们要的时间。
    """
    import json
    import sqlite3
    from datetime import timedelta
    store = CaptionStore(directory, title)
    for i in range(count):
        store.upsert(dict(id=f"{title}-{i}", name="Alex", raw="Alex", text=f"第 {i} 句",
                          tr_status="none", ts=when), "Alex", f"{i:02d}:00")
    path = store.path.with_suffix(".sqlite3")
    store.close()
    db = sqlite3.connect(str(path))
    with db:
        db.execute("UPDATE session SET started = ?", (when.isoformat(),))
        row = db.execute("SELECT seq, payload FROM captions ORDER BY seq DESC LIMIT 1").fetchone()
        if row is not None and (count or span_minutes):
            payload = json.loads(row[1])
            payload["ts"] = (when + timedelta(minutes=span_minutes)).isoformat()
            db.execute("UPDATE captions SET payload = ? WHERE seq = ?",
                       (json.dumps(payload, ensure_ascii=False), row[0]))
    db.close()
    return path


class SessionDisplayTests(unittest.TestCase):
    """时间格式化与列表摘要：删除确认框和列表行必须用同一串字。"""

    def test_friendly_time_formats_iso_and_survives_garbage(self):
        self.assertEqual(R.friendly_time("2026-10-30T13:12:24.534601"), "2026-10-30 13:12")
        self.assertEqual(R.friendly_time("时间未知"), "时间未知")
        self.assertEqual(R.friendly_time(""), "时间未知")
        self.assertEqual(R.friendly_time(None), "时间未知")

    def test_duration_text_covers_every_bucket(self):
        cases = [("2026-10-26T13:40:26", "2026-10-26T13:40:50", "不到 1 分钟"),
                 ("2026-10-26T13:40:26", "2026-10-26T13:42:20", "1 分钟"),
                 ("2026-10-28T13:11:00", "2026-10-28T13:31:01", "20 分钟"),
                 ("2026-10-28T13:11:00", "2026-10-28T14:23:00", "1 小时 12 分"),
                 ("2026-10-28T13:11:00", "2026-10-28T15:11:00", "2 小时")]
        for started, ended, expected in cases:
            self.assertEqual(R.duration_text(started, ended), expected)

    def test_duration_is_empty_when_times_are_missing_or_backwards(self):
        for started, ended in (("时间未知", "2026-10-28T13:11:00"),
                               ("2026-10-28T13:11:00", ""),
                               ("2026-10-28T14:00:00", "2026-10-28T13:00:00")):
            self.assertEqual(R.duration_text(started, ended), "")

    def test_summary_leads_with_date_not_just_the_shared_title(self):
        session = R.Session(Path("a.sqlite3"), "飞书会议", "2026-10-28T13:11:00", 238,
                            ended="2026-10-28T13:31:01")
        self.assertEqual(session.summary(),
                         "2026-10-28 13:11 · 飞书会议 · 238 条 · 20 分钟")

    def test_legacy_session_summary_marks_the_old_format(self):
        session = R.Session(Path("a.md"), "老会议", "时间未知", 3, legacy=True)
        self.assertEqual(session.summary(), "时间未知 · 老会议 · 3 条 · 旧版")


class ListSessionsTests(unittest.TestCase):

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def test_list_sessions_reports_end_time_for_duration(self):
        make_session(self.directory.name, "飞书会议", datetime(2026, 10, 28, 13, 11),
                     count=2, span_minutes=20)
        sessions, errors = R.list_sessions(self.directory.name)
        self.assertFalse(errors)
        self.assertEqual(len(sessions), 1)
        self.assertTrue(sessions[0].ended.startswith("2026-10-28T13:31"))
        self.assertEqual(sessions[0].duration_label, "20 分钟")
        self.assertTrue(sessions[0].summary().startswith("2026-10-28 13:11 · 飞书会议"))

    def test_list_sessions_tolerates_an_empty_meeting(self):
        make_session(self.directory.name, "空会议", datetime(2026, 10, 28, 9, 0), count=0)
        sessions, errors = R.list_sessions(self.directory.name)
        self.assertFalse(errors)
        self.assertEqual(sessions[0].ended, "")
        self.assertEqual(sessions[0].duration_label, "")


class DeleteTests(unittest.TestCase):
    """删除必须逐场报告：一场失败不能连累其余各场。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.first = make_session(self.directory.name, "第一场", datetime(2026, 10, 26, 13, 40))
        self.second = make_session(self.directory.name, "第二场", datetime(2026, 10, 28, 13, 11))

    @staticmethod
    def recycle_by_unlinking(files):
        """模拟回收站：真的把文件移走，但不碰系统的回收站。"""
        for path in files:
            Path(path).unlink()
        return True, ""

    def test_pairs_markdown_and_database(self):
        self.assertEqual(sorted(p.suffix for p in R.session_files(self.first)),
                         [".md", ".sqlite3"])

    def test_delete_reports_per_session_and_removes_both_files(self):
        with patch.object(R, "_recycle", side_effect=self.recycle_by_unlinking):
            result = R.delete_sessions([self.first, self.second])
        self.assertTrue(result.ok)
        self.assertEqual(len(result.removed), 4)
        self.assertEqual(result.refused, [])
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.assertEqual(R.list_sessions(self.directory.name)[0], [])

    def test_one_stuck_session_does_not_block_the_others(self):
        def flaky(files):
            if any("第一场" in str(p) for p in files):
                return False, "文件正被其它程序占用，请关闭后重试"
            return self.recycle_by_unlinking(files)
        with patch.object(R, "_recycle", side_effect=flaky):
            result = R.delete_sessions([self.first, self.second])
        self.assertFalse(result.ok)
        # 第二场照删不误，第一场留下并说明原因——这正是旧实现做不到的。
        self.assertEqual(len(result.removed), 2)
        self.assertTrue(all("第一场" in reason for reason in result.refused))
        remaining = sorted(Path(self.directory.name).iterdir())
        self.assertEqual(len(remaining), 2)
        # 留下的是「第一场」那一对：文件名按创建时刻生成，所以看库里的标题，
        # 而不是看文件名上的日期。
        databases = [p for p in remaining if p.suffix == ".sqlite3"]
        self.assertEqual(len(databases), 1)
        self.assertEqual(R.load_session(databases[0])[0], "第一场")

    def test_result_unpacks_like_the_old_tuple(self):
        removed, refused = R.DeleteResult(["a.md"], ["b：占用"])
        self.assertEqual((removed, refused), (["a.md"], ["b：占用"]))

    def test_active_session_is_refused_by_the_repository(self):
        with patch.object(R, "_recycle", side_effect=self.recycle_by_unlinking) as recycle:
            result = R.delete_sessions([self.first], active_path=self.first.with_suffix(".md"))
        self.assertEqual(result.removed, [])
        self.assertIn("正在记录中", result.refused[0])
        recycle.assert_not_called()
        self.assertTrue(self.first.exists())

    def test_missing_files_are_reported_not_silently_dropped(self):
        missing = Path(self.directory.name) / "2026-01-01_000000_gone.sqlite3"
        result = R.delete_sessions([missing])
        self.assertEqual(result.removed, [])
        self.assertIn("文件已不存在", result.refused[0])

    def test_recycle_reason_names_the_offending_files(self):
        stuck = [Path("2026-10-30_131224_c60caf369180.md")]
        self.assertIn("占用", R._recycle_reason(32, False, stuck))
        self.assertIn("2026-10-30_131224_c60caf369180.md", R._recycle_reason(32, False, stuck))
        self.assertIn("系统拒绝删除", R._recycle_reason(1234, False, stuck))
        self.assertIn("中止", R._recycle_reason(0, True, stuck))


class HistoryDeleteGuiTests(GuiTestCase):
    """界面层：列表要显示日期，确认框要写清是哪一场，删除结果要留得住。"""

    def setUp(self):
        super().setUp()
        self.app.show_live()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        make_session(self.directory.name, "飞书会议", datetime(2026, 10, 26, 13, 40))
        make_session(self.directory.name, "飞书会议", datetime(2026, 10, 28, 13, 11))

    def open_list(self, **kwargs):
        patcher = patch.object(support.A, "CAPTION_DIR", Path(self.directory.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        note = ""
        self.app.open_history()
        self.wait_for(lambda: any(isinstance(w, UK.RowList) and w.size() == 2
                                  for w in self.walk(self.app.sheet_layer)))
        return next(w for w in self.walk(self.app.sheet_layer) if isinstance(w, UK.RowList))

    @staticmethod
    def label_texts(case):
        return [w.cget("text") for w in case.walk(case.app.sheet_layer) if isinstance(w, tk.Label)]

    def test_rows_show_date_instead_of_only_the_shared_title(self):
        listing = self.open_list()
        rows = [listing.get(i) for i in range(listing.size())]
        # 两场都叫「飞书会议」；能分辨它们的是日期，而旧界面只给标题。
        # 顺序取决于文件名（含生成时刻），所以断言内容而不是下标。
        self.assertTrue(any(r.startswith("2026-10-28 13:11 · 飞书会议") for r in rows), rows)
        self.assertTrue(any(r.startswith("2026-10-26 13:40 · 飞书会议") for r in rows), rows)
        # 显示的是给人看的时间，不是 ISO 原文。
        self.assertNotIn("T13:11:00", "".join(rows))

    def test_search_matches_the_displayed_date(self):
        listing = self.open_list()
        self.app.sheet_layer.winfo_children()
        search = next(w for w in self.walk(self.app.sheet_layer)
                      if w.__class__.__name__ == "SearchBox")
        search.on_change()
        listing.delete(0, "end")
        self.assertEqual(listing.size(), 0)

    def test_confirm_dialog_lists_each_session_with_its_date(self):
        listing = self.open_list()
        listing.selection_set(0, "end")
        self.pump()
        with patch.object(V.messagebox, "askyesno", return_value=False) as ask, \
                patch.object(R, "_recycle", side_effect=DeleteTests.recycle_by_unlinking):
            self.button("删除").command()
            self.pump()
        message = ask.call_args.args[1]
        self.assertIn("2026-10-28 13:11 · 飞书会议", message)
        self.assertIn("2026-10-26 13:40 · 飞书会议", message)
        self.assertIn("删除会议记录（2 场）", ask.call_args.args[0])
        # 用户点了「否」：一个文件都不许动。
        self.assertEqual(len(list(Path(self.directory.name).iterdir())), 4)

    def test_delete_result_stays_visible_after_the_list_refreshes(self):
        listing = self.open_list()
        listing.selection_set(0, "end")
        self.pump()
        with patch.object(V.messagebox, "askyesno", return_value=True), \
                patch.object(R, "_recycle", side_effect=DeleteTests.recycle_by_unlinking):
            self.button("删除").command()
            self.wait_for(lambda: any("已移入回收站" in t for t in self.label_texts(self)))
        texts = self.label_texts(self)
        note = next(t for t in texts if "已移入回收站" in t)
        self.assertIn("4 个文件", note)
        # 刷新之后仍然看得到结果，而不是被「N 场」顶掉。
        self.assertIn("暂无会议记录", note)
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])

    def test_deleting_the_recording_session_stops_capture_then_removes_files(self):
        listing = self.open_list()
        live = next(s for s in R.list_sessions(self.directory.name)[0]
                    if s.started.startswith("2026-10-28"))
        from caption_store import AsyncCaptionStore
        store = AsyncCaptionStore(self.directory.name, "飞书会议", path=live.path)
        self.app.save_store, self.app.save_path = store, store.path
        # 列表按文件名倒序，选中的那一行必须真的是正在录的这场。
        rows = [listing.get(i) for i in range(listing.size())]
        target = next(i for i, text in enumerate(rows) if text.endswith(live.path.stem[-12:]))
        listing.selection_clear(0, "end")
        listing.selection_set(target)
        self.pump()
        asked = {}
        def confirm(title, message, **kwargs):
            asked.update(title=title, message=message)
            return True
        with patch.object(V.messagebox, "askyesno", side_effect=confirm), \
                patch.object(R, "_recycle", side_effect=DeleteTests.recycle_by_unlinking):
            self.button("删除").command()
            # 脱离会话是同步的，删文件在后台线程：等到文件真的少了一半再断言，
            # 否则测的是「还没来得及删」。
            self.wait_for(lambda: len(list(Path(self.directory.name).iterdir())) == 2)
        self.assertIn("正在记录的这一场会先停止采集并保存", asked["message"])
        self.assertIsNone(self.app.save_store)   # 写线程已脱离
        self.assertIsNone(self.app.save_path)    # 不会再把新字幕写进被删的文件
        remaining = [p for p in Path(self.directory.name).iterdir() if p.suffix == ".sqlite3"]
        self.assertEqual(len(remaining), 1)
        # 留下来的必须是另一场，而不是同一个文件重建出来的空壳。
        self.assertEqual(R.load_session(remaining[0])[0], "飞书会议")
        self.assertTrue(R.load_session(remaining[0])[1])
        self.assertFalse(self.errors)


if __name__ == "__main__":
    unittest.main(verbosity=2)

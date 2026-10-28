# -*- coding: utf-8 -*-
"""碎句合并 / 改写识别 / 悬浮窗断行的回归测试。

样例文本均为构造出来的中性句子，不含任何真实会议内容。
"""
import queue
import time
import unittest

import tkinter as tk
import tkinter.font as tkfont

import caption_merge as CM
import teamsub as T
import ui_kit as UK


class RevisionTests(unittest.TestCase):
    """「这个方案」→「这个方案周四再确认」这类叠字+补全必须认成同一句。"""

    def test_stutter_extension_is_revision(self):
        prev = "这个方案"
        new = "这个方案周四再确认，负责人在会上也同意了。"
        self.assertTrue(CM.looks_like_revision(prev, new))
        # 改写时取内容更全的一条，不能拼成「这个方案这个方案周四…」
        self.assertEqual(CM.join_caption(prev, new), new)

    def test_word_completion_is_revision(self):
        self.assertTrue(CM.looks_like_revision("这是对的。", "这是对的吧？"))
        self.assertEqual(CM.join_caption("这是对的。", "这是对的吧？"), "这是对的吧？")

    def test_continuation_is_not_revision(self):
        prev = "我们先把议题记下来，"
        new = "会后同步给项目组。"
        self.assertFalse(CM.looks_like_revision(prev, new))
        self.assertEqual(CM.join_caption(prev, new), prev + new)

    def test_cjk_join_has_no_space(self):
        self.assertEqual(CM.join_caption("这是对", "的吧？"), "这是对的吧？")

    def test_english_join_uses_space(self):
        self.assertEqual(CM.join_caption("the design is", "approved"),
                         "the design is approved")

    def test_english_revision_ignores_punctuation(self):
        self.assertTrue(CM.looks_like_revision("ok.", "ok, let us move on."))
        self.assertEqual(CM.join_caption("ok.", "ok, let us move on."), "ok, let us move on.")

    def test_overlapping_tail_is_deduped(self):
        self.assertEqual(CM.join_caption("先看尺寸，公差", "公差这一版还没给。"),
                         "先看尺寸，公差这一版还没给。")


class ShouldMergeTests(unittest.TestCase):
    CONT = "会后同步给项目组"

    def rec(self, **kw):
        base = {"name": "Alex", "text": "我们先把议题记下来", "_seen": time.time()}
        base.update(kw)
        return base

    def test_allows_same_speaker_unfinished(self):
        self.assertTrue(CM.should_merge(self.rec(), "Alex", self.CONT, time.time(), 1.5))

    def test_sentence_end_blocks(self):
        self.assertFalse(CM.should_merge(self.rec(text="好的。"), "Alex", self.CONT,
                                         time.time(), 1.5))

    def test_speaker_change_blocks(self):
        self.assertFalse(CM.should_merge(self.rec(), "Bob", self.CONT, time.time(), 1.5))

    def test_gap_blocks(self):
        rec = self.rec()
        self.assertFalse(CM.should_merge(rec, "Alex", self.CONT, rec["_seen"] + 3, 1.5))

    def test_disabled_blocks(self):
        self.assertFalse(CM.should_merge(self.rec(), "Alex", self.CONT, time.time(), 0))

    def test_backlog_blocks(self):
        self.assertFalse(CM.should_merge(self.rec(backlog=True), "Alex", self.CONT,
                                         time.time(), 1.5))

    def test_restated_sentence_uses_wider_window(self):
        # 同一句被重发常差 2–4 秒，按续写窗口会漏掉
        rec = self.rec(text="我换一台电脑")
        self.assertTrue(CM.should_merge(rec, "Alex", "我换一台电脑，给大家看一下数据。",
                                        rec["_seen"] + 2.0, 1.5))

    def test_restated_sentence_is_not_unbounded(self):
        rec = self.rec(text="我换一台电脑")
        self.assertFalse(CM.should_merge(rec, "Alex", "我换一台电脑，给大家看一下数据。",
                                         rec["_seen"] + 30, 1.5))


class ReaderMergeTests(unittest.TestCase):
    """采集线程的窗口对齐：改写就地更新，续写折进上一条。"""

    def make(self, gap_ms=1500):
        return T.CaptionReader(queue.Queue(), merge_gap_ms=gap_ms)

    @staticmethod
    def frame(*lines):
        return [(name, name, text) for name, text in lines]

    @staticmethod
    def live(reader, *lines):
        """模拟会议进行中的一轮轮询：首轮历史字幕带 backlog，不参与合并。"""
        reader.merge(ReaderMergeTests.frame(*lines))
        for rec in reader.records:
            rec["backlog"] = False

    def test_partial_then_full_replaces_same_record(self):
        reader = self.make()
        self.live(reader, ("Alex", "这个方案"))
        events = reader.merge(self.frame(("Alex", "这个方案周四再确认，负责人在会上也同意了。")))
        self.assertEqual(len(reader.records), 1)
        self.assertEqual(reader.records[0]["text"], "这个方案周四再确认，负责人在会上也同意了。")
        self.assertEqual([e[0] for e in events], ["update"])

    def test_new_window_line_folds_into_previous(self):
        reader = self.make()
        self.live(reader, ("Alex", "我们先把议题记下来，"))
        events = reader.merge(self.frame(("Alex", "我们先把议题记下来，"),
                                         ("Alex", "会后同步给项目组。")))
        self.assertEqual(len(reader.records), 1)
        self.assertEqual(reader.records[0]["text"], "我们先把议题记下来，会后同步给项目组。")
        self.assertEqual([e[0] for e in events], ["update"])

    def test_folded_line_revision_keeps_prefix(self):
        reader = self.make()
        self.live(reader, ("Alex", "前半句，"))
        reader.merge(self.frame(("Alex", "前半句，"), ("Alex", "后")))
        reader.merge(self.frame(("Alex", "前半句，"), ("Alex", "后半句。")))
        self.assertEqual(len(reader.records), 1)
        self.assertEqual(reader.records[0]["text"], "前半句，后半句。")

    def test_speaker_change_stays_separate(self):
        reader = self.make()
        self.live(reader, ("Alex", "这个议题我记一下"))
        reader.merge(self.frame(("Alex", "这个议题我记一下"), ("Bob", "好的。")))
        self.assertEqual(len(reader.records), 2)

    def test_gap_zero_keeps_every_line(self):
        reader = self.make(gap_ms=0)
        self.live(reader, ("Alex", "前半句，"))
        reader.merge(self.frame(("Alex", "前半句，"), ("Alex", "后半句")))
        self.assertEqual(len(reader.records), 2)


class WrapTests(unittest.TestCase):
    """悬浮窗断行：标点不占行首、行不超宽、西文不拆。"""

    SAMPLE = "我们再看一版这个零件的布置方案，包括间隙和公差，以及后续的验证计划"

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError:                      # 无显示环境跳过
            raise unittest.SkipTest("no display")
        cls.font = (UK.FAMILY, 12)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def test_no_punctuation_at_line_start(self):
        for line in UK.wrap_cjk(self.SAMPLE, self.font, 180).split("\n"):
            self.assertTrue(line.strip())
            self.assertNotIn(line[0], "，。、；：？！")

    def test_lines_fit_width(self):
        face = tkfont.Font(font=self.font)
        for width in (150, 180, 240):
            for line in UK.wrap_cjk(self.SAMPLE, self.font, width).split("\n"):
                self.assertLessEqual(face.measure(line), width)

    def test_latin_word_not_split(self):
        # 「C 柱」这类带空格的西文词不能被空格提前截断
        out = UK.wrap_cjk("现在看 C 柱这一块，然后是二排的饰板", self.font, 150)
        lines = out.split("\n")
        for left, right in zip(lines, lines[1:]):
            self.assertFalse(left.rstrip().endswith("C") and right.lstrip().startswith("柱"))

    def test_max_lines_truncates(self):
        out = UK.wrap_cjk(self.SAMPLE * 3, self.font, 120, max_lines=2)
        self.assertEqual(len(out.split("\n")), 2)
        self.assertTrue(out.endswith("…"))


if __name__ == "__main__":
    unittest.main()

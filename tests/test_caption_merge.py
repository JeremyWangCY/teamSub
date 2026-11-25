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
        events = reader.merge(self.frame(("Alex", "前半句，"), ("Alex", "后半句。")))
        self.assertEqual(len(reader.records), 1)
        self.assertEqual(reader.records[0]["text"], "前半句，后半句。")
        self.assertEqual(len(events), 1)
        # 画面未发生变化时，后续轮询绝不能在「前半句，」与「前半句，后半句。」之间反复发出假 update
        rev_before = reader.records[0]["revision"]
        idle_events = reader.merge(self.frame(("Alex", "前半句，"), ("Alex", "后半句。")))
        self.assertEqual(idle_events, [])
        self.assertEqual(reader.records[0]["revision"], rev_before)
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


class SpeakerNameTests(unittest.TestCase):
    """Teams 文本节点里认出发言人：外部嘉宾的「姓名 - 公司」不能被当成字幕。"""

    def test_org_suffix_name_is_speaker(self):
        reader = T.CaptionReader(queue.Queue())
        pairs = reader.to_pairs(["Nguyễn Đức Thắng (VF-KPTX-VTKMĐPTX)", "Yeah.",
                                 "Leonardo Rossi - Torino Design", "OK.",
                                 "Nguyễn Đức Thắng (VF-KPTX-VTKMĐPTX)", "So I think you should move it."])
        self.assertEqual([(p[0], p[2]) for p in pairs],
                         [("Nguyễn Đức Thắng", "Yeah."), ("Leonardo Rossi", "OK."),
                          ("Nguyễn Đức Thắng", "So I think you should move it.")])

    def test_sentence_with_dash_is_not_name(self):
        for text in ("So we move it - Torino", "OK - fine", "Yes - Right"):
            self.assertFalse(T.is_org_name(text), text)

    def test_teams_banner_and_enterprise_speaker_names(self):
        reader = T.CaptionReader(queue.Queue())
        nodes = [
            "字幕将以", "英语(美国)", "显示。",
            "Jeremy Wang", "Hi, Jenny.",
            "Jeni Mathan (VF-KPTX-VTKMĐPTX)", "Hello.",
            "zhang jian-sh", "Hello Jenny.",
            "wang_zewei", "Take a minute.",
            "guo_jiaxing", "No problem.",
            "zouyumin", "What was that?",
            "AN Chao_LD Interior", "I can speak English.",
            "AN Chao", "Very soon.",
        ]
        pairs = reader.to_pairs(nodes)
        self.assertEqual(
            [(p[0], p[2]) for p in pairs],
            [
                ("Jeremy Wang", "Hi, Jenny."),
                ("Jeni Mathan", "Hello."),
                ("zhang jian-sh", "Hello Jenny."),
                ("wang_zewei", "Take a minute."),
                ("guo_jiaxing", "No problem."),
                ("zouyumin", "What was that?"),
                ("AN Chao_LD Interior", "I can speak English."),
                ("AN Chao", "Very soon."),
            ],
        )

    def test_top_orphan_caption_keeps_original_speaker_when_header_scrolls_out(self):
        reader = T.CaptionReader(queue.Queue())
        frame1 = reader.to_pairs(["Jeremy Wang", "Fancy magazines.", "Jeni Mathan (VF-KPTX-VTKMĐPTX)", "Yes, yes."])
        reader.merge(frame1)
        # 下一帧顶部 Jeremy Wang 的姓名节点已滚出视口，但底部最新发言人是 Jeni Mathan：
        # 顶部的 "Fancy magazines." 必须继续归属 Jeremy Wang，不能因变成 Jeni Mathan 而重复入库。
        frame2 = reader.to_pairs(["Fancy magazines.", "Jeni Mathan (VF-KPTX-VTKMĐPTX)", "Yes, yes."])
        events = reader.merge(frame2)
        self.assertEqual(len(reader.records), 2)
        self.assertEqual(reader.records[0]["name"], "Jeremy Wang")
        self.assertEqual(events, [])

    def test_english_folded_revision_preserves_space_and_restatement_replaces(self):
        reader = T.CaptionReader(queue.Queue(), merge_gap_ms=1500)
        ReaderMergeTests.live(reader, ("Jeremy Wang", "We need to"))
        reader.merge([("Jeremy Wang", "Jeremy Wang", "We need to"),
                      ("Jeremy Wang", "Jeremy Wang", "check")])
        reader.merge([("Jeremy Wang", "Jeremy Wang", "We need to"),
                      ("Jeremy Wang", "Jeremy Wang", "check the tracker.")])
        self.assertEqual(len(reader.records), 1)
        self.assertEqual(reader.records[0]["text"], "We need to check the tracker.")

        reader2 = T.CaptionReader(queue.Queue(), merge_gap_ms=1500)
        ReaderMergeTests.live(reader2, ("Rajesh", "Now it is five"))
        reader2.merge([("Rajesh", "Rajesh", "Now it is five"),
                       ("Rajesh", "Rajesh", "Now it is 520")])
        reader2.merge([("Rajesh", "Rajesh", "Now it is five"),
                       ("Rajesh", "Rajesh", "Now it is 520, almost three hours.")])
        self.assertEqual(len(reader2.records), 1)
        self.assertEqual(reader2.records[0]["text"], "Now it is 520, almost three hours.")

    def test_structural_uia_tree_identifies_arbitrary_speakers_without_regex(self):
        class FakeNode:
            def __init__(self, ct, name="", cls="", aid="", kids=()):
                self.ControlTypeName = ct
                self.Name = name
                self.ClassName = cls
                self.AutomationId = aid
                self._kids = list(kids)

            def GetChildren(self):
                return self._kids

            def Exists(self, *_a):
                return True

        # 1) ListItemControl 结构：即使姓名是 "ops/007"、"#9527" 这类任意格式，也能直接按结构识别
        reader = T.CaptionReader(queue.Queue())
        reader.group = FakeNode("GroupControl", "实时字幕", kids=[
            FakeNode("ListControl", kids=[
                FakeNode("ListItemControl", kids=[
                    FakeNode("TextControl", "ops/007"),
                    FakeNode("TextControl", "Please check the surface."),
                ]),
                FakeNode("ListItemControl", kids=[
                    FakeNode("TextControl", "#9527"),
                    FakeNode("TextControl", "OK."),
                ]),
            ])
        ])
        pairs = reader.to_pairs(reader.read_nodes())
        self.assertEqual([(p[0], p[2]) for p in pairs],
                         [("ops/007", "Please check the surface."), ("#9527", "OK.")])

        # 2) 父容器带 author/caption-text 语义，叶子 TextControl 无 ClassName/AutomationId
        reader2 = T.CaptionReader(queue.Queue())
        reader2.group = FakeNode("GroupControl", "实时字幕", kids=[
            FakeNode("GroupControl", cls="fui-ChatMessage__author", kids=[
                FakeNode("TextControl", "A"),
            ]),
            FakeNode("GroupControl", aid="closed-caption-text", kids=[
                FakeNode("TextControl", "Hong Kong"),
            ]),
        ])
        pairs2 = reader2.to_pairs(reader2.read_nodes())
        self.assertEqual([(p[0], p[2]) for p in pairs2], [("A", "Hong Kong")])

        # 3) 头像 ImageControl 紧邻发言人文本 + 重复兄弟 GroupControl 条目
        reader3 = T.CaptionReader(queue.Queue())
        reader3.group = FakeNode("GroupControl", "实时字幕", kids=[
            FakeNode("GroupControl", kids=[
                FakeNode("ImageControl", ""),
                FakeNode("TextControl", "01-Engineer"),
                FakeNode("TextControl", "Ready now."),
            ]),
            FakeNode("GroupControl", kids=[
                FakeNode("ImageControl", ""),
                FakeNode("TextControl", "02-Lead"),
                FakeNode("TextControl", "Let's go."),
            ]),
        ])
        pairs3 = reader3.to_pairs(reader3.read_nodes())
        self.assertEqual([(p[0], p[2]) for p in pairs3],
                         [("01-Engineer", "Ready now."), ("02-Lead", "Let's go.")])

        # 4) 从 Teams 会议窗口的参与者列表自动学习参会人姓名
        class FakeWin:
            def TreeControl(self, Name="", searchDepth=0):
                if Name == "参与者":
                    return FakeNode("TreeControl", "参与者", kids=[
                        FakeNode("TreeItemControl", "03-Guest, 已静音"),
                        FakeNode("TreeItemControl", "AN Chao_LD Interior, 演示者"),
                    ])
                return type("Missing", (), {"Exists": lambda self, *a: False})()

            def ListControl(self, Name="", searchDepth=0):
                return type("Missing", (), {"Exists": lambda self, *a: False})()

        reader4 = T.CaptionReader(queue.Queue())
        reader4.window = FakeWin()
        reader4.sync_meeting_participants()
        self.assertTrue(reader4.is_name("03-Guest"))
        self.assertTrue(reader4.is_name("AN Chao_LD Interior"))

    def test_feishu_nested_pane_and_renamed_classes(self):
        """飞书历史面板：即使外层多套一层滚动 Pane 且类名改名，也能准确识别说话人与 EditControl 字幕，过滤头像/设备噪声按钮。"""
        import queue
        import teamsub as T

        class FakeNode:
            def __init__(self, ct, cls="", name="", children=()):
                self.ControlTypeName = ct
                self.ClassName = cls
                self.Name = name
                self._children = list(children)

            def GetChildren(self):
                return list(self._children)

            def GetLastChildControl(self):
                return self._children[-1] if self._children else None

            def GetPreviousSiblingControl(self):
                return None

        scroll_pane = FakeNode("PaneControl", "ScrollContent", "", [
            FakeNode("EditControl", "TextBox", "半截残句"),
            FakeNode("ButtonControl", "AvatarBtn", ""),
            FakeNode("ButtonControl", "SpeakerBtn", "房立勇"),
            FakeNode("EditControl", "TextBox", "我们上周四开跨部门会也说了。"),
            FakeNode("ButtonControl", "AvatarBtn", "Rich Label"),
            FakeNode("ButtonControl", "SpeakerBtn", "杨艳"),
            FakeNode("ButtonControl", "DeviceBadge", "用 陈东浩 的设备"),
            FakeNode("EditControl", "TextBox", "刚才等一下要说到的。"),
        ])
        outer_panel = FakeNode("PaneControl", "BilingualSubtitleHistoryPanelView", "", [
            FakeNode("ButtonControl", "HdrBtn", "查看实时字幕"),
            FakeNode("ButtonControl", "HdrBtn", "AI 同传"),
            FakeNode("ButtonControl", "HdrBtn", "翻译"),
            FakeNode("ButtonControl", "HdrBtn", "更多"),
            FakeNode("ButtonControl", "HdrBtn", "关闭字幕"),
            FakeNode("PaneControl", "BodyWrap", "", [
                FakeNode("EditControl", "SearchInput", ""),
                FakeNode("ButtonControl", "FilterBtn", "按参会人筛选"),
                FakeNode("PaneControl", "ScrollViewport", "", [scroll_pane]),
            ]),
        ])
        reader = T.CaptionReader(queue.Queue(), mode="feishu")
        reader.group = outer_panel
        pairs = reader.read_feishu()
        self.assertEqual(pairs, [
            ("房立勇", "房立勇", "我们上周四开跨部门会也说了。"),
            ("杨艳", "杨艳", "刚才等一下要说到的。"),
        ])




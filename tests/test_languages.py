"""Multilingual detection, direction and the searchable language picker."""
import update_test_support as support  # noqa: F401  (sets up an isolated data dir)
import tkinter as tk
import unittest
import translator as T
import ui_kit as UK


class DetectTests(unittest.TestCase):
    def test_scripts_and_latin_languages(self):
        cases = {
            "我们下周review一下这个design": "zh",
            "Let us check the gap on the door trim": "en",
            "これはテストです": "ja",
            "이것은 테스트입니다": "ko",
            "Nous allons vérifier la porte avec vous": "fr",
            "Wir müssen das noch prüfen, aber es ist gut": "de",
            "Vamos a revisar el diseño, pero está bien": "es",
            "Vamos verificar isso, não está pronto": "pt",
            "Мы проверим это завтра": "ru",
            "Questo è il design della porta": "it",
            "هذا هو التصميم الجديد": "ar",
            "นี่คือการออกแบบใหม่": "th",
            "Chúng ta sẽ kiểm tra thiết kế này": "vi",
        }
        for text, lang in cases.items():
            self.assertEqual(T.detect_lang(text, ("zh", "en")), lang, text)

    def test_direction(self):
        pair = ("zh", "ja")
        self.assertEqual(T.decide_target("zh", "auto", pair), "ja")
        self.assertEqual(T.decide_target("ja", "auto", pair), "zh")
        self.assertEqual(T.decide_target("en", "auto", pair), "zh")   # 第三种语言译成我方
        self.assertEqual(T.decide_target("fr", "de"), "de")
        self.assertIsNone(T.decide_target("de", "de"))

    def test_prompt_names_target_language(self):
        cfg = T.Config(enabled=False)
        provider = T.OpenAICompatProvider(cfg)
        prompt = provider._sys_prompt("ko", "zh")
        self.assertIn("韩语", prompt)
        self.assertIn("한국어", prompt)
        self.assertNotIn("中英互译", prompt)

    def test_qa_check_ignores_term_matching_and_checks_numbers(self):
        cfg = T.Config(topic="custom1", topics_custom={
            "custom1": {"name": "车身", "brief": "", "glossary": "body-in-white=白车身"}
        })
        issues = T.qa_check("Check the body-in-white gap 3.5 mm", "检查车身总成间隙 3.5 毫米", cfg, "en", "zh")
        self.assertEqual(issues, [])
        missing_num = T.qa_check("The gap is 3.5 mm", "间隙合适", cfg, "en", "zh")
        self.assertTrue(any(i["type"] == "number" for i in missing_num))

    def test_topic_export_and_import_formats(self):
        exported = T.format_topic_export({
            "name": "车身设计",
            "brief": "汽车车身开发与供应商评审",
            "glossary": "body-in-white=白车身\nflush=面差\n",
        })
        self.assertEqual(exported, {
            "name": "车身设计",
            "brief": "汽车车身开发与供应商评审",
            "glossary": ["body-in-white=白车身", "flush=面差"],
        })
        import json
        # 1) 导出格式原样导入
        parsed = T.parse_topics_import(json.dumps(exported, ensure_ascii=False))
        self.assertEqual(parsed, [{
            "name": "车身设计",
            "brief": "汽车车身开发与供应商评审",
            "glossary": "body-in-white=白车身\nflush=面差",
        }])
        # 2) AI 常见输出：带 ```json 代码块、数组 + 字典形式 glossary + 冒号分隔
        ai_text = """```json
        [
          {
            "name": "电池包",
            "brief": "动力电池系统评审",
            "glossary": {"cell-to-pack": "CTP 无模组", "thermal runaway": "热失控"}
          },
          {
            "name": "底盘",
            "brief": "悬架与转向调校",
            "glossary": ["control arm: 控制臂", "knuckle：转向节"]
          }
        ]
        ```"""
        parsed_multi = T.parse_topics_import(ai_text)
        self.assertEqual(len(parsed_multi), 2)
        self.assertEqual(parsed_multi[0]["name"], "电池包")
        self.assertIn("cell-to-pack=CTP 无模组", parsed_multi[0]["glossary"])
        self.assertEqual(parsed_multi[1]["glossary"], "control arm=控制臂\nknuckle=转向节")


class PickerTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("900x600")
        self.picked = []
        self.picker = UK.DirectionPicker(self.root, T.LANG_NAME, "auto", ("zh", "en"),
                                         command=lambda *a: self.picked.append(a),
                                         hints=T.LANG_HINT, keywords=T.LANG_KEYS)
        self.picker.pack()
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def test_search_filters_and_enter_picks(self):
        sel = self.picker.sel_b
        sel._open()
        self.root.update()
        entry = sel._search
        entry.insert(0, "korean")
        self.root.update()
        self.assertEqual([row[0] for row in sel._rows], ["韩语"])
        entry.focus_force()
        self.root.update()
        entry.event_generate("<Return>")
        self.root.update()
        self.assertIsNone(sel._pop)
        self.assertEqual(self.picked[-1], ("auto", "zh", "ko"))

    def test_same_language_swaps_and_target_mode(self):
        self.picker._on_lang("a", "英语")
        self.assertEqual(self.picker.get(), ("auto", "en", "zh"))
        self.picker._on_seg("译成")
        self.picker._on_lang("b", "德语")
        self.assertEqual(self.picker.get()[0], "de")


if __name__ == "__main__":
    unittest.main()

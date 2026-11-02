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

"""DPI 换算层：在子进程里装 1.5 倍缩放（换算层是进程级、不可撤销的）。"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, tkinter as tk
import ui_kit as UK, icons
root = tk.Tk()
UK.install_scale(1.5)
out = {"S": UK.S}
cv = tk.Canvas(root, width=100, height=40, highlightthickness=0)
cv.pack(padx=10, pady=(0, 8))
item = cv.create_rectangle(10, 10, 20, 20)
out["coords"] = cv.coords(item)
out["cget_width"] = cv["width"]
out["phys_width"] = int(cv.tk.call(cv._w, "cget", "-width"))
out["pack_padx"] = str(cv.pack_info()["padx"])
frame = tk.Frame(root, width=200, height=30)
out["frame_width"] = int(frame["width"])
label = tk.Label(root, text="x", width=12, wraplength=100)
out["label_width"] = int(label["width"])
out["wraplength"] = int(float(str(label["wraplength"])))
out["icon"] = icons.photo("search", 16, "#000000", master=root).width()
pill = UK.Pill(root, "确定", kind="primary")
pill.pack()
root.update_idletasks()
out["pill_logical_h"] = pill.winfo_reqheight()
out["pill_phys_h"] = tk.Misc.winfo_reqheight(pill)
root.destroy()
print(json.dumps(out))
"""


class DpiScaleTests(unittest.TestCase):
    def test_scale_layer_converts_logical_pixels(self):
        env = dict(os.environ, TEAMSUB_DATA_DIR=tempfile.mkdtemp(prefix="ts-dpi-"))
        res = subprocess.run([sys.executable, "-c", PROBE], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout.strip().splitlines()[-1])
        self.assertEqual(out["S"], 1.5)
        # Canvas 对外是逻辑坐标，底下是物理像素
        self.assertEqual(out["coords"], [10.0, 10.0, 20.0, 20.0])
        self.assertEqual(out["cget_width"], 100.0)
        self.assertEqual(out["phys_width"], 150)
        self.assertEqual(out["pack_padx"], "15")
        self.assertEqual(out["frame_width"], 300)
        # Label 的 width 是字符数，不能乘；wraplength 是像素，要乘
        self.assertEqual(out["label_width"], 12)
        self.assertEqual(out["wraplength"], 150)
        self.assertEqual(out["icon"], 24)
        self.assertAlmostEqual(out["pill_logical_h"], 36, delta=0.5)
        self.assertEqual(out["pill_phys_h"], 54)


if __name__ == "__main__":
    unittest.main()

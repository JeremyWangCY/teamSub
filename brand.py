# -*- coding: utf-8 -*-
"""
brand —— TeamsSub 标识
======================
一份绘制逻辑同时用于 teamsub.ico（任务栏、托盘、安装包）和界面里的品牌标。

造型：强调蓝圆角方块里一个白色对话气泡，气泡里两行字幕——上行原文（深蓝），
下行译文（浅蓝、略短）。16/20/24 px 去掉气泡，只留两条白色字幕条，保证托盘
小尺寸下仍然清楚。

重新生成图标：
    python brand.py teamsub.ico
"""
import sys
from PIL import Image, ImageDraw

TOP = (0x52, 0x7B, 0xD4)        # 渐变上沿，比界面强调色 A500 略亮
BOTTOM = (0x30, 0x55, 0xA8)     # 渐变下沿，接近 A600
INK = (0x2B, 0x4F, 0xA3)        # 气泡里的原文行
SOFT = (0x9F, 0xB7, 0xE8)       # 气泡里的译文行
WHITE = (0xFF, 0xFF, 0xFF)

ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SS = 8                           # 超采样倍数


def _gradient(size):
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel((0, y), tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)) + (255,))
    return grad.resize((size, size))


def render(size: int) -> Image.Image:
    big = size * SS
    u = big / 24.0                     # 24 单位网格
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big - 1, big - 1), radius=5.4 * u, fill=255)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    img.paste(_gradient(big), (0, 0), mask)
    d = ImageDraw.Draw(img)

    def box(x1, y1, x2, y2):
        return (x1 * u, y1 * u, x2 * u, y2 * u)

    if size <= 24:
        # 小尺寸：两条粗字幕条，像素对齐优先于细节
        d.rounded_rectangle(box(4.5, 8, 19.5, 11), radius=1.5 * u, fill=WHITE)
        d.rounded_rectangle(box(4.5, 13.5, 15, 16.5), radius=1.5 * u, fill=WHITE + (200,))
    else:
        # 气泡主体 + 左下角小尾巴
        d.rounded_rectangle(box(4, 5, 20, 16.5), radius=3.6 * u, fill=WHITE)
        d.polygon([(6.6 * u, 15.5 * u), (6.4 * u, 19.6 * u), (10.6 * u, 16.2 * u)], fill=WHITE)
        d.rounded_rectangle(box(7, 8.3, 17, 10), radius=.85 * u, fill=INK)
        d.rounded_rectangle(box(7, 11.6, 14, 13.3), radius=.85 * u, fill=SOFT)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def write_ico(path):
    frames = [render(s) for s in ICO_SIZES]
    frames[-1].save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES],
                    append_images=frames[:-1])


if __name__ == "__main__":
    write_ico(sys.argv[1] if len(sys.argv) > 1 else "teamsub.ico")

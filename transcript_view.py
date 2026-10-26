# -*- coding: utf-8 -*-
"""transcript_view —— 一条字幕的共用画法

实时会议界面和历史阅读界面必须长得一模一样：同一个序号圆点、同样的
「时间 / 姓名 / 原文 / →译文」排版、同样的缩进与行距。两边各写一份格式化，
用不了多久就会走形——历史详情的旧实现直接往 Text 里塞了一段纯文本导出，
连说话人圆点和译文缩进都没有，读起来像另一个产品。

这里只负责「怎么画一条记录」；说话人颜色、显示姓名、时间文本由调用方通过
ctx 提供（App 上已经有 name_of / ts_of / speaker_color）。
"""
import tkinter as tk

import ui_kit as UK


def configure_tags(txt, *, reading_size=12, density="comfort", accent=None):
    """统一配置正文字号、行距、颜色与译文缩进。"""
    spacing = 12 if density == "comfort" else 4
    txt.tag_configure("time", foreground=UK.C.T3, font=UK.F_TIME)
    txt.tag_configure("body", foreground=UK.C.TEXT,
                      font=(UK.FAMILY, reading_size),
                      lmargin1=0, lmargin2=0, spacing3=4)
    txt.tag_configure("trans", foreground=UK.C.SUB,
                      font=(UK.FAMILY, reading_size),
                      lmargin1=14, lmargin2=14, spacing3=spacing)
    txt.tag_configure("trpending", foreground=UK.C.T3, font=UK.F_MINI,
                      lmargin1=14, lmargin2=14, spacing3=spacing)
    txt.tag_configure("trfail", foreground=UK.C.RED, background=UK.C.RED_S,
                      font=UK.F_MINI,
                      lmargin1=14, lmargin2=14, spacing3=spacing, underline=True)
    txt.tag_configure("qawarn", foreground=UK.C.AMBER, background=UK.C.AMBER_S)
    txt.tag_configure("hit", background=UK.C.A50)
    txt.tag_configure("editmark", foreground=accent or UK.C.A500,
                      font=UK.F_MINI, underline=True)


def render_block(txt, i, rec, ctx, display_mode="both", at=None, retry=True):
    """在 at 处（缺省为末尾）画一条记录，并在起点打 mark 以便原地重绘。

    mark 必须设在具体索引上：设成 "end" 的话 Tk 会让它跟着缓冲区末尾走，
    后续 ``delete(mark, "end-1c")`` 就成了空操作，每改写一次就多一块。
    ``retry=False`` 时不提示「点击重试」——历史记录是只读快照，点了也没有
    翻译服务在跑，写个假承诺比不写更糟。
    """
    pos = txt.index(at) if at else txt.index("end-1c")
    txt.mark_set(f"rec{i}", pos)
    txt.mark_gravity(f"rec{i}", "left")
    name = ctx.name_of(rec)
    color = ctx.speaker_color(rec["name"])
    tag = f"sp{i % 40}"
    txt.tag_configure(tag, foreground=UK.C.TEXT, font=UK.F_NAME)
    dot = f"dot{i % 40}"
    txt.tag_configure(dot, foreground=color)

    parts = [("●  ", dot), (f"{ctx.ts_of(rec)}  ", "time"),
             (f"{name}\n", (tag,)),
             (f"{rec.get('text', '')}\n", "body")]
    if display_mode == "translation":
        parts = parts[:-1]
    status = rec.get("tr_status") if display_mode != "source" else None
    if status == "ok" and rec.get("tr"):
        row_tag = f"trrec{i}"
        if rec.get("qa"):
            # 前后各留一空格：底色徽牌更饱满，且与 "→  " 行同宽对齐
            parts.append((" ⚠ ", ("trans", "qawarn", row_tag)))
        else:
            parts.append(("→  ", ("trans", row_tag)))
        parts.append((f"{rec['tr']}\n", ("trans", row_tag)))
    elif status == "pending":
        parts.append(("→  翻译中…\n", "trpending"))
    elif status == "fail":
        hint = " ⚠  翻译失败 · 点击重试\n" if retry else " ⚠  翻译失败\n"
        parts.append((hint, ("trfail", f"trfail{i}")))
    if display_mode == "translation" and status not in ("ok", "pending", "fail"):
        parts.append(("无需翻译\n" if rec.get("tr_status") == "skip" else "译文未完成\n",
                      "trpending"))
    cur = pos
    for text, tags in parts:
        txt.insert(cur, text, tags)
        cur = txt.index(f"{cur} + {len(text)}c")
    return cur


def render_range(txt, records, ctx, display_mode="both", start=0, retry=True,
                 index_of=None):
    """批量画一段记录；返回最后一条记录的序号（用于「编辑」定位）。"""
    for offset, rec in enumerate(records):
        i = index_of(offset) if index_of else start + offset
        render_block(txt, i, rec, ctx, display_mode, retry=retry)
    return start + len(records)

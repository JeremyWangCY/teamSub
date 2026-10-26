# -*- coding: utf-8 -*-
"""modes —— 会议模式注册表

TeamsSub 从不同会议软件读取实时字幕。每种模式一条记录：

    key         存进配置的值
    name        完整名（状态文案、导出文件名用）
    short       左上角切换控件上的短名
    desc        弹层里的一行说明
    dot         标识色（浅色主题）
    dot_dark    标识色（深色主题，整体提亮）
    available   False = 尚未支持：UI 置灰并标注「即将支持」，点击只提示不切换

新增一种会议模式（如飞书）只需两步，UI / 配置 / 窗口标题 / 导出名全部由本表驱动：

  1. 在 MODES 里加一条记录（available 先设 False，界面自动出现并置灰）；
  2. 在 teamsub.CaptionReader 的 FINDERS 里登记对应的窗口发现函数。
"""

MODES = [
    {
        "key": "teams",
        "name": "Teams 会议",
        "short": "Teams",
        "desc": "Microsoft Teams 实时字幕",
        "dot": "#4A67F6",
        "dot_dark": "#6E87FF",
        "available": True,
    },
    {
        "key": "feishu",
        "name": "飞书会议",
        "short": "飞书",
        "desc": "飞书会议实时字幕",
        "dot": "#3370FF",
        "dot_dark": "#5B8DEF",
        "available": True,
    },
]

DEFAULT_MODE = "teams"


def all_modes() -> list:
    """全部模式（含未上线），按注册顺序。"""
    return [dict(m) for m in MODES]


def get(key):
    """按 key 取模式；不存在返回 None。"""
    for m in MODES:
        if m["key"] == key:
            return dict(m)
    return None


def available_keys() -> list:
    return [m["key"] for m in MODES if m.get("available")]


def resolve(key) -> dict:
    """把任意 key 收敛成可用模式：不可用/不存在回退默认。"""
    m = get(key)
    if m is None or not m.get("available"):
        return get(DEFAULT_MODE)
    return m

# -*- coding: utf-8 -*-
"""
TeamsSub —— 多会议平台实时字幕阅读器
====================================
通过 Windows UIA（无障碍接口）直接读取会议软件中的「实时字幕」文本节点，
不截图、不 OCR、不改动会议软件本体。左上角可切换会议模式（Teams / 飞书…）。

用法：双击 start.bat，或在会议中打开实时字幕（更多 -> 语言和语音 -> 打开实时字幕）。
"""
import os
import re
import sys
import json
import time
import queue
import threading
import traceback
import copy
import uuid
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import floatbar
import modes as MD
import transcript_view as TV
import ui_kit as UK

try:
    import tray as tray_mod          # 系统托盘（pywin32）
except Exception:                    # 缺模块时退化为任务栏模式
    tray_mod = None
from caption_store import CaptionStore, AsyncCaptionStore
import meeting_export as ME
import meeting_repository as MR

try:
    from translator import (Config as TransConfig, TranslationService, detect_lang,
                            is_sentence_end, all_topics, qa_check, TOPICS)
    TRANSLATOR_OK = True
except Exception as _exc:      # 缺模块时降级为纯字幕模式
    TRANSLATOR_OK = False
    TransConfig = None
    TranslationService = None
    detect_lang = None
    is_sentence_end = None
    all_topics = lambda cfg=None: {}
    qa_check = lambda *a, **k: []
    TOPICS = {}
    print("[warn] 翻译模块不可用：", _exc)

try:
    import uiautomation as auto
except Exception as e:  # pragma: no cover
    print("缺少依赖 uiautomation：", e)
    raise

APP_DIR = Path(__file__).resolve().parent

# 版本号：用户来报问题时第一句总是「你用的哪个版本」，得有个地方能自报。
# 常量放在模块顶层（而不是 pyproject/打包元数据里），是为了让源码运行、
# 冻结成 exe 两种方式读到的都是同一个值。
APP_DISPLAY_NAME = "TeamsSub"
APP_VERSION = "0.1.0"

CAPTION_DIR = Path(os.environ.get("TEAMSSUB_DATA_DIR", APP_DIR)) / "captions"
CAPTION_DIR.mkdir(parents=True, exist_ok=True)


def data_dir():
    """数据根目录：字幕、配置、日志都在它下面。

    每次现读环境变量，和 _error_path() 保持同一套隔离语义——测试把
    TEAMSSUB_DATA_DIR 指到临时目录后，这里也必须跟着走。
    """
    return Path(os.environ.get("TEAMSSUB_DATA_DIR", APP_DIR))


# ----------------------------------------------------------------------------
# 后台线程里的 Tk 变量回收
# ----------------------------------------------------------------------------
# Tk 的 Variable.__del__ 会去调 Tcl。后台线程（Markdown 保存、翻译请求）
# 分配对象时会顺带触发垃圾回收，正好收掉界面重建剩下的 StringVar /
# BooleanVar —— 在那种线程里调 Tcl 就抛 "main thread is not in main loop"，
# 表现为 stderr 里一串 "Exception ignored ... Traceback"。
#
# 这些异常不影响功能（只是少删一个 PY_VAR 名字），但会污染日志，也让
# 自动化测试误判成「有堆栈 = 崩过」。非主线程直接跳过清理即可。
_MAIN_THREAD = threading.main_thread()


def _install_variable_guard():
    var_cls = getattr(tk, "Variable", None)
    original = getattr(var_cls, "__del__", None) if var_cls else None
    if original is None or getattr(original, "_teamsub_guard", False):
        return

    def safe_del(self):
        if threading.current_thread() is not _MAIN_THREAD:
            return
        try:
            original(self)
        except Exception:
            pass

    safe_del._teamsub_guard = True
    var_cls.__del__ = safe_del


_install_variable_guard()


# ----------------------------------------------------------------------------
# 退出预算
# ----------------------------------------------------------------------------
# 关窗时要先把自动保存收尾。正常情况写线程几十毫秒就退出了（主线程同步等
# 一小会儿即可，行为等同于同步关闭，测试也最省心）；可一旦它卡在 os.fsync /
# os.replace（慢盘、杀软扫描、网络盘），同步等下去就是「窗口假死 30 秒然后
# 弹保存超时」——在用户眼里就是程序坏了。
#
# 所以分三段：先在主线程同步等 CLOSE_SYNC_BUDGET；没完就转后台轮询，总预算
# CLOSE_ASYNC_BUDGET 用完再让用户三选一。两个值都是模块级的，测试可以调小。
CLOSE_SYNC_BUDGET = 0.5      # 主线程上最多同步等这么久
CLOSE_ASYNC_BUDGET = 7.0     # 转后台等待后的总预算，超了再问用户
CLOSE_POLL_MS = 80           # 后台等待时的轮询间隔


# ----------------------------------------------------------------------------
# 崩溃现场
# ----------------------------------------------------------------------------
# pythonw 没有控制台，任何未捕获异常都表现为「窗口突然消失、进程也没了」，
# 用户看到的只是空白桌面和握不住的实例锁。用 GUI 启动时把栈写进
# cache/error.log：下次再遇到静默退出，能直接看到死在哪一行，而不是只能猜。
ERROR_LOG = APP_DIR / "cache" / "error.log"

# 日志轮转：这个文件是「一直在」的，但一个跑几个月不重启的进程会把它攒成几十
# MB —— 悄悄吃掉用户磁盘，真出问题时还得先翻过一堆历史才能看到这次。留 1 份
# 历史就够对比「这次」和「上次」，再老的没有诊断价值；两份封顶约 2 MB，比任何
# 用户会去手动清理的规模都小。刻意不用 logging.handlers：为了封个顶引一套配置
# 体系不划算，几行 os.replace 足够。
ERROR_LOG_MAX_BYTES = 1024 * 1024


def _rotate_error_log(path):
    """超过上限就把 error.log 滚成 error.log.1（只留 1 份）。

    尽力而为：任何失败都咽掉，让调用方继续往当前文件追加。最典型的失败是
    error.log.1 正被别的进程读着/占用（Windows 上 os.replace 会 PermissionError），
    其次是磁盘满。这时宁可日志无上限地再长一会儿，也不能把正在出错的主流程
    再崩一次——日志是排障工具，工具本身不该成为新的故障源。
    """
    try:
        if path.stat().st_size < ERROR_LOG_MAX_BYTES:
            return
        backup = path.with_name(path.name + ".1")
        os.replace(path, backup)          # 备份已存在时会被整体覆盖，天然只留 1 份
    except Exception:
        pass


# 当前会话登记：正常退出时删除。它存在 = 上次没走完关闭流程（崩溃、断电、
# 被任务管理器结束）。字幕本身已逐条落盘，缺的只是「让人知道去哪找」。
SESSION_STAMP = APP_DIR / "cache" / "session.json"


def session_stamp_path():
    if os.environ.get("TEAMSSUB_DATA_DIR"):
        return Path(os.environ["TEAMSSUB_DATA_DIR"]) / "cache" / "session.json"
    return SESSION_STAMP


def _error_path():
    """测试会把 TEAMSSUB_DATA_DIR 指到临时目录，日志也跟着走。"""
    if ERROR_LOG.parent == APP_DIR / "cache" and \
            os.environ.get("TEAMSSUB_DATA_DIR"):
        return Path(os.environ["TEAMSSUB_DATA_DIR"]) / "cache" / "error.log"
    return ERROR_LOG


def log_exception(exc_type, exc, tb, note=""):
    try:
        path = _error_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_error_log(path)
        with path.open("a", encoding="utf-8") as f:
            f.write("\n===== %s %s =====\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), note).replace("  =", " ="))
            f.write("thread: %s\n" % threading.current_thread().name)
            traceback.print_exception(exc_type, exc, tb, file=f)
    except Exception:
        pass


def log_note(text):
    """写一行事件记录（用于区分「正常退出」和「崩溃」）。"""
    try:
        path = _error_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_error_log(path)
        with path.open("a", encoding="utf-8") as f:
            f.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except Exception:
        pass


def install_crash_log(win=None):
    """挂上 GUI/后台线程/Tk 回调三处未捕获异常记录。"""
    def hook(exc_type, exc, tb):
        log_exception(exc_type, exc, tb, "未捕获异常")
    sys.excepthook = hook

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        log_exception(args.exc_type, args.exc_value, args.exc_traceback,
                      "线程 %s 异常" % getattr(args.thread, "name", "?"))
    threading.excepthook = thread_hook
    if win is not None:
        win.report_callback_exception = \
            lambda et, ev, tb: log_exception(et, ev, tb, "Tk 回调异常")

GROUP_NAMES = ["实时字幕", "实时辅助字幕", "Live captions", "字幕"]
UI_NOISE = re.compile(
    r"^(提供实时辅助字幕反馈|隐藏实时字幕|实时字幕，|字幕设置|显示字幕|"
    r"打开实时字幕|翻译为|关闭原文|中号字体|展开字幕|收起字幕)"
)
NAME_HINT = re.compile(r"(\([A-ZĐ0-9][A-ZĐ0-9\-]{3,}\)\s*$)|(\s_\s*\[)")
OBJ_CHAR = "\ufffc"

# ----------------------------------------------------------------------------
# 文本工具
# ----------------------------------------------------------------------------
def clean(s: str) -> str:
    if not s:
        return ""
    return s.replace("\u200b", "").replace(OBJ_CHAR, "").strip()


def simplify_name(raw: str) -> str:
    """Gundadze Teimuraz _ [VINFAST] Chief Engineer... -> Gundadze Teimuraz"""
    n = clean(raw)
    n = re.split(r"\s_\s*\[", n)[0]
    n = re.sub(r"\s*\([A-ZĐ0-9][A-ZĐ0-9\-]{3,}\)\s*$", "", n)
    n = re.sub(r"\s{2,}", " ", n).strip(" -_·")
    return n or clean(raw)


def initials(name: str) -> str:
    parts = [p for p in re.split(r"[\s\.\-]+", name) if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def shorten(s: str, n: int = 20) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ----------------------------------------------------------------------------
# 采集引擎：UIA 轮询 + 增量对齐
# ----------------------------------------------------------------------------
def _find_teams():
    """Teams 模式：找 TeamsWebView / 标题含 Microsoft Teams 的窗口 + 实时字幕组。

    返回 (group, window, meeting_title)；没找到返回 (None, None, "")。
    新增会议模式（如飞书）时按同签名加一个 _find_xxx，再登记进 FINDERS。
    """
    try:
        root = auto.GetRootControl()
        cands = []
        for w in root.GetChildren():
            try:
                nm = w.Name or ""
                cls = w.ClassName or ""
                if cls == "TeamsWebView" or "Microsoft Teams" in nm:
                    cands.append(w)
            except Exception:
                continue
        for w in cands:
            for gname in GROUP_NAMES:
                try:
                    g = w.GroupControl(Name=gname, searchDepth=40)
                    if g.Exists(0.15):
                        title = clean(w.Name)
                        title = re.sub(r"\s*\|\s*Microsoft\s*Teams\s*$", "",
                                       title, flags=re.I).strip()
                        return g, w, title
                except Exception:
                    continue
    except Exception:
        pass
    return None, None, ""


def _is_caption_label(ct: str, cls: str) -> bool:
    """飞书字幕正文节点。类名随客户端版本变化，实测出现过：

        RichLabel                   旧版独立实时字幕窗
        SubtitleRichLabel           双语句对照
        SubtitleSelectableRichLabel 可选中字幕（2026-10 实测）

    写死 `endswith("SubtitleRichLabel")` 会漏掉 Selectable 版本——那正是
    「飞书会议开着、字幕窗也有内容，但一条都采不到」的直接原因。判据只要求
    以 RichLabel 结尾，容器本身已经是字幕列表，不会误收其它文本。
    """
    return ct in ("TextControl", "EditControl") and cls.endswith("RichLabel")


_FEISHU_EXE = ("feishu", "lark")     # 进程映像名关键字（国内版 / 国际版）
_FEISHU_HINT = {"hwnd": 0, "ctrl": None, "win": None}   # 上次命中的容器


def _window_exe_name(hwnd) -> str:
    """窗口所属进程的映像名（小写、不含路径）；取不到返回空串。"""
    try:
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD))
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32.OpenProcess(0x1000, False, pid.value)    # QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            size = wintypes.DWORD(512)
            buf = ctypes.create_unicode_buffer(size.value)
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return ""
            return buf.value.rsplit("\\", 1)[-1].lower()
        finally:
            k32.CloseHandle(h)
    except Exception:
        return ""


def _find_feishu():
    """飞书模式：优先独立实时字幕窗口，其次会议窗口内嵌字幕历史面板。

    飞书会议是 Chromium(CEF) 架构，字幕节点类名（实测）：
      独立字幕窗  BilingualSubtitleRealtimeWindowBase → BilingualSubtitleRealtimeListView
      条目        BilingualSubtitleRealtimeListItem（内含 IronNameButton + RichLabel）
      内嵌面板    BilingualSubtitleHistoryPanelView（扁平序列：IronAvatar +
                  IronNameButton + *SubtitleRichLabel）
    会议小窗模式（Meeting mini window）没有字幕节点，须先「恢复窗口」。

    性能是这条路径的关键约束：本机同时开着 40+ 个 Chrome_WidgetWin_0 窗口
    （浏览器、Electron 应用都会留下这种窗口），逐个用 4 秒超时去搜会把发现拖到 5～7 秒，
    期间界面一直停在「正在查找」。所以先按进程名把候选压到飞书自己的窗口，
    再用短超时两步定位（先容器、后列表），最后缓存命中的容器。
    """
    try:
        root = auto.GetRootControl()
        wins = []
        for w in root.GetChildren():
            try:
                if (w.ClassName or "") == "Chrome_WidgetWin_0":
                    wins.append(w)
            except Exception:
                continue
        feishu = []
        for w in wins:
            try:
                exe = _window_exe_name(w.NativeWindowHandle)
                if any(h in exe for h in _FEISHU_EXE):
                    feishu.append(w)
            except Exception:
                continue
        cands = feishu or wins      # 进程名取不到时退回全部：宁可慢，不误判
        last_hwnd = _FEISHU_HINT.get("hwnd") or 0
        if last_hwnd:               # 上次命中的窗口排最前，重复发现一步到位
            cands.sort(key=lambda w: 0 if w.NativeWindowHandle == last_hwnd else 1)

        hit = _FEISHU_HINT.get("ctrl")
        if hit is not None:         # 同一容器还在用：重复发现近乎零成本
            try:
                if hit.Exists(0.15):
                    return hit, _FEISHU_HINT.get("win"), "飞书会议"
            except Exception:
                pass
            _FEISHU_HINT["ctrl"] = None

        # 每个候选的超时要短：飞书会同时开多个 Chromium 窗口，超时定长了
        # 冷启动光「等待不存在的节点」就要好几秒。
        for w in cands:
            try:
                rt = w.PaneControl(ClassName="BilingualSubtitleRealtimeWindowBase",
                                   searchDepth=12)
                if not rt.Exists(0.4, 0.15):
                    continue
                lv = rt.PaneControl(ClassName="BilingualSubtitleRealtimeListView",
                                    searchDepth=8)
                if lv.Exists(0.4, 0.15):
                    _FEISHU_HINT.update(hwnd=w.NativeWindowHandle, ctrl=lv, win=w)
                    return lv, w, "飞书会议"
            except Exception:
                continue
        for w in cands:
            try:
                panel = w.PaneControl(ClassName="BilingualSubtitleHistoryPanelView",
                                      searchDepth=16)
                if panel.Exists(0.45, 0.15):
                    _FEISHU_HINT.update(hwnd=w.NativeWindowHandle, ctrl=panel, win=w)
                    return panel, w, "飞书会议"
            except Exception:
                continue
    except Exception:
        pass
    return None, None, ""


# 模式 key → 窗口发现函数。UI 的 modes.py 管「有哪些模式」，这里管「每种模式怎么找窗口」。
FINDERS = {"teams": _find_teams, "feishu": _find_feishu}


class CaptionReader(threading.Thread):
    """后台线程：轮询当前会议模式的「实时字幕」组，把增量记录推到队列。"""

    def __init__(self, out_q: queue.Queue, interval: float = 0.4,
                 mode: str = "teams"):
        super().__init__(daemon=True)
        self.q = out_q
        self.interval = interval
        self.mode = mode                  # 会议模式 key（见 modes.py）
        self.stop_evt = threading.Event()
        self.paused = False
        self.records = []        # [{name, text, ts}] 已确认 + 进行中
        self.open_pairs = []     # 上一次轮询的 (name, raw, text) 列表
        self.open_records = []   # 与上一帧条目对应的记录，而非只有最后一句
        self.generation = uuid.uuid4().hex
        self.recent_keys = []    # 已消费的 (姓名, 文本) 指纹，用于防重
        self.last_name = None    # 上一帧最后的说话人，用于兜底继承
        self.known_raw = set()
        self.known_norm = set()
        self.group = None
        self.window = None
        self.meeting_title = ""
        self.empty_streak = 0
        self._feishu_full = True  # 飞书：下一轮读全量（换容器后要重新拿历史）
        self.first_pass = True   # 首次拉到的是会议历史字幕，真实时间不可知
        self.status = "init"

    # -- 发现字幕控件 ---------------------------------------------------
    def discover(self):
        find = FINDERS.get(self.mode) or _find_teams
        try:
            g, w, title = find()
        except Exception:
            g = w = None
            title = ""
        if g is not None:
            if g is not self.group:          # 换了容器 → 下一轮重新读全量历史
                self._feishu_full = True
            self.group, self.window, self.meeting_title = g, w, title
            return True
        self.group = None
        self._feishu_full = True
        return False

    # -- 单次读取 -------------------------------------------------------
    def read_nodes(self):
        nodes = []

        def walk(node, depth):
            if depth > 12:
                return
            try:
                kids = node.GetChildren()
            except Exception:
                return
            for ch in kids:
                try:
                    ct = ch.ControlTypeName
                    if ct == "TextControl":
                        nm = clean(ch.Name)
                        if nm and not UI_NOISE.match(nm):
                            # 优先利用客户端暴露的说话人语义，不依赖姓名格式。
                            try:
                                role = f"{ch.AutomationId} {ch.ClassName}".lower()
                                if any(h in role for h in ('speaker', 'author', 'displayname', 'display-name')):
                                    self.known_raw.add(nm)
                                    self.known_norm.add(simplify_name(nm))
                            except Exception:
                                pass
                            nodes.append(nm)
                    elif ct in ("GroupControl", "CustomControl", "PaneControl",
                                "ListControl", "ListItemControl"):
                        walk(ch, depth + 1)
                except Exception:
                    continue

        walk(self.group, 0)
        return nodes

    def is_name(self, t: str) -> bool:
        if t in self.known_raw or simplify_name(t) in self.known_norm:
            return True
        return bool(NAME_HINT.search(t))

    def read_feishu(self):
        """飞书字幕是结构化条目：直接产出 (规范名, 原名, 文本)，
        不走 Teams 的「文本节点猜姓名」启发式。"""
        pairs, last = [], ""
        try:
            kind = self.group.ClassName or ""
        except Exception:
            return pairs

        nodes = self._feishu_nodes()
        if not nodes:
            return pairs

        if "RealtimeListView" in kind:
            for item in nodes:
                try:
                    if "RealtimeListItem" not in (item.ClassName or ""):
                        continue
                except Exception:
                    continue
                name, text = self._feishu_entry(item)
                if text:
                    nm = name or last or "未知发言人"
                    last = name or last
                    pairs.append((simplify_name(nm), nm, text))
        else:   # BilingualSubtitleHistoryPanelView：扁平序列
            # last 用 None 起步：尾部窗口可能从某条字幕的正文中间切入，此时
            # 说话人按钮在窗口之外。若把这种条目当成「未知发言人」收下，它会
            # 因为说话人不同而无法与上轮的记录对齐，于是同一句被重复入库。
            # 宁可丢掉这条已被记录过的残句，也不要污染记录。
            last = None
            for ch in nodes:
                try:
                    cls, ct = ch.ClassName or "", ch.ControlTypeName
                    if ct == "ButtonControl" and cls == "IronNameButton":
                        nm = clean(ch.Name)
                        if nm:
                            last = nm        # 空名字按钮（内容重绘）不能覆盖说话人
                        continue
                    if _is_caption_label(ct, cls):
                        text = clean(ch.Name)
                        if text and last:
                            pairs.append((simplify_name(last), last, text))
                except Exception:
                    continue
        return pairs

    # 每轮只读尾部这么多子节点：约 8 条字幕，足够覆盖一轮内 1～2 条新增
    FEISHU_TAIL = 32

    @staticmethod
    def _sibling_tail(parent, limit):
        """反向读兄弟链，取末尾 limit 个子节点。

        Chromium 的 GetChildren() 一次枚举全部子节点：飞书历史面板有 200+
        个，实测 1.4s；反向兄弟链取 32 个约 0.35s。字幕是追加的，只看尾部
        就够，不必每轮把整段历史重新枚举一遍。
        """
        out, guard = [], 0
        try:
            cur = parent.GetLastChildControl()
        except Exception:
            return []
        while cur is not None and len(out) < limit and guard <= limit + 8:
            guard += 1
            out.append(cur)
            try:
                cur = cur.GetPreviousSiblingControl()
            except Exception:
                break
        out.reverse()
        return out

    def _feishu_nodes(self):
        """容器子节点：切换容器后首轮全量（拿会议历史），之后只取尾部窗口。"""
        if self.group is None:
            return []
        if getattr(self, "_feishu_full", True):
            try:
                nodes = self.group.GetChildren()
            except Exception:
                return []
            self._feishu_full = False
            return nodes
        nodes = self._sibling_tail(self.group, self.FEISHU_TAIL)
        if not nodes:                    # 兄弟链不可用 → 下一轮回退全量
            self._feishu_full = True
        return nodes

    def _feishu_entry(self, item):
        """条目 → (name, text)：第一个非空 IronNameButton + 字幕文本。

        名字按钮可能在 SubtitleNameView 内或条目直属（飞书两种排布都有），
        所以取条目内「第一个名字非空」的 IronNameButton；文本在
        ScrollView → ContentItem → RichLabel，类名可能是 RichLabel 或
        *RichLabel（见 _is_caption_label）。BFS 探查并限制深度防走全树。
        """
        name, text = "", ""
        queue_, seen = [item], 0
        while queue_ and seen < 60:
            node = queue_.pop(0)
            try:
                kids = node.GetChildren()
            except Exception:
                continue
            for ch in kids:
                seen += 1
                try:
                    cls = ch.ClassName or ""
                    ct = ch.ControlTypeName
                    if not name and ct == "ButtonControl" \
                            and cls == "IronNameButton":
                        name = clean(ch.Name)
                    elif not text and _is_caption_label(ct, cls):
                        text = clean(ch.Name)
                except Exception:
                    continue
                if not name or not text:
                    queue_.append(ch)
        return name, text

    def to_pairs(self, nodes):
        pairs, cur = [], self.last_name
        # 无结构元数据的旧客户端：仅在完整的「姓名/字幕」交替序列中补认普通姓名。
        alternating = (len(nodes) >= 2 and len(nodes) % 2 == 0
                       and all(self._looks_like_name(nodes[i]) for i in range(0, len(nodes), 2))
                       and any(not self._looks_like_name(nodes[i]) for i in range(1, len(nodes), 2)))
        for i, t in enumerate(nodes):
            if self.is_name(t) or (alternating and i % 2 == 0):
                cur = t
                self.last_name = t
                self.known_raw.add(t)
                self.known_norm.add(simplify_name(t))
            else:
                pairs.append((simplify_name(cur) if cur else "未知发言人", cur or "", t))
        return pairs

    @staticmethod
    def _looks_like_name(text):
        words = text.strip().split()
        return (0 < len(words) <= 5 and len(text) <= 70
                and all(w.replace('-', '').replace("'", '').isalpha() for w in words)
                and all(not w.isascii() or w[0].isupper() for w in words))

    # -- 增量对齐 -------------------------------------------------------
    @staticmethod
    def _key(p):
        return (p[0], p[2])          # (规范化姓名, 文本)

    @staticmethod
    def _same_utterance(a: str, b: str) -> bool:
        """判断 b 是 a 的修订版（ASR 会反复改写同一句）。"""
        la, lb = a.lower().strip(), b.lower().strip()
        if la == lb:
            return True
        if la.startswith(lb) or lb.startswith(la):
            return True
        if not la or not lb:
            return False
        # 忽略标点差异的词级前缀：'ok.' -> 'ok, they say into their bag.'
        ta = re.findall(r"[\w']+", la)
        tb = re.findall(r"[\w']+", lb)
        if ta and tb:
            short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if long_[: len(short)] == short:
                return True
        # 长句会被 ASR 整句重排：前缀失效，改用词集合 Jaccard
        if max(len(la), len(lb)) >= 40:
            sa = set(re.findall(r"[\w\u4e00-\u9fff]+", la))
            sb = set(re.findall(r"[\w\u4e00-\u9fff]+", lb))
            if sa and sb:
                jac = len(sa & sb) / len(sa | sb)
                if jac >= 0.60:
                    return True
        return SequenceMatcher(None, la, lb).ratio() >= 0.72

    def merge(self, cur):
        """对齐每个窗口条目：独立插入保持独立，替换才允许更新旧记录。"""
        events = []
        if not cur:
            return events
        old = self.open_pairs
        prior = self.open_records if len(self.open_records) == len(old) else []
        aligned = [None] * len(cur)
        matcher = SequenceMatcher(None, [self._key(p) for p in old],
                                  [self._key(p) for p in cur], autojunk=False)
        for tag, a, b, c, d in matcher.get_opcodes():
            if tag == 'equal' and prior:
                aligned[c:d] = prior[a:b]
            elif tag == 'replace' and prior:
                # 仅在被替换的窗口槽位中找修订；不能跨越新增条目的边界合并。
                available = list(range(a, b))
                for j in range(c, d):
                    for i in list(available):
                        if old[i][0] == cur[j][0] and self._same_utterance(old[i][2], cur[j][2]):
                            aligned[j] = prior[i]
                            available.remove(i)
                            break
        for i, (name, raw, text) in enumerate(cur):
            now = datetime.now()
            rec = aligned[i]
            if rec is not None:
                if rec['text'] != text:
                    rec.update(text=text, raw=raw or rec['raw'], backlog=False)
                    rec['revision'] = rec.get('revision', 0) + 1
                    events.append(('update', rec))
                continue
            rec = {"name": name, "raw": raw, "text": text, "ts": now,
                   "backlog": self.first_pass, "id": uuid.uuid4().hex, "revision": 0}
            aligned[i] = rec
            self.records.append(rec)
            events.append(("new", rec))
        self.open_pairs, self.open_records = list(cur), aligned
        if self.first_pass and cur:
            self.first_pass = False
        return events

    # -- 主循环 ---------------------------------------------------------
    def _emit(self, event):
        if not self.stop_evt.is_set() and not self.paused:
            kind, value, *rest = event
            if kind in ('new', 'update'):
                value = dict(value)   # 采集线程不能继续改写 UI 已接收的字典
            self.q.put(('capture', (self.generation, (kind, value, *rest))))

    def run(self):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        while not self.stop_evt.is_set():
            t0 = time.time()
            try:
                if self.paused:
                    self.status = "paused"
                    time.sleep(0.25)
                    continue
                if self.group is None:
                    if not self.discover():
                        self.status = "no_group"
                        self._emit(("status", "no_group", ""))
                        self.stop_evt.wait(1.2)
                        continue
                    self._emit(("meeting", self.meeting_title))
                    self.status = "ok"
                nodes = self.read_nodes() if self.mode != "feishu" else None
                if self.mode == "feishu":
                    cur = self.read_feishu()
                    if not cur:
                        self.empty_streak += 1
                        if self.empty_streak >= 5:
                            self.group = None      # 会议结束/面板重绘 -> 重新发现
                            self.empty_streak = 0
                        self._emit(("status", "idle", ""))
                    else:
                        self.empty_streak = 0
                        for ev in self.merge(cur):
                            self._emit(ev)
                        self._emit(("status", "ok", ""))
                elif not nodes:
                    self.empty_streak += 1
                    if self.empty_streak >= 5:
                        self.group = None      # 会议结束/面板重绘 -> 重新发现
                        self.empty_streak = 0
                    self._emit(("status", "idle", ""))
                else:
                    self.empty_streak = 0
                    for ev in self.merge(self.to_pairs(nodes)):
                        self._emit(ev)
                    self._emit(("status", "ok", ""))
            except Exception:
                self.group = None
                self._emit(("status", "error", traceback.format_exc(limit=1)))
            spent = time.time() - t0
            self.stop_evt.wait(max(0.05, self.interval - spent))

    def stop(self):
        self.stop_evt.set()


# ----------------------------------------------------------------------------
# 主界面
# ----------------------------------------------------------------------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._async_storage = True
        self.display_start = 0
        self.display_mode = "both"
        self.reading_size = 12
        self.density = "comfort"
        self._export_jobs = []
        self.records = []
        self.view_indices = []
        self.view_pos = {}
        self.speaker_filter = None
        self.auto_scroll = True
        self.simplify = True
        self.save_path = None
        self.save_store = None
        self.color_map = {}
        self.pinned = False
        self.paused = False
        self.hidden_until = 0.0
        self._status_txt, self._status_color = "连接中", UK.C.T3
        self.float_bar = None            # 悬浮字幕窗实例
        self._float_sig = None            # 最近一次同步的内容指纹
        self._hidden_since = 0.0          # 「一个窗口都没有」的起始时刻（看门狗用）
        self._closing = False             # 已确认退出：看门狗/采集回调全部停手
        self._quit_pending = False        # 退出流程进行中，但还可能被取消
        self._tray_poll_on = False        # 托盘轮询链是否已排队（防重复排程）
        self._close_poll = None           # 等保存收尾的后台轮询 job id
        self._was_paused = False          # 关闭前的暂停状态，取消退出时还原
        self._min_switching = False       # 正在执行「最小化切悬浮条」，防 <Unmap> 递归
        self._was_iconic = False          # 主窗口当前的最小化是自己切的（供还原时收条）
        self._stamp_path = None           # 已登记的会话文件（避免每条字幕都写盘）
        self.tray = None                  # 托盘图标实例（main 里挂载）
        self._tray_hidden = False         # 主窗口是用户主动收进托盘的
        self._tray_hinted = False         # 「已收进托盘」气泡只弹一次

        self.q = queue.Queue()

        # ---- 翻译子系统 ----
        self.tcfg = TransConfig.load()
        self.reading_size = getattr(self.tcfg, "reading_size", 12)
        self.density = getattr(self.tcfg, "reading_density", "comfort")
        self.svc = None
        self.pending_tr = {}          # rec_id -> 最近一次文本变更时间
        self.tr_requests = {}         # 不复用的请求 token -> (记录对象, 原文快照)
        self.settle_sec = max(0.2, self.tcfg.settle_ms / 1000.0)
        self.trans_on = False
        self.tr_stats_txt = ""
        self.qa_hits = 0
        self.float_on = bool(getattr(self.tcfg, "float_bar", False))

        # ---- 会议模式（采集引擎按模式分派，见 modes.py / FINDERS）----
        self.mode = MD.resolve(getattr(self.tcfg, "meeting_mode", ""))["key"]
        self.tcfg.meeting_mode = self.mode
        self._meeting_txt = f"正在查找 {MD.get(self.mode)['name']}…"

        # 采集线程要在模式确定之后创建
        self.reader = CaptionReader(self.q, mode=self.mode)
        self.reader.simplify = True

        UK.set_theme(getattr(self.tcfg, "theme", "light"))
        self._build_ui()
        self.reader.start()
        if self.tcfg.enabled and self._has_credentials():
            self.start_translation(silent=True)
        if self.float_on:
            self.float_bar = floatbar.FloatBar(self)
            self.float_sync(force=True)
        self.root.bind("<Unmap>", self._on_minimize)
        self.root.bind("<Map>", self._on_restore)
        self.root.after(100, self.pump)
        # 上次若没收尾（崩溃/断电），启动后把那份记录指给用户
        self.root.after(1200, self._check_recovery)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- 窗口可见性 ----------------
    def _win_state(self, win):
        """Toplevel/Tk 的窗口状态：normal / iconic / withdrawn。"""
        try:
            if win is None or not win.winfo_exists():
                return "withdrawn"
            return win.state()
        except Exception:
            return "withdrawn"

    def _main_visible(self) -> bool:
        return self._win_state(self.root) == "normal"

    def _float_visible(self) -> bool:
        bar = getattr(self, "float_bar", None)
        return bool(bar) and self._win_state(bar.win) == "normal"

    def _visible_windows(self) -> int:
        """当前真正显示在屏幕上的窗口数（主窗口 + 悬浮条）。"""
        return int(self._main_visible()) + int(self._float_visible())

    def _own_window_event(self, e):
        """这个 <Map>/<Unmap> 是不是主窗口自己的？

        Tk 的绑定按 bindtags 冒泡：每个子控件的 bindtags 里都有顶层窗口的
        路径（"."），所以 ``root.bind("<Unmap>")`` 会收到「任意子孙控件被
        unmap」的事件。推层界面（sheet）每次重建都会 destroy 一批控件，
        于是点一下「返回」就等于收到一串 <Unmap> —— 旧代码把它当成用户
        最小化，直接把主窗口 withdraw 进托盘。表现就是「点返回，窗口没了」。
        只有事件源确实是主窗口，才允许走最小化逻辑。
        """
        if e is None:
            return True                 # 无事件（程序内部调用）不拦
        return getattr(e, "widget", None) is self.root

    def _on_minimize(self, e=None):
        """最小化时收进托盘（挂了托盘）或切到悬浮条（没挂托盘）。

        两处顺序与方式都不能含糊：

        1. 先显示悬浮条、确认它真的可见，再收起主窗口。反过来写的话，一旦
           悬浮条创建失败，主窗口已经没了，进程就退化成「活着但没有任何
           窗口」的隐形实例，继续握着单实例锁。
        2. 挂了托盘时用 ``withdraw``：任务栏本来就没有它的按钮，iconify 只会
           留下一个既不在任务栏、也点不回来的最小化窗口。托盘图标是此时唯一
           入口，所以只有托盘真的挂上了才允许走这条路（见 attach_tray）。
           没挂托盘时必须退回 ``iconify``，把任务栏按钮留给用户。
        """
        if getattr(self, "_closing", False):
            return
        if not self._own_window_event(e):
            return                      # 子孙控件被销毁/隐藏，不是最小化
        if getattr(self, "_min_switching", False):
            return                      # 自身 iconify/withdraw 触发的 <Unmap>
        if getattr(self, "_tray_hidden", False):
            return                      # 已经在托盘里了
        tray_mode = getattr(self, "tray", None) is not None
        if not tray_mode and not getattr(self.tcfg, "float_on_min", False):
            return
        self._min_switching = True
        try:
            if getattr(self.tcfg, "float_on_min", False):
                self.set_float(True)
                if self.float_bar is not None:
                    self.float_bar.win.deiconify()
                    self.float_bar.win.lift()
                    if self._float_visible():
                        self.float_sync(force=True)
            self._was_iconic = True     # 供 _on_restore / _reconcile 判断
            if tray_mode:
                self.root.withdraw()
                self._tray_hidden = True
                self._tray_hint()
            else:
                self.root.iconify()
        except Exception:
            self.flash("悬浮字幕窗打开失败，已保留主窗口")
            if self._win_state(self.root) != "normal":
                self.root.deiconify()   # 绝不留下零窗口
        finally:
            self._min_switching = False

    def _tray_hint(self):
        """第一次收进托盘时提示一句，避免用户以为程序退出了。"""
        if self._tray_hinted or self.tray is None:
            return
        self._tray_hinted = True
        try:
            self.tray.balloon(
                "TeamsSub 已收进托盘",
                "任务栏不再占位。点击右下角图标（或双击）随时把窗口叫回来。")
        except Exception:
            pass

    # ---------------- 系统托盘 ----------------
    def attach_tray(self, tray):
        """挂托盘图标，并把主窗口从任务栏摘掉。

        顺序不可颠倒：先确认托盘可用，再隐藏任务栏按钮。反过来的话，托盘一旦
        没挂上，用户就同时失去任务栏和托盘两个入口——那正是「窗口自己消失」
        的老问题。托盘不可用时宁可留着任务栏按钮。
        """
        self.tray = tray
        try:
            self.tray.set_tip("TeamsSub · %s" % (self._meeting_txt or "实时字幕"))
        except Exception:
            pass
        self._hide_taskbar_button()
        self._schedule_tray_poll()

    def _hide_taskbar_button(self):
        """加 WS_EX_TOOLWINDOW、去 WS_EX_APPWINDOW，让窗口不占任务栏。"""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            u.GetAncestor.restype = wintypes.HWND
            u.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            u.GetWindowLongW.restype = ctypes.c_long
            u.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int,
                                         ctypes.c_long)
            u.SetWindowLongW.restype = ctypes.c_long
            hwnd = u.GetAncestor(self.root.winfo_id(), 2)     # GA_ROOT
            if not hwnd:
                return
            ex = u.GetWindowLongW(hwnd, -20)                  # GWL_EXSTYLE
            u.SetWindowLongW(hwnd, -20, (ex | 0x00000080) & ~0x00040000)
            # 扩展样式改完要重新 show 一次，任务栏按钮才会跟着刷新
            self._min_switching = True
            try:
                self.root.withdraw()
                self.root.deiconify()
            finally:
                self._min_switching = False
        except Exception:
            pass

    def _sync_tray_tip(self):
        """托盘悬停文本跟随当前会议（内容不变时不重复调 Win32）。"""
        tray = getattr(self, "tray", None)
        if tray is None:
            return
        txt = "TeamsSub · %s" % (self._meeting_txt or "实时字幕")
        if getattr(self, "_tray_tip", None) == txt:
            return
        self._tray_tip = txt
        try:
            tray.set_tip(txt)
        except Exception:
            pass

    def _schedule_tray_poll(self, delay=150):
        """保证托盘轮询链有且只有一条，且永远不断。

        旧代码的 quit 分支在 on_close() 之后直接 ``return``，跳过了末尾的
        ``after`` —— 只要那次关闭被用户取消，轮询就永久停摆：图标还在，
        但点菜单任何一项都没反应，只能去任务管理器杀进程。
        """
        if self.tray is None or getattr(self, "_closing", False):
            return
        if getattr(self, "_tray_poll_on", False):
            return                      # 已排程，别重复加链
        self._tray_poll_on = True
        try:
            self.root.after(delay, self._poll_tray)
        except tk.TclError:
            self._tray_poll_on = False  # 窗口已销毁：链子到此为止

    def _poll_tray(self):
        """把托盘线程收到的事件搬到 Tk 主线程执行（约 150ms 一次）。"""
        self._tray_poll_on = False
        tray = getattr(self, "tray", None)
        if tray is None or getattr(self, "_closing", False):
            return
        try:
            while True:
                cmd = tray.events.get_nowait()
                if cmd == "show":
                    self.restore_main()
                elif cmd == "float":
                    self.toggle_float()
                    try:
                        self.var_float.set(self.float_on)
                    except Exception:
                        pass
                elif cmd == "quit":
                    # 不 return：请求退出 ≠ 一定退出（保存超时可能被用户取消、
                    # 用户也可能点「取消关闭」）。轮询必须继续活着。
                    self.on_close()
        except queue.Empty:
            pass
        except Exception:
            pass
        self._schedule_tray_poll()      # 无论刚处理了什么，都要把链子接上

    def restore_main(self):
        """从托盘/悬浮条回到主界面。"""
        self._tray_hidden = False
        self._was_iconic = False
        if self.float_bar is not None and self.float_on:
            self.float_bar.win.withdraw()
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def _on_restore(self, e=None):
        """从最小化还原时收起悬浮条（启动时的普通 Map 事件不打扰）。"""
        if not self._own_window_event(e):
            return                      # 子控件被 map，不代表主窗口还原
        if getattr(self, "_min_switching", False):
            return
        if not getattr(self, "_was_iconic", False):
            return
        self._was_iconic = False
        if self.float_bar is not None and self.float_on:
            self.float_bar.win.withdraw()

    # ---------------- UI ----------------
    def _build_ui(self):
        import frontend_shell
        frontend_shell.build(self)

    # ---------------- 内推层（设置 / 帮助 / 确认） ----------------
    def _show_sheet(self, title, builder, push=False):
        """在内容区推入一层。push=True 时保留当前层，返回时回到它。"""
        if push and getattr(self, "_sheet_open", False):
            self._sheet_stack.append((self._sheet_title, self._sheet_builder))
        self._sheet_title, self._sheet_builder = title, builder
        self._sync_nav(title)
        self._sheet_open = True
        # 直接把层放到位，不做位移动画：整层上下滑 8px 时，最上方的标题
        # 跳动最明显，看起来像「点开时标题闪了一下」。层切换本来就足够
        # 明确（旧层被换掉、导航高亮变化），位移动画只带来视觉噪声。
        self.sheet_layer.place(relx=0, rely=0, relwidth=1, relheight=1, y=0)
        self.sheet_layer.lift()
        self._render_sheet(title, builder)


    def _render_sheet(self, title, builder):
        layer = self.sheet_layer
        UK.close_pops()
        for w in layer.winfo_children():
            w.destroy()

        top = tk.Frame(layer, bg=UK.C.N50)
        top.pack(fill="x", padx=6, pady=(2, 10))
        UK.Pill(top, "‹ 返回", self._close_sheet, kind="ghost", padx=10).pack(side="left")
        tk.Label(top, text=title, bg=UK.C.N50, fg=UK.C.TEXT,
                 font=UK.F_HEAD).pack(side="left", padx=12)

        self._sheet_footer = tk.Frame(layer, bg=UK.C.N50)
        wrap = tk.Frame(layer, bg=UK.C.N50)
        wrap.pack(fill="both", expand=True, padx=24, pady=(0, 8))
        # Sheet controls always fit in the viewport. Only document widgets scroll.
        inner = tk.Frame(wrap, bg=UK.C.N50)
        inner.pack(fill="both", expand=True)
        builder(inner)

    def _close_sheet(self):
        if not getattr(self, "_sheet_open", False):
            return
        if self._sheet_stack:
            title, builder = self._sheet_stack.pop()
            self._sheet_title, self._sheet_builder = title, builder
            self._render_sheet(title, builder)
            return
        self._sheet_open = False
        self._sync_nav()
        for w in self.sheet_layer.winfo_children():
            w.destroy()
        self.sheet_layer.place_forget()

    def _confirm(self, message, on_yes, yes_text="确定", push=True):
        def build(parent):
            card = UK.Card(parent, radius=UK.R_CARD, padding=20, autosize=True)
            card.pack(fill="x", padx=2, pady=2)
            tk.Label(card.body, text=message, bg=UK.C.N0, fg=UK.C.TEXT,
                     font=UK.F_UI, wraplength=560, justify="left").pack(anchor="w")
            row = tk.Frame(card.body, bg=UK.C.N0)
            row.pack(anchor="e", pady=(18, 0))

            def ok():
                # 先执行业务回调再关层：关层时若栈里有父层会按 builder 重建
                # （旧控件全部销毁），回调必须在此之前改完配置，且不能碰旧控件
                on_yes()
                self._close_sheet()

            UK.Pill(row, yes_text, ok, kind="primary").pack(side="right")
            UK.Pill(row, "取消", self._close_sheet, kind="ghost").pack(side="right", padx=(0, 8))
            card.fit()

        self._show_sheet("确认", build, push=push)

    # ---------------- 悬浮窗 ----------------
    def set_float(self, on: bool):
        """开/关悬浮窗；开启时若主窗口可见则保持并存，最小化才自动切换。"""
        on = bool(on)
        if on and self.float_on and self.float_bar is not None:
            # 条子可能被临时收起过（从任务栏还原主窗口时会收条）。此时开关
            # 仍是「开」，直接 return 会让开关与可见性永久不一致，用户再也
            # 叫不出条子。所以这里要把它重新露出来。
            try:
                self.float_bar.win.deiconify()
                self.float_bar.win.lift()
            except Exception:
                pass
            return
        if not on and not self.float_on and self.float_bar is None:
            return                      # 已关着，别重复拆
        self.float_on = on
        self.tcfg.float_bar = on
        try:
            self.tcfg.save()
        except Exception:
            pass
        if on:
            if self.float_bar is None:
                self.float_bar = floatbar.FloatBar(self)
            self.float_bar.win.deiconify()
            self.float_sync(force=True)     # 新条子是空的，必须灌一次
            self.flash("悬浮字幕窗已开启")
        else:
            if self.float_bar is not None:
                self.float_bar.destroy()
                self.float_bar = None
            # 关闭悬浮条后必须保证还留着一个可见窗口。若主窗口此时仍是隐藏
            # 状态（最小化切到悬浮条后，用户点了悬浮条上的 ✕），进程就会变成
            # 「活着但零窗口」的隐形实例，继续占着单实例锁——用户既看不到
            # 界面，也启动不了新版本。这里主动把主窗口收回来。
            if not self._main_visible():
                try:
                    self.root.deiconify()
                    self.root.lift()
                except Exception:
                    pass
            self.flash("悬浮字幕窗已关闭")

    def toggle_float(self):
        self.set_float(not self.float_on)
        self.var_float.set(self.float_on)

    def toggle_float_on_min(self):
        self.tcfg.float_on_min = not getattr(self.tcfg, "float_on_min", False)
        try:
            self.tcfg.save()
        except Exception:
            pass
        self.var_fmin.set(self.tcfg.float_on_min)
        self.flash("最小化时自动切到悬浮窗" if self.tcfg.float_on_min
                   else "已取消最小化切换")

    def hide_float(self):
        """回到主界面：收起悬浮窗，恢复主窗口。"""
        if self.float_bar is not None:
            self.float_bar.win.withdraw()
        self.root.deiconify()
        self.root.lift()

    def _reconcile_window(self):
        """把 Tk 的内部状态与真实窗口状态对齐。

        从外部用 ``ShowWindow(hwnd, SW_RESTORE)`` 还原窗口（单实例接管、任务栏
        菜单、第三方工具都会这么干）时，Win32 已经把窗口显示了，但 Tk 仍认为
        自己是 iconic：既不触发 ``<Map>``，``state()`` 也还是 "iconic"。结果是
        主窗口回来了、悬浮条却赖着不收，下一次点最小化按钮的行为也会错乱。
        这里每秒校正一次，让 Tk 追上真实状态。
        """
        if getattr(self, "_closing", False) or getattr(self, "_min_switching", False):
            return
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user32.GetAncestor.restype = wintypes.HWND
            hwnd = user32.GetAncestor(self.root.winfo_id(), 2)   # GA_ROOT
            if not hwnd or not user32.IsWindowVisible(hwnd):
                return
            if user32.IsIconic(hwnd):
                return
            if getattr(self, "_was_iconic", False):
                # 主窗口已被还原（点击任务栏、单实例接管、托盘都算）→ 收悬浮条
                self._was_iconic = False
                self._tray_hidden = False
                if self.root.state() == "iconic":
                    self.root.deiconify()
                if self.float_bar is not None and self.float_on:
                    self.float_bar.win.withdraw()
                return
            if self.root.state() == "iconic":
                self.root.deiconify()
        except tk.TclError:
            pass
        except Exception:
            pass

    def _watchdog(self):
        """兜底自愈：一个窗口都没有时把主窗口收回来。

        「关掉悬浮条、主窗口还藏着」是最典型的隐形实例来源，但同类路径
        未来还可能冒出来（异常退出某处、用户用三方工具隐藏窗口等）。与其
        逐个堵，不如让程序自己发现「我怎么一个窗口都没了」并恢复——这样
        进程永远不会安静地握着单实例锁、却什么界面都不给。
        """
        if getattr(self, "_closing", False):
            return
        try:
            if getattr(self, "_tray_hidden", False):
                self._hidden_since = 0.0
                return                      # 用户主动收进托盘，别自作主张弹出来
            if self._visible_windows() > 0:
                self._hidden_since = 0.0
                return
            if self._win_state(self.root) == "iconic":
                # 用户主动最小化：任务栏条目还在，随时点得回来，不算"消失"
                self._hidden_since = 0.0
                return
            now = time.monotonic()
            since = getattr(self, "_hidden_since", 0.0)
            if not since:
                self._hidden_since = now
                return
            if now - since < 2.0:
                return                      # 给正常切换留出过渡时间
            self._hidden_since = 0.0
            self.hide_float()
            log_note("看门狗：检测到零窗口，已唤出主窗口")
            self.flash("窗口已被重新唤出")
        except tk.TclError:
            pass                            # 正在销毁
        except Exception:
            pass

    def float_sync(self, force=False):
        """把最新一条字幕推给悬浮窗（指纹不变则跳过）。"""
        if self.float_bar is None or not self.float_on:
            return
        rec = None
        for i in reversed(self.view_indices):
            r = self.records[i]
            if (r.get("text") or "").strip():
                rec = r
                break
        sig = None if rec is None else (rec.get("text"), rec.get("tr"),
                                        rec.get("tr_status"), rec.get("ts"))
        if sig == self._float_sig and not force:
            return
        self._float_sig = sig
        try:
            self.float_bar.sync(rec)
        except Exception:
            pass

    # ---------------- 会议模式 ----------------
    def set_mode(self, key):
        """切换会议模式：持久化 + 换采集引擎（停旧线程、按新模式起新线程）。"""
        m = MD.get(key)
        if m is None or not m.get("available"):
            return
        if key == self.mode:
            return
        self.mode = key
        self.tcfg.meeting_mode = key
        try:
            self.tcfg.save()
        except Exception:
            pass
        # 换模式 = 换采集源：旧线程停掉，按新模式的发现函数重启
        try:
            self.reader.stop()
        except Exception:
            pass
        self.reader = CaptionReader(self.q, mode=key)
        self.reader.paused = self.paused
        self.reader.start()
        # 状态文案复位到「正在查找」
        self._meeting_txt = f"正在查找 {m['name']}…"
        self.lbl_meeting.configure(text=self._meeting_txt)
        self.root.title(f"TeamsSub · {m['short']} 实时字幕")
        self.status.set(*self._status_for("init"))
        self.lbl_stat.configure(text="")
        if hasattr(self, "mode_sw"):
            self.mode_sw.set(key)
        self.flash(f"已切换到{m['name']}模式")

    def on_mode_blocked(self, key):
        """点到未上线模式：只提示，不切换。"""
        m = MD.get(key) or {}
        self.flash(f"{m.get('name', '该')}模式即将支持")

    # ---------------- 主题 ----------------
    def toggle_theme(self):
        dark = UK.theme() != "dark"
        if not UK.set_theme("dark" if dark else "light"):
            return
        self.tcfg.theme = UK.theme()
        try:
            self.tcfg.save()
        except Exception:
            pass
        if getattr(self, "var_theme", None):
            self.var_theme.set(dark)
        self._rebuild_ui()
        self.flash("已切换到深色模式" if dark else "已切换到浅色模式")

    def _rebuild_ui(self):
        """主题切换：销毁并按新令牌重建主界面（服务线程不受影响）。"""
        query = self.search.value()
        position = self.txt.yview()[0]
        # 只在正常显示时快照几何。最小化/隐藏状态下 geometry() 返回的是
        # 占位尺寸（如 "1x1+0+0"），原样写回会把窗口缩成一个点或移出屏幕，
        # 看起来就是「窗口自己消失了」。
        geometry = None
        if self._win_state(self.root) == "normal":
            geometry = self.root.geometry()
        self._sheet_stack = []
        self._sheet_open = False
        UK.close_pops()
        for w in self.root.winfo_children():
            w.destroy()
        self._build_ui()
        if geometry:
            self.root.geometry(geometry)
        if query:
            self.search._ph = False
            self.search.var.set(query)
        if self.float_on and self.float_bar is not None:
            self.float_bar.repaint()
        self.full_render()
        self.refresh_sidebar()
        # 恢复状态显示
        self.status.set(self._status_txt, self._status_color)
        self.lbl_meeting.configure(text=self._meeting_txt)
        self.sync_seg()
        if not self.auto_scroll:
            self.root.after(100, lambda: self.txt.yview_moveto(position))

    def sync_seg(self):
        self.seg.set({"auto": 0, "zh": 1, "en": 2}.get(self.tcfg.mode, 0))

    # ---------------- 更多菜单 ----------------
    def _open_menu(self):
        m = tk.Menu(self.root, tearoff=0)
        UK.style_menu(m)
        m.add_command(label="复制全部", command=self.copy_all)
        m.add_command(label="复制最近 5 条", command=lambda: self.copy_last(5))
        m.add_command(label="导出整场会议…", command=self.export)
        m.add_command(label="导出双语 SRT…", command=self.export_srt)
        m.add_separator()
        m.add_command(label="重译失败项", command=self.retry_failed)
        m.add_command(label="翻译历史", command=self.translate_history)
        m.add_separator()
        m.add_checkbutton(label="精简姓名", variable=self.var_simplify,
                          command=self.on_simplify)
        m.add_checkbutton(label="窗口置顶", variable=self.var_pin,
                          command=self.toggle_pin)
        m.add_command(label=("继续采集" if self.paused else "暂停采集"),
                      command=self.toggle_pause)
        m.add_separator()
        m.add_command(label="清屏（保留整场记录）…", command=self.clear)
        m.add_command(label="历史会议…", command=self.open_history)
        m.add_command(label="新建会议…", command=self.new_meeting)
        m.add_command(label="结束并归档…", command=self.end_meeting)
        m.add_separator()
        self.var_theme = tk.BooleanVar(value=UK.theme() == "dark")
        m.add_checkbutton(label="深色模式", variable=self.var_theme,
                          command=self.toggle_theme)
        self.var_float = tk.BooleanVar(value=self.float_on)
        m.add_checkbutton(label="悬浮字幕窗", variable=self.var_float,
                          command=self.toggle_float)
        self.var_fmin = tk.BooleanVar(value=getattr(self.tcfg, "float_on_min", False))
        m.add_checkbutton(label="最小化时切到悬浮窗", variable=self.var_fmin,
                          command=self.toggle_float_on_min)
        m.add_command(label="帮助", command=self.show_help)
        try:
            m.tk_popup(self.btn_more.winfo_rootx() - 120,
                       self.btn_more.winfo_rooty() + self.btn_more.winfo_height() + 6)
        finally:
            m.grab_release()

    def speaker_color(self, name):
        if name not in self.color_map:
            self.color_map[name] = UK.speaker_colors()[len(self.color_map) % len(UK.speaker_colors())]
        return self.color_map[name]

    def name_of(self, rec):
        """显示用姓名：精简模式用规范化名，否则回退原始名。"""
        if self.simplify:
            return rec["name"]
        return clean(rec.get("raw") or rec["name"])

    def ts_of(self, rec):
        """历史字幕（启动前已产生的）没有可信时间，显示占位。"""
        return "--:--:--" if rec.get("backlog") or not isinstance(rec.get("ts"), datetime) else rec["ts"].strftime("%H:%M:%S")

    def show_placeholder(self):
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")
        try:
            self.empty_box.place(relx=0.5, rely=0.38, anchor="center")
        except Exception:
            pass
        self.placeholder_active = True

    def clear_placeholder(self):
        if getattr(self, "placeholder_active", False):
            try:
                self.empty_box.place_forget()
            except Exception:
                pass
            self.placeholder_active = False

    def _visible(self, i, rec):
        if i < getattr(self, 'display_start', 0):
            return False
        query = self.search.value() if hasattr(self, 'search') else ''
        return bool(ME.filtered([rec], self.speaker_filter, query))

    def full_render(self):
        self._render_generation = getattr(self, '_render_generation', 0) + 1
        generation = self._render_generation
        position = self.txt.yview()[0]
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        for mark in self.txt.mark_names():
            if mark.startswith('rec'):
                self.txt.mark_unset(mark)
        self.clear_placeholder()
        self.view_indices, self.view_pos = [], {}
        indices = [i for i, r in enumerate(self.records) if self._visible(i, r)]
        self._rendering = True
        self._render_dirty = False
        def chunk(offset=0):
            if generation != self._render_generation or not self.txt.winfo_exists():
                return
            self.txt.configure(state='normal')
            deadline = time.monotonic() + .012
            while offset < len(indices):
                i = indices[offset]
                if i < len(self.records):
                    self.view_pos[i] = len(self.view_indices)
                    self.view_indices.append(i)
                    self.render_block(i, self.records[i])
                offset += 1
                if len(indices) > 200 and time.monotonic() > deadline:
                    break
            self.txt.configure(state='disabled')
            if offset < len(indices):
                self.root.after(1, chunk, offset)
                return
            self._rendering = False
            if self._render_dirty:
                self.full_render()
                return
            if not self.view_indices:
                self.show_placeholder()
            self.refresh_sidebar()
            self._highlight_search()
            if self.auto_scroll:
                self.scroll_bottom()
            else:
                self.txt.yview_moveto(position)
        chunk()

    def render_block(self, i, rec, at=None):
        """在 at 处（缺省为末尾）渲染一条记录，并在其起点打 mark 以便原地重绘。

        具体画法在 transcript_view 里，历史阅读界面共用同一份实现——两处各写
        一遍的话，改了一边忘了另一边，两个界面就会慢慢长歪。
        """
        TV.render_block(self.txt, i, rec, self,
                        getattr(self, "display_mode", "both"), at=at, retry=True)

    def _block_end(self, i):
        """该块的结束位置 = 下一个已渲染块的起点。"""
        pos = self.view_pos.get(i)
        if pos is not None and pos + 1 < len(self.view_indices):
            try:
                return self.txt.index(f"rec{self.view_indices[pos + 1]}")
            except tk.TclError:
                pass
        return self.txt.index("end-1c")

    def update_block(self, i, rec):
        if getattr(self, "_rendering", False):
            self._render_dirty = True
            return
        if not self._visible(i, rec):
            if i in self.view_pos:
                self.full_render()
            return
        """整块重绘：删除旧块再按新内容重画（字幕改写与译文到达都走这里）。"""
        try:
            start = self.txt.index(f"rec{i}")
        except tk.TclError:
            if self.speaker_filter and rec["name"] != self.speaker_filter:
                return
            self.append_record(i, rec)
            return
        end = self._block_end(i)
        was = str(self.txt.cget("state"))
        self.txt.configure(state="normal")
        self.txt.delete(start, end)
        next_mark = None
        pos = self.view_pos.get(i)
        if pos is not None and pos + 1 < len(self.view_indices):
            next_mark = f"rec{self.view_indices[pos+1]}"
            self.txt.mark_gravity(next_mark, 'right')
        self.render_block(i, rec, at=start)
        if next_mark:
            self.txt.mark_gravity(next_mark, 'left')
        if was == "disabled":
            self.txt.configure(state="disabled")
        if self.auto_scroll:
            self.scroll_bottom()

    # 兼容旧调用
    def draw_translation(self, i, rec):
        self.update_block(i, rec)
        self.float_sync()

    def append_record(self, i, rec):
        if getattr(self, '_rendering', False):
            self._render_dirty = True
            return
        if not self._visible(i, rec) or i in self.view_pos:
            return
        self.txt.configure(state="normal")
        self.clear_placeholder()
        self.view_pos[i] = len(self.view_indices)
        self.view_indices.append(i)
        self.render_block(i, rec)
        self.txt.configure(state="disabled")
        if self.auto_scroll:
            self.scroll_bottom()

    def update_record(self, i, rec):
        self.update_block(i, rec)

    def scroll_bottom(self):
        if getattr(self, "_batching", False):
            self._batch_scroll = True
            return
        self.txt.see("end")

    def refresh_sidebar(self):
        counts, order, latest = {}, [], {}
        for rec in self.records:
            nm = rec["name"]
            if nm not in counts:
                order.append(nm)
                counts[nm] = 0
            counts[nm] += 1
            latest[nm] = rec
        items = [(nm, self.name_of(latest[nm]), counts[nm], self.speaker_color(nm))
                 for nm in order]
        self.speakers.set_items(items, self.speaker_filter)

    # ---------------- 事件 ----------------
    def pump(self):
        if not getattr(self, "_closing", False):
            now = time.monotonic()
            if now - getattr(self, "_watch_ts", 0.0) >= 1.0:
                self._watch_ts = now
                self._reconcile_window()
                self._watchdog()
                self._sync_tray_tip()
        self._batching = True
        self._batch_scroll = False
        dirty_sidebar = False
        try:
            deadline = time.monotonic() + .020
            for _ in range(50):
                if time.monotonic() > deadline:
                    break
                kind, a, *rest = self.q.get_nowait()
                if kind == 'capture':
                    generation, event = a
                    if generation != self.reader.generation:
                        continue
                    kind, a, *rest = event
                if kind == 'ui':
                    callback, value = a
                    try:
                        callback(value)
                    except tk.TclError:
                        pass  # The originating view may have been closed while the worker ran.
                    except Exception as exc:
                        self._notice('操作未完成：'+str(exc))
                elif kind == 'save_error':
                    self.lbl_save.configure(text='自动保存失败：'+str(a), fg=UK.C.RED)
                elif kind == "new":
                    rec = a
                    idx = len(self.records)
                    self.records.append(rec)
                    if not self.speaker_filter or rec["name"] == self.speaker_filter:
                        self.append_record(idx, rec)
                    self.autosave(rec)
                    dirty_sidebar = True
                    if self.trans_on:
                        self.pending_tr[idx] = time.time()
                elif kind == "update":
                    idx = next((k for k, r in enumerate(self.records)
                                if r is a or (a.get('id') and r.get('id') == a['id'])), None)
                    if idx is not None:
                        rec = self.records[idx]
                        rec.update(a)
                        self._invalidate_translation(rec)
                        if not self.speaker_filter or rec["name"] == self.speaker_filter:
                            self.update_record(idx, rec)
                        # 文本还在改写：重置稳定计时，译文稍后重做
                        self.autosave(rec)
                        if self.trans_on:
                            self.pending_tr[idx] = time.time()
                elif kind == "tr":
                    token, tr, meta = a
                    request = self.tr_requests.pop(token, None)
                    if request is None:
                        continue
                    rec, source = request
                    rid = next((i for i, r in enumerate(self.records) if r is rec), None)
                    if (rid is not None and rec.get('_tr_request') == token
                            and rec['text'] == source):
                        rec.pop('_tr_request', None)
                        st = meta.get("status")
                        if st == "ok":
                            rec["tr"] = tr
                            rec["tr_status"] = "ok"
                            rec["tr_key"] = meta.get("key")
                            rec["tr_src"] = meta.get("src") or "en"
                            rec["tr_tgt"] = meta.get("tgt") or "zh"
                            try:
                                rec["qa"] = qa_check(rec["text"], tr, self.tcfg,
                                                     rec["tr_src"], rec["tr_tgt"])
                            except Exception:
                                rec["qa"] = []
                            if rec["qa"]:
                                self.qa_hits += 1
                            self.autosave_tr(rec)
                        elif st == "skip":
                            rec["tr_status"] = "skip"
                            rec["tr"] = ""
                        else:
                            rec["tr_status"] = "fail"
                            rec["tr_err"] = meta.get("error", "")
                        if not self.speaker_filter or rec["name"] == self.speaker_filter:
                            self.draw_translation(rid, rec)
                elif kind == "meeting":
                    fallback = (MD.get(self.mode) or {}).get("name", "会议")
                    self._meeting_txt = f"会议：{a}" if a else fallback
                    self.lbl_meeting.configure(text=self._meeting_txt)
                elif kind == "status":
                    self.on_status(a)
                elif kind == 'test_result':
                    label, ok, message = a
                    try:
                        label.configure(text=('✓ ' if ok else '✗ ') + message[:70],
                                        fg=UK.C.GREEN if ok else UK.C.RED)
                    except tk.TclError:
                        pass  # 测试结束前设置面板已关闭
        except queue.Empty:
            pass
        if dirty_sidebar:
            self.refresh_sidebar()
            n = len(self.records)
            spk = len({r['name'] for r in self.records})
            self.lbl_stat.configure(text=f"已捕获 {n} 条 · {spk} 位发言人")
        self.pump_translation()
        self._batching = False
        if self._batch_scroll and self.auto_scroll:
            self.scroll_bottom()
        self.root.after(25 if not self.q.empty() else 120, self.pump)

    # ---------------- 翻译调度 ----------------
    def _context_for(self, rid, n):
        """取该句之前的 n 句原文作为语境（旧 → 新）。"""
        if n <= 0:
            return []
        out, i = [], rid - 1
        while i >= 0 and len(out) < n:
            rec = self.records[i]
            t = (rec.get("text") or "").strip()
            if t:
                who = self.name_of(rec)
                out.append(f"{who}: {t}" if who else t)
            i -= 1
        out.reverse()
        return out

    def pump_translation(self):
        """触发判定：**一句话结束**后才提交，三条通道取最早满足者。

        1. 已被新句顶掉（ASR 已经往下说了）→ supersede_grace_ms 后即可翻译；
        2. 出现了句末标点且 450ms 内无改写 → 说明这句语法闭合，立即翻译；
        3. 兜底：无标点又没被顶掉（如最后一句），等满 settle_ms 再翻。

        这样既不会在句子还没说完时发请求，又能把平均等待压到 0.3–0.6s。
        """
        if not self.trans_on or not self.svc:
            return
        now = time.time()
        n = len(self.records)
        g_super = max(0.15, self.tcfg.supersede_grace_ms / 1000.0)
        g_sent = max(0.15, self.tcfg.sentence_end_grace_ms / 1000.0)
        ready = []
        for rid, ts in list(self.pending_tr.items()):
            if rid >= n:
                self.pending_tr.pop(rid, None)
                continue
            age = now - ts
            text = self.records[rid].get("text") or ""
            superseded = rid < n - 1
            if superseded and age >= g_super:
                ready.append(rid)
            elif is_sentence_end(text) and age >= g_sent:
                ready.append(rid)
            elif age >= self.settle_sec:
                ready.append(rid)
        for rid in ready:
            self.pending_tr.pop(rid, None)
            if rid >= len(self.records):
                continue
            rec = self.records[rid]
            rec["tr_status"] = "pending"
            if not self.speaker_filter or rec["name"] == self.speaker_filter:
                self.draw_translation(rid, rec)
            self._submit_translation(rid)
        # 服务端报告的 token 用量
        if ready or int(now) % 2 == 0:
            s = self.svc.stats()
            def count(key):
                value = s[key]
                return '未提供' if value is None else f'{value:,}'
            partial = any(s['token_reports'][key] < s['calls'] and s['token_reports'][key]
                          for key in ('input_tokens','output_tokens','cache_hit_tokens'))
            self.lbl_cache.configure(
                text=f"输入 {count('input_tokens')} · 输出 {count('output_tokens')} · 命中 {count('cache_hit_tokens')}"
                     + (' · 部分用量' if partial else '')
                     + (f" · 队列 {s['queue']}" if s['queue'] else ''))

    def on_status(self, s):
        self._status_txt, self._status_color = self._status_for(s)
        if s == "ok":
            self.status.set(*self._status_for(s))
            if self.lbl_stat.cget("text") in ("初始化…", ""):
                self.lbl_stat.configure(text="")
            if self.lbl_meeting.cget("text").startswith("正在查找"):
                self.lbl_meeting.configure(text="已连接")
        elif s == "no_group":
            self.status.set(*self._status_for(s))
            self.lbl_stat.configure(text="未连接")
            self.lbl_meeting.configure(text="未检测到字幕")
        elif s in ("idle", "error", "paused"):
            self.status.set(*self._status_for(s))

    def _status_for(self, s):
        return {"ok": ("已连接", UK.C.GREEN),
                "no_group": ("等待会议", UK.C.AMBER),
                "idle": ("静默", UK.C.AMBER),
                "error": ("读取异常", UK.C.RED),
                "paused": ("已暂停", UK.C.T3)}.get(s, ("连接中", UK.C.T3))

    def _sync_scroll_hint(self):
        at_bottom = self.txt.yview()[1] > 0.98
        self.auto_scroll = at_bottom
        try:
            if at_bottom:
                self.to_bottom.place_forget()
            else:
                self.to_bottom.place(relx=1.0, rely=1.0, x=-16, y=-12, anchor="se")
                self.to_bottom.lift()
        except Exception:
            pass

    def _jump_bottom(self):
        self.auto_scroll = True
        self.txt.see("end")
        self._sync_scroll_hint()

    def on_click(self, e):
        self._sync_scroll_hint()

    def on_wheel(self, e):
        self.root.after(60, self._sync_scroll_hint)

    def on_dblclick(self, e):
        try:
            line = self.txt.index(f"@{e.x},{e.y}").split(".")[0]
            content = self.txt.get(f"{line}.0", f"{line}.end")
            if content.strip():
                self.root.clipboard_clear()
                self.root.clipboard_append(content.strip())
                self.flash(f"已复制该行：{content.strip()[:40]}")
        except Exception:
            pass

    def _notice(self, message, title="提示"):
        self._confirm(message, lambda: None, "知道了", push=bool(self._sheet_open))

    # ---------------- 翻译服务管理 ----------------
    def _invalidate_translation(self, rec):
        token = rec.pop('_tr_request', None)
        self.tr_requests.pop(token, None)
        for key in ('tr', 'tr_status', 'tr_cached', 'tr_key', 'tr_src', 'tr_tgt', 'tr_err', 'qa'):
            rec.pop(key, None)

    def _submit_translation(self, rid, force=False):
        if not self.trans_on or self.svc is None:
            return
        rec = self.records[rid]
        old = rec.get('_tr_request')
        self.tr_requests.pop(old, None)
        token = uuid.uuid4().hex
        rec['_tr_request'] = token
        self.tr_requests[token] = (rec, rec['text'])
        self.svc.submit(token, rec['text'],
                        context=self._context_for(rid, self.tcfg.context_n), force=force)

    def _has_credentials(self):
        return bool(self.tcfg.api_key.strip()) or self.tcfg.engine in ("deepl", "azure")

    def start_translation(self, silent=False):
        if not TRANSLATOR_OK:
            self._notice("缺少 translator.py")
            return False
        if self.tcfg.engine == "openai" and not self.tcfg.api_key.strip():
            self._notice("未配置 API，点右上「设置」")
            return False
        self.svc = TranslationService(copy.deepcopy(self.tcfg), self._on_tr_result)
        self.svc.start()
        self.trans_on = True
        self.btn_tr.set_text("翻译 开")
        self.btn_tr.set_kind("tint")
        # 启动时不追译全部历史：只补最近 backlog_max 条，其余按需点「翻译历史」
        ids = [i for i in range(len(self.records)) if not self.records[i].get("tr_status")]
        limit = max(0, self.tcfg.backlog_max)
        for k, i in enumerate(ids[-limit:] if limit else []):
            self.pending_tr[i] = time.time() + k * 0.2
        if len(ids) > self.tcfg.backlog_max:
            self.flash(f"已开启翻译；历史 {len(ids)} 条中只补译最近 {self.tcfg.backlog_max} 条，"
                       f"其余可点「翻译历史」")
        elif not silent:
            self.flash("翻译已开启 · 中英双向互译")
        return True

    def stop_translation(self):
        self.trans_on = False
        self.pending_tr.clear()
        self.tr_requests.clear()
        for rec in self.records:
            rec.pop('_tr_request', None)
            if rec.get('tr_status') == 'pending':
                rec.pop('tr_status', None)
        if self.svc:
            self.svc.stop()
            self.svc = None
        self.btn_tr.set_text("翻译 关")
        self.btn_tr.set_kind("ghost")
        self.lbl_cache.configure(text="")

    def toggle_translate(self):
        # 主界面的开关与设置页共用 tcfg.enabled，切换后持久化保持一致
        if self.trans_on:
            self.stop_translation()
            self.tcfg.enabled = False
        else:
            self.tcfg.enabled = bool(self.start_translation())
        self.tcfg.save()

    def on_mode_pick(self, label):
        mapping = {"双向互译": "auto", "全部译中": "zh", "全部译英": "en"}
        was_on = self.trans_on
        self.stop_translation()
        self.tcfg.mode = mapping.get(label, "auto")
        self.tcfg.save()
        # 模式变化会改变目标语言方向，旧译文不再适用 -> 清空重译
        for rec in self.records:
            self._invalidate_translation(rec)
            self.autosave(rec)
        self.full_render()
        if was_on and self.start_translation(silent=True):
            self.pending_tr = {i: time.time() for i in range(len(self.records))}
        self.flash(f"翻译方向：{label}")

    def _on_tr_result(self, rid, translated, meta):
        """服务线程回调：只入队，绝不直接碰控件。"""
        self.q.put(("tr", (rid, translated, meta)))

    def on_retry_click(self, e):
        idx = self.txt.index(f"@{e.x},{e.y}")
        for tag in self.txt.tag_names(idx):
            if tag.startswith("trfail") and tag[6:].isdigit():
                rid = int(tag[6:])
                if rid < len(self.records) and self.trans_on and self.svc:
                    rec = self.records[rid]
                    rec["tr_status"] = "pending"
                    self.draw_translation(rid, rec)
                    self._submit_translation(rid, force=True)
                return "break"

    def translate_history(self):
        """按需把尚未翻译的历史字幕排队（错峰提交，避免一次性打爆队列）。"""
        if not self.trans_on:
            self.flash("请先开启翻译（点右上「翻译：关」）")
            return
        now = time.time()
        n = 0
        for i, rec in enumerate(self.records):
            if not rec.get("tr_status") and i not in self.pending_tr:
                self.pending_tr[i] = now + n * 0.2
                n += 1
        self.flash(f"已把 {n} 条历史字幕加入翻译队列" if n else "没有待补译的历史字幕")

    def retry_failed(self):
        if not self.trans_on:
            return
        n = 0
        for rid, rec in enumerate(self.records):
            if rec.get("tr_status") == "fail":
                rec["tr_status"] = "pending"
                self.draw_translation(rid, rec)
                self._submit_translation(rid, force=True)
                n += 1
        self.flash(f"已重新提交 {n} 条失败译文")

    def open_settings(self):
        import settings_view
        settings_view.open_settings(self)

    def _persist_topics(self, order, topics):
        """改动过的主题写回配置；纯自建必存，预设仅在偏离时存覆盖。"""
        for k in order:
            t = topics.get(k, {})
            preset = TOPICS.get(k)
            payload = {"name": t.get("name", k), "brief": t.get("brief", ""),
                       "glossary": t.get("glossary", "")}
            if preset is None:
                self.tcfg.topics_custom[k] = payload
            elif (payload["name"].strip() != (preset.get("name") or "").strip()
                  or payload["brief"].strip() != (preset.get("brief") or "").strip()
                  or payload["glossary"].strip() != (preset.get("glossary") or "").strip()):
                self.tcfg.topics_custom[k] = payload
            else:
                self.tcfg.topics_custom.pop(k, None)


    def on_right_click(self, e):
        """右键：点中译文行可修正译法；其余位置弹复制菜单。

        正文是只读的，Ctrl+C 一直能复制，但右键什么都不出会让人觉得不能复制。
        所以命中译文时保留原来的「修正」，没命中就让复制菜单出来。
        """
        base = self.txt.index(f"@{e.x},{e.y}")
        for off in ("", "+1c", "-1c"):
            try:
                idx = self.txt.index(base + off) if off else base
            except tk.TclError:
                continue
            for tag in self.txt.tag_names(idx):
                if tag.startswith("trrec") and tag[5:].isdigit():
                    self.open_edit(int(tag[5:]))
                    return "break"
        return None                 # 落到 ui_kit 挂的复制菜单

    def open_edit(self, rid):
        if not (0 <= rid < len(self.records)):
            return
        rec = self.records[rid]

        def build(parent):
            card = UK.Card(parent, padding=20, autosize=False)
            card.pack(fill='both',expand=True)
            b=card.body
            b.columnconfigure(0,weight=1,uniform='edit');b.columnconfigure(1,weight=1,uniform='edit')
            b.rowconfigure(1,weight=1)
            tk.Label(b,text='原文',bg=UK.C.N0,fg=UK.C.SUB,font=UK.F_SMALL).grid(row=0,column=0,sticky='w',pady=(0,10))
            tk.Label(b,text='译文',bg=UK.C.N0,fg=UK.C.SUB,font=UK.F_SMALL).grid(row=0,column=1,sticky='w',padx=(16,0),pady=(0,10))
            source=tk.Text(b,height=3,wrap='word',font=UK.F_UI,bg=UK.C.N100,fg=UK.C.TEXT,bd=0,padx=12,pady=10)
            source.grid(row=1,column=0,sticky='nsew')
            source.insert('1.0',rec.get('text',''));source.configure(state='disabled')
            box=tk.Text(b,height=3,wrap='word',font=UK.F_UI,bg=UK.C.N100,fg=UK.C.TEXT,bd=0,padx=12,pady=10,
                        insertbackground=UK.C.TEXT)
            box.grid(row=1,column=1,sticky='nsew',padx=(16,0));box.insert('1.0',rec.get('tr','') or '')
            self._sheet_footer.pack(side='bottom',fill='x',padx=24,pady=(8,12))
            row=tk.Frame(self._sheet_footer,bg=UK.C.N50);row.pack(fill='x')
            if rec.get('qa'):
                UK.Pill(row,f"质检 {len(rec['qa'])} 项",lambda:self._notice('；'.join(x['detail'] for x in rec['qa'])),kind='ghost').pack(side='left')

            def save():
                new = box.get("1.0", "end").strip()
                if not new:
                    return
                token = rec.pop('_tr_request', None)
                self.tr_requests.pop(token, None)
                self.pending_tr.pop(rid, None)
                rec["tr"] = new
                rec["tr_status"] = "ok"
                try:
                    rec["qa"] = qa_check(rec["text"], new, self.tcfg,
                                         rec.get("tr_src", "en"), rec.get("tr_tgt", "zh"))
                except Exception:
                    rec["qa"] = []
                self.autosave_tr(rec)
                self._close_sheet()
                self.update_block(rid, rec)
                self.flash("已保存")

            UK.Pill(row, "保存", save, kind="primary").pack(side="right")
            UK.Pill(row, "取消", self._close_sheet, kind="ghost").pack(side="right", padx=(0, 8))

        self._show_sheet("修正译文", build)

    def on_pick_speaker(self, key):
        """key=None 表示「全部发言人」。"""
        if key == self.speaker_filter:
            return
        self.speaker_filter = key
        self.full_render()
        name = key or "全部"
        self.lbl_stat.configure(
            text=f"{len(self.records)} 条 · 筛选：{shorten(name, 14)}")

    def on_simplify(self):
        self.simplify = bool(self.var_simplify.get())
        self.full_render()

    def toggle_pin(self):
        self.pinned = bool(self.var_pin.get())
        self.root.attributes("-topmost", self.pinned)
        self.flash("窗口已置顶" if self.pinned else "已取消置顶")

    def toggle_pause(self):
        self.paused = not self.paused
        self.reader.paused = self.paused
        if hasattr(self, "btn_pause"):
            self.btn_pause.set_text("继续采集" if self.paused else "暂停采集")
        self.status.set("已暂停" if self.paused else "已连接",
                        UK.C.T3 if self.paused else UK.C.GREEN)
        self.flash("已暂停采集" if self.paused else "已恢复采集")

    def flash(self, msg):
        old = self.lbl_stat.cget("text")
        self.lbl_stat.configure(text=msg, fg=UK.C.A500)
        self.root.after(1600, lambda: self.lbl_stat.configure(text=old, fg=UK.C.SUB))

    # ---------------- 检索 ----------------
    def apply_search(self):
        if hasattr(self, '_search_after'):
            self.root.after_cancel(self._search_after)
        if len(self.records) < 200:
            self.full_render()
        else:
            self._search_after = self.root.after(160, self.full_render)

    def _highlight_search(self):
        self.txt.configure(state="normal")
        self.txt.tag_remove("hit", "1.0", "end")
        q = self.search.value()
        if q:
            start = "1.0"
            while True:
                pos = self.txt.search(q, start, stopindex="end", nocase=True)
                if not pos:
                    break
                end = f"{pos}+{len(q)}c"
                self.txt.tag_add("hit", pos, end)
                start = end
        self.txt.configure(state="disabled")

    # ---------------- 输出 ----------------
    def as_text(self, only=None):
        recs = self.records if only is None else self.records[-only:]
        out = []
        for rec in recs:
            line = f"[{self.ts_of(rec)}] {self.name_of(rec)}: {rec['text']}"
            if rec.get("tr_status") == "ok" and rec.get("tr"):
                line += f"\n　　→ {rec['tr']}"
            out.append(line)
        return "\n".join(out)

    def copy_all(self):
        if not self.records:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.as_text())
        self.flash(f"已复制全部 {len(self.records)} 条字幕")

    def copy_last(self, n=5):
        if not self.records:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.as_text(only=n))
        self.flash(f"已复制最近 {n} 条")

    def export(self):
        import frontend_views
        frontend_views.open_export(self)

    def clear(self):
        self._confirm('清屏只隐藏当前字幕，整场记录仍会保存并可导出。', self.clear_display, '清屏', push=False)

    def clear_display(self):
        self.display_start = len(self.records)
        self.full_render()
        self.flash('已清屏 · 整场记录仍可导出')

    def _do_clear(self, start_reader=True):
        if self.save_store is not None:
            self.save_store.close()
        self.display_start = 0
        self._render_generation = getattr(self, "_render_generation", 0) + 1
        self._rendering = False
        self.tr_requests.clear()
        self.pending_tr.clear()
        old_reader = self.reader
        old_reader.stop()
        self.reader = CaptionReader(self.q, mode=self.mode)
        self.reader.paused = self.paused
        self.reader.meeting_title = old_reader.meeting_title
        # 先清空数据与界面，最后才启动新采集线程。反过来写会留下一个可观测的
        # 中间态：「窗口代号已经换了、记录还没清」——调用方（含「新建会议」
        # 的成功判定）可能正好落在这一瞬，看到旧会议的记录还在。
        self.records.clear()
        self.view_pos.clear()
        self.save_store, self.save_path = None, None
        self._clear_session_stamp()     # 上一场已收尾，别再提示"未正常结束"
        self.speaker_filter = None
        if hasattr(self, "search"):
            self.search._show_placeholder()
        self.lbl_save.configure(text="等待字幕 · 自动保存至本机")
        self.full_render()
        self.lbl_stat.configure(text="新会议已就绪")
        if start_reader:
            self.reader.start()

    # ---------------- 会话登记（崩溃恢复） ----------------
    def _stamp_session(self):
        """把当前会话记到磁盘，供异常退出后找回。每场会议只写一次。

        字幕本身是逐条落盘的（SQLite + Markdown 原子替换），所以崩溃不会真
        丢数据；真正的问题在于「人不知道它去哪了」——重开程序看到空白界面，
        很容易以为这场白开了。登记文件就是那个指路牌。
        """
        if not self.save_path:
            return
        if getattr(self, "_stamp_path", None) == str(self.save_path):
            return
        self._stamp_path = str(self.save_path)
        try:
            path = session_stamp_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            title = getattr(self.save_store, "title", "") or self._meeting_txt
            path.write_text(json.dumps({"path": str(self.save_path),
                                        "title": title,
                                        "mode": self.mode,
                                        "ts": time.time()},
                                       ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _clear_session_stamp(self):
        self._stamp_path = None
        try:
            session_stamp_path().unlink()
        except Exception:
            pass

    def _check_recovery(self):
        """启动时若发现上次没收尾，把那份记录指给用户。

        只给入口，不自动续接：自动切到旧会议会让下一句字幕记进错误的文件，
        比让用户多点一下糟糕得多。
        """
        try:
            path = session_stamp_path()
            if not path.exists():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self._clear_session_stamp()
            return
        try:
            target = Path(data.get("path") or "")
            if not target.exists():
                self._clear_session_stamp()
                return
            age = time.time() - float(data.get("ts") or 0)
        except Exception:
            self._clear_session_stamp()
            return
        if age > 7 * 86400:             # 一周前的残留不再打扰
            self._clear_session_stamp()
            return
        title = data.get("title") or target.stem
        when = datetime.fromtimestamp(float(data.get("ts") or time.time()))
        self._confirm(
            "上次会议「%s」（%s）没有正常结束——可能是崩溃、断电或被强制结束。\n\n"
            "字幕已逐条写入磁盘，记录仍在：\n%s\n\n"
            "要现在打开这份记录吗？打开只是查看，不会切换当前的采集源。"
            % (title, when.strftime("%m-%d %H:%M"), target.name),
            lambda: self._open_file(target), "打开记录", push=False)
        self._clear_session_stamp()

    @staticmethod
    def _open_file(path):
        try:
            os.startfile(str(path))
        except Exception:
            pass

    def autosave(self, rec):
        try:
            if self.save_store is None:
                m = MD.get(self.mode) or {}
                title = self.reader.meeting_title or m.get("name", "会议")
                self.save_store = (AsyncCaptionStore(CAPTION_DIR, title,
                    on_error=lambda exc: self.q.put(('save_error', str(exc)))) if getattr(self, '_async_storage', False)
                    else CaptionStore(CAPTION_DIR, title))
                self.save_path = self.save_store.path
                self.lbl_save.configure(text=f"已保存 · {self.save_path.stem[-12:]}")
            self.save_store.upsert(rec, self.name_of(rec), self.ts_of(rec))
            self._stamp_session()
        except Exception as exc:
            self.lbl_save.configure(text=f"自动保存失败：{exc}")

    def autosave_tr(self, rec):
        """按记录 ID 更新，译文完成顺序不影响原文关联。"""
        self.autosave(rec)

    # ---------------- 双语 SRT ----------------
    @staticmethod
    def _srt_ts(delta) -> str:
        """把相对时差格式化为 SRT 时间码 00:00:03,000。"""
        ms = int(delta.total_seconds() * 1000) if hasattr(delta, "total_seconds") \
            else int(delta)
        h, ms = divmod(max(0, ms), 3600000)
        m, ms = divmod(ms, 60000)
        sec, ms = divmod(ms, 1000)
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    def export_srt(self):
        import frontend_views
        frontend_views.open_export(self, initial_format='srt')

    def show_help(self):
        """帮助：一屏放完的快捷键/操作表。

        旧版把每节写成一句句长文案堆在 Label 里，窗口一小就要滚动，而且
        读起来像说明书。这里改成「操作 → 效果」两列表格：不滚、不堆、
        一眼能找到要按哪个键。
        """
        import frontend_views as FV
        def build(parent):
            b = FV.card(parent)
            head = tk.Label(b, bg=UK.C.N0, fg=UK.C.SUB, font=UK.F_MINI,
                            anchor='w', justify='left')
            head.pack(fill='x')
            head.configure(text=f"{APP_DISPLAY_NAME} v{APP_VERSION} · {data_dir()}",
                           wraplength=680)
            tabs = tk.Frame(b, bg=UK.C.N0)
            tabs.pack(anchor='w', pady=(10, 12))
            grid = tk.Frame(b, bg=UK.C.N0)
            grid.pack(fill='both', expand=True)
            grid.columnconfigure(1, weight=1)
            rows = {
                '采集': [('Ctrl+E', '导出整场记录'), ('点「清屏」', '只隐藏显示，记录保留'),
                         ('点「新建」', '归档当前会议并开始新记录'), ('点「结束」', '归档并暂停采集')],
                '导出': [('Markdown / 纯文本', '整场记录，不受搜索和筛选影响'),
                         ('双语 SRT', '按采集相对时间；未知时间会让你选阅读时间轴'),
                         ('补译后导出', '会调用当前翻译服务，先确认再开始')],
                '历史': [('Ctrl 多选', '按列表顺序合并导出，保留来源'), ('续接', '把所选会议设为当前记录'),
                         ('删除', '移入系统回收站，可还原'), ('旧版 Markdown', '缺时间的一律记为未知')],
                '操作': [('Ctrl+F', '搜索'), ('Ctrl+L', '清屏'), ('Escape', '返回上一层'),
                         ('点失败译文', '重试该条'), ('右键译文', '修正译法'),
                         ('右键正文', '复制 / 全选'), ('更多菜单', '置顶、悬浮窗、导出')]}
            buttons = []
            holder = {}

            def select(key):
                for w in grid.winfo_children():
                    w.destroy()
                for r, (how, what) in enumerate(rows[key]):
                    tk.Label(grid, text=how, bg=UK.C.N0, fg=UK.C.TEXT, font=UK.F_UI,
                             anchor='w').grid(row=r, column=0, sticky='w', pady=3)
                    tk.Label(grid, text=what, bg=UK.C.N0, fg=UK.C.SUB, font=UK.F_UI,
                             anchor='w', justify='left').grid(row=r, column=1, sticky='w', padx=(18, 0), pady=3)
                for name, btn in buttons:
                    btn.set_kind('selected' if name == key else 'tab')
            for key in rows:
                btn = UK.Pill(tabs, key, lambda key=key: select(key), kind='tab', bg=UK.C.N0)
                btn.pack(side='left', padx=(0, 8))
                buttons.append((key, btn))
            b.bind('<Configure>', lambda e: head.configure(wraplength=max(200, e.width - 10)))
            select('采集')
        self._show_sheet('帮助', build)

    def background(self, work, done, failed=None):
        def run():
            try:
                value = work()
            except Exception as exc:
                self.q.put(('ui', (failed or (lambda e: self._notice(str(e))), exc)))
            else:
                self.q.put(('ui', (done, value)))
        threading.Thread(target=run, daemon=True).start()

    def _sync_nav(self, title='实时会议'):
        target = '历史会议' if title in ('历史会议', '会议记录') else title
        for label, btn in getattr(self, 'nav_buttons', {}).items():
            btn.set_kind('selected' if target in label else 'ghost')

    def show_live(self):
        self._sync_nav()
        self._sheet_stack = []
        self._close_sheet()

    def open_history(self):
        import frontend_views
        self._sheet_stack = []          # 从导航进来是「重新开始」，不叠旧层
        frontend_views.open_history(self)

    def open_reading(self):
        import frontend_views
        frontend_views.open_reading(self)

    def set_display(self, label):
        self.display_mode = {'双语':'both', '原文':'source', '译文':'translation'}[label]
        self.full_render()
        self.float_sync(force=True)

    def _flush_before_switch(self, action):
        # Pause the producer, let its already queued events persist, then flush off the Tk thread.
        self.reader.paused = True
        self.flash('正在保存当前会议…')
        def drained():
            if not self.q.empty():
                self.root.after(25, drained)
                return
            store = self.save_store
            if store and hasattr(store, 'flush'):
                def failed(exc):
                    self.reader.paused = self.paused
                    self._notice('保存未完成，保留当前会议：'+str(exc))
                self.background(lambda: store.flush(), lambda _: action(), failed)
            else:
                action()
        self.root.after(1, drained)

    def new_meeting(self):
        def start():
            self._do_clear()
            self.paused = self.reader.paused = False
            if hasattr(self, 'btn_pause'):
                self.btn_pause.set_text('暂停采集')
            self.flash('已新建独立会议记录')
        self._confirm('归档当前记录并开始新会议？同名会议也会分别保存。', lambda: self._flush_before_switch(start), '新建会议', push=False)

    def end_meeting(self):
        def end():
            self.paused = self.reader.paused = True
            if hasattr(self, 'btn_pause'):
                self.btn_pause.set_text('继续采集')
            self.status.set('已归档 · 暂停采集', UK.C.SUB)
            store = self.save_store
            if store and hasattr(store, 'flush'):
                self.background(lambda: store.flush(), lambda _: self.flash('会议已保存，可导出或新建下一场'))
            else:
                self.flash('会议已保存，可导出或新建下一场')
        self._confirm('结束本次采集并保存会议？仍可导出整场内容。', end, '结束并归档', push=False)

    def resume_meeting(self, session):
        if self.save_path and Path(self.save_path).with_suffix('.sqlite3') == session.path:
            self.show_live()
            self.flash('当前已经在记录这场会议')
            return
        def resume():
            store = self.save_store
            def load():
                if store and hasattr(store, 'flush'):
                    store.flush()
                return MR.load_session(session.path)
            generation = self.reader.generation
            def apply(value):
                if generation != self.reader.generation:
                    self._notice('当前会议已切换，请重新选择要续接的会议。')
                    return
                title, records = value
                self._do_clear(start_reader=False)
                self.reader.stop()
                self.records = records
                if session.legacy:
                    # Legacy imports become a fresh structured session; the original is untouched.
                    for r in self.records:
                        self.autosave(r)
                else:
                    self.save_store = AsyncCaptionStore(CAPTION_DIR, title, path=session.path,
                        on_error=lambda exc: self.q.put(('save_error', str(exc))))
                    self.save_path = self.save_store.path
                self.reader = CaptionReader(self.q, mode=self.mode)
                seed = copy.deepcopy(records)
                self.reader.records = seed
                self.reader.open_pairs = [(r['name'],r.get('raw',r['name']),r['text']) for r in seed]
                self.reader.open_records = seed
                self.reader.known_raw = {r.get('raw',r['name']) for r in seed}
                self.reader.known_norm = {r['name'] for r in seed}
                self.reader.meeting_title = title
                self.reader.first_pass = False
                self.paused = self.reader.paused = False
                self.reader.start()
                self._meeting_txt = '续接：'+title
                self.lbl_meeting.configure(text=self._meeting_txt)
                self.lbl_save.configure(text='已保存 · '+self.save_path.stem[-12:])
                self.btn_pause.set_text('暂停采集')
                self.full_render()
                self.show_live()
            def failed(exc):
                self.reader.paused = self.paused
                self._notice('无法读取所选会议，已保留当前记录：'+str(exc))
            self.background(load, apply, failed)
        self._confirm(f'保存当前会议并续接「{session.title}」？请确认当前字幕采集源就是这场会议。', lambda: self._flush_before_switch(resume), '续接', push=False)

    # ---------------- 退出流程 ----------------
    # 两个老问题的根都在这里：
    #
    # 1) 关窗卡死：旧 on_close 在 Tk 主线程上直接 save_store.close()，而
    #    AsyncCaptionStore.close() 会 join 写线程 30 秒。写线程一旦卡在
    #    os.fsync / os.replace，「关窗」就等于「界面假死 30 秒」，然后弹出
    #    「保存未完成 · 等待自动保存结束超时」的二选一对话框。
    #
    # 2) 退出不彻底：旧 on_close 第一步就摘掉托盘图标，之后才检查保存。
    #    用户点「否」取消关闭时，图标已经没了、任务栏按钮也被
    #    WS_EX_TOOLWINDOW 摘掉 —— 窗口彻底失去入口（「点退出反而杀不死」）。
    #
    # 现在的顺序：先请保存线程收尾（不阻塞） → 收尾完再摘图标 → 关窗。
    # 任何一步被取消都会把采集/托盘轮询恢复成原来能用的样子。
    def on_close(self):
        """关闭主窗口。可被重复调用；可被用户在保存超时对话框里取消。

        正常路径**完全同步**收尾（写线程几十毫秒就退出），行为和旧版一致，
        也保证了「调用方不用再进事件循环」这件事继续成立 —— 不少测试和
        test_startup 都是 root.after(150, on_close) 之后直接结束主循环的。
        只有写线程在 CLOSE_SYNC_BUDGET 内没退出时才转后台轮询；那才是需要
        保住 UI 响应性的真场面，代价是把销毁推迟到之后的事件循环里。
        """
        if getattr(self, "_closing", False):
            return                      # 已在真正退出，别再走一遍
        if getattr(self, "_quit_pending", False):
            return                      # 已有一次退出流程在等保存
        self._quit_pending = True
        self._was_paused = bool(getattr(self.reader, "paused", False))
        log_note("主窗口被关闭 → 开始退出流程（PID %d）" % os.getpid())
        # 采集先停：退出期间不该再产生新记录，否则保存线程永远追不上。
        self.reader.paused = True
        store = self.save_store          # 固定住对象：轮询期间 save_store 可能被换掉
        done = self._start_store_close(store)
        if done is None or done.wait(CLOSE_SYNC_BUDGET):
            self._quit_now()            # 常见路径：同步收尾，不惊动事件循环
            return
        self._flash_saving()
        self._poll_store_close(store, time.monotonic() + CLOSE_ASYNC_BUDGET)

    def _start_store_close(self, store):
        """投递关闭请求，返回一个「写线程已结束」的 Event（无 store 时返回 None）。

        优先用 caption_store 的非阻塞 API；万一拿到的是旧版对象（没有
        close_async），退回后台线程里调阻塞 close()，绝不在主线程上 join。
        """
        if store is None:
            return None
        done = threading.Event()
        if hasattr(store, "close_async") and hasattr(store, "wait_closed"):
            def wait():
                store.wait_closed(CLOSE_ASYNC_BUDGET + 30)
                done.set()
            try:
                store.close_async()
            except Exception:
                done.set()
                return done
            threading.Thread(target=wait, name="close-wait", daemon=True).start()
            return done

        def legacy_close():
            try:
                store.close()
            except Exception as exc:
                log_note("自动保存收尾失败：%s" % exc)
            finally:
                done.set()
        threading.Thread(target=legacy_close, name="close-legacy", daemon=True).start()
        return done

    def _poll_store_close(self, store, deadline):
        """后台等待写线程收尾，到点仍未结束就交给用户决定。"""
        finished = self._store_closed(store)
        if finished:
            self._quit_now()
            return
        if time.monotonic() >= deadline:
            self._ask_close_anyway(store)
            return
        self._close_poll = self.root.after(CLOSE_POLL_MS,
                                          lambda: self._poll_store_close(store, deadline))

    @staticmethod
    def _store_closed(store):
        """写线程是否已经收尾（兼容只有 closed/thread 的旧对象）。"""
        if store is None:
            return True
        try:
            thread = getattr(store, "thread", None)
            if thread is not None:
                return not thread.is_alive()
            return bool(getattr(store, "closed", True))
        except Exception:
            return True

    def _flash_saving(self):
        """把「正在保存」显示出来（此刻主线程已经空出来，界面不会假死）。"""
        try:
            self.lbl_save.configure(text="正在保存…", fg=UK.C.SUB)
            self.root.update_idletasks()
        except Exception:
            pass

    def _ask_close_anyway(self, store):
        """保存迟迟收不了尾：给「等待 / 放弃并关闭 / 取消关闭」三个出路。

        旧版本是 askyesno 二选一，且不管选哪个都已经把托盘图标摘了 —— 选
        「否」之后窗口就再也找不回来。这里改三选一，且一律先恢复入口。
        """
        self._quit_pending = False      # 结果未定，允许再次点关闭
        try:
            ans = messagebox.askyesnocancel(
                "保存还没结束",
                "自动保存比平时慢很多（可能正被杀毒软件或网络盘占着）。\n\n"
                "是：再等一会儿（最多 %d 秒）\n"
                "否：放弃等待，仍然关闭（已逐条写入的记录不会丢）\n"
                "取消：不关闭，继续使用" % int(CLOSE_ASYNC_BUDGET),
                parent=self.root)
        except Exception:
            ans = None
        if ans is True:
            self._quit_pending = True
            self._flash_saving()
            self._poll_store_close(store, time.monotonic() + CLOSE_ASYNC_BUDGET)
            return
        if ans is False:
            log_note("用户选择放弃等待自动保存，强制退出")
            self._quit_now()
            return
        self._cancel_quit()             # 取消（或对话框出错＝保守地不关）

    def _cancel_quit(self):
        """取消退出：把入口和采集恢复成关闭前能用的样子。"""
        self._quit_pending = False
        self._closing = False
        self._cancel_close_poll()
        tray = getattr(self, "tray", None)
        if tray is not None and getattr(tray, "icon_present", True) is not False:
            self._schedule_tray_poll()  # 图标还在，把轮询链接上就行
        elif tray is not None:
            # 图标已被摘掉，但这个对象还能重新挂（tray.py 的 start() 支持再来一轮）。
            # 挂不上就必须把任务栏按钮还回去，否则窗口没有入口。
            try:
                reattached = tray.start()
            except Exception:
                reattached = False
            if reattached:
                self._schedule_tray_poll()
            else:
                self._restore_taskbar_entry()
        else:
            self._restore_taskbar_entry()
        try:
            self.reader.paused = self._was_paused
        except Exception:
            pass
        try:
            self.lbl_save.configure(text=self._save_label_text(), fg=UK.C.T3)
        except Exception:
            pass
        self.flash("已取消关闭，记录仍在自动保存")

    def _save_label_text(self):
        path = getattr(self, "save_path", None)
        if path is not None:
            return f"已保存 · {Path(path).stem[-12:]}"
        return "等待字幕 · 自动保存至本机"

    def _cancel_close_poll(self):
        job = getattr(self, "_close_poll", None)
        self._close_poll = None
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass

    def _restore_taskbar_entry(self):
        """没有托盘兜底时，把任务栏按钮还回去（有它在，用户就找得回窗口）。"""
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            u.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            u.GetAncestor.restype = wintypes.HWND
            u.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            u.GetWindowLongW.restype = ctypes.c_long
            u.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int,
                                         ctypes.c_long)
            hwnd = u.GetAncestor(self.root.winfo_id(), 2)
            if not hwnd:
                return
            ex = u.GetWindowLongW(hwnd, -20)
            u.SetWindowLongW(hwnd, -20, (ex | 0x00040000) & ~0x00000080)
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass

    def _quit_now(self):
        """真正退出：此刻才摘图标、停线程、销毁窗口。"""
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self._quit_pending = False
        self._cancel_close_poll()
        for cancel in getattr(self, "_export_jobs", []):
            cancel.set()
        try:
            self.reader.stop()
        except Exception:
            pass
        try:
            if self.svc:
                self.svc.stop()
        except Exception:
            pass
        # 图标放到这里才摘：在此之前任何一步被取消，托盘都还是活的。
        try:
            if self.tray is not None:
                # stop(wait) 返回托盘线程是否已收工。False 表示它卡在模态循环里，
                # 但图标本身已经被摘掉（tray.stop 保证先摘图标再等线程），
                # 所以照样可以往下销毁窗口——那个线程是 daemon，进程退出即随之结束。
                if not self.tray.stop(wait=2.0):
                    log_note("托盘线程未在 2s 内收工（图标已摘），继续退出")
                self.tray = None
        except Exception:
            pass
        try:
            self._clear_session_stamp()   # 正常收尾：下次启动不再提示"未正常结束"
        except Exception:
            pass
        log_note("退出流程完成（PID %d）" % os.getpid())
        try:
            for job in self.root.tk.call('after', 'info'):
                self.root.after_cancel(job)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


_MUTEX_NAME = "Local\\TeamsSub_SingleInstance"
_MUTEX_HANDLE = None      # 持有句柄：进程存活期间锁才有效（勿 CloseHandle）
_INSTANCE_FILE = APP_DIR / "cache" / "instance.pid"


# ---------------------------------------------------------------------------
# 实例登记
# ---------------------------------------------------------------------------
# 互斥量只能回答「有没有实例在跑」，回答不了「它在哪」。可见窗口一消失，
# 新启动的进程就彻底失去线索，只能弹一句「请到任务管理器结束」。所以启动
# 时把 PID 写到磁盘：新实例照着 PID 反查进程，确实是个 python 就直接问一句
# 「要不要结束它、启动新版本」，用户不必再开任务管理器。
def _write_instance_file():
    try:
        _INSTANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _INSTANCE_FILE.write_text("%d %d" % (os.getpid(), int(time.time())),
                                  encoding="utf-8")
    except Exception:
        pass


def _read_instance_pid() -> int:
    try:
        return int(_INSTANCE_FILE.read_text(encoding="utf-8").split()[0])
    except Exception:
        return 0


def _clear_instance_file():
    try:
        _INSTANCE_FILE.unlink()
    except Exception:
        pass


def _pid_state(pid: int) -> str:
    """进程状态：'none' 不存在 / 'python' 是 python 解释器 / 'other' 其它程序。

    用 QueryFullProcessImageNameW 读映像名，比 WMI、psutil 都轻，也不受
    命令行长度限制。注意必须显式声明 argtypes：64 位下 HANDLE 若按默认
    c_int 传参会被截断，OpenProcess 直接失败。
    """
    if pid <= 0:
        return "none"
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD))
        k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)          # QUERY_LIMITED_INFORMATION
        if not h:
            return "none"
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return "other"
            name = buf.value.lower().rsplit("\\", 1)[-1]
            return "python" if name in ("python.exe", "pythonw.exe") else "other"
        finally:
            k32.CloseHandle(h)
    except Exception:
        return "none"


def _kill_instance(pid: int) -> bool:
    """结束指定进程。venv 的 pythonw.exe 是转发器，必须连子进程一起收。"""
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        import subprocess
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, creationflags=0x08000000,
                       timeout=10)                       # CREATE_NO_WINDOW
    except Exception:
        pass
    for _ in range(20):
        if _pid_state(pid) == "none":
            return True
        time.sleep(0.15)
    try:                                                # 兜底：直接 TerminateProcess
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k32.TerminateProcess.restype = wintypes.BOOL
        h = k32.OpenProcess(0x0001, False, pid)         # PROCESS_TERMINATE
        if h:
            k32.TerminateProcess(h, 1)
            k32.CloseHandle(h)
    except Exception:
        pass
    for _ in range(20):
        if _pid_state(pid) == "none":
            return True
        time.sleep(0.15)
    return False


def _find_orphan_pids():
    """反查残留的 teamsub.py 进程（排除自己与自己的祖先进程）。

    注意 venv 的 pythonw.exe 是转发器：它用同样的命令行再起一个基础解释器，
    父子两个进程的命令行都含 teamsub.py。若按命令行盲杀，很可能把「自己的
    父进程」当成残留实例干掉（/T 会连同自己一起收）。所以必须顺着
    ParentProcessId 往上找到自己的祖先链，逐个排除。
    """
    import subprocess
    me = os.getpid()
    rows = {}
    try:
        script = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\""
                  " | ForEach-Object { \"$($_.ProcessId)|$($_.ParentProcessId)"
                  "|$($_.CommandLine)\" }")
        res = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                              "-Command", script],
                             capture_output=True, text=True, timeout=20,
                             creationflags=0x08000000)
        for line in (res.stdout or "").splitlines():
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            rows[pid] = (ppid, parts[2])
    except Exception:
        pass

    ancestors, cur = {me}, me
    for _ in range(8):
        nxt = rows.get(cur, (0, ""))[0]
        if not nxt or nxt in ancestors:
            break
        ancestors.add(nxt)
        cur = nxt

    # 只认命令行里带 teamsub.py 的进程；PID 文件若有更快的线索就置顶
    cands = [p for p, (_pp, cmd) in rows.items()
             if "teamsub.py" in cmd.lower() and p not in ancestors]
    hint = _read_instance_pid()
    if hint in cands:
        cands.remove(hint)
        cands.insert(0, hint)
    if not rows:                      # WMI 不可用时的尽力而为
        if hint and hint not in ancestors and _pid_state(hint) == "python":
            cands.append(hint)
    return [p for p in cands if _pid_state(p) == "python"]


def _find_teamssub_window():
    """找正在运行实例的**主窗口**，主窗口不在时再退回悬浮条。

    优先级不能只看「谁可见」：最小化后主窗口仍然是 visible(iconic)，而悬浮
    条也 visible，枚举顺序里条子往往先出现。若随便返回一个，点快捷方式就
    会去还原那条没有标题栏的小字幕条，主窗口依旧停在最小化——用户看到的是
    「点了没反应」。所以主窗口永远排第一，无论它是 normal 还是 iconic；
    隐藏（withdrawn）的主窗口也排在悬浮条之前，调用方用 ShowWindow 能唤出。
    """
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    main_vis, main_hid, float_vis, float_hid = [], [], [], []

    @EnumProc
    def cb(hwnd, lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if title.startswith("TeamsSub"):
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                # 只认 Tk 的顶层窗口，避开 TclNotifier / IME 之类的辅助窗口
                if cls.value == "TkTopLevel":
                    shown = bool(user32.IsWindowVisible(hwnd))
                    if "悬浮" in title:
                        (float_vis if shown else float_hid).append(hwnd)
                    else:
                        (main_vis if shown else main_hid).append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    for bucket in (main_vis, main_hid, float_vis, float_hid):
        if bucket:
            return bucket[0]
    return None


def _focus_teamssub(hwnd):
    """还原并置前旧窗口；置前被系统拦截时闪烁任务栏提醒。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.ShowWindow(hwnd, 9)                 # SW_RESTORE（隐藏窗口也会被唤出）
    user32.SetForegroundWindow(hwnd)

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD), ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD)]

    info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 3 | 12, 5, 0)
    user32.FlashWindowEx(ctypes.byref(info))   # FLASHW_ALL | FLASHW_TIMERNOFG


def _window_pid(hwnd) -> int:
    """窗口所属进程 PID。"""
    try:
        import ctypes
        from ctypes import wintypes
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except Exception:
        return 0


def _proc_start_time(pid: int) -> float:
    """进程创建时间（epoch 秒）。取不到返回 0。

    需要判断「旧实例是不是在我改完代码之后启动的」：用创建时间对比源文件
    修改时间即可，不必额外落盘任何状态。
    """
    if pid <= 0:
        return 0.0
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.GetProcessTimes.argtypes = (
            wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME))
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        h = k32.OpenProcess(0x1000, False, pid)     # QUERY_LIMITED_INFORMATION
        if not h:
            return 0.0
        try:
            created, exited = wintypes.FILETIME(), wintypes.FILETIME()
            kernel, user = wintypes.FILETIME(), wintypes.FILETIME()
            if not k32.GetProcessTimes(h, ctypes.byref(created), ctypes.byref(exited),
                                       ctypes.byref(kernel), ctypes.byref(user)):
                return 0.0
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return ticks / 1e7 - 11644473600.0      # FILETIME → Unix epoch
        finally:
            k32.CloseHandle(h)
    except Exception:
        return 0.0


def _sources_newer_than(pid: int) -> bool:
    """旧实例启动后，磁盘上的源码是否又被改过（说明该重启加载新版）。"""
    started = _proc_start_time(pid)
    if not started:
        return False
    newest = 0.0
    try:
        for path in APP_DIR.glob("*.py"):
            try:
                newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    except Exception:
        return False
    return newest > started + 2.0       # 2s 容差：避开复制/缓存的时间抖动


def _acquire_single_instance() -> bool:
    """命名互斥量防双开。已有实例在跑时的处理，按「用户想干什么」分层：

    - **窗口还在**（最小化、被隐藏、被别的窗口盖住）→ 直接还原置前，本次
      启动退出，一个框都不弹。双击快捷方式 = 把窗口还给我，这是桌面应用的
      基本预期；旧版在这里弹「是否关闭当前实例并启动新版本」，等于用重启
      问题回答「我的窗口哪去了」。
    - **窗口还在，但源码比旧实例新**（改完代码点快捷方式）→ 这才值得问一次
      「是否重启加载新版」，因为不重启确实看不到改动。
    - **一个窗口都没有**（隐形残留）→ 反查 PID，问是否结束残留并启动。
    """
    try:
        import ctypes
        global _MUTEX_HANDLE
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        from ctypes import wintypes
        k32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        k32.CreateMutexW.restype = wintypes.HANDLE
        k32.CloseHandle.argtypes = (wintypes.HANDLE,)
        k32.CloseHandle.restype = wintypes.BOOL
        user32 = ctypes.windll.user32
        # 关键：必须用 use_last_error=True + ctypes.get_last_error()。
        # 用 windll.kernel32.GetLastError() 取值会被 Python 内部 Win32 调用
        # 冲掉，导致「根本没实例却误判已在运行」的幽灵锁。
        def acquire():
            global _MUTEX_HANDLE
            handle = k32.CreateMutexW(None, False, _MUTEX_NAME)
            existed = ctypes.get_last_error() == 183
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            if existed:
                # 不能持有重复句柄，否则旧进程退出后互斥量仍被本进程保活。
                k32.CloseHandle(handle)
                return False
            _MUTEX_HANDLE = handle
            return True

        def wait_lock(rounds, delay):
            """等旧实例退出、锁可获取；返回 True 表示已拿到锁。"""
            for _ in range(rounds):
                time.sleep(delay)
                if acquire():
                    return True
            return False

        if acquire():
            return True

        # 旧实例可能正处于启动中（窗口还没显示）或正在退出（锁将释放）：
        # 耐心轮询，别急着报错——连点两次快捷方式就会命中这种状态。
        # 冷启动可能被杀毒扫描拖到 10s+，等待窗口不能太短。
        hwnd = None
        for _ in range(50):                     # 最多等 ~20s
            if acquire():                       # 旧实例已退出 → 直接启动新版
                return True
            hwnd = _find_teamssub_window()
            if hwnd:
                break
            time.sleep(0.4)

        if hwnd is None:
            # 锁被占着却一个窗口都没有：隐形残留实例。自己能清就自己清，
            # 别把「去任务管理器结束进程」这种活推给用户。
            victims = _find_orphan_pids()
            if not victims:
                user32.MessageBoxW(
                    None,
                    "TeamsSub 已在运行，但未找到它的窗口。\n\n"
                    "请打开任务管理器 →「详细信息」页，结束 pythonw.exe，再试一次。",
                    "TeamsSub", 0x30)           # MB_ICONWARNING
                return False
            ans = user32.MessageBoxW(
                None,
                "检测到一个没有窗口的 TeamsSub 残留实例（PID %s）。\n"
                "它正占用单实例锁，导致新版本无法启动。\n\n"
                "是否结束该进程并启动新版本？" % "、".join(str(p) for p in victims),
                "TeamsSub", 0x04 | 0x20)        # MB_YESNO | MB_ICONQUESTION
            if ans != 6:
                return False
            for p in victims:
                _kill_instance(p)
            _clear_instance_file()
            if wait_lock(40, 0.25):             # 最多等 ~10s
                return True
            user32.MessageBoxW(None, "残留实例未能退出，请重启电脑后再试。",
                               "TeamsSub", 0x30)
            return False

        old_pid = _window_pid(hwnd)
        if _pid_state(old_pid) == "none":
            # 询问/等待期间旧实例自己退了，锁多半已释放
            if wait_lock(8, 0.25):
                return True

        if not _sources_newer_than(old_pid):
            # 源码没动过：用户只是想把窗口找回来。静默还原，本次退出。
            _focus_teamssub(hwnd)
            return False

        ans = user32.MessageBoxW(
            None,
            "TeamsSub 正在运行，且程序文件已更新。\n\n"
            "是否关闭当前实例并启动新版本？\n"
            "（点「否」则保留正在运行的旧版本）",
            "TeamsSub", 0x04 | 0x20)            # MB_YESNO | MB_ICONQUESTION
        if ans != 6:                            # IDNO → 聚焦旧窗口
            _focus_teamssub(hwnd)
            return False
        if _pid_state(old_pid) == "none":
            if wait_lock(8, 0.25):
                return True

        user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE → on_close 优雅退出
        if wait_lock(30, 0.4):                    # 最多等 ~12s
            return True
        _focus_teamssub(hwnd)
        user32.MessageBoxW(None, "旧实例未能退出，已保留原实例。",
                           "TeamsSub", 0x30)
        return False
    except Exception:
        return True        # 单实例机制自身出错时宁可照常启动，不挡用户


def main():
    if not _acquire_single_instance():
        return
    _write_instance_file()
    root = tk.Tk()
    install_crash_log(root)
    try:
        root.iconbitmap(default=str(APP_DIR / "teamsub.ico"))
    except Exception:
        pass
    try:
        app = App(root)
        if tray_mod is not None:
            try:
                tray = tray_mod.TrayIcon(APP_DIR / "teamsub.ico")
                if tray.start():
                    app.attach_tray(tray)
                else:
                    log_note("托盘图标挂载失败 → 保留任务栏按钮")
            except Exception:
                log_exception(*sys.exc_info(), "托盘初始化失败（退回任务栏模式）")
        root.mainloop()
    except Exception:
        log_exception(*sys.exc_info(), "主循环异常")
        raise
    finally:
        _clear_instance_file()


if __name__ == "__main__":
    main()

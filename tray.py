# -*- coding: utf-8 -*-
"""系统托盘图标：任务栏不占位，右下角常驻入口。

Windows 的规则是「任务栏上有按钮，才能从任务栏找回窗口」。一旦把窗口设成
WS_EX_TOOLWINDOW（从任务栏隐藏），就必须有一个托盘图标兜底——否则最小化
之后程序就成了没有入口的幽灵进程，继续握着单实例锁，用户点快捷方式只会
得到「已经在运行」。这两件事是配套的，不能只做一半。

实现上刻意不引入 pystray：它自带一套主循环，和 Tk 抢线程容易互相卡死。
这里用一个独立的隐藏窗口接收托盘消息，跑在守护线程里，只把「用户点了什么」
通过队列交给 Tk 主线程处理，两边不共享任何窗口句柄状态。
"""
import queue
import threading
import traceback

import win32api
import win32con
import win32gui

WM_TRAY = win32con.WM_USER + 20
WM_TASKBAR_CREATED = win32gui.RegisterWindowMessage("TaskbarCreated")

CMD_SHOW = "show"
CMD_FLOAT = "float"
CMD_QUIT = "quit"

_MENU_SHOW, _MENU_FLOAT, _MENU_QUIT = 1, 2, 3


class TrayIcon:
    """托盘图标。start() 返回是否真的挂上去了。

    挂不上去（找不到 explorer、图标文件损坏等）时返回 False，调用方应当
    **继续保留任务栏按钮**——这是安全的一侧，绝不能在没有兜底入口的情况下
    把窗口从任务栏摘掉。

    退出路径上「摘图标」必须是可确认、可重复的一步：用户点「退出」之后还会
    被问一句「保存没完成，仍然关闭吗」，答「否」时窗口要能完整地留在原地
    （图标、轮询都得还在）。所以 stop() 只负责把图标摘掉并把托盘线程收干净，
    调用方先让它变绿，再决定要不要真的销毁窗口；状态随时可查。
    """

    #: 隐藏窗口的类名。每个实例一个——RegisterClass 对同名类会抛「类已存在」，
    #: 而「取消退出 → 重新挂图标」这条路要求同一个对象能再来一轮。
    _CLASS_PREFIX = "TeamsSubTrayWindow"

    def __init__(self, icon_path, tip="TeamsSub", on_error=None):
        self.icon_path = str(icon_path)
        self.tip = tip
        self.on_error = on_error      # 托盘侧错误回调（主程序接日志）
        self.events = queue.Queue()
        self._hwnd = None
        self._icon = None
        self._atom = None
        self._ready = threading.Event()
        self._ok = False
        self._thread = None
        self._class_name = "%s_%d" % (self._CLASS_PREFIX, id(self))
        self._lock = threading.RLock()      # 保护下面的状态：任意线程都能 stop()
        self._stop_lock = threading.Lock()  # 串行化 stop()，连调两次不会互相打断
        self._icon_added = False            # 图标当前算不算挂在托盘上
        self._icon_removed = False          # NIM_DELETE 是否真的走完
        self._stopping = False              # 是否已经要求托盘线程收工

    # ---------------- 生命周期 ----------------
    def start(self, wait=3.0):
        """挂图标并启动消息泵线程；返回是否真的挂上去了。

        允许在 stop() 之后再次调用（用户取消退出 → 重新挂图标）：上一次的
        隐藏窗口与窗口类已经在收尾时清掉，这里只是重来一轮。

        上一次的线程要是还卡着（stop() 等超时了），就直接返回 False 而不是硬
        再挂一套：两套线程会抢同一个 _hwnd。调用方看到 False 会保留任务栏按钮，
        那才是安全的一侧。
        """
        with self._stop_lock:              # 与 stop() 互斥，别让 start/stop 交错
            old = self._thread
            if old is not None and old.is_alive():
                # 上一次 stop() 等超时了（线程卡在模态循环里）。这时不能再挂一套
                # 窗口：两套线程会抢同一个 _hwnd。宁可返回 False——调用方看到
                # False 会保留任务栏按钮，那才是安全的一侧。
                return False
            self._ready.clear()
            with self._lock:
                self._icon_added = False
                self._icon_removed = False
                self._stopping = False
            self._ok = False
            self._thread = threading.Thread(target=self._run, name="tray",
                                            daemon=True)
            self._thread.start()
            self._ready.wait(wait)
            return self._ok

    def _run(self):
        hinst = None
        try:
            hinst = win32api.GetModuleHandle(None)
            # 上一次异常退出可能把同名类留在这个进程里，先清掉再注册
            try:
                win32gui.UnregisterClass(self._class_name, hinst)
            except Exception:
                pass
            wc = win32gui.WNDCLASS()
            wc.hInstance = hinst
            wc.lpszClassName = self._class_name
            wc.lpfnWndProc = self._wndproc
            atom = win32gui.RegisterClass(wc)
            self._atom = atom
            # 这个窗口必须**可见**，哪怕只有一个像素、还挪到了屏幕外。
            #
            # 原因：右键菜单用 TrackPopupMenu 弹出，而它要求「拥有菜单的窗口」
            # 是可见的前台窗口，否则会立刻返回 0、什么也不显示 —— 现象就是
            # 「右键图标没反应」。实测：同一个 hwnd，隐藏时 0.00s 空返回，
            # 可见时正常阻塞到用户选完或点走。
            #
            # 所以这里建一个 1x1、位于 (-32000,-32000) 的 WS_POPUP 窗口，
            # 加 WS_EX_TOOLWINDOW 让它不进任务栏/Alt+Tab，用 SW_SHOWNOACTIVATE
            # 显示（不抢焦点，用户不会看到闪一下）。托盘图标本身仍挂在这个窗口上。
            self._hwnd = win32gui.CreateWindow(
                atom, self._class_name,
                win32con.WS_POPUP | win32con.WS_VISIBLE,
                -32000, -32000, 1, 1,
                0, 0, hinst, None)
            try:
                win32gui.SetWindowLong(
                    self._hwnd, win32con.GWL_EXSTYLE,
                    win32gui.GetWindowLong(self._hwnd, win32con.GWL_EXSTYLE)
                    | win32con.WS_EX_TOOLWINDOW)
                win32gui.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
            except Exception:
                pass
            self._icon = win32gui.LoadImage(
                0, self.icon_path, win32con.IMAGE_ICON, 0, 0,
                win32con.LR_LOADFROMFILE | win32con.LR_DEFAULTSIZE)
            self._ok = bool(self._add())
        except Exception:
            self._ok = False
        finally:
            self._ready.set()
        if not self._ok:
            self._cleanup_window(hinst)
            return
        # stop() 可能在 start() 返回之前就来了（退出流程不看有没有挂成功），
        # 那就不要进消息泵了，直接收尾。
        with self._lock:
            stopping = self._stopping
        if stopping:
            self._cleanup_window(hinst)
            return
        self._pump(hinst)

    def _pump(self, hinst=None):
        """消息泵：WM_CLOSE / WM_QUIT 之后回来收尾。"""
        try:
            win32gui.PumpMessages()
        except Exception:
            pass
        finally:
            self._cleanup_window(hinst)

    def _remove_icon(self, hwnd):
        """把图标从托盘上摘掉；只在它确实挂着的时候发 NIM_DELETE。

        重复 NIM_DELETE 会被外壳判成错误，所以「摘过就别再摘」这件事必须由
        这里保证——stop() 连调两次就是靠它保持干净的。
        """
        with self._lock:
            if not self._icon_added:
                self._icon_removed = True    # 没挂着 = 没有残留，重复调用也走到这
                return True
            # 先记「不挂了」再真去删：删的过程中别的线程进来也不会重复发
            self._icon_added = False
        removed = False
        if hwnd:
            try:
                win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (hwnd, 0))
                removed = True
            except Exception:
                removed = False     # 外壳报错也可能是已经没了；如实记，别硬说成功
        with self._lock:
            self._icon_removed = removed
        return removed

    def _cleanup_window(self, hinst=None):
        """托盘线程收尾：摘图标、销毁隐藏窗口、注销窗口类。

        图标也要在这里摘——stop() 可能在 start() 还没挂上窗口时就到了，那条
        路上没人再发 NIM_DELETE，漏掉就会在托盘里留一个点不动的死图标。

        不注销窗口类的话，同一进程里再挂一次托盘会撞上「类已存在」——用户
        取消退出后重新挂图标正好会走到这里。
        """
        if hinst is None:
            try:
                hinst = win32api.GetModuleHandle(None)
            except Exception:
                pass
        with self._lock:
            hwnd, atom = self._hwnd, self._atom
        self._remove_icon(hwnd)
        with self._lock:
            self._hwnd = None
            self._atom = None
        try:
            if hwnd and win32gui.IsWindow(hwnd):
                win32gui.DestroyWindow(hwnd)
        except Exception:
            pass
        try:
            if atom and hinst:
                win32gui.UnregisterClass(self._class_name, hinst)
        except Exception:
            pass

    def stop(self, wait=2.0):
        """摘掉托盘图标并结束托盘线程；返回托盘线程是否已结束。

        幂等：第二次调用不会再去删图标（同一个图标重复 NIM_DELETE 会被外壳
        判成错误），也不会再等一遍线程。任意线程可调。wait 是等待上界——托盘
        线程可能正卡在右键菜单的模态循环里，退出流程不能被它拖住，超时就返回
        False 交给调用方决定要不要强退（图标这时已经摘掉了，用户不会看到死图标）。
        """
        with self._stop_lock:          # 串行化：两个线程同时点退出也只走一遍
            with self._lock:
                hwnd = self._hwnd
                thread = self._thread
                self._stopping = True
            # 摘图标和「结束线程」是两件独立的事：图标先摘，用户立刻看到效果；
            # 线程可能卡在模态循环里，等不到就如实返回 False。
            self._remove_icon(hwnd)
            try:
                if hwnd and win32gui.IsWindow(hwnd):
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
            if thread is None:
                return True
            if thread is threading.current_thread():
                return False           # 在托盘线程里调 stop()：没法等自己
            thread.join(max(0.0, float(wait)))
            return not thread.is_alive()

    # ---------------- 状态（调用方与测试都靠它断言） ----------------
    @property
    def running(self):
        """托盘线程是否还在跑（消息泵活着 = 图标还能响应点击）。"""
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    @property
    def alive(self):
        """running 的同义词，读起来顺一点。"""
        return self.running

    @property
    def icon_present(self):
        """图标是否还挂在托盘上（stop() 之后必须为 False）。"""
        return bool(self._icon_added)

    @property
    def icon_removed(self):
        """NIM_DELETE 是否真的走完了（给退出流程留的可断言痕迹）。"""
        return bool(self._icon_removed)

    # ---------------- 图标 ----------------
    def _add(self):
        """挂图标（含 explorer 重启后的重挂），并把状态记成「挂着」。"""
        with self._lock:
            # 正在摘图标/退出时，explorer 恰好重启不该把图标又挂回来——
            # 那会留下一个点了没反应的死图标，属于最难排查的那类残留。
            if self._stopping:
                return False
        nid = (self._hwnd, 0,
               win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
               WM_TRAY, self._icon, self.tip)
        try:
            # pywin32 成功时返回 None（不是 True）；只有失败才会返回 False
            # 或抛异常。用 bool() 判断会把「成功」当成「失败」。
            added = win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, nid) is not False
        except Exception:
            added = False
        if added:
            with self._lock:
                # 重挂成功说明图标又在了；之前记的「已摘除」不再成立
                self._icon_added = True
                self._icon_removed = False
        return added

    def set_tip(self, text):
        """鼠标悬停滞留文本（会议状态、连接情况）。"""
        self.tip = (text or "TeamsSub")[:120]
        if not self._hwnd:
            return
        try:
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_MODIFY,
                (self._hwnd, 0, win32gui.NIF_TIP, WM_TRAY, self._icon,
                 self.tip))
        except Exception:
            pass

    def balloon(self, title, text):
        """气泡提示（收进托盘时告诉用户去哪找）。"""
        if not self._hwnd:
            return
        try:
            win32gui.Shell_NotifyIcon(
                win32gui.NIM_MODIFY,
                (self._hwnd, 0, win32gui.NIF_INFO, WM_TRAY, self._icon,
                 self.tip, title, text, 0x00000001))   # NIIF_INFO
        except Exception:
            pass

    def _note_error(self, what):
        """记一笔托盘侧的错误。

        托盘线程里没有界面可弹，异常又不能往外抛（会把消息泵打断），所以写成
        一行带堆栈的记录。排查「右键没反应」这类「什么都不发生」的问题时，
        这行日志往往是唯一线索。
        """
        try:
            if self.on_error is not None:
                self.on_error(what)
                return
        except Exception:
            pass
        try:
            print("[tray] %s\n%s" % (what, traceback.format_exc()))
        except Exception:
            pass

    # ---------------- 消息 ----------------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                if lparam in (win32con.WM_LBUTTONUP,
                              win32con.WM_LBUTTONDBLCLK):
                    self.events.put(CMD_SHOW)
                elif lparam == win32con.WM_RBUTTONUP:
                    self._popup(hwnd)
                return 0
            if msg == WM_TASKBAR_CREATED:
                # explorer 重启后旧图标全部失效，必须重新挂
                self._add()
                return 0
            if msg == win32con.WM_COMMAND:
                # 兜底：_popup 用 TPM_RETURNCMD 取命令号，正常不会走到这里。
                # 但万一别处（或系统辅助工具）发来 WM_COMMAND，也要能响应，
                # 只是不要让它和返回值那条路重复执行——所以这里同样按命令号派发，
                # 由调用方保证不会两边都发。
                cmd = win32api.LOWORD(wparam)
                action = {_MENU_SHOW: CMD_SHOW,
                          _MENU_FLOAT: CMD_FLOAT,
                          _MENU_QUIT: CMD_QUIT}.get(cmd)
                if action:
                    self.events.put(action)
                return 0
            if msg == win32con.WM_DESTROY:
                win32gui.PostQuitMessage(0)
                return 0
        except Exception:
            # 消息处理里出错不能往外抛（会把消息泵打断），但也不能一声不响：
            # 「图标点不动」类问题的唯一线索就在这里。
            self._note_error("托盘消息处理失败 (msg=0x%04x)" % msg)
        try:
            # pywin32 的 WNDPROC 要求返回 int（不是 (result, func) 元组），
            # 也不接受 None，否则只会打印「cannot be converted to LRESULT」。
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam) or 0
        except Exception:
            return 0

    @staticmethod
    def _force_foreground(hwnd):
        """把 hwnd 变成前台窗口；后台进程靠 AttachThreadInput 拿到临时授权。

        先直接试一次（窗口本来就属于前台线程时能成）；不成再挂接前台线程的
        输入队列重试。全程不抛异常 —— 置前失败只影响菜单自动收起，不该让
        菜单弹不出来。
        """
        try:
            import ctypes
            u = ctypes.windll.user32
            k32 = ctypes.windll.kernel32
            if u.GetForegroundWindow() == hwnd:
                return True
            try:
                u.SetForegroundWindow(hwnd)
                if u.GetForegroundWindow() == hwnd:
                    return True
            except Exception:
                pass
            fg = u.GetForegroundWindow()
            fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
            my_tid = k32.GetCurrentThreadId()
            attached = False
            try:
                if fg_tid and fg_tid != my_tid:
                    attached = bool(u.AttachThreadInput(my_tid, fg_tid, True))
                u.BringWindowToTop(hwnd)
                u.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    try:
                        u.AttachThreadInput(my_tid, fg_tid, False)
                    except Exception:
                        pass
            return u.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def _popup(self, hwnd):
        """弹出右键菜单（阻塞到用户选完或点走）。

        两个坑，都是「右键毫无反应」的直接原因：

        1. **分隔符不能传 None**。新版 pywin32 会抛
           ``TypeError: None is not a valid string in this context``，而分隔符是
           菜单里最先建好的一项 —— 一抛，整个菜单都建不起来。必须传空串 ""。
        2. **异常不能静默吞掉**。这条消息是从 ``_wndproc`` 进来的，那里为了
           不让外壳消息把线程搞挂包了 try/except pass，于是菜单建失败时用户
           看到的就是「右键没反应」，日志里一个字都没有。这里自己兜住并记一笔。
        """
        menu = win32gui.CreatePopupMenu()
        try:
            win32gui.AppendMenu(menu, win32con.MF_STRING, _MENU_SHOW,
                                "显示主窗口")
            win32gui.AppendMenu(menu, win32con.MF_STRING, _MENU_FLOAT,
                                "显示 / 隐藏悬浮字幕")
            win32gui.AppendMenu(menu, win32con.MF_SEPARATOR, 0, "")
            win32gui.AppendMenu(menu, win32con.MF_STRING, _MENU_QUIT,
                                "退出 TeamsSub")
            x, y = win32gui.GetCursorPos()
            # TrackPopupMenu 有一条很老的约定：菜单弹出前，拥有它的窗口必须是
            # **前台窗口**，否则它会立刻返回、什么也不画 —— 用户看到的就是
            # 「右键没反应」。实测同一个窗口：是前台时正常阻塞到用户选完，
            # 不是前台时 0.00s 空返回、连错误码都不给。
            #
            # 问题在于后台进程无权直接抢前台：SetForegroundWindow 会失败（
            # 不抛异常、只返回 0），所以不能只依赖它。标准做法是先用
            # AttachThreadInput 把本线程的输入队列挂到当前前台线程上，取得
            # 临时授权后再置前，最后解除挂接。
            self._force_foreground(hwnd)
            try:
                # TPM_RETURNCMD 让 TrackPopupMenu 把命令号**返回**给我们，
                # 而不是自己再发一条 WM_COMMAND。不这样写的话，选中项会走两条
                # 路（返回值 + WM_COMMAND），同一个动作被执行两次；而且不带这个
                # 标志时返回值只是布尔（选中=1），根本分不清用户点了哪一项。
                choice = win32gui.TrackPopupMenu(
                    menu,
                    win32con.TPM_LEFTALIGN | win32con.TPM_RIGHTBUTTON
                    | win32con.TPM_RETURNCMD,
                    x, y, 0, hwnd, None)
            except Exception:
                self._note_error("托盘菜单弹出失败")
                return
            # 必须给外壳补一条 WM_NULL：否则菜单消失后前台焦点会卡在别处，
            # 下一次点菜单外部不收起（经典 TrackPopupMenu 用法要求）。
            win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
            if choice:                  # 0 = 用户点走了没选，什么都不做
                cmd = {_MENU_SHOW: CMD_SHOW,
                       _MENU_FLOAT: CMD_FLOAT,
                       _MENU_QUIT: CMD_QUIT}.get(choice)
                if cmd:
                    self.events.put(cmd)
                else:
                    self._note_error("托盘菜单返回了未知命令 %r" % (choice,))
        except Exception:
            self._note_error("托盘菜单构建失败")
        finally:
            try:
                win32gui.DestroyMenu(menu)
            except Exception:
                pass

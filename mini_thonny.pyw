"""Mini Thonny —— 受 Thonny 启发的简化版 Python 编辑器 / 调试器（单文件版）。

这个文件把原来的 ``minithonny`` 包（app / editor / debugger / variables /
shell / theme）合并成了一个可以直接运行的脚本，只依赖标准库：

    python mini_thonny.py
    python mini_thonny.py examples\\demo.py

文件内部按原来的模块顺序排列：

    1. 主题配色（theme）
    2. 调试引擎（debugger）
    3. 编辑器（editor，含语法补全、括号匹配、跳转定义）
    4. 大纲 / 查找定义面板（outline）
    5. 变量 / 调用栈面板（variables）
    6. 交互式 Shell（shell）
    7. 主窗口与入口（app）
"""

from __future__ import annotations

import ast
import builtins
import codeop
import contextlib
import difflib
import inspect
import io
import itertools
import keyword
import os
import queue
import re
import sys
import threading
import tokenize
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, simpledialog, ttk

APP_NAME = "Mini Thonny"
DEFAULT_FONT_SIZE = 11

SHORTCUTS_TEXT = """\
运行 / 继续            F5
调试运行（首行暂停）    Ctrl+F5
停止                   Shift+F5
单步跳过               F6
单步进入               F7
单步跳出               F8
切换断点               F9

新建文件               Ctrl+N
打开文件               Ctrl+O
保存                   Ctrl+S
另存为                 Ctrl+Shift+S
关闭标签               Ctrl+W
查找                   Ctrl+F
替换                   Ctrl+H
查找下一个 / 上一个     F3 / Shift+F3
跳转到行               Alt+G
转到符号（大纲）        Ctrl+R
注释 / 取消注释         Ctrl+/
缩进 / 反缩进          Tab / Shift+Tab
括号跳转               Ctrl+]
复制当前行             Ctrl+D
删除当前行             Ctrl+Shift+K
行上移 / 下移          Alt+↑ / Alt+↓
放大 / 缩小字号         Ctrl+= / Ctrl+-
恢复默认字号           Ctrl+0

补全列表               Alt+/（或输入时自动弹出）
确认补全               Tab / Enter / .
跳转定义               Ctrl+左键 / F12
查看引用               Shift+F12
返回上一个位置          Alt+←
前进到下一个位置        Alt+→
"""

SHELL_BANNER = (
    "Mini Thonny Shell —— 输入 Python 语句直接执行；"
    "调试暂停时可以使用当前帧的变量。\n"
)


# ---------------------------------------------------------------------- DPI
# Windows 上 Tk 默认是 DPI-unaware 的：系统会把整个窗口按缩放比例拉伸成位图，
# 所以在 125% / 150% 的屏幕上看起来又糊又扁。这里在创建 Tk 之前显式声明
# “每显示器 DPI 感知”，再按真实 DPI 设置 tk scaling，让字体按点数正确放大。


def set_process_dpi_aware():
    """声明进程 DPI 感知（必须在 ``tk.Tk()`` 之前调用，越早越好）。"""
    if sys.platform != "win32":
        return
    import ctypes

    try:  # Windows 10 1703+：PER_MONITOR_AWARE_V2
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:  # Windows 8.1+：PER_MONITOR_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:  # Windows Vista+：SYSTEM_DPI_AWARE
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def system_scale_factor():
    """返回系统的缩放倍数（96dpi -> 1.0，144dpi -> 1.5）。"""
    if sys.platform != "win32":
        return 1.0
    import ctypes

    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            return dpi / 96.0
    except Exception:
        pass
    return 1.0


set_process_dpi_aware()


# ======================================================================
# 1. 主题配色
# ======================================================================
# Thonny 的默认浅色主题接近 IDLE 的配色，这里沿用同样的直观风格；
# 深色主题则参考常见的编辑器配色。

LIGHT = {
    "name": "light",
    "bg": "#ffffff",
    "fg": "#1a1a1a",
    "caret": "#1a1a1a",
    "select_bg": "#cfe3ff",
    "select_fg": "#000000",
    "panel_bg": "#f3f3f3",
    "border": "#d0d0d0",
    "status_bg": "#ececec",
    "gutter_bg": "#f0f0f0",
    "gutter_fg": "#a0a0a0",
    "gutter_active_fg": "#b03a2e",
    "current_line": "#fdf6d8",
    "error_line": "#ffdede",
    "breakpoint": "#d94f45",
    # 语法
    "keyword": "#0000aa",
    "string": "#a31515",
    "comment": "#8c8c8c",
    "number": "#098658",
    "builtin": "#267f99",
    "function": "#795e26",
    "class": "#267f99",
    "decorator": "#aa5d00",
    "self": "#7a3e9d",
    # 编辑辅助
    "current_paren": "#ffe08a",
    "match_all": "#d8ecd8",
    "find_all": "#ffe9a8",
    "find_current": "#ffc966",
    "def_mark": "#888888",
    "readout_bg": "#fff8dc",
    "readout_fg": "#666666",
    # 补全弹窗
    "popup_bg": "#ffffff",
    "popup_select": "#cfe3ff",
    # shell
    "shell_stderr": "#b00020",
    "shell_prompt": "#0000aa",
    "shell_system": "#7a7a7a",
}

DARK = {
    "name": "dark",
    # 基础界面颜色
    "bg": "#1e1e1e",          # VS Code 编辑器背景
    "fg": "#cccccc",          # VS Code 默认文本颜色
    "caret": "#aeafad",       # VS Code 光标颜色
    "select_bg": "#264f78",   # VS Code 选中文本背景
    "select_fg": "#ffffff",   # VS Code 选中文本前景
    "panel_bg": "#252526",    # VS Code 侧边栏/面板背景
    "border": "#3e3e42",      # VS Code 边框颜色
    "status_bg": "#007acc",   # VS Code 状态栏背景 (可选，这里用蓝色更现代，或者用 #252526)

    # 行号区域 (Gutter)
    "gutter_bg": "#1e1e1e",   # 与编辑器背景一致
    "gutter_fg": "#858585",   # VS Code 行号颜色
    "gutter_active_fg": "#c586c0", # 当前行指示器颜色 (使用粉色突出显示)

    # 调试标记
    "current_line": "#282828", # 当前行高亮 (比背景稍亮)
    "error_line": "#5a1d1d",   # 错误行高亮 (暗红色)
    "breakpoint": "#e06c75",   # 断点颜色 (红色)

    # 语法高亮 (VS Code Dark Modern 标准色)
    "keyword": "#569cd6",     # 蓝色 (if, else, def, class...)
    "string": "#ce9178",      # 橙色 ("hello", 'world')
    "comment": "#6a9955",     # 绿色 (# comment)
    "number": "#b5cea8",      # 浅绿色 (123, 3.14)
    "builtin": "#4ec9b0",     # 青色 (print, len, list...)
    "function": "#dcdcaa",    # 浅黄色 (my_function)
    "class": "#4ec9b0",       # 青色 (MyClass)
    "decorator": "#dcdcaa",   # 浅黄色 (@decorator) - VS Code 中通常与函数同色或略不同
    "self": "#9cdcfe",        # 浅蓝色 (self, cls) - VS Code 中变量通常为浅蓝

    # 编辑辅助
    "current_paren": "#5a4a22",   # 括号匹配高亮
    "match_all": "#2d4a2d",       # 同词高亮
    "find_all": "#5a4a1a",        # 全部匹配
    "find_current": "#8a6a1a",    # 当前匹配
    "def_mark": "#8a8a8a",        # 定义行波浪线
    "readout_bg": "#3a3a00",
    "readout_fg": "#dddddd",
    # 补全弹窗
    "popup_bg": "#252526",
    "popup_select": "#264f78",
    # Shell 输出颜色
    "shell_stderr": "#f48771", # 浅红色 (错误输出)
    "shell_prompt": "#569cd6", # 蓝色 (>>> 提示符)
    "shell_system": "#808080", # 灰色 (系统消息)
}


def get_theme(dark: bool):
    return DARK if dark else LIGHT


def read_source(path):
    """尽量用正确的编码读取源码（utf-8 / gbk 回退），并统一成 LF。"""
    with open(path, "rb") as handle:
        data = handle.read()
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def is_identifier_char(ch):
    return ch.isalnum() or ch == "_"


def module_search_paths(root, exclude=None):
    """从 ``root`` 出发，收集可能包含本地模块的目录（自上而下、不递归进
    明显的虚拟环境），用于把 ``import xxx`` 解析成本地文件。"""
    root = os.path.abspath(root)
    if os.path.isfile(root):
        root = os.path.dirname(root)
    exclude = exclude or set()
    dirs = []
    directory = root
    for _ in range(4):
        if not directory or directory in exclude:
            break
        dirs.append(directory)
        entries = ()
        try:
            entries = os.listdir(directory)
        except OSError:
            pass
        for name in ("src", "lib", "lib64", "packages", "code"):
            candidate = os.path.join(directory, name)
            if os.path.isdir(candidate) and candidate not in exclude:
                dirs.append(candidate)
        parent = os.path.dirname(directory)
        if parent == directory:
            break
        directory = parent
    return dirs


def resolve_module_file(name, search_dirs):
    """把模块名解析成本地 .py 文件路径；解析不到返回 ``None``。"""
    if not name:
        return None
    parts = name.split(".")
    stem = os.sep.join(parts)
    for base in search_dirs:
        candidate = os.path.join(base, stem + ".py")
        if os.path.isfile(candidate):
            return candidate
        candidate = os.path.join(base, stem, "__init__.py")
        if os.path.isfile(candidate):
            return candidate
    return None


def resolve_relative_module_file(current_file, level, name):
    """解析 ``from .pkg import mod`` 这类相对导入。"""
    if not current_file:
        return None
    directory = os.path.dirname(os.path.abspath(current_file))
    for _ in range(max(0, level - 1)):
        directory = os.path.dirname(directory)
    stem = os.sep.join(name.split(".")) if name else ""
    candidate = os.path.join(directory, stem, "__init__.py") if name else None
    if candidate and os.path.isfile(candidate):
        return candidate
    candidate = os.path.join(directory, stem + ".py") if name else None
    if candidate and os.path.isfile(candidate):
        return candidate
    if not name:
        init = os.path.join(directory, "__init__.py")
        if os.path.isfile(init):
            return init
    return None


# ======================================================================
# 2. 调试引擎
# ======================================================================
# 设计说明（对应 Thonny 的 frontend / backend 拆分）：
#
# * Thonny 把用户程序放在独立的 back-end 进程里运行，用 XML-RPC 通信，
#   断点则通过在源码中插入 ``breakpoint()`` 来实现。
# * 这里做了简化：用户程序跑在一个工作线程里，用 ``sys.settrace`` 实现
#   单步 / 断点，线程与界面之间只用一个消息队列通信。
#   好处是变量可以直接从 frame 里读取，不需要序列化；
#   代价是程序与 IDE 共享同一个解释器（见 README 的“已知限制”）。
#
# 所有与界面交互的数据都通过 ``queue.Queue`` 传递，界面线程用 ``after``
# 轮询，这样界面在程序运行或暂停时都不会卡住。


class _StopProgram(BaseException):
    """从 trace 函数里抛出，用来中断用户程序。

    继承 BaseException，避免被用户代码里的 ``except Exception`` 吞掉。
    """


class FrameInfo:
    """一帧运行栈的快照。frame 对象在暂停期间可被界面线程安全读取。"""

    __slots__ = ("frame", "filename", "lineno", "function")

    def __init__(self, frame):
        self.frame = frame
        self.filename = frame.f_code.co_filename
        self.lineno = frame.f_lineno
        self.function = frame.f_code.co_name

    @property
    def short_filename(self):
        return os.path.basename(self.filename) or self.filename


class _QueueWriter:
    """把 print / sys.stderr 的输出转发到消息队列。

    只有运行用户程序的线程才走队列；IDE 自己（Tk 主线程）的输出仍然
    写到原来的流里，避免界面报错被误当成程序输出。
    """

    def __init__(self, messages: queue.Queue, tag: str, fallback=None):
        self._messages = messages
        self._tag = tag
        self._fallback = fallback
        self._thread = threading.current_thread()

    def write(self, text):
        if not text:
            return 0
        if threading.current_thread() is self._thread:
            self._messages.put((self._tag, text))
        elif self._fallback is not None:
            try:
                self._fallback.write(text)
            except Exception:
                pass
        return len(text)

    def flush(self):
        pass

    def isatty(self):
        return False

    def writable(self):
        return True


class DebugSession:
    """一次程序运行（可能带调试）。

    状态：``running``、``paused``；程序结束后两者都为 False，
    但 ``globals`` 仍保留，方便在 Shell 里继续检查结果。
    """

    def __init__(self):
        self._messages = queue.Queue()
        self._cond = threading.Condition()
        self._command = None
        self._stop_requested = False
        self._input_waiters = {}
        self._thread = None

        self.globals = None
        self.frames = []
        self.paused = False
        self.running = False
        self.exit_code = None

        self._mode = "continue"
        self._step_depth = 0
        self._entry_pending = False
        self._source = ""
        self._path = ""
        self._user_files = set()
        self._breakpoints = set()
        self._path_cache = {}

    # ------------------------------------------------------------------ API
    def start(self, path, source, breakpoints, stop_on_entry=False):
        """启动程序。

        breakpoints: ``{文件绝对路径: {行号, ...}}``
        stop_on_entry: 为 True 时在第一行暂停（“调试运行”）。
        """
        self._path = path
        self._source = source
        self._user_files = {path} | set(breakpoints)
        self._breakpoints = {
            (self._norm(p), line)
            for p, lines in breakpoints.items()
            for line in lines
        }
        self._entry_pending = stop_on_entry
        self._mode = "continue"
        self.running = True
        self.paused = False
        self.exit_code = None

        self._thread = threading.Thread(
            target=self._run, name="mini-thonny-program", daemon=True
        )
        self._thread.start()

    def send(self, command):
        """继续执行：``continue`` / ``step_over`` / ``step_into`` / ``step_out``。"""
        with self._cond:
            if not self.paused:
                return False
            if command not in ("continue", "step_over", "step_into", "step_out"):
                return False
            self._command = command
            self._cond.notify_all()
            return True

    def request_stop(self):
        with self._cond:
            self._stop_requested = True
            self._cond.notify_all()

    def provide_input(self, token, value):
        """界面把 ``input()`` 的输入交回给程序。value 为 None 表示取消。"""
        waiter = self._input_waiters.get(token)
        if waiter is not None:
            waiter[1] = value
            waiter[0].set()

    def update_breakpoints(self, breakpoints):
        """运行期间增删断点时即时生效。"""
        self._breakpoints = {
            (self._norm(p), line)
            for p, lines in breakpoints.items()
            for line in lines
        }
        self._user_files = self._user_files | set(breakpoints)

    def get_message(self):
        try:
            return self._messages.get_nowait()
        except queue.Empty:
            return None

    # ------------------------------------------------------------- internal
    def _norm(self, path):
        # 每执行一行都会调用，做个缓存避免反复 abspath。
        cached = self._path_cache.get(path)
        if cached is None:
            cached = os.path.normcase(os.path.abspath(path))
            self._path_cache[path] = cached
        return cached

    def _put(self, message):
        self._messages.put(message)

    def _run(self):
        path = self._path
        namespace = {
            "__name__": "__main__",
            "__file__": path,
            "__package__": None,
            "__spec__": None,
            "__builtins__": self._make_builtins(),
        }
        self.globals = namespace

        old_trace = sys.gettrace()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        old_argv = sys.argv
        old_path = list(sys.path)
        script_dir = os.path.dirname(os.path.abspath(path))
        sys.argv = [path]
        sys.path.insert(0, script_dir)
        sys.stdout = _QueueWriter(self._messages, "stdout", old_stdout)
        sys.stderr = _QueueWriter(self._messages, "stderr", old_stderr)

        try:
            code = compile(self._source, path, "exec")
            sys.settrace(self._trace)
            exec(code, namespace)
            self.exit_code = 0
        except _StopProgram:
            self.exit_code = None
            self._put(("stopped",))
        except SystemExit as exc:
            if isinstance(exc.code, str):
                self._put(("stderr", exc.code + "\n"))
            self.exit_code = 0 if exc.code is None else (
                exc.code if isinstance(exc.code, int) else 1
            )
        except BaseException:
            self.exit_code = 1
            location = self._last_user_location()
            self._put(("exception", self._format_exception(), location))
        finally:
            sys.settrace(old_trace)
            sys.stdout, sys.stderr = old_stdout, old_stderr
            sys.argv = old_argv
            sys.path[:] = old_path
            self.running = False
            self.paused = False
            self._put(("finished", self.exit_code))

    def _make_builtins(self):
        # 给程序一个定制的 input()，这样界面可以用对话框收集输入。
        namespace = dict(vars(builtins))
        namespace["input"] = self._input
        return namespace

    def _input(self, prompt=""):
        if self._stop_requested:
            raise _StopProgram()
        token = object()
        event = threading.Event()
        self._input_waiters[token] = [event, None]
        self._put(("input_request", str(prompt), token))
        while not event.wait(0.1):
            if self._stop_requested:
                self._input_waiters.pop(token, None)
                raise _StopProgram()
        waiter = self._input_waiters.pop(token)
        value = waiter[1]
        if value is None:
            raise EOFError("输入被取消")
        return value

    # ----------------------------------------------------------------- trace
    def _trace(self, frame, event, arg):
        if self._stop_requested:
            raise _StopProgram()
        # 只跟踪用户自己的文件，避免进入标准库时大量开销。
        if frame.f_code.co_filename not in self._user_files:
            return None
        if event == "line":
            self._on_line(frame)
        return self._trace

    def _depth(self, frame):
        depth = 0
        while frame is not None:
            if frame.f_code.co_filename in self._user_files:
                depth += 1
            frame = frame.f_back
        return depth

    def _on_line(self, frame):
        if self._stop_requested:
            raise _StopProgram()

        line = frame.f_lineno
        key = (self._norm(frame.f_code.co_filename), line)
        if key in self._breakpoints:
            self._pause(frame, "断点")
            return

        mode = self._mode
        if mode == "step_into":
            self._pause(frame, "单步")
        elif mode in ("step_over", "step_out"):
            if self._depth(frame) <= self._step_depth:
                self._pause(frame, "单步")
        elif self._entry_pending:
            self._entry_pending = False
            self._pause(frame, "开始调试")

    def _pause(self, frame, reason):
        with self._cond:
            self.paused = True
            self._mode = "paused"
            self._entry_pending = False
        self.frames = self._build_stack(frame)
        self._put(("paused", self.frames, reason))
        self._wait_for_command(frame)

    def _wait_for_command(self, frame):
        with self._cond:
            while self._command is None and not self._stop_requested:
                self._cond.wait(0.1)
            command = self._command
            self._command = None

        self.paused = False
        if self._stop_requested:
            raise _StopProgram()
        if command is not None:
            self._apply_command(command, frame)
        self.frames = []

    def _apply_command(self, command, frame):
        if command == "continue":
            self._mode = "continue"
        elif command == "step_into":
            self._mode = "step_into"
        elif command == "step_over":
            self._mode = "step_over"
            self._step_depth = self._depth(frame)
        elif command == "step_out":
            self._mode = "step_out"
            self._step_depth = self._depth(frame) - 1

    def _build_stack(self, frame):
        frames = []
        while frame is not None and len(frames) < 100:
            if frame.f_code.co_filename in self._user_files:
                frames.append(FrameInfo(frame))
            frame = frame.f_back
        return frames

    def _last_user_location(self):
        """找到 traceback 里最后一个用户文件位置，用于在编辑器中标红。"""
        location = None
        tb = sys.exc_info()[2]
        while tb is not None:
            filename = tb.tb_frame.f_code.co_filename
            if filename in self._user_files:
                location = (filename, tb.tb_lineno)
            tb = tb.tb_next
        return location

    def _format_exception(self):
        """只显示用户代码的调用栈，去掉 IDE 内部的 exec 帧。"""
        etype, evalue, tb = sys.exc_info()
        entries = [
            entry
            for entry in traceback.extract_tb(tb)
            if entry.filename in self._user_files
        ]
        parts = ["Traceback (most recent call last):\n"] if entries else []
        parts.extend(traceback.format_list(entries))
        parts.extend(traceback.format_exception_only(etype, evalue))
        return "".join(parts)


# ======================================================================
# 3. 编辑器
# ======================================================================

INDENT = "    "
SYNTAX_TAGS = (
    "keyword",
    "string",
    "comment",
    "number",
    "builtin",
    "function",
    "class",
    "decorator",
    "self",
)
BUILTIN_NAMES = set(dir(builtins))

OPEN_BRACKETS = "([{"
CLOSE_BRACKETS = ")]}"
BRACKET_PAIR = {"(": ")", "[": "]", "{": "}"}
BRACKET_MIRROR = {")": "(", "]": "[", "}": "{"}
BRACKETS = OPEN_BRACKETS + CLOSE_BRACKETS

_FSTRING_START = getattr(tokenize, "FSTRING_START", -1000)
_FSTRING_END = getattr(tokenize, "FSTRING_END", -2000)


def build_symbol_index(text):
    """解析源码，返回 (符号表, 模块名映射, 类成员映射)。

    符号表：``名称 -> [(kind, 行号, 签名), ...]``，按出现顺序排列。
    语法有错误时（代码写到一半），先尝试补齐括号再解析，尽量给出结果。
    """
    symbols = {}
    module_map = {}
    class_members = {}

    def add(name, kind, node, signature=""):
        if not name:
            return
        symbols.setdefault(name, []).append(
            (kind, getattr(node, "lineno", 1), signature)
        )

    def target_names(target):
        if isinstance(target, ast.Name):
            return [target.id]
        if isinstance(target, (ast.Tuple, ast.List)):
            return [
                element.id
                for element in target.elts
                if isinstance(element, ast.Name)
            ]
        return []

    def call_signature(node):
        try:
            return "(" + ast.unparse(node.args) + ")"
        except Exception:
            return "()"

    def parse(source):
        try:
            return ast.parse(source)
        except SyntaxError:
            pass
        stack = []
        opener = {"(": ")", "[": "]", "{": "}"}
        closer = {")": "(", "]": "[", "}": "{"}
        in_string = None
        previous = ""
        for ch in source:
            if in_string:
                if ch == "\\":
                    continue
                if ch == in_string:
                    in_string = None
                continue
            if ch in "\"'" and previous != "\\":
                in_string = ch
            elif ch in opener:
                stack.append(ch)
            elif ch in closer and stack and stack[-1] == closer[ch]:
                stack.pop()
            previous = ch
        try:
            return ast.parse(source + "".join(reversed(stack)) + "\n")
        except SyntaxError:
            return None

    tree = parse(text)
    if tree is None:
        return symbols, module_map, class_members

    def handle_import(node):
        for alias in node.names:
            top = alias.name.split(".")[0]
            name = alias.asname or top
            module_map.setdefault(name, alias.name)
            add(name, "import", node, "← " + alias.name)

    def handle_import_from(node):
        base = ("." * (node.level or 0)) + (node.module or "")
        for alias in node.names:
            name = alias.asname or alias.name
            if alias.name == "*":
                continue
            module_map.setdefault(name, (base or ".") + ":" + alias.name)
            add(name, "import", node, "← " + (base or ".") + "." + alias.name)

    def scan_body(body, class_name):
        for node in body:
            if isinstance(node, ast.Import):
                if class_name is None:
                    handle_import(node)
            elif isinstance(node, ast.ImportFrom):
                if class_name is None:
                    handle_import_from(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                add(node.name, "function", node, call_signature(node))
                if class_name is not None:
                    class_members.setdefault(class_name, set()).add(node.name)
                scan_body(node.body, None)
            elif isinstance(node, ast.ClassDef):
                add(node.name, "class", node, "()")
                class_members.setdefault(node.name, set())
                scan_body(node.body, node.name)
            elif isinstance(node, ast.Assign):
                value_kind = None
                if isinstance(node.value, ast.Lambda):
                    value_kind = "function"
                elif isinstance(node.value, ast.Call) and isinstance(
                    node.value.func, ast.Name
                ):
                    value_kind = "class"
                for target in node.targets:
                    for name in target_names(target):
                        if class_name is not None:
                            class_members.setdefault(class_name, set()).add(name)
                        add(name, value_kind or "variable", node, "")
            elif isinstance(node, ast.AnnAssign):
                for name in target_names(node.target):
                    if class_name is not None:
                        class_members.setdefault(class_name, set()).add(name)
                    add(name, "variable", node, "")
            elif isinstance(node, ast.AugAssign):
                for name in target_names(node.target):
                    add(name, "variable", node, "")
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.AsyncWith)):
                scan_body(node.body, class_name)
                for extra in (
                    getattr(node, "orelse", None),
                    getattr(node, "finalbody", None),
                ):
                    if extra:
                        scan_body(extra, class_name)
                for handler in getattr(node, "handlers", None) or []:
                    scan_body(handler.body, class_name)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                scan_body(node.body, class_name)
                if getattr(node, "orelse", None):
                    scan_body(node.orelse, class_name)

    scan_body(tree.body, None)
    return symbols, module_map, class_members


def build_context_maps(text):
    """从源码提取 类 -> 属性集合、名字 -> 模块 两张映射，供补全使用。"""
    class_members = {}
    module_map = {}
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return class_members, module_map

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            members = class_members.setdefault(node.name, set())
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    members.add(item.name)
                elif isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            members.add(target.id)
                elif isinstance(item, ast.AnnAssign) and isinstance(
                    item.target, ast.Name
                ):
                    members.add(item.target.id)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_map[(alias.asname or alias.name).split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                module_map[alias.asname or alias.name] = (
                    node.module + "." + alias.name
                )
    return class_members, module_map


def scope_completions(source, offset):
    """从当前文件源码收集补全候选（纯标准库的轻量“静态分析”）。"""
    class_members, module_map = build_context_maps(source)
    candidates = set(dir(builtins))

    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None

    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                candidates.add(node.id)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                candidates.add(node.name)
            elif isinstance(node, ast.arg):
                candidates.add(node.arg)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    candidates.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        candidates.add(alias.asname or alias.name)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                candidates.add(node.name)
            elif isinstance(node, (ast.Global, ast.Nonlocal)):
                candidates.update(node.names)

    for match in re.finditer(r"\b[A-Za-z_]\w*\b", source):
        name = match.group()
        if len(name) <= 20:
            candidates.add(name)

    for members in class_members.values():
        candidates.update(members)
    candidates.update(class_members)
    candidates.update(module_map)

    used = {
        match.group()
        for match in re.finditer(r"\b[A-Za-z_]\w*\b", source[: max(0, offset)])
    }
    ranked = sorted(
        candidates,
        key=lambda n: (n not in used, n not in keyword.kwlist, n.lower()),
    )
    return ranked[:900], class_members, module_map


def expression_before(source, offset):
    """返回光标处 ``base.`` 表达式中的 ``base`` 部分（不含结尾的点）。"""
    before = source[:offset]
    if not before.endswith("."):
        return ""
    match = re.search(r"([A-Za-z_][\w.]*)(?:\([^()]*\))?\.$", before, re.S)
    if match:
        return match.group(1)
    if before.endswith(")."):
        depth = 0
        index = len(before) - 2
        while index >= 0:
            ch = before[index]
            if ch == ")":
                depth += 1
            elif ch == "(":
                depth -= 1
                if depth == 0:
                    head = before[:index]
                    tail = re.search(r"([A-Za-z_][\w.]*)$", head)
                    return tail.group(1) if tail else ""
            index -= 1
    return ""


def resolve_member_path(base_expr, class_members, module_map):
    """把 ``obj.attr.attr`` 解析成 (可选类名, 可选模块对象, 最后一段前缀)。

    返回 ``(None, None, prefix)`` 表示只能按“最后一个点号之后”给出通用候选。
    """
    segments = base_expr.split(".")
    if not segments:
        return None, None, ""
    prefix = segments[-1]
    if not prefix.isidentifier() and prefix != "":
        return None, None, prefix
    segments = segments[:-1]

    if not segments:
        return None, None, prefix
    head = segments[0]
    rest = segments[1:]
    if head == "self":
        return None, None, prefix
    class_name = None
    module_obj = None
    if head in class_members:
        class_name = head
    elif head in module_map:
        try:
            module_obj = __import__(module_map[head], fromlist=["*"])
        except BaseException:
            module_obj = None
    elif ".".join(segments) in module_map:
        try:
            module_obj = __import__(".".join(segments), fromlist=["*"])
        except BaseException:
            module_obj = None
    else:
        return None, None, prefix

    for attr in rest:
        if class_name is not None:
            if attr[:1].isupper() and attr in class_members:
                class_name = attr
            else:
                return None, None, prefix
        elif module_obj is not None:
            try:
                module_obj = getattr(module_obj, attr)
            except AttributeError:
                return None, None, prefix
    return class_name, module_obj, prefix


def tokenize_completion_context(source, offset):
    """粗略判断光标是否位于字符串 / 注释内部（补全前过滤用）。"""
    if offset <= 0 or not source:
        return False, False
    line_start = source.rfind("\n", 0, offset) + 1
    line_end = source.find("\n", offset)
    if line_end == -1:
        line_end = len(source)
    line = source[line_start:line_end]
    column = offset - line_start
    stripped = line.lstrip()
    if stripped.startswith("#"):
        return False, True
    hash_at = line.find("#")
    if hash_at != -1 and hash_at < column:
        before = line[:hash_at]
        if before.count("'") % 2 == 0 and before.count('"') % 2 == 0:
            return False, True
    before = line[:column]
    single = before.count("'") - before.count("\\'")
    double = before.count('"') - before.count('\\"')
    if single % 2 or double % 2:
        return True, False
    return False, False


def collect_definition_positions(app, word, current_editor):
    """在其它已打开标签页与同目录 .py 文件中收集 ``word`` 的定义位置。"""
    results = []
    seen = set()
    current_path = current_editor.path if current_editor else None

    for editor in app.editors:
        if editor is current_editor:
            continue
        source = editor.get_content()
        symbols, _, _ = build_symbol_index(source)
        for kind, lineno, signature in symbols.get(word, ()):
            if kind not in ("function", "class", "variable", "import"):
                continue
            try:
                line_text = editor.text.get("%d.0" % lineno, "%d.end" % lineno)
            except tk.TclError:
                line_text = ""
            key = (id(editor), lineno)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                (editor.title + " 第%d行" % lineno, editor, lineno, 0, line_text)
            )

    target_name = os.path.basename(current_path) if current_path else None
    for directory in module_search_paths(current_path or os.getcwd()):
        try:
            entries = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in entries:
            if not name.endswith(".py") or name == target_name:
                continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            if app._find_editor(path) is not None:
                continue
            try:
                text = read_source(path)
            except OSError:
                continue
            symbols, _, _ = build_symbol_index(text)
            lines = text.splitlines()
            for kind, lineno, signature in symbols.get(word, ()):
                if kind not in ("function", "class"):
                    continue
                key = (path, lineno)
                if key in seen:
                    continue
                seen.add(key)
                line_text = lines[lineno - 1] if lineno <= len(lines) else ""
                results.append(
                    ("%s 第%d行" % (name, lineno), path, lineno, 0, line_text)
                )
    return results


class CompletionPopup:
    """编辑器下方的补全列表（Toplevel + Listbox，无额外依赖）。"""

    ICONS = {
        "keyword": "◆",
        "builtin": "★",
        "function": "ƒ",
        "method": "ƒ",
        "class": "©",
        "module": "▣",
        "variable": "▸",
        "attribute": "·",
        "import": "↦",
        "magic": "◆",
    }

    def __init__(self, editor):
        self.editor = editor
        self.text = editor.text
        self.items = []
        self.top = None
        self.listbox = None
        self.scrollbar = None
        self._visible = False
        self._applied = False
        self._offset = 0

    # -------------------------------------------------------------- 生命周期
    def _ensure(self):
        if self.top is not None:
            return
        self.top = tk.Toplevel(self.text)
        self.top.wm_overrideredirect(True)
        self.top.attributes("-topmost", True)
        frame = ttk.Frame(self.top, padding=1)
        frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            frame,
            activestyle="single",
            exportselection=False,
            takefocus=0,
            relief="flat",
            highlightthickness=1,
            borderwidth=0,
            font=self.editor.app.code_font,
            selectmode="single",
            height=9,
            width=46,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.scrollbar = ttk.Scrollbar(
            frame, orient="vertical", command=self.listbox.yview
        )
        self.scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=self.scrollbar.set)
        self.listbox.bind("<ButtonRelease-1>", self._on_click, add="+")
        self.apply_theme(self.editor.app.theme)
        self.top.withdraw()

    def apply_theme(self, theme):
        if self.top is None:
            return
        self.listbox.configure(
            bg=theme["popup_bg"],
            fg=theme["fg"],
            selectbackground=theme["popup_select"],
            selectforeground=theme["select_fg"],
            highlightbackground=theme["border"],
            highlightcolor=theme["border"],
        )

    def _sorted(self, items, prefix):
        lower = prefix.lower()
        scored = []
        for index, value in enumerate(items):
            name = value[0]
            if name.lower().startswith(lower):
                score = 0
            elif lower and lower in name.lower():
                score = 1
            else:
                score = 2
            scored.append((score, name.lower(), index, value))
        scored.sort()
        return [value for _, _, _, value in scored][:200]

    def _format(self, name, kind, detail):
        icon = self.ICONS.get(kind, "•")
        text = "%s %s" % (icon, name)
        if detail:
            text += "   " + detail
        return text[:68]

    def show(self, items, prefix):
        self._ensure()
        self.items = self._sorted(items, prefix)
        if not self.items:
            self.hide()
            return
        self.listbox.delete(0, "end")
        for name, kind, detail in self.items:
            self.listbox.insert("end", self._format(name, kind, detail))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(0)
        self.listbox.activate(0)
        self.listbox.see(0)
        self._applied = False
        self.show_at_cursor()
        self.top.deiconify()
        self.top.attributes("-topmost", True)
        self._visible = True

    def show_at_cursor(self):
        try:
            bbox = self.text.bbox("insert")
            if not bbox:
                self.hide()
                return
            x, y, _width, height = bbox
            need = self.top.winfo_reqheight()
            if y + height + need > self.text.winfo_height() and y > need:
                y -= need + height
            self.top.wm_geometry(
                "+%d+%d"
                % (
                    self.text.winfo_rootx() + max(0, x),
                    self.text.winfo_rooty() + y + height,
                )
            )
        except tk.TclError:
            self.hide()

    def hide(self):
        self._visible = False
        self._applied = False
        if self.top is not None:
            try:
                self.top.withdraw()
            except tk.TclError:
                pass

    @property
    def visible(self):
        return self._visible and self.top is not None

    # ------------------------------------------------------------------ 操作
    def move(self, delta):
        if not self.visible or not self.items:
            return
        selection = self.listbox.curselection()
        current = selection[0] if selection else 0
        current = max(0, min(len(self.items) - 1, current + delta))
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(current)
        self.listbox.activate(current)
        self.listbox.see(current)

    def selected(self):
        selection = self.listbox.curselection()
        if not selection:
            return None
        try:
            return self.items[selection[0]]
        except IndexError:
            return None

    def accept(self):
        if not self.visible:
            return False
        item = self.selected()
        self.hide()
        if item is None:
            return False
        self._apply(item)
        return True

    def update_items(self, items, prefix):
        if not self.visible:
            return False
        keep = self.selected()
        keep_name = keep[0] if keep else None
        self.items = self._sorted(items, prefix)
        if not self.items:
            self.hide()
            return False
        self.listbox.delete(0, "end")
        for name, kind, detail in self.items:
            self.listbox.insert("end", self._format(name, kind, detail))
        target = 0
        if keep_name:
            for index, value in enumerate(self.items):
                if value[0] == keep_name:
                    target = index
                    break
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(target)
        self.listbox.activate(target)
        self.listbox.see(target)
        self.show_at_cursor()
        return True

    def bind_context(self, offset):
        self._offset = offset

    def _on_click(self, event):
        index = self.listbox.nearest(event.y)
        if index < 0 or index >= len(self.items):
            return
        item = self.items[index]
        self.hide()
        self._apply(item)
        self.editor.focus_editor()

    def _apply(self, item):
        """把选中的候选写进缓冲区（只替换光标前正在输入的词）。"""
        if self._applied:
            return
        self._applied = True
        name, kind, _detail = item
        text = self.text
        line, column = text.index("insert").split(".")
        line_text = text.get("%s.linestart" % line, "insert")
        start = len(line_text)
        while start > 0 and is_identifier_char(line_text[start - 1]):
            start -= 1
        insert_text = name
        caret_back = 0
        if kind in ("function", "method", "magic", "class"):
            insert_text += "("
            caret_back = 1
        elif kind == "module":
            insert_text += "."
        elif kind == "keyword" and name in (
            "if", "elif", "while", "for", "return", "import", "from", "and", "or",
            "not", "in", "is", "with", "as", "lambda", "del", "assert", "global",
            "nonlocal", "raise", "yield", "else", "try", "except", "finally", "pass",
            "break", "continue", "def", "class", "async", "await", "match", "case",
        ):
            insert_text += " "
        text.edit_separator()
        text.delete("%s.%d" % (line, start), "insert")
        text.insert("insert", insert_text)
        if caret_back:
            text.mark_set("insert", "insert-%dc" % caret_back)
        text.edit_separator()

    def destroy(self):
        if self.top is not None:
            try:
                self.top.destroy()
            except tk.TclError:
                pass
            self.top = None
            self.listbox = None
            self.scrollbar = None


class FindReplaceBar(ttk.Frame):
    """编辑器顶部的查找 / 替换栏（Ctrl+F / Ctrl+H，F3 / Shift+F3）。"""

    def __init__(self, master, editor):
        super().__init__(master, padding=(6, 2))
        self.editor = editor
        self.app = editor.app
        self._matches = []
        self._current = -1
        self._source_cache = ""
        self._rowcount = 0
        self._row_starts = [0]

        self.search_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.case_var = tk.BooleanVar(value=False)
        self.word_var = tk.BooleanVar(value=False)
        self.regex_var = tk.BooleanVar(value=False)
        self.wrap_var = tk.BooleanVar(value=True)

        self.rowconfigure(0, weight=1)

        self.search_combo = ttk.Combobox(self, textvariable=self.search_var, width=30)
        self.search_combo.grid(row=0, column=0, sticky="w")
        self.search_combo.bind("<Return>", self.find_next)
        self.search_combo.bind("<Shift-Return>", self.find_previous)
        self.search_combo.bind("<KeyRelease>", self._on_key_release)
        self.search_combo.bind("<Escape>", self.close)

        self.match_label = ttk.Label(self, text="", width=9)
        self.match_label.grid(row=0, column=1, padx=(6, 2))

        ttk.Button(self, text="查找 ▲", width=7, command=self.find_previous).grid(
            row=0, column=2, padx=2
        )
        ttk.Button(self, text="查找 ▼", width=7, command=self.find_next).grid(
            row=0, column=3, padx=2
        )
        ttk.Button(self, text="替换", command=self.replace_one).grid(
            row=0, column=4, padx=2
        )
        ttk.Button(self, text="全部替换", command=self.replace_all).grid(
            row=0, column=5, padx=2
        )
        ttk.Checkbutton(self, text="大小写", variable=self.case_var).grid(
            row=0, column=6, padx=(6, 2)
        )
        ttk.Checkbutton(self, text="整词", variable=self.word_var).grid(
            row=0, column=7, padx=2
        )
        ttk.Checkbutton(self, text="正则", variable=self.regex_var).grid(
            row=0, column=8, padx=2
        )
        ttk.Checkbutton(self, text="循环", variable=self.wrap_var).grid(
            row=0, column=9, padx=2
        )
        ttk.Button(self, text="✕", width=3, command=self.close).grid(
            row=0, column=10, padx=(6, 0)
        )
        self.columnconfigure(11, weight=1)

        self.replace_entry = ttk.Entry(self, textvariable=self.replace_var, width=30)
        self.replace_entry.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.replace_entry.bind("<Return>", self.replace_all)
        self.replace_entry.bind("<Escape>", self.close)
        ttk.Label(self, text="替换为").grid(row=1, column=1, sticky="e", pady=(3, 0))
        ttk.Button(self, text="替换当前并跳下一个", command=self._replace_and_next).grid(
            row=1, column=2, columnspan=3, padx=2, pady=(3, 0)
        )
        self.replace_visible = False

        for var in (self.case_var, self.word_var, self.regex_var):
            var.trace_add("write", lambda *args: self.refresh())

    # -------------------------------------------------------------------- API
    def open(self, replace=False, preset=None):
        if preset == "@":
            selection = ""
            try:
                if self.editor.text.tag_ranges("sel"):
                    selection = self.editor.text.get("sel.first", "sel.last")
            except tk.TclError:
                selection = ""
            if selection and len(selection) <= 60 and "\n" not in selection:
                self.search_var.set(selection)
        self.search_combo.configure(values=list(self.app.find_history)[:25])
        self.grid(row=0, column=0, columnspan=3, sticky="ew")
        self.editor.text.grid(row=1, column=1, sticky="nsew")
        self.editor.vsb.grid(row=1, column=2, sticky="ns")
        self.editor.hsb.grid(row=2, column=1, sticky="ew")
        self.editor.gutter.grid(row=1, column=0, sticky="ns")
        if replace:
            self.show_replace_row()
        self.search_combo.focus_set()
        self.search_combo.selection_range(0, "end")
        self.refresh()
        self.editor._schedule_gutter()

    def show_replace_row(self):
        if not self.replace_visible:
            self.replace_entry.grid(row=1, column=0, sticky="w", pady=(3, 0))
            ttk.Label(self, text="替换为").grid(row=1, column=1, sticky="e", pady=(3, 0))
            self._replace_button = ttk.Button(
                self, text="替换当前并跳下一个", command=self._replace_and_next
            )
            self._replace_button.grid(row=1, column=2, columnspan=3, padx=2, pady=(3, 0))
            self.replace_visible = True
        self.replace_entry.focus_set()

    def close(self, event=None):
        self.grid_forget()
        self.editor.text.grid(row=0, column=1, sticky="nsew")
        self.editor.vsb.grid(row=0, column=2, sticky="ns")
        self.editor.hsb.grid(row=1, column=1, sticky="ew")
        self.editor.gutter.grid(row=0, column=0, sticky="ns")
        self.editor.text.tag_remove("find_all", "1.0", "end")
        self.editor.text.tag_remove("find_current", "1.0", "end")
        self.editor._schedule_gutter()
        self.editor.focus_editor()
        return "break"

    # ---------------------------------------------------------------- 历史
    def remember(self, term):
        if not term:
            return
        history = self.app.find_history
        if term in history:
            history.remove(term)
        history.insert(0, term)
        del history[25:]
        self.search_combo.configure(values=list(history))

    def _on_key_release(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape", "Shift_L", "Shift_R"):
            return
        self.refresh()

    # ------------------------------------------------------------------ 搜索
    def _rebuild_cache(self):
        source = self.editor.get_content()
        if source != self._source_cache:
            self._source_cache = source
            starts = [0]
            index = source.find("\n")
            while index != -1:
                starts.append(index + 1)
                index = source.find("\n", index + 1)
            self._row_starts = starts
        return self._source_cache

    def _index(self, offset):
        starts = self._row_starts
        line = 1
        low, high = 0, len(starts) - 1
        while low <= high:
            mid = (low + high) // 2
            if starts[mid] <= offset:
                line = mid + 1
                low = mid + 1
            else:
                high = mid - 1
        return "%d.%d" % (line, offset - starts[line - 1])

    def _offset(self, index):
        source = self._rebuild_cache()
        line, column = index.split(".")
        starts = self._row_starts
        line = int(line)
        if line - 1 < len(starts):
            return starts[line - 1] + int(column)
        return len(source)

    def _compile(self):
        pattern = self.search_var.get()
        if not pattern:
            return None, "请输入查找内容"
        flags = 0 if self.case_var.get() else re.IGNORECASE
        try:
            if self.regex_var.get():
                return re.compile(pattern, flags), None
            escaped = re.escape(pattern)
            if self.word_var.get():
                escaped = r"\b" + escaped + r"\b"
            return re.compile(escaped, flags), None
        except re.error as exc:
            return None, "正则错误：%s" % exc

    def refresh(self, event=None):
        source = self._rebuild_cache()
        regex, error = self._compile()
        self.editor.text.tag_remove("find_all", "1.0", "end")
        self.editor.text.tag_remove("find_current", "1.0", "end")
        if error:
            self._matches = []
            self._current = -1
            self.match_label.configure(
                text=error if self.search_var.get() else ""
            )
            return
        matches = []
        for match in regex.finditer(source):
            if match.start() == match.end():
                continue
            matches.append(match.span())
            if len(matches) >= 3000:
                break
        self._matches = matches
        for start, end in matches[:1500]:
            try:
                self.editor.text.tag_add(
                    "find_all", self._index(start), self._index(end)
                )
            except tk.TclError:
                pass
        if not self.search_var.get():
            self.match_label.configure(text="")
        elif matches:
            self.match_label.configure(text="%d 处" % len(matches))
        else:
            self.match_label.configure(text="无匹配")

    def find_next(self, event=None):
        self.remember(self.search_var.get())
        if not self._matches:
            self.refresh()
        if not self._matches:
            self._not_found()
            return "break"
        text = self.editor.text
        if text.tag_ranges("sel"):
            anchor = self._offset(text.index("sel.last"))
        else:
            anchor = self._offset(text.index("insert"))
        target = None
        for position, (start, _end) in enumerate(self._matches):
            if start >= anchor:
                target = position
                break
        if target is None:
            if not self.wrap_var.get():
                self._not_found()
                return "break"
            target = 0
        self._jump(target)
        return "break"

    def find_previous(self, event=None):
        self.remember(self.search_var.get())
        if not self._matches:
            self.refresh()
        if not self._matches:
            self._not_found()
            return "break"
        text = self.editor.text
        if text.tag_ranges("sel"):
            anchor = self._offset(text.index("sel.first"))
        else:
            anchor = self._offset(text.index("insert"))
        target = None
        for position in range(len(self._matches) - 1, -1, -1):
            if self._matches[position][1] <= anchor:
                target = position
                break
        if target is None:
            if not self.wrap_var.get():
                self._not_found()
                return "break"
            target = len(self._matches) - 1
        self._jump(target)
        return "break"

    def _jump(self, position):
        self._current = position
        start, end = self._matches[position]
        text = self.editor.text
        text.tag_remove("find_current", "1.0", "end")
        try:
            text.tag_add("find_current", self._index(start), self._index(end))
            text.tag_remove("sel", "1.0", "end")
            text.tag_add("sel", self._index(start), self._index(end))
            text.mark_set("insert", self._index(end))
            text.see(self._index(start))
        except tk.TclError:
            return
        self.match_label.configure(
            text="%d/%d" % (position + 1, len(self._matches))
        )

    def _not_found(self):
        self.match_label.configure(text="未找到")
        self.app.status_var.set("未找到“%s”" % self.search_var.get())

    # ------------------------------------------------------------------ 替换
    def _replacement_text(self, original):
        raw = self.replace_var.get()
        if not self.regex_var.get():
            return raw
        template = re.sub(r"\\(\d)", r"g<\1>", raw)
        regex, error = self._compile()
        if error:
            return raw
        try:
            match = regex.search(original)
            if match:
                return match.expand(template)
        except (re.error, IndexError):
            pass
        return raw

    def replace_one(self, event=None):
        if not self._matches:
            self.refresh()
        if not self._matches:
            self._not_found()
            return "break"
        if not (0 <= self._current < len(self._matches)):
            self.find_next()
            return "break"
        start, end = self._matches[self._current]
        source = self._rebuild_cache()
        replacement = self._replacement_text(source[start:end])
        text = self.editor.text
        text.edit_separator()
        text.delete(self._index(start), self._index(end))
        text.insert(self._index(start), replacement)
        text.edit_separator()
        self.editor.dirty = True
        self.app.on_editor_modified(self.editor)
        anchor = start + len(replacement)
        self.refresh()
        if self._matches:
            position = 0
            for index, (m_start, _m_end) in enumerate(self._matches):
                if m_start >= anchor:
                    position = index
                    break
            self._jump(position)
        return "break"

    def _replace_and_next(self):
        if not (0 <= self._current < len(self._matches)):
            self.find_next()
            return "break"
        self.replace_one()
        self.find_next()
        return "break"

    def replace_all(self, event=None):
        term = self.search_var.get()
        if not term:
            return "break"
        self.remember(term)
        regex, error = self._compile()
        if error:
            self.match_label.configure(text=error)
            return "break"
        source = self._rebuild_cache()
        count = 0
        pieces = []
        last = 0
        for match in regex.finditer(source):
            if match.start() == match.end():
                continue
            pieces.append(source[last:match.start()])
            pieces.append(self._replacement_text(match.group(0)))
            last = match.end()
            count += 1
            if count >= 20000:
                break
        if not count:
            self._not_found()
            return "break"
        pieces.append(source[last:])
        new_source = "".join(pieces)
        insert = self.editor.text.index("insert")
        self.editor.set_content(new_source)
        try:
            self.editor.text.mark_set("insert", insert)
        except tk.TclError:
            pass
        self.editor.dirty = True
        self.app.on_editor_modified(self.editor)
        self.refresh()
        self.match_label.configure(text="已替换 %d 处" % count)
        self.app.status_var.set("已替换 %d 处" % count)
        return "break"


class EditorPane(ttk.Frame):
    """一个文本编辑器标签页。"""

    WORD_JUMP = 260

    def __init__(self, master, app, path=None, content=""):
        super().__init__(master)
        self.app = app
        self.path = path
        self.dirty = False
        self.breakpoints = set()
        self.current_line = None
        self.error_line = None

        self._loading = False
        self._highlight_job = None
        self._gutter_job = None
        self._nav_job = None
        self._linespace = app.code_font.metrics("linespace")
        self._symbols = {}
        self._module_map = {}
        self._class_members = {}
        self._nav_stack = []
        self._nav_forward = []
        self._popup_prefix = ""
        self._popup_base = ""
        self._popup_words = None
        self._auto_popup_on_type = True
        self._ignore_release = False
        self._ignore_next_keypress = False

        self.columnconfigure(1, weight=1)
        # 纵向剩余空间必须给 row 0（文本区）；row 1 是横向滚动条那一行，
        # 若给它 weight 会让滚动条行吃掉多余高度，文本区反而不跟随窗口变高。
        self.rowconfigure(0, weight=1)

        self.gutter = tk.Canvas(
            self, width=54, highlightthickness=0, bd=0, takefocus=0
        )
        self.gutter.grid(row=0, column=0, sticky="ns")

        self.text = tk.Text(
            self,
            undo=True,
            maxundo=-1,
            autoseparators=True,
            wrap="none",
            font=app.code_font,
            padx=6,
            pady=4,
            bd=0,
            highlightthickness=0,
            insertwidth=2,
            tabs=(max(8, app.code_font.measure("    ")),),
        )
        self.text.grid(row=0, column=1, sticky="nsew")

        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.vsb.grid(row=0, column=2, sticky="ns")
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.hsb.grid(row=1, column=1, sticky="ew")
        self.text.configure(
            yscrollcommand=self._on_yscroll, xscrollcommand=self.hsb.set
        )

        self.findbar = FindReplaceBar(self, self)
        self.completion = CompletionPopup(self)

        self._bind_events()
        self.apply_theme(app.theme)
        self.set_content(content)

    # ---------------------------------------------------------------- 事件绑定
    def _bind_events(self):
        text = self.text
        text.bind("<Return>", self._on_return)
        text.bind("<KP_Enter>", self._on_return)
        text.bind("<Tab>", self._on_tab)
        text.bind("<Shift-Tab>", self._on_shift_tab)
        text.bind("<<Paste>>", self._on_paste)
        text.bind("<<Modified>>", self._on_modified)
        text.bind("<KeyPress>", self._on_key_press)
        text.bind("<KeyRelease>", self._on_key_release)
        text.bind("<MouseWheel>", lambda e: self._schedule_gutter(), add="+")
        text.bind("<Button-1>", self._on_button1, add="+")
        text.bind("<B1-Motion>", lambda e: self._schedule_gutter(), add="+")
        text.bind("<Configure>", self._on_configure)
        text.bind("<Button-3>", self._on_context_menu)
        text.bind("<<Undo>>", lambda e: self._schedule_nav(), add="+")
        text.bind("<<Redo>>", lambda e: self._schedule_nav(), add="+")

        for ch in OPEN_BRACKETS:
            text.bind(ch, self._make_opener_handler(ch))
        for ch in CLOSE_BRACKETS:
            text.bind(ch, self._make_closer_handler(ch))
        for ch in "\"'":
            text.bind(ch, self._make_quote_handler(ch))
        text.bind("<BackSpace>", self._on_backspace)
        text.bind("<Delete>", self._on_delete)
        text.bind("<Right>", self._on_arrow_right)
        text.bind("<End>", self._on_end)
        text.bind("<Home>", self._on_home)

        text.bind("<Escape>", self._on_escape)
        text.bind("<Alt-slash>", self._manual_complete)
        text.bind("<Alt-i>", self._manual_complete)
        text.bind("<Up>", self._on_arrow_up)
        text.bind("<Down>", self._on_arrow_down)
        text.bind("<Alt-Up>", self._move_line_up)
        text.bind("<Alt-Down>", self._move_line_down)
        # 括号匹配跳转（Ctrl+]）绑定在 App._bind_shortcuts 的 <Control-bracketright> 上。
        text.bind("<F12>", self._on_f12)
        text.bind("<Shift-F12>", self._on_shift_f12)
        text.bind("<Control-Button-1>", self._on_ctrl_click)
        text.bind("<Control-d>", self._duplicate_line)
        text.bind("<Control-Shift-K>", self._delete_line)

        self.gutter.bind("<Button-1>", self._on_gutter_click)
        self.gutter.bind("<MouseWheel>", self._on_gutter_wheel)
        self.gutter.bind("<Configure>", lambda e: self._schedule_gutter())

    # ---------------------------------------------------------------- 内容
    @property
    def title(self):
        if self.path:
            return os.path.basename(self.path)
        return "未命名"

    def get_content(self):
        return self.text.get("1.0", "end-1c")

    def set_content(self, content):
        self._loading = True
        self.text.delete("1.0", "end")
        self.text.insert("1.0", content)
        self.text.edit_reset()
        self.text.edit_modified(False)
        self._loading = False
        self.dirty = False
        self.completion.hide()
        self._schedule_highlight()
        self._schedule_gutter()
        self._schedule_nav()

    # ---------------------------------------------------------------- 外观
    def apply_theme(self, theme):
        self.text.configure(
            bg=theme["bg"],
            fg=theme["fg"],
            insertbackground=theme["caret"],
            selectbackground=theme["select_bg"],
            selectforeground=theme["select_fg"],
            inactiveselectbackground=theme["select_bg"],
        )
        self.gutter.configure(bg=theme["gutter_bg"])
        self.text.tag_configure("current_line", background=theme["current_line"])
        self.text.tag_configure("error_line", background=theme["error_line"])
        self.text.tag_configure(
            "keyword", foreground=theme["keyword"], font=self.app.bold_font
        )
        self.text.tag_configure("string", foreground=theme["string"])
        self.text.tag_configure(
            "comment", foreground=theme["comment"], font=self.app.italic_font
        )
        self.text.tag_configure("number", foreground=theme["number"])
        self.text.tag_configure("builtin", foreground=theme["builtin"])
        self.text.tag_configure(
            "function", foreground=theme["function"], font=self.app.bold_font
        )
        self.text.tag_configure(
            "class", foreground=theme["class"], font=self.app.bold_font
        )
        self.text.tag_configure("decorator", foreground=theme["decorator"])
        self.text.tag_configure("self", foreground=theme["self"])
        self.text.tag_configure("current_paren", background=theme["current_paren"])
        self.text.tag_configure("match_all", background=theme["match_all"])
        self.text.tag_configure(
            "def_mark", foreground=theme["def_mark"], underline=True
        )
        self.text.tag_configure("find_all", background=theme["find_all"])
        self.text.tag_configure("find_current", background=theme["find_current"])
        self.text.tag_lower("current_line")
        self.text.tag_raise("sel")
        self.completion.apply_theme(theme)
        self._schedule_gutter()

    def refresh_metrics(self):
        self._linespace = self.app.code_font.metrics("linespace")
        self.text.configure(tabs=(max(8, self.app.code_font.measure("    ")),))
        self._schedule_gutter()
        self._schedule_highlight()

    # ------------------------------------------------------------ 行号/断点
    def _on_yscroll(self, first, last):
        self.vsb.set(first, last)
        self._schedule_gutter()

    def _schedule_gutter(self):
        if self._gutter_job is None:
            self._gutter_job = self.after(20, self._draw_gutter)

    def _draw_gutter(self):
        self._gutter_job = None
        canvas = self.gutter
        try:
            canvas.delete("all")
            text = self.text
            first = text.index("@0,0")
            info = text.dlineinfo(first)
        except tk.TclError:
            return
        if not info:
            return

        theme = self.app.theme
        line_space = max(1, self._linespace)
        top_line = int(first.split(".")[0])
        y0 = info[1]
        height = canvas.winfo_height()
        width = canvas.winfo_width()
        last_line = int(text.index("end-1c").split(".")[0])
        count = int(height // line_space) + 3

        for offset in range(count):
            lineno = top_line + offset
            if lineno > last_line:
                break
            y = y0 + offset * line_space
            if y < -line_space or y > height:
                continue
            if lineno in self.breakpoints:
                canvas.create_oval(
                    5, y + 3, 15, y + 13, fill=theme["breakpoint"], outline=""
                )
            if lineno == self.current_line:
                canvas.create_polygon(
                    18,
                    y + 3,
                    18,
                    y + 13,
                    26,
                    y + 8,
                    fill=theme["gutter_active_fg"],
                    outline="",
                )
                color = theme["gutter_active_fg"]
            elif lineno == self.error_line:
                color = theme["breakpoint"]
            else:
                color = theme["gutter_fg"]
            canvas.create_text(
                width - 6,
                y + 1,
                anchor="ne",
                text=str(lineno),
                fill=color,
                font=self.app.gutter_font,
            )

    def _on_gutter_click(self, event):
        try:
            index = self.text.index("@1,%d" % max(1, event.y))
            lineno = int(index.split(".")[0])
        except tk.TclError:
            return
        self.toggle_breakpoint(lineno)

    def _on_gutter_wheel(self, event):
        delta = -1 if event.delta > 0 else 1
        self.text.yview_scroll(delta * 3, "units")
        self._schedule_gutter()

    def toggle_breakpoint(self, lineno=None):
        if lineno is None:
            lineno = int(self.text.index("insert").split(".")[0])
        if lineno in self.breakpoints:
            self.breakpoints.discard(lineno)
        else:
            self.breakpoints.add(lineno)
        self._schedule_gutter()
        self.app.on_breakpoints_changed(self)

    def set_breakpoints(self, lines):
        self.breakpoints = set(lines)
        self._schedule_gutter()

    # ------------------------------------------------------------- 调试标记
    def set_current_line(self, lineno):
        if self.current_line is not None:
            self.text.tag_remove(
                "current_line", f"{self.current_line}.0", f"{self.current_line}.end"
            )
        self.current_line = lineno
        if lineno is not None:
            self.text.tag_add("current_line", f"{lineno}.0", f"{lineno}.end")
            self.text.tag_raise("current_line")
        self._schedule_gutter()

    def set_error_line(self, lineno):
        if self.error_line is not None:
            self.text.tag_remove(
                "error_line", f"{self.error_line}.0", f"{self.error_line}.end"
            )
        self.error_line = lineno
        if lineno is not None:
            self.text.tag_add("error_line", f"{lineno}.0", f"{lineno}.end")
            self.text.tag_raise("error_line")
        self._schedule_gutter()

    def clear_debug_marks(self):
        self.set_current_line(None)
        self.set_error_line(None)

    def goto_line(self, lineno):
        index = f"{lineno}.0"
        self.text.mark_set("insert", index)
        self.text.see(index)
        self.text.focus_set()

    def see_line(self, lineno):
        self.text.see(f"{lineno}.0")

    # ---------------------------------------------------------------- 编辑
    def _on_modified(self, event=None):
        if not self.text.edit_modified():
            return
        self.text.edit_modified(False)
        if self._loading:
            return
        self.dirty = True
        self.app.on_editor_modified(self)
        self._schedule_highlight()
        self._schedule_gutter()
        self._schedule_nav()

    def _on_configure(self, event=None):
        self._schedule_gutter()
        if self.completion.visible:
            self.completion.show_at_cursor()

    def _on_button1(self, event):
        if self.completion.visible:
            self.after_idle(self.completion.hide)
        self._schedule_gutter()
        self._schedule_nav()

    def _on_key_press(self, event):
        # Ctrl/Alt 组合键产生的字符按键要拦掉，防止快捷键把字母插进缓冲区；
        # 但 Windows 上 Alt+/ 的字符就是 "/"，需要照常插入，再弹补全。
        if event.state & 0x0004 and event.char == "/":
            return None
        if event.state & 0x000C and event.keysym not in (
            "Control_L", "Control_R", "Alt_L", "Alt_R",
        ):
            if event.keysym not in (
                "Left", "Right", "Up", "Down", "Home", "End", "space",
                "Return", "Tab", "BackSpace", "Delete",
            ):
                self._ignore_next_keypress = True
                return "break"
        return None

    def _on_key_release(self, event):
        if self._ignore_next_keypress:
            self._ignore_next_keypress = False
            return
        if self._ignore_release:
            self._ignore_release = False
            self._schedule_gutter()
            self._maybe_complete(None)
            return
        if event.keysym in (
            "Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
            "Win_L", "Win_R", "Caps_Lock",
        ):
            return
        self._schedule_gutter()
        self._maybe_complete(event)

    def _type_char(self, chars):
        """由绑定的按键处理函数插入文本，并立即触发补全过滤。"""
        self.text.insert("insert", chars)
        self._ignore_release = True

    # ---------------------------------------------------------- 括号 / 引号
    def _make_opener_handler(self, ch):
        def handler(event):
            self._ignore_release = True
            self.text.insert("insert", ch + BRACKET_PAIR[ch])
            self.text.mark_set("insert", "insert-1c")
            self._highlight_matching()
            return "break"

        return handler

    def _make_closer_handler(self, ch):
        def handler(event):
            text = self.text
            self._ignore_release = True
            if text.tag_ranges("sel"):
                selected = text.get("sel.first", "sel.last")
                first = text.index("sel.first")
                last = text.index("sel.last")
                text.delete("sel.first", "sel.last")
                text.insert(first, ch + selected + BRACKET_MIRROR[ch])
                text.tag_add("sel", first, last)
                text.mark_set("insert", "%s+%dc" % (first, len(selected) + 2))
            elif text.get("insert", "insert+1c") == ch:
                text.mark_set("insert", "insert+1c")
            else:
                text.insert("insert", ch)
            self._highlight_matching()
            return "break"

        return handler

    def _make_quote_handler(self, ch):
        def handler(event):
            text = self.text
            self._ignore_release = True
            line = text.get("insert linestart", "insert lineend")
            column = int(text.index("insert").split(".")[1])
            before3 = text.get("insert-3c", "insert")
            if before3 == ch * 3:
                text.insert("insert", ch)
                return "break"
            if text.get("insert-1c", "insert") == ch and text.get(
                "insert", "insert+1c"
            ) == ch:
                # 已有成对引号，中间又按了一次：尝试补成三引号
                text.insert("insert", ch + ch)
                text.mark_set("insert", "insert-2c")
                return "break"
            if self._inside_string_or_comment():
                text.insert("insert", ch)
                return "break"
            following = text.get("insert", "insert+1c")
            if following == ch:
                text.mark_set("insert", "insert+1c")
                return "break"
            quote_count = self._quote_count(line, column, ch)
            if quote_count % 2 == 0:
                text.insert("insert", ch + ch)
                text.mark_set("insert", "insert-1c")
            else:
                text.insert("insert", ch)
            return "break"

        return handler

    @staticmethod
    def _quote_count(line, column, ch):
        count = 0
        index = 0
        limit = min(column, len(line))
        while index < limit:
            c = line[index]
            if c == "#":
                break
            if c == "\\":
                index += 2
                continue
            if c in "\"'":
                quote = c
                if line.startswith(quote * 3, index):
                    end = line.find(quote * 3, index + 3)
                    if end == -1:
                        break
                    index = end + 3
                    count += 2
                    continue
                index += 1
                while index < limit:
                    if line[index] == "\\":
                        index += 2
                        continue
                    if line[index] == quote:
                        break
                    index += 1
                count += 1
                continue
            index += 1
        return count

    def _offset_of(self, index, source=None):
        line, column = index.split(".")
        line = int(line)
        column = int(column)
        if source is None:
            source = self.get_content()
        lines = source.split("\n")
        if line - 1 >= len(lines):
            return len(source)
        return sum(len(lines[i]) + 1 for i in range(line - 1)) + column

    def _index_of(self, offset, source=None):
        if source is None:
            source = self.get_content()
        line = source.count("\n", 0, offset) + 1
        line_start = source.rfind("\n", 0, offset) + 1
        return "%d.%d" % (line, offset - line_start)

    def _inside_string_or_comment(self):
        source = self.get_content()
        offset = self._offset_of(self.text.index("insert"), source)
        in_string, in_comment = tokenize_completion_context(source, offset)
        return in_string or in_comment

    def _on_backspace(self, event):
        if self.completion.visible:
            self.completion.hide()
        text = self.text
        if text.tag_ranges("sel"):
            return None
        before = text.get("insert-1c", "insert")
        following = text.get("insert", "insert+1c")
        if before in OPEN_BRACKETS and following == BRACKET_PAIR.get(before):
            text.delete("insert-1c", "insert+1c")
            return "break"
        if before in CLOSE_BRACKETS and following == BRACKET_MIRROR.get(before):
            text.delete("insert-1c", "insert+1c")
            return "break"
        if before in "\"'" and following == before:
            text.delete("insert-1c", "insert+1c")
            return "break"
        column = int(text.index("insert").split(".")[1])
        line = text.get("insert linestart", "insert lineend")
        if column and not line[:column].strip():
            width = len(INDENT)
            remove = min(width, column)
            chunk = line[column - remove:column]
            if chunk.strip() == "":
                text.delete("insert-%dc" % remove, "insert")
                return "break"
        return None

    def _on_delete(self, event):
        text = self.text
        if text.tag_ranges("sel"):
            return None
        following = text.get("insert", "insert+1c")
        if following in CLOSE_BRACKETS + "\"'":
            if text.get("insert-1c", "insert") != following:
                text.delete("insert", "insert+1c")
                return "break"
        return None

    def _on_arrow_right(self, event):
        if self.completion.visible:
            return None
        following = self.text.get("insert", "insert+1c")
        if following in CLOSE_BRACKETS + "\"'":
            self.text.mark_set("insert", "insert+1c")
            self._highlight_matching()
            return "break"
        return None

    def _on_end(self, event):
        text = self.text
        line = text.get("insert linestart", "insert lineend")
        stripped = line.rstrip()
        tail = line[len(stripped):]
        if stripped and tail and set(tail) <= set(CLOSE_BRACKETS + "\"'"):
            text.mark_set(
                "insert", "insert lineend-%dc" % len(tail)
            )
            return "break"
        return None

    def _on_home(self, event):
        text = self.text
        line = text.get("insert linestart", "insert lineend")
        column = int(text.index("insert").split(".")[1])
        indent = len(line) - len(line.lstrip(" \t"))
        lineno = text.index("insert").split(".")[0]
        if column and column != indent:
            text.mark_set("insert", "%s.0" % lineno)
        else:
            text.mark_set("insert", "%s.%d" % (lineno, indent))
        return "break"

    # -------------------------------------------------------- 括号匹配高亮
    def _highlight_matching(self):
        text = self.text
        text.tag_remove("current_paren", "1.0", "end")
        for base in ("insert-1c", "insert"):
            ch = text.get(base, base + "+1c")
            if ch not in BRACKETS:
                continue
            partner = self._find_bracket_partner(
                self._offset_of(text.index(base), self.get_content()), ch
            )
            if partner is not None:
                text.tag_add("current_paren", base, base + "+1c")
                text.tag_add(
                    "current_paren",
                    self._index_of(partner),
                    self._index_of(partner + 1),
                )
            return

    def _find_bracket_partner(self, offset, ch=None):
        source = self.get_content()
        if not (0 <= offset < len(source)):
            return None
        ch = ch or source[offset]
        if ch not in BRACKETS:
            return None
        step = 1 if ch in OPEN_BRACKETS else -1
        target = BRACKET_PAIR[ch] if step > 0 else BRACKET_MIRROR[ch]
        depth = 0
        i = offset
        in_string = None
        escaped = False
        while 0 <= i < len(source):
            c = source[i]
            if in_string:
                if escaped:
                    escaped = False
                elif c == "\\":
                    escaped = True
                elif source.startswith(in_string, i):
                    i += len(in_string) - 1
                    in_string = None
            elif c == "#":
                newline = source.find("\n", i)
                if newline == -1:
                    return None
                i = newline
            elif source.startswith('"""', i) or source.startswith("'''", i):
                in_string = source[i:i + 3]
                i += 2
            elif c in "\"'":
                in_string = c
            elif c in OPEN_BRACKETS:
                depth += 1
            elif c in CLOSE_BRACKETS:
                depth -= 1
            if i != offset:
                if depth == 0 and c == target:
                    return i
                if (step > 0 and depth < 0) or (step < 0 and depth > 0):
                    return None
            i += step
        return None

    def _goto_matching_bracket(self, event=None):
        text = self.text
        for base in ("insert-1c", "insert"):
            if text.get(base, base + "+1c") in BRACKETS:
                offset = self._offset_of(text.index(base))
                partner = self._find_bracket_partner(offset)
                if partner is not None:
                    self.push_nav()
                    target = self._index_of(partner)
                    text.mark_set("insert", target)
                    text.see(target)
                    self._highlight_matching()
                return "break"
        return "break"

    # -------------------------------------------------------- 当前词高亮
    def _schedule_nav(self):
        if self._nav_job is None:
            self._nav_job = self.after(self.WORD_JUMP, self._update_word_marks)

    def _update_word_marks(self):
        self._nav_job = None
        text = self.text
        text.tag_remove("match_all", "1.0", "end")
        text.tag_remove("def_mark", "1.0", "end")
        source = self.get_content()
        if len(source) > 400000:
            return
        try:
            self._symbols, self._module_map, self._class_members = (
                build_symbol_index(source)
            )
        except BaseException:
            return
        for name, entries in self._symbols.items():
            for kind, lineno, signature in entries:
                if kind not in ("function", "class"):
                    continue
                try:
                    line_text = text.get("%d.0" % lineno, "%d.end" % lineno)
                except tk.TclError:
                    continue
                match = re.search(r"\b%s\b" % re.escape(name), line_text)
                if match:
                    try:
                        text.tag_add(
                            "def_mark",
                            "%d.%d" % (lineno, match.start()),
                            "%d.%d" % (lineno, match.end()),
                        )
                    except tk.TclError:
                        pass
        word = self._word_at("insert")
        if not word or len(word) < 2:
            return
        regex = re.compile(r"\b%s\b" % re.escape(word))
        for match in regex.finditer(source):
            try:
                text.tag_add(
                    "match_all",
                    self._index_of(match.start(), source),
                    self._index_of(match.end(), source),
                )
            except tk.TclError:
                pass

    def _word_at(self, index):
        text = self.text
        line = text.get(index + " linestart", index + " lineend")
        column = int(text.index(index).split(".")[1])
        start = column
        while start > 0 and is_identifier_char(line[start - 1]):
            start -= 1
        end = column
        while end < len(line) and is_identifier_char(line[end]):
            end += 1
        return line[start:end]

    # ---------------------------------------------------------------- 补全
    def _maybe_complete(self, event):
        """输入时自动弹出 / 过滤补全列表。"""
        if event is not None:
            keysym = event.keysym
            if keysym in ("Escape", "Return", "Tab", "Up", "Down", "Left", "Right"):
                return
            if keysym in ("BackSpace", "Delete"):
                if self.completion.visible:
                    items, prefix = self._filter_args()
                    self.completion.update_items(items, prefix)
                return
            if event.state & 0x000C:
                return
            char = event.char
            if char and not (is_identifier_char(char) or char == "."):
                if self.completion.visible:
                    self.completion.hide()
                return
        if self._inside_string_or_comment():
            self.completion.hide()
            return
        source = self.get_content()
        line_text = self.text.get("insert linestart", "insert")
        match = re.search(r"[A-Za-z_]\w*$", line_text)
        prefix = match.group(0) if match else ""
        offset = self._offset_of(self.text.index("insert"), source)
        base = expression_before(source, offset)

        if self.completion.visible:
            if base == self._popup_base and (
                not prefix or prefix.lower().startswith(self._popup_prefix.lower())
            ):
                items, base, prefix = self._popup_candidates(prefix, source)
                if items and self.completion.update_items(items, prefix):
                    self._popup_prefix = prefix
                    self._popup_base = base
                    return
                self.completion.hide()
            return

        if not self._auto_popup_on_type:
            return
        if len(prefix) < 2 and not base.endswith("."):
            return
        items, base, prefix = self._popup_candidates(prefix, source)
        if not items:
            return
        self._popup_prefix = prefix
        self._popup_base = base
        self.completion.bind_context(offset)
        self.completion.show(items, prefix)

    def _filter_args(self):
        source = self.get_content()
        line_text = self.text.get("insert linestart", "insert")
        match = re.search(r"[A-Za-z_]\w*$", line_text)
        prefix = match.group(0) if match else ""
        items, base, prefix = self._popup_candidates(prefix, source)
        self._popup_prefix = prefix
        self._popup_base = base
        return items, prefix

    def _popup_candidates(self, prefix, source):
        """返回 (候选列表, base 表达式, 前缀)。"""
        offset = self._offset_of(self.text.index("insert"), source)
        base = expression_before(source, offset)
        items = []
        prefix_filter = prefix
        if base:
            class_name, module_obj, tail = resolve_member_path(
                base, self._class_members, self._module_map
            )
            prefix_filter = tail
            if class_name is not None:
                for member in sorted(self._class_members.get(class_name, ())):
                    items.append((member, "method", ""))
                for member in dir(object):
                    if member.startswith("__") and member.endswith("__"):
                        items.append((member, "magic", ""))
            elif module_obj is not None:
                for name in dir(module_obj):
                    if name.startswith("_"):
                        continue
                    try:
                        value = getattr(module_obj, name)
                    except BaseException:
                        value = None
                    if isinstance(value, type):
                        kind = "class"
                    elif value is not None and not callable(value):
                        kind = "variable"
                    else:
                        kind = "function"
                    items.append((name, kind, ""))
            else:
                for name in sorted(BUILTIN_NAMES):
                    kind = "class" if name[:1].isupper() else "builtin"
                    items.append((name, kind, ""))
        if not items:
            ranked, class_members, module_map = scope_completions(source, offset)
            seen = set()
            for name in ranked:
                if name in seen:
                    continue
                seen.add(name)
                if keyword.iskeyword(name):
                    kind = "keyword"
                elif name in BUILTIN_NAMES:
                    kind = "class" if name[:1].isupper() else "builtin"
                elif name in module_map:
                    kind = "module"
                else:
                    kind = "variable"
                    for entry_kind, _lineno, _sig in self._symbols.get(name, ()):
                        if entry_kind in ("function", "class"):
                            kind = entry_kind
                            break
                detail = ""
                entries = self._symbols.get(name)
                if entries:
                    entry_kind, entry_line, entry_sig = entries[-1]
                    if entry_kind in ("function", "class"):
                        detail = entry_sig
                items.append((name, kind, detail))
        filtered = []
        lower = prefix_filter.lower()
        for item in items:
            name = item[0]
            if not lower or name.lower().startswith(lower) or lower in name.lower():
                filtered.append(item)
        return filtered, base, prefix_filter

    def _manual_complete(self, event=None):
        """Alt+/ 或 Ctrl+Space：手动打开补全列表。"""
        if self.completion.visible:
            self.completion.hide()
            return "break"
        if self._inside_string_or_comment():
            return "break"
        source = self.get_content()
        offset = self._offset_of(self.text.index("insert"), source)
        items, base, prefix = self._popup_candidates("", source)
        if not items:
            return "break"
        self._popup_prefix = prefix
        self._popup_base = base
        self.completion.bind_context(offset)
        self.completion.show(items, prefix)
        return "break"

    def _on_arrow_up(self, event):
        if self.completion.visible:
            self.completion.move(-1)
            return "break"
        return None

    def _on_arrow_down(self, event):
        if self.completion.visible:
            self.completion.move(1)
            return "break"
        return None

    def _on_escape(self, event):
        if self.completion.visible:
            self.completion.hide()
            return "break"
        if self.findbar.winfo_ismapped():
            return self.findbar.close()
        return None

    # ------------------------------------------------------------ 输入行为
    def _on_return(self, event):
        if self.completion.visible:
            self.completion.hide()
            return "break"
        text = self.text
        line_start = text.index("insert linestart")
        before = text.get(line_start, "insert")
        indent = re.match(r"[ \t]*", before).group(0)
        extra = INDENT if before.rstrip().endswith(":") else ""
        closing = ""
        following = text.get("insert", "insert+1c")
        if before.strip() and following in CLOSE_BRACKETS:
            offset = self._offset_of(text.index("insert+1c"))
            if self._find_bracket_partner(offset) is not None:
                closing = following
                text.delete("insert", "insert+1c")
        text.insert("insert", "\n" + indent + extra)
        if closing:
            text.insert("insert", "\n" + indent + closing)
            text.mark_set(
                "insert", "insert linestart-%dc" % (len(indent) + 1)
            )
        text.see("insert")
        self._highlight_matching()
        return "break"

    def _on_tab(self, event):
        if self.completion.visible:
            self.completion.accept()
            self.after(0, self._continue_member_completion)
            return "break"
        if self.text.tag_ranges("sel"):
            self._indent_selection(1)
        else:
            self._type_char(INDENT)
        return "break"

    def _continue_member_completion(self):
        """接受补全后如果刚补的是 ``xxx.``，继续弹出成员列表。"""
        source = self.get_content()
        offset = self._offset_of(self.text.index("insert"), source)
        if source[:offset].endswith("."):
            items, base, prefix = self._popup_candidates("", source)
            if items:
                self._popup_prefix = prefix
                self._popup_base = base
                self.completion.bind_context(offset)
                self.completion.show(items, prefix)

    def _on_shift_tab(self, event):
        self._indent_selection(-1)
        return "break"

    def _indent_selection(self, direction):
        text = self.text
        try:
            first = text.index("sel.first")
            last = text.index("sel.last")
        except tk.TclError:
            first = last = text.index("insert")
        start = int(first.split(".")[0])
        end = int(last.split(".")[0])
        if last.split(".")[1] == "0" and end > start:
            end -= 1
        text.edit_separator()
        for lineno in range(start, end + 1):
            line = text.get(f"{lineno}.0", f"{lineno}.end")
            if direction > 0:
                if line.strip():
                    text.insert(f"{lineno}.0", INDENT)
            else:
                stripped = len(line) - len(line.lstrip(" \t"))
                amount = min(stripped, len(INDENT))
                if amount:
                    text.delete(f"{lineno}.0", f"{lineno}.{amount}")
        text.edit_separator()

    def _duplicate_line(self, event=None):
        text = self.text
        lineno = int(text.index("insert").split(".")[0])
        column = int(text.index("insert").split(".")[1])
        line = text.get("%d.0" % lineno, "%d.end" % lineno)
        text.edit_separator()
        text.insert("%d.end" % lineno, "\n" + line)
        text.mark_set("insert", "%d.%d" % (lineno + 1, column))
        text.see("insert")
        text.edit_separator()
        return "break"

    def _delete_line(self, event=None):
        text = self.text
        lineno = int(text.index("insert").split(".")[0])
        last_line = int(text.index("end-1c").split(".")[0])
        text.edit_separator()
        if lineno < last_line:
            text.delete("%d.0" % lineno, "%d.0" % (lineno + 1))
        else:
            text.delete("%d.end-1c" % lineno, "%d.end" % lineno)
        text.see("insert")
        text.edit_separator()
        return "break"

    def _move_line_up(self, event=None):
        return self._move_line(-1)

    def _move_line_down(self, event=None):
        return self._move_line(1)

    def _move_line(self, direction):
        text = self.text
        try:
            first = int(text.index("sel.first").split(".")[0])
            last = int(text.index("sel.last").split(".")[0])
            had_selection = True
        except tk.TclError:
            first = last = int(text.index("insert").split(".")[0])
            had_selection = False
        if (
            had_selection
            and text.index("sel.last").endswith(".0")
            and last > first
        ):
            last -= 1
        last_line = int(text.index("end-1c").split(".")[0])
        if direction < 0:
            if first <= 1:
                return "break"
            target = first - 1
        else:
            if last >= last_line:
                return "break"
            target = last + 1
        lines = [text.get("%d.0" % n, "%d.end" % n) for n in range(first, last + 1)]
        anchor_line = int(text.index("insert").split(".")[0])
        anchor_column = int(text.index("insert").split(".")[1])
        shift = anchor_line - first
        text.edit_separator()
        if last < last_line:
            text.delete("%d.0" % first, "%d.0" % (last + 1))
        else:
            text.delete("%d.0" % first, "end-1c")
        text.insert("%d.0" % target, "\n".join(lines) + "\n")
        text.edit_separator()
        new_first = target + shift
        text.mark_set("insert", "%d.%d" % (new_first, anchor_column))
        text.tag_remove("sel", "1.0", "end")
        if had_selection:
            text.tag_add(
                "sel", "%d.0" % target, "%d.end" % (target + (last - first))
            )
        text.see("insert")
        return "break"

    def toggle_comment(self):
        text = self.text
        try:
            first = text.index("sel.first")
            last = text.index("sel.last")
        except tk.TclError:
            first = text.index("insert linestart")
            last = text.index("insert lineend")
        start = int(first.split(".")[0])
        end = int(last.split(".")[0])
        if last.split(".")[1] == "0" and end > start:
            end -= 1
        lines = [text.get(f"{lineno}.0", f"{lineno}.end") for lineno in range(start, end + 1)]
        non_empty = [line for line in lines if line.strip()]
        text.edit_separator()
        if non_empty and all(line.lstrip().startswith("#") for line in non_empty):
            for lineno in range(end, start - 1, -1):
                line = text.get(f"{lineno}.0", f"{lineno}.end")
                match = re.match(r"([ \t]*)#[ ]?", line)
                if match:
                    text.delete(f"{lineno}.{len(match.group(1))}",
                                f"{lineno}.{match.end()}")
        else:
            indent = min(
                (len(line) - len(line.lstrip(" \t")) for line in non_empty),
                default=0,
            )
            for lineno in range(end, start - 1, -1):
                line = text.get(f"{lineno}.0", f"{lineno}.end")
                if line.strip():
                    text.insert(f"{lineno}.{indent}", "# ")
        text.edit_separator()

    def _on_paste(self, event=None):
        try:
            content = self.clipboard_get()
        except tk.TclError:
            return "break"
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if self.text.tag_ranges("sel"):
            self.text.delete("sel.first", "sel.last")
        self.text.insert("insert", content)
        self.text.see("insert")
        return "break"

    def _on_context_menu(self, event):
        text = self.text
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="剪切", command=lambda: text.event_generate("<<Cut>>"))
        menu.add_command(label="复制", command=lambda: text.event_generate("<<Copy>>"))
        menu.add_command(label="粘贴", command=lambda: text.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="切换注释", command=self.toggle_comment)
        menu.add_command(label="复制当前行", command=self._duplicate_line)
        menu.add_command(label="删除当前行", command=self._delete_line)
        menu.add_separator()
        menu.add_command(label="补全列表 (Alt+/)", command=self._manual_complete)
        menu.add_command(label="跳转到定义 (F12)", command=self.goto_definition)
        menu.add_command(label="查看引用 (Shift+F12)", command=self.show_references)
        menu.add_command(label="跳转匹配括号", command=self._goto_matching_bracket)
        menu.add_separator()
        menu.add_command(label="查找…", command=lambda: self.open_find(False))
        menu.add_command(label="替换…", command=lambda: self.open_find(True))
        menu.add_separator()
        menu.add_command(label="切换断点", command=lambda: self.toggle_breakpoint())
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()

    # ------------------------------------------------------------ 查找入口
    def open_find(self, replace=False):
        self.findbar.open(replace=replace, preset="@")
        return "break"

    # ------------------------------------------------------------ 导航栈
    def push_nav(self):
        """把当前位置压入历史栈（供 Alt+← 返回）。"""
        try:
            entry = self.text.index("insert")
        except tk.TclError:
            return
        if self._nav_stack and self._nav_stack[-1] == entry:
            return
        self._nav_stack.append(entry)
        del self._nav_stack[:-120]
        self._nav_forward.clear()

    def nav_back(self):
        if not self._nav_stack:
            self.app.status_var.set("没有更早的位置")
            return
        target = self._nav_stack.pop()
        try:
            self._nav_forward.append(self.text.index("insert"))
        except tk.TclError:
            pass
        self.app._goto_location(self, target)
        self.app.status_var.set("返回上一处：%s" % target)

    def nav_forward(self):
        if not self._nav_forward:
            self.app.status_var.set("没有更新的位置")
            return
        target = self._nav_forward.pop()
        try:
            self._nav_stack.append(self.text.index("insert"))
        except tk.TclError:
            pass
        self.app._goto_location(self, target)

    # ------------------------------------------------------------ 跳转定义
    def _definition_index(self, word, source):
        """返回本文件中 ``word`` 的定义位置（index 字符串），找不到返回 None。"""
        entries = self._symbols.get(word)
        if not entries:
            symbols, _, _ = build_symbol_index(source)
            entries = symbols.get(word)
        if entries:
            _kind, lineno, _signature = entries[0]
            try:
                line_text = self.text.get("%d.0" % lineno, "%d.end" % lineno)
            except tk.TclError:
                return "%d.0" % lineno
            match = re.search(r"\b%s\b" % re.escape(word), line_text)
            if match:
                return "%d.%d" % (lineno, match.start())
            return "%d.0" % lineno
        pattern = re.compile(
            r"^[ \t]*(?:async[ \t]+def|def|class)[ \t]+%s\b" % re.escape(word), re.M
        )
        match = pattern.search(source)
        if match:
            return self._index_of(match.start(), source)
        return None

    def goto_definition(self, event=None, word=None):
        """Ctrl+左键 / F12：跳转到符号定义。"""
        if word is None:
            word = self._word_at("insert")
        if not word or keyword.iskeyword(word):
            self.app.status_var.set("光标处没有可跳转的名称")
            return "break"
        source = self.get_content()
        # 1. 当前文件中的定义
        index = self._definition_index(word, source)
        if index:
            self.push_nav()
            self.app._goto_location(self, index, select_word=word)
            self.app.status_var.set(
                "已跳转到 “%s” 的定义（第 %s 行）" % (word, index.split(".")[0])
            )
            return "break"
        # 2. import 的名字：本地文件优先，其次标准库源码
        module_name = self._module_map.get(word)
        if module_name and self.app.open_module_source(module_name, self.path, word):
            return "break"
        # 3. 其它标签页 / 同目录文件
        results = collect_definition_positions(self.app, word, self)
        if len(results) == 1:
            label, target, lineno, column, line_text = results[0]
            self.push_nav()
            self.app.open_location(target, lineno, word)
            self.app.status_var.set("已跳转到 “%s”（%s）" % (word, label))
            return "break"
        if results:
            self.app.show_choice("“%s” 的定义" % word, results, word)
            return "break"
        # 4. 标准库对象：显示签名和文档
        self.app.show_object_doc(word)
        return "break"

    def _on_ctrl_click(self, event):
        self.text.mark_set("insert", "@%d,%d" % (event.x, event.y))
        word = self._word_at("insert")
        if word:
            self.goto_definition(word=word)
        return "break"

    def _on_f12(self, event):
        return self.goto_definition()

    def _on_shift_f12(self, event):
        return self.show_references()

    def show_references(self, event=None, word=None):
        """Shift+F12：列出当前文件和其它标签页里对 ``word`` 的所有引用。"""
        if word is None:
            word = self._word_at("insert")
        if not word or len(word) < 2 or keyword.iskeyword(word):
            self.app.status_var.set("光标处没有可查找的名称")
            return "break"
        results = []
        for editor in self.app.editors:
            source = editor.get_content()
            regex = re.compile(r"\b%s\b" % re.escape(word))
            lines = None
            for count, match in enumerate(regex.finditer(source)):
                if count > 400:
                    break
                if lines is None:
                    lines = source.split("\n")
                lineno = source.count("\n", 0, match.start()) + 1
                line_text = lines[lineno - 1].strip() if lineno <= len(lines) else ""
                results.append(
                    (
                        "%s 第%d行" % (editor.title, lineno),
                        editor,
                        lineno,
                        0,
                        line_text,
                    )
                )
        if not results:
            self.app.status_var.set("没有找到 “%s” 的引用" % word)
            return "break"
        if len(results) == 1:
            label, editor, lineno, column, line_text = results[0]
            self.app.open_location(editor, lineno, word)
            return "break"
        self.app.show_choice(
            "“%s” 的 %d 处引用" % (word, len(results)), results, word, references=True
        )
        return "break"

    # ----------------------------------------------------------------- 高亮
    def _schedule_highlight(self):
        if self._highlight_job is None:
            self._highlight_job = self.after(120, self._highlight)

    def _highlight(self):
        self._highlight_job = None
        text = self.text
        for tag in SYNTAX_TAGS:
            text.tag_remove(tag, "1.0", "end")
        source = text.get("1.0", "end-1c")
        if not source or len(source) > 400000:
            return
        if not self._tag_tokens(source):
            return
        self._tag_decorators(source)

    def _tag_tokens(self, source):
        tokens = []
        try:
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                tokens.append(token)
        except (tokenize.TokenError, SyntaxError, IndentationError):
            # 代码正在输入、还不完整时，尽量高亮已经解析出来的部分。
            pass

        previous_type = None
        previous_text = ""
        for token in tokens:
            token_type = token.type
            value = token.string
            tag = None
            if token_type == tokenize.NAME:
                if keyword.iskeyword(value):
                    tag = "keyword"
                elif value in ("self", "cls"):
                    tag = "self"
                elif previous_type == tokenize.NAME and previous_text == "def":
                    tag = "function"
                elif previous_type == tokenize.NAME and previous_text == "class":
                    tag = "class"
                elif value in BUILTIN_NAMES:
                    tag = "builtin"
            elif token_type == tokenize.STRING:
                tag = "string"
            elif token_type == tokenize.NUMBER:
                tag = "number"
            elif token_type == tokenize.COMMENT:
                tag = "comment"
            elif _FSTRING_START <= token_type <= _FSTRING_END:
                tag = "string"

            if tag:
                self._add_tag(token, tag)

            if token_type not in (
                tokenize.NL,
                tokenize.NEWLINE,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.COMMENT,
            ):
                previous_type = token_type
                previous_text = value
        return True

    def _add_tag(self, token, tag):
        (start_row, start_col) = token.start
        (end_row, end_col) = token.end
        try:
            self.text.tag_add(
                tag, f"{start_row}.{start_col}", f"{end_row}.{end_col}"
            )
        except tk.TclError:
            pass

    def _tag_decorators(self, source):
        pattern = re.compile(r"(?m)^[ \t]*(@[A-Za-z_][\w.]*)")
        for match in pattern.finditer(source):
            start = match.start(1)
            row = source.count("\n", 0, start) + 1
            line_start = source.rfind("\n", 0, start) + 1
            column = start - line_start
            try:
                self.text.tag_add(
                    "decorator",
                    f"{row}.{column}",
                    f"{row}.{column + len(match.group(1))}",
                )
            except tk.TclError:
                pass

    # ----------------------------------------------------------------- 杂项
    def destroy(self):
        for job in (self._gutter_job, self._highlight_job, self._nav_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
        self._gutter_job = None
        self._highlight_job = None
        self._nav_job = None
        try:
            self.completion.destroy()
        except Exception:
            pass
        super().destroy()

    def focus_editor(self):
        self.text.focus_set()


# ======================================================================
# 4. 大纲面板与查找定义面板
# ======================================================================

KIND_ICONS = {
    "module": "▣",
    "class": "©",
    "function": "ƒ",
    "method": "ƒ",
    "variable": "▸",
    "attribute": "·",
    "import": "↦",
    "constant": "#",
    "keyword": "◆",
    "builtin": "★",
    "magic": "◆",
    "property": "@",
}


def build_outline_rows(text):
    """把源码解析成 [(缩进层级, 名称, 行号, 类型, 说明), ...]。"""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _outline_by_regex(text)

    rows = []

    def signature(node):
        try:
            return "(" + ast.unparse(node.args) + ")"
        except Exception:
            return "()"

    def walk(body, depth):
        for node in body:
            if isinstance(node, ast.ClassDef):
                bases = ""
                if node.bases:
                    try:
                        bases = "(" + ", ".join(
                            ast.unparse(base) for base in node.bases
                        ) + ")"
                    except Exception:
                        bases = ""
                rows.append((depth, node.name, node.lineno, "class", bases))
                walk(node.body, depth + 1)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "method" if depth else "function"
                rows.append(
                    (depth, node.name, node.lineno, kind, signature(node))
                )
                walk(node.body, depth + 1)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        rows.append((depth, target.id, node.lineno, "variable", ""))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                rows.append((depth, node.target.id, node.lineno, "variable", ""))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.split(".")[0]
                    rows.append((depth, name, node.lineno, "import", alias.name))
            elif isinstance(node, ast.ImportFrom):
                base = ("." * (node.level or 0)) + (node.module or "")
                for alias in node.names:
                    name = alias.asname or alias.name
                    rows.append(
                        (depth, name, node.lineno, "import", "%s.%s" % (base, alias.name))
                    )

    walk(tree.body, 0)
    return rows


def _outline_by_regex(text):
    rows = []
    pattern = re.compile(
        r"^([ \t]*)(?:@\w+\s*\n\s*)?(def|class|async[ \t]+def)[ \t]+([A-Za-z_]\w*)"
    )
    for match in pattern.finditer(text):
        if match.group(1) and "\t" in match.group(1):
            indent = len(match.group(1).expandtabs(4))
        else:
            indent = len(match.group(1))
        depth = indent // 4
        kind = "class" if match.group(2) == "class" else "function"
        lineno = text.count("\n", 0, match.start()) + 1
        rows.append((depth, match.group(3), lineno, kind, ""))
    return rows


class OutlinePanel(ttk.Frame):
    """当前文件的符号大纲（类 / 函数 / 变量），双击跳转。"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._editor = None
        self._suppress = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        ttk.Label(self, text="大纲", style="PanelTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=8, pady=(4, 2)
        )
        self.filter_var = tk.StringVar()
        entry = ttk.Entry(self, textvariable=self.filter_var, font=app.code_font)
        entry.grid(row=1, column=0, sticky="ew", padx=6, pady=(0, 3))
        entry.bind("<KeyRelease>", lambda event: self._render())
        entry.bind("<Return>", lambda event: self._focus_tree())
        entry.bind("<Down>", self._focus_tree)

        self.tree = ttk.Treeview(self, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="名称")
        self.tree.column("#0", width=220, stretch=True)
        self.tree.grid(row=2, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda event: self._activate())
        self.tree.bind("<Return>", lambda event: self._activate())
        self._placeholder()

    # ------------------------------------------------------------------ 显示
    def _placeholder(self, text="（空）"):
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        self.tree.insert("", "end", text=text)

    def update_for(self, editor):
        self._editor = editor
        if editor is None:
            self._rows = []
            self._placeholder()
            return
        try:
            self._rows = build_outline_rows(editor.get_content())
        except Exception:
            self._rows = []
        self._render()

    def _render(self):
        if self._editor is None:
            return
        keyword = self.filter_var.get().strip().lower()
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        if not self._rows:
            self.tree.insert("", "end", text="（无可解析的符号）")
            return

        # 先确认至少有一个匹配的符号。
        shown = 0
        for _depth, name, _lineno, kind, _detail in self._rows:
            if not keyword or keyword in name.lower() or keyword in kind:
                shown += 1
        if not shown:
            self.tree.insert("", "end", text="（没有匹配的符号）")
            return

        # 维护 深度 -> item 的映射；被过滤的节点只从映射里消失，
        # 它的子节点会自然挂到更浅层的匹配祖先下。
        depth_to_item = {}
        for depth, name, lineno, kind, detail in self._rows:
            if keyword and keyword not in name.lower() and keyword not in kind:
                for deeper in list(depth_to_item):
                    if deeper > depth:
                        del depth_to_item[deeper]
                continue
            parent = "" if depth == 0 else depth_to_item.get(depth - 1, "")
            for deeper in list(depth_to_item):
                if deeper > depth:
                    del depth_to_item[deeper]
            if parent != "" and parent not in self.tree.get_children(""):
                parent = ""
            icon = KIND_ICONS.get(kind, "•")
            item = self.tree.insert(
                parent, "end", text="%s %s%s" % (icon, name, detail)
            )
            self.tree.item(item, tags=(str(lineno),))
            depth_to_item[depth] = item

    def _focus_tree(self, event=None):
        children = self.tree.get_children("")
        if children:
            self.tree.focus(children[0])
            self.tree.selection_set(children[0])
        return "break"

    def _activate(self):
        selection = self.tree.selection()
        if not selection:
            return
        tags = self.tree.item(selection[0], "tags")
        if not tags:
            return
        try:
            lineno = int(tags[0])
        except (ValueError, TypeError):
            return
        if self._editor is None:
            return
        self._editor.push_nav()
        self._editor.goto_line(lineno)
        self._editor.text.tag_add("match_all", "%s.0" % lineno, "%s.end" % lineno)


class SymbolSearchPanel(ttk.Frame):
    """跨标签页 / 跨文件查找定义（Ctrl+R）。"""

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._results = []
        self._visible = False

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        bar = ttk.Frame(self)
        bar.grid(row=0, column=0, sticky="ew")
        bar.columnconfigure(0, weight=1)
        self.query_var = tk.StringVar()
        self.query_entry = ttk.Entry(bar, textvariable=self.query_var, font=app.code_font)
        self.query_entry.grid(row=0, column=0, sticky="ew")
        self.query_entry.bind("<KeyRelease>", self._on_key)
        self.query_entry.bind("<Return>", lambda event: self._activate())
        self.query_entry.bind("<Down>", self._focus_tree)
        self.query_entry.bind("<Escape>", lambda event: self.close())
        ttk.Button(bar, text="✕", width=3, command=self.close).grid(
            row=0, column=1, padx=(4, 0)
        )
        self.hint = ttk.Label(
            self, text="输入名称查找定义（当前文件 / 所有标签页 / 同目录 .py）"
        )
        self.hint.grid(row=2, column=0, sticky="w", padx=4)

        self.tree = ttk.Treeview(self, columns=("kind", "where"), show="tree headings")
        self.tree.heading("#0", text="名称")
        self.tree.heading("kind", text="类型")
        self.tree.heading("where", text="位置")
        self.tree.column("#0", width=170, stretch=True)
        self.tree.column("kind", width=54, stretch=False)
        self.tree.column("where", width=110, stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda event: self._activate())
        self.tree.bind("<Return>", lambda event: self._activate())
        self.grid(row=0, column=0, columnspan=3, sticky="nsew")
        self.grid_remove()

    # -------------------------------------------------------------------- API
    def open(self, preset=""):
        self._visible = True
        self.grid()
        if preset:
            self.query_var.set(preset)
        self.query_entry.focus_set()
        self.query_entry.select_range(0, "end")
        self._search()

    def close(self, event=None):
        self._visible = False
        self.grid_remove()
        editor = self.app.active_editor
        if editor is not None:
            editor.focus_editor()
        return "break"

    @property
    def visible(self):
        return self._visible

    # ------------------------------------------------------------------ 搜索
    def _on_key(self, event):
        if event.keysym in ("Up", "Down", "Return", "Escape"):
            return
        self._search()

    def _focus_tree(self, event=None):
        children = self.tree.get_children("")
        if children:
            self.tree.focus(children[0])
            self.tree.selection_set(children[0])
        return "break"

    def _search(self):
        query = self.query_var.get().strip().lower()
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        self._results = []
        if len(query) < 2:
            return
        editor = self.app.active_editor
        current_path = editor.path if editor else None

        buckets = []
        for tab in self.app.editors:
            symbols, _, _ = build_symbol_index(tab.get_content())
            buckets.append((tab, symbols, tab.title))
        opened = {
            os.path.normcase(os.path.abspath(e.path))
            for e in self.app.editors
            if e.path
        }
        for directory in module_search_paths(current_path or os.getcwd()):
            try:
                entries = sorted(os.listdir(directory))
            except OSError:
                continue
            for name in entries:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(directory, name)
                if not os.path.isfile(path):
                    continue
                if os.path.normcase(os.path.abspath(path)) in opened:
                    continue
                try:
                    source = read_source(path)
                except OSError:
                    continue
                symbols, _, _ = build_symbol_index(source)
                buckets.append((path, symbols, name))

        scored = []
        current_title = editor.title if editor else ""
        for target, symbols, label in buckets:
            for name, entries in symbols.items():
                lower = name.lower()
                if query not in lower:
                    continue
                score = 0 if lower == query else (1 if lower.startswith(query) else 2)
                score += 0 if label == current_title else 3
                for kind, lineno, detail in entries[:3]:
                    scored.append(
                        (score, lower, name, kind, lineno, detail, target, label)
                    )
        scored.sort()
        for score, _, name, kind, lineno, detail, target, label in scored[:120]:
            self.tree.insert(
                "",
                "end",
                text="%s %s%s" % (KIND_ICONS.get(kind, "•"), name, detail or ""),
                values=(kind, "%s:%d" % (label, lineno)),
            )
            self._results.append((target, lineno, name))
        if not self._results:
            self.tree.insert("", "end", text="（没有匹配的定义）")

    def _activate(self):
        selection = self.tree.selection()
        if not selection:
            children = self.tree.get_children("")
            if not children:
                return
            selection = children[:1]
        try:
            index = self.tree.index(selection[0])
        except tk.TclError:
            return
        if not (0 <= index < len(self._results)):
            return
        target, lineno, name = self._results[index]
        if isinstance(target, str):
            editor = self.app.open_path(target)
            if editor is None:
                return
        else:
            editor = target
            try:
                self.app.notebook.select(editor)
            except tk.TclError:
                return
        editor.push_nav()
        editor.goto_line(lineno)
        editor.text.tag_add("match_all", "%d.0" % lineno, "%d.end" % lineno)


# ======================================================================
# 5. 变量面板与调用栈面板
# ======================================================================

MAX_CHILDREN = 200
MAX_REPR = 180


def describe(value):
    """把任意对象变成一行可读文本。"""
    try:
        text = repr(value)
    except BaseException:
        text = "<无法显示>"
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    if len(text) > MAX_REPR:
        text = text[: MAX_REPR - 3] + "..."
    return text


def is_expandable(value):
    return isinstance(value, (dict, list, tuple, set, frozenset, bytearray)) or (
        hasattr(value, "__dict__")
        and not isinstance(value, (str, bytes, int, float, complex, bool))
    )


def children_of(value):
    """返回 [(显示名, 子对象), ...]，失败时返回空列表。"""
    try:
        if isinstance(value, dict):
            return [
                (describe(k), v)
                for k, v in itertools.islice(value.items(), MAX_CHILDREN)
            ]
        if isinstance(value, (list, tuple)):
            return [(f"[{i}]", v) for i, v in enumerate(value[:MAX_CHILDREN])]
        if isinstance(value, (set, frozenset)):
            return [
                (f"[{i}]", v)
                for i, v in enumerate(itertools.islice(value, MAX_CHILDREN))
            ]
        if isinstance(value, (bytearray,)):
            return [(f"[{i}]", v) for i, v in enumerate(bytes(value[:MAX_CHILDREN]))]
        attributes = vars(value)
        return [
            (f".{name}", attributes[name])
            for name in sorted(attributes)
            if not name.startswith("__")
        ][:MAX_CHILDREN]
    except BaseException:
        return []


class VariablesView(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._objects = {}

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="变量", style="PanelTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=8, pady=(4, 2)
        )

        self.tree = ttk.Treeview(
            self,
            columns=("value", "type"),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="名称")
        self.tree.heading("value", text="值")
        self.tree.heading("type", text="类型")
        self.tree.column("#0", width=100, stretch=False)
        self.tree.column("value", width=130, stretch=True)
        self.tree.column("type", width=54, stretch=False)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self._placeholder()

    # ------------------------------------------------------------------ 显示
    def _placeholder(self, text="（未在调试）"):
        self.clear()
        self.tree.insert("", "end", text=text, values=("", ""))

    def clear(self):
        self._objects.clear()
        for item in self.tree.get_children(""):
            self.tree.delete(item)

    def show_frame(self, frame_info):
        self.clear()
        if frame_info is None:
            self._placeholder()
            return
        frame = frame_info.frame
        try:
            locals_map = dict(frame.f_locals)
            globals_map = dict(frame.f_globals)
        except BaseException:
            self._placeholder("（无法读取变量）")
            return

        if frame_info.function == "<module>":
            root = self.tree.insert(
                "", "end", text="全局变量", open=True, values=("", "")
            )
            self._fill(root, globals_map)
        else:
            locals_root = self.tree.insert(
                "", "end", text="局部变量", open=True, values=("", "")
            )
            self._fill(locals_root, locals_map)
            globals_root = self.tree.insert(
                "", "end", text="全局变量", open=False, values=("", "")
            )
            self._fill(globals_root, globals_map)

    def _fill(self, parent, mapping):
        for name in sorted(mapping, key=lambda n: (n.startswith("__"), n)):
            self._add(parent, name, mapping[name])

    def _add(self, parent, name, value):
        item = self.tree.insert(
            parent,
            "end",
            text=name,
            values=(describe(value), type(value).__name__),
        )
        self._objects[item] = value
        if is_expandable(value):
            # 占位子节点，展开时再真正填充（懒加载，避免大对象卡顿）。
            self.tree.insert(item, "end", text="…")

    def _on_open(self, event=None):
        item = self.tree.focus()
        if not item or item not in self._objects:
            return
        children = self.tree.get_children(item)
        if len(children) != 1 or self.tree.item(children[0], "text") != "…":
            return
        self.tree.delete(children[0])
        for name, value in children_of(self._objects[item]):
            self._add(item, name, value)


class StackView(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._items = []

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="调用栈", style="PanelTitle.TLabel").grid(
            row=0, column=0, sticky="w", padx=8, pady=(4, 2)
        )

        self.tree = ttk.Treeview(
            self, columns=("location",), show="tree headings", selectmode="browse"
        )
        self.tree.heading("#0", text="函数")
        self.tree.heading("location", text="位置")
        self.tree.column("#0", width=105, stretch=False)
        self.tree.column("location", width=160, stretch=True)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self._suppress = False

    def show_frames(self, frames):
        self._suppress = True
        self._items = list(frames)
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        for index, frame_info in enumerate(self._items):
            name = frame_info.function
            display = f"{name}()" if name != "<module>" else "<模块>"
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                text=display,
                values=(f"{frame_info.short_filename}:{frame_info.lineno}",),
            )
        if self._items:
            self.tree.selection_set("0")
            self.tree.focus("0")
        self._suppress = False

    def clear(self):
        self._suppress = True
        self._items = []
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        self._suppress = False

    def _on_select(self, event=None):
        if self._suppress:
            return
        selection = self.tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0])
        except ValueError:
            return
        if 0 <= index < len(self._items):
            self.app.on_stack_frame_selected(index)


# ======================================================================
# 6. 交互式 Shell
# ======================================================================
# 程序的标准输出、异常信息都显示在这里；暂停时还可以直接执行语句，
# 用当前帧的变量做实验（和 Thonny 的 shell 类似）。


class ShellPane(ttk.Frame):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self._namespace_provider = None
        self._buffer = ""
        self._history = []
        self._history_pos = 0

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        self.text = tk.Text(
            self,
            height=8,
            width=40,
            wrap="word",
            bd=0,
            highlightthickness=0,
            padx=app._px(8),
            pady=app._px(6),
            state="disabled",
            font=app.code_font,
            insertwidth=2,
        )
        self.text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.text.configure(yscrollcommand=scrollbar.set)

        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew",
                 padx=app._px(8), pady=(0, app._px(6)))
        bar.columnconfigure(1, weight=1)

        self.prompt_var = tk.StringVar(value=">>>")
        self.prompt = ttk.Label(bar, textvariable=self.prompt_var, font=app.bold_font)
        self.prompt.grid(row=0, column=0, padx=(0, 6))

        self.line_var = tk.StringVar()
        self.entry = ttk.Entry(bar, textvariable=self.line_var, font=app.code_font)
        self.entry.grid(row=0, column=1, sticky="ew")
        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<Up>", self._history_up)
        self.entry.bind("<Down>", self._history_down)

        self.apply_theme(app.theme)
        self.set_enabled(True)
        self.write(SHELL_BANNER, "system")

    # ------------------------------------------------------------------ API
    def set_namespace_provider(self, provider):
        """provider() -> (globals, locals)"""
        self._namespace_provider = provider

    def set_enabled(self, enabled):
        self.entry.configure(state="normal" if enabled else "disabled")

    def focus(self):
        self.entry.focus_set()

    def write(self, text, tag="stdout"):
        if not text:
            return
        self.text.configure(state="normal")
        self.text.insert("end", text, tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def apply_theme(self, theme):
        self.text.configure(
            bg=theme["bg"],
            fg=theme["fg"],
            insertbackground=theme["caret"],
            selectbackground=theme["select_bg"],
            selectforeground=theme["select_fg"],
            highlightbackground=theme["border"],
            highlightcolor=theme["select_bg"],
        )
        self.text.tag_configure("stdout", foreground=theme["fg"])
        self.text.tag_configure("stderr", foreground=theme["shell_stderr"])
        self.text.tag_configure(
            "prompt", foreground=theme["shell_prompt"], font=self.app.bold_font
        )
        self.text.tag_configure(
            "system", foreground=theme["shell_system"], font=self.app.italic_font
        )

    # ----------------------------------------------------------------- 执行
    def _submit(self, event=None):
        line = self.line_var.get()
        self.line_var.set("")
        if not self._buffer and not line.strip():
            return "break"

        prefix = "... " if self._buffer else ">>> "
        self.write(prefix + line + "\n", "prompt")

        source = self._buffer + line
        if self._buffer and not line.strip():
            # 空行结束多行语句（和标准 REPL 一致）
            source = self._buffer + "\n"
        try:
            code = codeop.compile_command(source, "<shell>", "single")
        except (SyntaxError, OverflowError, ValueError):
            self.write(self._format_exception(), "stderr")
            self._buffer = ""
            self.prompt_var.set(">>>")
            return "break"

        if code is None:
            # 语句还没写完（例如 if 后面还没空行）。
            self._buffer = source + "\n"
            self.prompt_var.set("...")
            self.line_var.set(self._continuation_indent(source))
            self.entry.icursor("end")
            return "break"

        self._buffer = ""
        self.prompt_var.set(">>>")
        if line.strip() or source.strip():
            self._history.append(source)
            self._history_pos = len(self._history)
        self._execute(code)
        return "break"

    @staticmethod
    def _continuation_indent(source):
        lines = source.splitlines()
        last = lines[-1] if lines else ""
        indent = re.match(r"[ \t]*", last).group(0)
        if last.rstrip().endswith(":"):
            indent += "    "
        return indent

    def _execute(self, code):
        namespace = self._get_namespace()
        if namespace is None:
            self.write("（当前没有可用的命名空间）\n", "stderr")
            return
        globals_map, locals_map = namespace
        output = io.StringIO()
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exec(code, globals_map, locals_map)
        except SystemExit:
            self.write("（SystemExit：交互环境已忽略）\n", "system")
        except BaseException:
            output.write(self._format_exception())
        if output.getvalue():
            self.write(output.getvalue(), "stdout")

    def _get_namespace(self):
        if self._namespace_provider is None:
            return None
        try:
            return self._namespace_provider()
        except BaseException:
            return None

    @staticmethod
    def _format_exception():
        etype, evalue, tb = sys.exc_info()
        keep = [
            entry
            for entry in traceback.extract_tb(tb)
            if os.path.normcase(os.path.abspath(entry.filename))
            != os.path.normcase(os.path.abspath(__file__))
        ]
        parts = []
        if keep:
            parts.append("Traceback (most recent call last):\n")
            parts.extend(traceback.format_list(keep))
        parts.extend(traceback.format_exception_only(etype, evalue))
        return "".join(parts)

    # ---------------------------------------------------------------- 历史
    def _history_up(self, event):
        if not self._history:
            return "break"
        self._history_pos = max(0, self._history_pos - 1)
        self.line_var.set(self._history[self._history_pos].rstrip("\n"))
        self.entry.icursor("end")
        return "break"

    def _history_down(self, event):
        if not self._history:
            return "break"
        self._history_pos = min(len(self._history), self._history_pos + 1)
        if self._history_pos == len(self._history):
            self.line_var.set("")
        else:
            self.line_var.set(self._history[self._history_pos].rstrip("\n"))
        self.entry.icursor("end")
        return "break"


# ======================================================================
# 7. 主窗口与入口
# ======================================================================


class ToolBar(ttk.Frame):
    """会自动换行的工具栏。

    用 ``pack(side="left")`` 排按钮时，窗口一窄右边的按钮就被裁掉（小屏或
    高 DPI 下按钮更宽，更容易放不下）。这里改用 ``grid``：根据当前宽度把
    按钮排成若干行，放不下就换行，行首的分隔线自动省略。
    """

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self._items = []
        self._width = -1
        self.bind("<Configure>", self._on_configure)

    def add(self, widget):
        """登记一个控件，交给 ``_relayout`` 摆放（不要自己 pack/grid）。"""
        self._items.append(widget)
        return widget

    def add_separator(self):
        return self.add(ttk.Separator(self, orient="vertical"))

    def _on_configure(self, event):
        width = int(event.width)
        if width == self._width:
            return
        self._width = width
        self._relayout(width)

    def relayout_now(self):
        """配色或字号变了之后重新按当前宽度排一次。"""
        width = self.winfo_width()
        if width > 1:
            self._width = width
            self._relayout(width)

    def _relayout(self, width):
        if not self._items:
            return
        try:
            self.update_idletasks()  # 让 winfo_reqwidth 拿到真实宽度
            for widget in self._items:
                widget.grid_remove()
            row = 0
            column = 0
            used = 0
            at_row_start = True
            limit = max(80, width)
            for widget in self._items:
                is_separator = isinstance(widget, ttk.Separator)
                if at_row_start and is_separator:
                    continue  # 行首不画分隔线
                need = widget.winfo_reqwidth()
                if not at_row_start and used + need > limit:
                    row += 1
                    column = 0
                    used = 0
                    at_row_start = True
                    if is_separator:
                        continue
                padx = 6 if is_separator else 2
                widget.grid(
                    row=row, column=column, sticky="w", padx=padx, pady=1
                )
                column += 1
                used += need + 2 * padx
                at_row_start = False
            self.columnconfigure(column, weight=1, minsize=0)
        except tk.TclError:
            pass


class App:
    def __init__(self, start_files=None):
        self.dark = False
        self.theme = get_theme(False)
        self._menus = []
        self.find_history = []

        self.root = tk.Tk()
        self.root.title(APP_NAME)
        # 高 DPI：进程已经是 DPI-aware 的，Tk 需要知道真实 DPI 才能把
        # “点”为单位的字号换算成正确的像素；窗口尺寸用像素，按倍数放大。
        self.scale = system_scale_factor()
        try:
            self.root.tk.call("tk", "scaling", 96.0 * self.scale / 72.0)
        except tk.TclError:
            pass
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        self.root.geometry(
            "%dx%d"
            % (
                min(self._px(1150), screen_w - self._px(40)),
                min(self._px(760), screen_h - self._px(80)),
            )
        )
        self.root.minsize(self._px(700), self._px(420))

        self.session = None
        self._own_globals = {"__name__": "__main__"}
        self._frame_index = 0
        self.editors = []
        self._closing = False
        self._poll_job = None
        self._sash_job = None
        self._outline_job = None
        self._outline_enabled = True

        self._init_fonts()
        self._build_ui()
        self._build_menu()
        self._bind_shortcuts()
        self._apply_theme()

        if start_files:
            for path in start_files:
                self.open_path(path)
        if not self.editors:
            self.new_file()

        self.shell.set_namespace_provider(self._shell_namespace)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Destroy>", self._on_destroy)
        # 让符号搜索面板始终盖在标签页（编辑器、查找栏）之上。
        self.symbol_search.lift()
        self._poll_job = self.root.after(60, self._poll)
        self._sash_job = self.root.after(150, self._set_sashes)

    # ------------------------------------------------------------------ 字体
    def _init_fonts(self):
        families = set(tkfont.families(self.root))
        family = "TkFixedFont"
        for candidate in ("Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New"):
            if candidate in families:
                family = candidate
                break
        self.code_font = tkfont.Font(root=self.root, family=family, size=DEFAULT_FONT_SIZE)
        self.bold_font = tkfont.Font(
            root=self.root, family=family, size=DEFAULT_FONT_SIZE, weight="bold"
        )
        self.italic_font = tkfont.Font(
            root=self.root, family=family, size=DEFAULT_FONT_SIZE, slant="italic"
        )
        self.gutter_font = tkfont.Font(
            root=self.root, family=family, size=DEFAULT_FONT_SIZE - 1
        )
        default = tkfont.nametofont("TkDefaultFont")
        self.ui_bold_font = tkfont.Font(
            root=self.root,
            family=default.cget("family"),
            size=default.cget("size"),
            weight="bold",
        )

    # -------------------------------------------------------------------- UI
    def _px(self, value):
        """把 100% 缩放下的像素值换算成当前 DPI 下需要的像素值。"""
        return max(1, int(round(value * getattr(self, "scale", 1.0))))

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        toolbar = ToolBar(self.root, padding=(self._px(6), self._px(4)))
        toolbar.grid(row=0, column=0, sticky="ew")

        self.btn_run = self._tool_button(toolbar, "▶ 运行", self.cmd_run)
        self.btn_stop = self._tool_button(toolbar, "■ 停止", self.cmd_stop)
        toolbar.add_separator()
        self.btn_over = self._tool_button(toolbar, "↷ 跳过", self.cmd_step_over)
        self.btn_into = self._tool_button(toolbar, "↓ 进入", self.cmd_step_into)
        self.btn_out = self._tool_button(toolbar, "↑ 跳出", self.cmd_step_out)
        toolbar.add_separator()
        self._tool_button(toolbar, "保存", self.cmd_save)
        self._tool_button(toolbar, "打开", self.open_file)
        self._tool_button(toolbar, "新建", self.new_file)
        toolbar.add_separator()
        self._tool_button(toolbar, "查找", self.open_find)
        self._tool_button(toolbar, "替换", self.open_replace)
        self._tool_button(toolbar, "查找定义", self.open_symbol_search)
        self._toolbar = toolbar
        toolbar.grid_columnconfigure(0, weight=0)

        self.vpaned = ttk.PanedWindow(self.root, orient="vertical")
        self.vpaned.grid(row=1, column=0, sticky="nsew")

        self.hpaned = ttk.PanedWindow(self.vpaned, orient="horizontal")
        self.vpaned.add(self.hpaned, weight=4)

        left = ttk.Frame(self.hpaned)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        self.notebook = ttk.Notebook(left)
        self.notebook.grid(row=0, column=0, sticky="nsew")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self.notebook.bind("<Button-3>", self._on_tab_right_click)
        self.notebook.bind("<Button-2>", self._on_tab_middle_click)
        self.hpaned.add(left, weight=3)

        right = ttk.Frame(self.hpaned)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=2)
        right.rowconfigure(1, weight=3)
        right.rowconfigure(2, weight=2)
        self.outline = OutlinePanel(right, self)
        self.outline.grid(row=0, column=0, sticky="nsew")
        self.variables = VariablesView(right, self)
        self.variables.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        self.stack = StackView(right, self)
        self.stack.grid(row=2, column=0, sticky="nsew", pady=(4, 0))
        self.hpaned.add(right, weight=1)

        self.shell = ShellPane(self.vpaned, self)
        self.vpaned.add(self.shell, weight=1)

        self.symbol_search = SymbolSearchPanel(self.notebook, self)

        self.status_var = tk.StringVar(
            value="就绪 · Python %s · F1 查看快捷键" % sys.version.split()[0]
        )
        self.status = ttk.Label(
            self.root,
            textvariable=self.status_var,
            style="Status.TLabel",
            anchor="w",
        )
        self.status.grid(row=2, column=0, sticky="ew")

        self._update_buttons()

    def _tool_button(self, toolbar, text, command):
        button = ttk.Button(
            toolbar, text=text, command=command, style="Toolbar.TButton"
        )
        return toolbar.add(button)

    def _set_sashes(self):
        self._sash_job = None
        try:
            self.vpaned.sashpos(0, max(self._px(220), int(self.root.winfo_height() * 0.62)))
            self.hpaned.sashpos(0, max(self._px(360), int(self.root.winfo_width() * 0.73)))
        except tk.TclError:
            pass

    def _build_menu(self):
        menubar = tk.Menu(self.root)
        self._menus.append(menubar)

        file_menu = self._add_menu(menubar, "文件")
        file_menu.add_command(label="新建", accelerator="Ctrl+N", command=self.new_file)
        file_menu.add_command(label="打开…", accelerator="Ctrl+O", command=self.open_file)
        file_menu.add_command(label="保存", accelerator="Ctrl+S", command=self.cmd_save)
        file_menu.add_command(
            label="另存为…", accelerator="Ctrl+Shift+S", command=self.cmd_save_as
        )
        file_menu.add_separator()
        file_menu.add_command(
            label="关闭标签", accelerator="Ctrl+W", command=self.cmd_close_tab
        )
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.on_close)

        edit_menu = self._add_menu(menubar, "编辑")
        edit_menu.add_command(
            label="撤销", accelerator="Ctrl+Z",
            command=lambda: self._editor_event("<<Undo>>"),
        )
        edit_menu.add_command(
            label="重做", accelerator="Ctrl+Shift+Z",
            command=lambda: self._editor_event("<<Redo>>"),
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="查找", accelerator="Ctrl+F", command=self.open_find
        )
        edit_menu.add_command(
            label="替换", accelerator="Ctrl+H", command=self.open_replace
        )
        edit_menu.add_command(
            label="跳转到行…", accelerator="Alt+G", command=self.cmd_goto_line
        )
        edit_menu.add_command(
            label="补全列表", accelerator="Alt+/", command=self.cmd_complete
        )
        edit_menu.add_separator()
        edit_menu.add_command(
            label="切换注释", accelerator="Ctrl+/", command=self._toggle_comment
        )
        edit_menu.add_command(
            label="复制当前行", accelerator="Ctrl+D", command=self._duplicate_line
        )
        edit_menu.add_command(
            label="删除当前行", accelerator="Ctrl+Shift+K", command=self._delete_line
        )
        edit_menu.add_command(label="全选", accelerator="Ctrl+A", command=self._select_all)

        nav_menu = self._add_menu(menubar, "转到")
        nav_menu.add_command(
            label="跳转到定义", accelerator="F12 / Ctrl+左键",
            command=self.cmd_goto_definition,
        )
        nav_menu.add_command(
            label="查看引用", accelerator="Shift+F12", command=self.cmd_show_references
        )
        nav_menu.add_command(
            label="查找定义…（所有文件）", accelerator="Ctrl+R",
            command=self.open_symbol_search,
        )
        nav_menu.add_separator()
        nav_menu.add_command(
            label="返回上一个位置", accelerator="Alt+←", command=self.cmd_nav_back
        )
        nav_menu.add_command(
            label="前进到下一个位置", accelerator="Alt+→", command=self.cmd_nav_forward
        )
        nav_menu.add_command(
            label="跳转匹配括号", accelerator="Ctrl+]", command=self.cmd_goto_bracket
        )

        run_menu = self._add_menu(menubar, "运行")
        run_menu.add_command(label="运行 / 继续", accelerator="F5", command=self.cmd_run)
        run_menu.add_command(
            label="调试运行（首行暂停）", accelerator="Ctrl+F5", command=self.cmd_debug
        )
        run_menu.add_command(
            label="停止", accelerator="Shift+F5", command=self.cmd_stop
        )
        run_menu.add_separator()
        run_menu.add_command(
            label="单步跳过", accelerator="F6", command=self.cmd_step_over
        )
        run_menu.add_command(
            label="单步进入", accelerator="F7", command=self.cmd_step_into
        )
        run_menu.add_command(
            label="单步跳出", accelerator="F8", command=self.cmd_step_out
        )
        run_menu.add_separator()
        run_menu.add_command(
            label="切换断点", accelerator="F9", command=self.cmd_toggle_breakpoint
        )

        view_menu = self._add_menu(menubar, "视图")
        self.dark_var = tk.BooleanVar(value=False)
        view_menu.add_checkbutton(
            label="深色主题", variable=self.dark_var, command=self._toggle_dark
        )
        self.outline_var = tk.BooleanVar(value=True)
        view_menu.add_checkbutton(
            label="大纲面板", variable=self.outline_var, command=self._toggle_outline
        )
        view_menu.add_separator()
        view_menu.add_command(
            label="放大字号", accelerator="Ctrl+=", command=lambda: self._zoom(1)
        )
        view_menu.add_command(
            label="缩小字号", accelerator="Ctrl+-", command=lambda: self._zoom(-1)
        )
        view_menu.add_command(
            label="默认字号", accelerator="Ctrl+0", command=self._zoom_reset
        )
        view_menu.add_separator()
        view_menu.add_command(label="清空 Shell", command=self.shell.clear)

        tools_menu = self._add_menu(menubar, "工具")
        self.auto_popup_var = tk.BooleanVar(value=True)
        tools_menu.add_checkbutton(
            label="输入时自动弹出补全",
            variable=self.auto_popup_var,
            command=self._toggle_auto_popup,
        )
        tools_menu.add_command(
            label="补全列表", accelerator="Alt+/", command=self.cmd_complete
        )
        tools_menu.add_command(
            label="查看快捷键…", accelerator="F1", command=self._show_shortcuts
        )

        help_menu = self._add_menu(menubar, "帮助")
        help_menu.add_command(label="快捷键", command=self._show_shortcuts)
        help_menu.add_command(label="关于", command=self._show_about)

        self.root.config(menu=menubar)

    def _add_menu(self, menubar, label):
        menu = tk.Menu(menubar, tearoff=False)
        self._menus.append(menu)
        menubar.add_cascade(label=label, menu=menu)
        return menu

    def _bind_shortcuts(self):
        bindings = {
            "<F5>": self.cmd_run,
            "<Control-F5>": self.cmd_debug,
            "<Shift-F5>": self.cmd_stop,
            "<F6>": self.cmd_step_over,
            "<F7>": self.cmd_step_into,
            "<F8>": self.cmd_step_out,
            "<F9>": self.cmd_toggle_breakpoint,
            "<F12>": self.cmd_goto_definition,
            "<Shift-F12>": self.cmd_show_references,
            "<F3>": self.cmd_find_next,
            "<Shift-F3>": self.cmd_find_previous,
            "<F1>": self._show_shortcuts,
            "<Control-n>": self.new_file,
            "<Control-o>": self.open_file,
            "<Control-s>": self.cmd_save,
            "<Control-Shift-S>": self.cmd_save_as,
            "<Control-w>": self.cmd_close_tab,
            "<Control-f>": self.open_find,
            "<Control-h>": self.open_replace,
            "<Control-r>": self.open_symbol_search,
            "<Alt-g>": self.cmd_goto_line,
            "<Control-slash>": self._toggle_comment,
            "<Control-a>": self._select_all,
            "<Control-d>": self._duplicate_line,
            "<Control-Shift-K>": self._delete_line,
            "<Alt-Left>": self.cmd_nav_back,
            "<Alt-Right>": self.cmd_nav_forward,
            "<Alt-slash>": self.cmd_complete,
            "<Control-bracketright>": self.cmd_goto_bracket,
            "<Control-plus>": lambda: self._zoom(1),
            "<Control-equal>": lambda: self._zoom(1),
            "<Control-minus>": lambda: self._zoom(-1),
            "<Control-9>": self._zoom_reset,
            "<Control-0>": self._zoom_reset,
        }
        # Ctrl+数字 在部分键盘上产生小数字键，补数字形式绑定。
        for digit, action in (("9", self._zoom_reset), ("0", self._zoom_reset)):
            bindings["<Control-KeyPress-%s>" % digit] = action
        for sequence, function in bindings.items():
            self.root.bind_all(sequence, self._make_handler(function))

    @staticmethod
    def _make_handler(function):
        def handler(event=None):
            function()
            return "break"

        return handler

    # ------------------------------------------------------------------ 主题
    def _apply_theme(self):
        theme = self.theme
        self.root.configure(bg=theme["bg"])
        self._apply_ttk_style()
        # Shell 先上色：单个编辑器出错时不应该把终端留在旧配色上。
        if hasattr(self, "shell"):
            self.shell.apply_theme(theme)
        for editor in self.editors:
            try:
                editor.apply_theme(theme)
            except tk.TclError:
                pass
        if getattr(self, "_toolbar", None) is not None:
            self._toolbar.relayout_now()
        for menu in self._menus:
            try:
                menu.configure(
                    bg=theme["bg"],
                    fg=theme["fg"],
                    activebackground=theme["select_bg"],
                    activeforeground=theme["fg"],
                    selectcolor=theme["fg"],
                )
            except tk.TclError:
                pass

    def _apply_ttk_style(self):
        theme = self.theme
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            ".",
            background=theme["panel_bg"],
            foreground=theme["fg"],
            fieldbackground=theme["bg"],
            bordercolor=theme["border"],
            darkcolor=theme["border"],
            lightcolor=theme["panel_bg"],
        )
        style.configure("TFrame", background=theme["panel_bg"])
        style.configure("TLabel", background=theme["panel_bg"], foreground=theme["fg"])
        style.configure("PanelTitle.TLabel", font=self.ui_bold_font)
        style.configure(
            "Status.TLabel",
            background=theme["status_bg"],
            foreground=theme["fg"],
            padding=(self._px(8), self._px(3)),
        )
        style.configure("TButton", padding=(self._px(8), self._px(3)))
        style.map(
            "TButton",
            background=[("active", theme["select_bg"])],
            foreground=[("disabled", theme["gutter_fg"])],
        )
        style.configure(
            "Toolbar.TButton", padding=(self._px(10), self._px(4))
        )
        # ttk 的输入框被禁用时（例如程序运行期间禁用 Shell 输入行）默认会画成
        # 浅灰色，深色主题下非常刺眼，这里把各个状态都指回主题色。
        style.map(
            "TEntry",
            fieldbackground=[
                ("disabled", theme["panel_bg"]),
                ("readonly", theme["bg"]),
                ("focus", theme["bg"]),
                ("active", theme["bg"]),
            ],
            foreground=[("disabled", theme["gutter_fg"])],
            bordercolor=[("focus", theme["select_bg"])],
            lightcolor=[("focus", theme["select_bg"])],
            darkcolor=[("focus", theme["select_bg"])],
        )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("disabled", theme["panel_bg"]),
                ("focus", theme["bg"]),
                ("readonly", theme["bg"]),
            ],
            background=[("selected", theme["select_bg"])],
        )
        # Combobox 的下拉列表其实是原生 Listbox，不跟随 ttk 样式，得单独指定，
        # 否则深色主题下它还是一块浅色（默认 tk_bisque）。
        for key, value in (
            ("*TCombobox*Listbox*Background", theme["popup_bg"]),
            ("*TCombobox*Listbox*Foreground", theme["fg"]),
            ("*TCombobox*Listbox*selectBackground", theme["popup_select"]),
            ("*TCombobox*Listbox*selectForeground", theme["select_fg"]),
        ):
            self.root.option_add(key, value)
        style.configure(
            "TScrollbar",
            background=theme["panel_bg"],
            troughcolor=theme["bg"],
            bordercolor=theme["border"],
            arrowcolor=theme["fg"],
            lightcolor=theme["panel_bg"],
            darkcolor=theme["panel_bg"],
        )
        style.map(
            "TScrollbar", background=[("active", theme["select_bg"])]
        )
        style.configure(
            "Treeview",
            background=theme["bg"],
            fieldbackground=theme["bg"],
            foreground=theme["fg"],
            rowheight=max(self._px(20), self.code_font.metrics("linespace") + self._px(4)),
        )
        style.configure(
            "Treeview.Heading", background=theme["panel_bg"], foreground=theme["fg"]
        )
        style.map(
            "Treeview",
            background=[("selected", theme["select_bg"])],
            foreground=[("selected", theme["select_fg"])],
        )
        style.configure("TNotebook", background=theme["panel_bg"], borderwidth=0)
        style.configure(
            "TNotebook.Tab",
            background=theme["panel_bg"],
            foreground=theme["fg"],
            padding=(10, 4),
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", theme["bg"])],
            foreground=[("selected", theme["fg"])],
        )
        style.configure("TPanedwindow", background=theme["panel_bg"])
        style.configure(
            "TCheckbutton", background=theme["panel_bg"], foreground=theme["fg"]
        )
        style.map("TCheckbutton", background=[("active", theme["panel_bg"])])
        style.configure(
            "TCombobox",
            fieldbackground=theme["bg"],
            background=theme["panel_bg"],
            foreground=theme["fg"],
        )

    def _toggle_dark(self):
        self.dark = bool(self.dark_var.get())
        self.theme = get_theme(self.dark)
        self._apply_theme()

    def _toggle_outline(self):
        self._outline_enabled = bool(self.outline_var.get())
        if self._outline_enabled:
            self.outline.grid()
            self._refresh_outline()
        else:
            self.outline.grid_remove()

    def _toggle_auto_popup(self):
        enabled = bool(self.auto_popup_var.get())
        for editor in self.editors:
            editor._auto_popup_on_type = enabled

    def _zoom(self, delta):
        size = int(self.code_font.cget("size")) + delta
        size = max(8, min(28, size))
        self._apply_font_size(size)

    def _zoom_reset(self):
        self._apply_font_size(DEFAULT_FONT_SIZE)

    def _apply_font_size(self, size):
        for font in (self.code_font, self.bold_font, self.italic_font):
            font.configure(size=size)
        self.gutter_font.configure(size=max(8, size - 1))
        for editor in self.editors:
            editor.refresh_metrics()
        ttk.Style(self.root).configure(
            "Treeview", rowheight=max(20, self.code_font.metrics("linespace") + 4)
        )

    # ------------------------------------------------------------------ 文件
    @property
    def active_editor(self):
        try:
            widget = self.notebook.nametowidget(self.notebook.select())
        except (tk.TclError, KeyError):
            return None
        return widget if isinstance(widget, EditorPane) else None

    def new_file(self):
        editor = EditorPane(self.notebook, self)
        self._add_editor(editor)
        return editor

    def _add_editor(self, editor):
        self.editors.append(editor)
        self.notebook.add(editor, text=editor.title)
        self.notebook.select(editor)
        self._update_title()
        editor.focus_editor()
        editor._auto_popup_on_type = bool(self.auto_popup_var.get())
        self._schedule_outline()

    def open_file(self):
        path = filedialog.askopenfilename(
            parent=self.root,
            title="打开 Python 文件",
            filetypes=[("Python 文件", "*.py"), ("所有文件", "*.*")],
        )
        if path:
            self.open_path(path)

    def open_path(self, path):
        path = os.path.abspath(path)
        key = os.path.normcase(path)
        for editor in self.editors:
            if editor.path and os.path.normcase(os.path.abspath(editor.path)) == key:
                self.notebook.select(editor)
                editor.focus_editor()
                return editor
        try:
            source = read_source(path)
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)
            return None
        editor = EditorPane(self.notebook, self, path=path)
        editor.set_content(source)
        self._add_editor(editor)
        return editor

    def save_file(self, editor=None, ask_target=False):
        editor = editor or self.active_editor
        if editor is None:
            return False
        target = editor.path
        if ask_target or not target:
            target = filedialog.asksaveasfilename(
                parent=self.root,
                title="保存 Python 文件",
                defaultextension=".py",
                filetypes=[("Python 文件", "*.py"), ("所有文件", "*.*")],
                initialfile=editor.title,
                initialdir=os.path.dirname(editor.path) if editor.path else "",
            )
            if not target:
                return False
        try:
            with open(target, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(editor.get_content())
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return False
        editor.path = os.path.abspath(target)
        editor.dirty = False
        self._update_tab_text(editor)
        self._update_title()
        return True

    def cmd_save(self):
        if self.active_editor is not None:
            self.save_file(self.active_editor)

    def cmd_save_as(self):
        if self.active_editor is not None:
            self.save_file(self.active_editor, ask_target=True)

    def cmd_close_tab(self):
        self.close_tab()

    def close_tab(self, index=None):
        if index is None:
            editor = self.active_editor
        else:
            try:
                editor = self.notebook.nametowidget(self.notebook.tabs()[index])
            except (tk.TclError, IndexError):
                return
        if editor is None or not self._confirm_close(editor):
            return
        if editor in self.editors:
            self.editors.remove(editor)
        self.notebook.forget(editor)
        editor.destroy()
        if not self.editors:
            self.new_file()
        self._update_title()
        self._schedule_outline()

    def _confirm_close(self, editor):
        if not editor.dirty:
            return True
        answer = messagebox.askyesnocancel(
            "保存",
            f"“{editor.title}” 已被修改，是否保存？",
            parent=self.root,
        )
        if answer is None:
            return False
        if answer:
            return self.save_file(editor)
        return True

    def _update_tab_text(self, editor):
        try:
            self.notebook.tab(editor, text=editor.title + (" *" if editor.dirty else ""))
        except tk.TclError:
            pass

    def _update_title(self):
        editor = self.active_editor
        if editor is None:
            self.root.title(APP_NAME)
            return
        mark = " *" if editor.dirty else ""
        self.root.title(f"{editor.title}{mark} — {APP_NAME}")

    def _on_tab_changed(self, event=None):
        if getattr(event, "widget", None) is not None and event.widget is not self.notebook:
            return
        self._update_title()
        self._schedule_outline()

    def _on_tab_right_click(self, event):
        try:
            index = self.notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        menu = tk.Menu(self.root, tearoff=False)
        menu.add_command(label="关闭", command=lambda: self.close_tab(index))
        menu.add_command(label="关闭其他", command=lambda: self._close_others(index))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
            menu.destroy()

    def _on_tab_middle_click(self, event):
        try:
            index = self.notebook.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        self.close_tab(index)

    def _close_others(self, keep_index):
        keep = self.notebook.nametowidget(self.notebook.tabs()[keep_index])
        for editor in list(self.editors):
            if editor is keep:
                continue
            if not self._confirm_close(editor):
                continue
            self.editors.remove(editor)
            self.notebook.forget(editor)
            editor.destroy()

    # ------------------------------------------------------------------ 编辑
    def on_editor_modified(self, editor):
        self._update_tab_text(editor)
        if editor is self.active_editor:
            self._update_title()
            self._schedule_outline()

    def _schedule_outline(self):
        if self._outline_job is None:
            self._outline_job = self.root.after(400, self._refresh_outline)

    def _refresh_outline(self):
        self._outline_job = None
        if not self._outline_enabled:
            return
        self.outline.update_for(self.active_editor)

    def on_breakpoints_changed(self, editor):
        if self.session is not None and self.session.running:
            breakpoints = {
                e.path: set(e.breakpoints)
                for e in self.editors
                if e.path and e.breakpoints
            }
            self.session.update_breakpoints(breakpoints)

    def _editor_event(self, virtual_event):
        editor = self.active_editor
        if editor is not None:
            editor.text.event_generate(virtual_event)

    def _toggle_comment(self):
        editor = self.active_editor
        if editor is not None:
            editor.toggle_comment()
            editor.focus_editor()

    def _duplicate_line(self):
        editor = self.active_editor
        if editor is not None:
            editor._duplicate_line()

    def _delete_line(self):
        editor = self.active_editor
        if editor is not None:
            editor._delete_line()

    def _select_all(self):
        widget = self.root.focus_get()
        if isinstance(widget, tk.Text):
            widget.tag_add("sel", "1.0", "end-1c")
            widget.mark_set("insert", "1.0")
        elif isinstance(widget, (ttk.Entry, tk.Entry)):
            widget.select_range(0, "end")
            widget.icursor("end")

    # ------------------------------------------------------------ 查找 / 导航
    def open_find(self):
        editor = self.active_editor
        if editor is not None:
            editor.open_find(replace=False)

    def open_replace(self):
        editor = self.active_editor
        if editor is not None:
            editor.findbar.open(replace=True, preset="@")

    def open_symbol_search(self):
        editor = self.active_editor
        preset = ""
        if editor is not None:
            word = editor._word_at("insert")
            if word and not keyword.iskeyword(word):
                preset = word
        self.symbol_search.open(preset)

    def cmd_find_next(self):
        editor = self.active_editor
        if editor is None:
            return
        bar = editor.findbar
        if not bar.winfo_ismapped():
            bar.open(replace=False, preset="@")
        else:
            bar.find_next()

    def cmd_find_previous(self):
        editor = self.active_editor
        if editor is None:
            return
        bar = editor.findbar
        if not bar.winfo_ismapped():
            bar.open(replace=False, preset="@")
        else:
            bar.find_previous()

    def cmd_goto_line(self):
        editor = self.active_editor
        if editor is None:
            return
        last = int(editor.text.index("end-1c").split(".")[0])
        current = int(editor.text.index("insert").split(".")[0])
        lineno = simpledialog.askinteger(
            "跳转到行",
            "行号（1 – %d）：" % last,
            initialvalue=current,
            minvalue=1,
            maxvalue=max(1, last),
            parent=self.root,
        )
        if lineno:
            editor.push_nav()
            editor.goto_line(lineno)

    def cmd_complete(self):
        editor = self.active_editor
        if editor is not None:
            editor._manual_complete()

    def cmd_goto_definition(self):
        editor = self.active_editor
        if editor is not None:
            editor.goto_definition()

    def cmd_show_references(self):
        editor = self.active_editor
        if editor is not None:
            editor.show_references()

    def cmd_goto_bracket(self):
        editor = self.active_editor
        if editor is not None:
            editor._goto_matching_bracket()

    def cmd_nav_back(self):
        editor = self.active_editor
        if editor is not None:
            editor.nav_back()

    def cmd_nav_forward(self):
        editor = self.active_editor
        if editor is not None:
            editor.nav_forward()

    # ------------------------------------------------------------ 位置跳转
    def _goto_location(self, editor, index, select_word=None):
        try:
            self.notebook.select(editor)
            editor.text.mark_set("insert", index)
            editor.text.see(index)
            editor.text.focus_set()
        except tk.TclError:
            return
        if select_word:
            try:
                editor.text.tag_remove("sel", "1.0", "end")
                start = editor.text.index("insert")
                editor.text.tag_add("sel", start, "insert+%dc" % len(select_word))
                editor.text.mark_set("insert", "insert+%dc" % len(select_word))
            except tk.TclError:
                pass

    def open_location(self, target, lineno, word=None):
        if isinstance(target, EditorPane):
            editor = target
            try:
                self.notebook.select(editor)
            except tk.TclError:
                return None
        else:
            editor = self.open_path(target)
            if editor is None:
                return None
        index = "%d.0" % lineno
        editor.push_nav()
        editor.text.mark_set("insert", index)
        editor.text.see("%d.0" % max(1, lineno - 3))
        editor.text.focus_set()
        if word:
            try:
                line = editor.text.get("%d.0" % lineno, "%d.end" % lineno)
                match = re.search(r"\b%s\b" % re.escape(word), line)
                if match:
                    start = "%d.%d" % (lineno, match.start())
                    end = "%d.%d" % (lineno, match.end())
                    editor.text.tag_remove("sel", "1.0", "end")
                    editor.text.tag_add("sel", start, end)
                    editor.text.mark_set("insert", end)
            except tk.TclError:
                pass
        return editor

    def show_choice(self, title, results, word, references=False):
        """弹出一个可过滤的候选列表（多个定义 / 多处引用时使用）。"""
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("680x420")
        window.transient(self.root)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(1, weight=1)

        header = ttk.Label(window, text=title, style="PanelTitle.TLabel")
        header.grid(row=0, column=0, sticky="w", padx=8, pady=(6, 2))

        tree = ttk.Treeview(window, columns=("where", "code"), show="tree headings")
        tree.heading("#0", text="类型")
        tree.heading("where", text="位置")
        tree.heading("code", text="代码")
        tree.column("#0", width=60, stretch=False)
        tree.column("where", width=170, stretch=False)
        tree.column("code", width=390, stretch=True)
        tree.grid(row=1, column=0, columnspan=2, sticky="nsew")
        scrollbar = ttk.Scrollbar(window, orient="vertical", command=tree.yview)
        scrollbar.grid(row=1, column=2, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        kind_label = "引用" if references else "定义"
        shown_results = results[:500]
        for label, target, lineno, column, line_text in shown_results:
            tree.insert(
                "",
                "end",
                text=kind_label,
                values=(label, line_text.strip()[:110]),
            )
        children = tree.get_children()
        if children:
            tree.selection_set(children[0])
            tree.focus(children[0])

        entry = ttk.Entry(window, font=self.code_font)
        entry.grid(row=2, column=0, columnspan=3, sticky="ew", padx=6, pady=(4, 6))
        if word and not references:
            entry.insert(0, word)

        def activate(event=None):
            selection = tree.selection()
            visible = tree.get_children()
            if not selection:
                selection = visible[:1]
            if not selection:
                return
            try:
                index = tree.index(selection[0])
            except tk.TclError:
                return
            index = min(index, len(shown_results) - 1)
            label, target, lineno, column, line_text = shown_results[index]
            window.destroy()
            self.open_location(target, lineno, word)

        def focus_tree(event=None):
            children = tree.get_children()
            if children:
                tree.focus(children[0])
                tree.selection_set(children[0])
            return "break"

        def apply_filter(event=None):
            wanted = entry.get().strip().lower()
            for index, item in enumerate(tree.get_children("")):
                values = tree.item(item, "values")
                text = ("%s %s" % (values[0], values[1])).lower()
                if wanted and wanted not in text:
                    tree.detach(item)
                else:
                    tree.reattach(item, "", "end")

        entry.bind("<KeyRelease>", apply_filter)
        entry.bind("<Return>", activate)
        entry.bind("<Down>", focus_tree)
        entry.bind("<Escape>", lambda event: window.destroy())
        tree.bind("<Double-1>", activate)
        tree.bind("<Return>", activate)

        entry.focus_set()

    # ------------------------------------------------------------ 定义解析
    def open_module_source(self, module_spec, current_path, word):
        """尝试打开 ``module_spec`` 的源码（本地文件优先，其次标准库）。"""
        if ":" in module_spec:
            base, member = module_spec.split(":", 1)
        else:
            base, member = module_spec, None
        path = None
        if base.startswith("."):
            level = len(base) - len(base.lstrip("."))
            name = base.lstrip(".")
            path = resolve_relative_module_file(current_path, level, name)
        else:
            search_dirs = module_search_paths(current_path or os.getcwd())
            path = resolve_module_file(base, search_dirs)
            if path is None:
                try:
                    module = __import__(base, fromlist=["*"])
                except BaseException:
                    module = None
                if module is not None:
                    path = getattr(module, "__file__", None)
                    if path and path.endswith((".pyc", ".pyo")):
                        path = path[:-1]
        if not path or not os.path.isfile(path):
            return False
        editor = self.open_path(path)
        if editor is None:
            return False
        target = member or word
        if target:
            source = editor.get_content()
            index = editor._definition_index(target, source)
            if index is None:
                match = re.search(r"\b%s\b" % re.escape(target), source)
                index = editor._index_of(match.start(), source) if match else None
            if index:
                editor.text.mark_set("insert", index)
                editor.text.see(index)
                editor.text.focus_set()
        return True

    def show_object_doc(self, word):
        """跳转定义的兜底：显示对象的签名和文档字符串。"""
        namespace = {name: getattr(builtins, name) for name in dir(builtins)}
        obj = None
        try:
            exec("obj = " + word, namespace)
            obj = namespace.get("obj")
        except BaseException:
            obj = None
        if obj is None:
            self.status_var.set("没有找到 “%s” 的定义（可能是未定义的名称）" % word)
            return
        try:
            signature = str(inspect.signature(obj))
        except (TypeError, ValueError):
            signature = ""
        try:
            doc = inspect.getdoc(obj) or "（无文档字符串）"
        except BaseException:
            doc = "（无法读取文档）"
        try:
            origin = inspect.getsourcefile(obj) or ""
        except BaseException:
            origin = ""

        window = tk.Toplevel(self.root)
        window.title("%s%s" % (word, signature))
        window.geometry("640x420")
        window.transient(self.root)
        text = tk.Text(
            window,
            wrap="word",
            font=self.code_font,
            bg=self.theme["bg"],
            fg=self.theme["fg"],
            insertbackground=self.theme["caret"],
            padx=10,
            pady=8,
        )
        text.pack(fill="both", expand=True)
        header = "%s%s%s" % (
            word,
            signature,
            ("\n来源：%s" % origin) if origin else "",
        )
        text.insert("end", header + "\n\n" + doc)
        text.configure(state="disabled")

        bar = ttk.Frame(window)
        bar.pack(fill="x", padx=6, pady=(0, 6))
        spec = ""
        editor = self.active_editor
        if editor is not None:
            spec = editor._module_map.get(word, "")
        entry = ttk.Entry(bar, font=self.code_font)
        entry.pack(side="left", fill="x", expand=True)
        entry.insert(0, spec)

        def jump(event=None):
            target = entry.get().strip()
            if not target:
                return
            current = self.active_editor.path if self.active_editor else None
            if self.open_module_source(target, current, word):
                window.destroy()
            else:
                self.status_var.set("找不到 “%s” 的源码文件" % target)

        entry.bind("<Return>", jump)
        ttk.Button(bar, text="打开源码", command=jump).pack(side="left", padx=(6, 0))

    # ------------------------------------------------------------------ 运行
    def cmd_run(self):
        if self.session is not None and self.session.running:
            if self.session.paused:
                self._resume("continue")
            return
        self._start_program(stop_on_entry=False)

    def cmd_debug(self):
        if self.session is not None and self.session.running:
            return
        self._start_program(stop_on_entry=True)

    def _start_program(self, stop_on_entry):
        editor = self.active_editor
        if editor is None:
            editor = self.new_file()
        if not editor.path:
            if not self.save_file(editor, ask_target=True):
                return
        if editor.dirty:
            answer = messagebox.askyesnocancel(
                "运行",
                f"“{editor.title}” 已被修改，运行前是否保存？",
                parent=self.root,
            )
            if answer is None:
                return
            if answer and not self.save_file(editor):
                return

        source = editor.get_content()
        breakpoints = {
            e.path: set(e.breakpoints)
            for e in self.editors
            if e.path and e.breakpoints
        }
        self._clear_debug_marks()
        self.stack.clear()
        self.variables.show_frame(None)
        self.session = DebugSession()
        self.session.start(editor.path, source, breakpoints, stop_on_entry)

        self.shell.write(f"\n=== 运行 {os.path.basename(editor.path)} ===\n", "system")
        self.shell.set_enabled(False)
        self._frame_index = 0
        self.status_var.set("正在运行…")
        self._update_buttons()

    def cmd_stop(self):
        if self.session is not None and self.session.running:
            self.session.request_stop()
            self.status_var.set("正在停止…")

    def _resume(self, command):
        if self.session is None or not self.session.paused:
            return
        if self.session.send(command):
            self._clear_debug_marks()
            self.stack.clear()
            self.variables.show_frame(None)
            self.shell.set_enabled(False)
            self.status_var.set("正在运行…")
            self._update_buttons()

    def cmd_step_over(self):
        self._resume("step_over")

    def cmd_step_into(self):
        self._resume("step_into")

    def cmd_step_out(self):
        self._resume("step_out")

    def cmd_toggle_breakpoint(self):
        editor = self.active_editor
        if editor is not None:
            editor.toggle_breakpoint()
            editor.focus_editor()

    def _update_buttons(self):
        running = bool(self.session and self.session.running)
        paused = bool(self.session and self.session.paused)
        self.btn_run.configure(
            text="继续" if paused else "▶ 运行",
            state="normal" if (not running or paused) else "disabled",
        )
        self.btn_stop.configure(state="normal" if running else "disabled")
        step_state = "normal" if paused else "disabled"
        for button in (self.btn_over, self.btn_into, self.btn_out):
            button.configure(state=step_state)

    # ------------------------------------------------------------------ 消息
    def _poll(self):
        self._poll_job = None
        try:
            if self.session is not None:
                while True:
                    message = self.session.get_message()
                    if message is None:
                        break
                    self._handle_message(message)
        finally:
            if not self._closing:
                self._poll_job = self.root.after(60, self._poll)

    def _handle_message(self, message):
        tag = message[0]
        if tag == "stdout":
            self.shell.write(message[1], "stdout")
        elif tag == "stderr":
            self.shell.write(message[1], "stderr")
        elif tag == "paused":
            self._on_paused(message[1], message[2])
        elif tag == "exception":
            self.shell.write(message[1], "stderr")
            if message[2]:
                self._mark_error(*message[2])
            self.status_var.set("程序因异常终止")
        elif tag == "finished":
            self._on_finished(message[1])
        elif tag == "stopped":
            self.shell.write("（程序已停止）\n", "system")
            self.status_var.set("已停止")
            self._update_buttons()
        elif tag == "input_request":
            self._ask_input(message[1], message[2])

    def _on_paused(self, frames, reason):
        if not frames:
            return
        self._frame_index = 0
        self.variables.show_frame(frames[0])
        self.stack.show_frames(frames)
        self._highlight_frame(0)
        self.shell.set_enabled(True)
        self.shell.focus()
        frame = frames[0]
        self.status_var.set(
            f"已暂停（{reason}）：{frame.short_filename} 第 {frame.lineno} 行"
        )
        self._update_buttons()

    def _on_finished(self, exit_code):
        # 只清除当前行标记，保留异常标红，方便用户定位出错位置。
        for editor in self.editors:
            editor.set_current_line(None)
        self.stack.clear()
        self.shell.set_enabled(True)
        if exit_code is None:
            self.status_var.set("已停止")
        elif exit_code == 0:
            self.status_var.set("运行结束")
        else:
            self.status_var.set(f"程序结束（退出码 {exit_code}）")
        self._update_buttons()

    def _ask_input(self, prompt, token):
        value = simpledialog.askstring(
            "程序需要输入", prompt or "请输入：", parent=self.root
        )
        if self.session is not None:
            self.session.provide_input(token, value)

    def on_stack_frame_selected(self, index):
        if self.session is None or not self.session.frames:
            return
        frames = self.session.frames
        if not 0 <= index < len(frames):
            return
        self._frame_index = index
        self.variables.show_frame(frames[index])
        self._highlight_frame(index)

    def _highlight_frame(self, index):
        if self.session is None:
            return
        frames = self.session.frames
        if not 0 <= index < len(frames):
            return
        frame_info = frames[index]
        editor = self._find_editor(frame_info.filename)
        if editor is None and os.path.exists(frame_info.filename):
            editor = self.open_path(frame_info.filename)
        if editor is None:
            return
        for other in self.editors:
            other.set_current_line(None)
        self.notebook.select(editor)
        editor.set_current_line(frame_info.lineno)
        editor.see_line(frame_info.lineno)

    def _find_editor(self, path):
        key = os.path.normcase(os.path.abspath(path))
        for editor in self.editors:
            if editor.path and os.path.normcase(os.path.abspath(editor.path)) == key:
                return editor
        return None

    def _mark_error(self, path, lineno):
        editor = self._find_editor(path)
        if editor is None and os.path.exists(path):
            editor = self.open_path(path)
        if editor is not None:
            self.notebook.select(editor)
            editor.set_error_line(lineno)
            editor.see_line(lineno)

    def _clear_debug_marks(self):
        for editor in self.editors:
            editor.clear_debug_marks()

    def _shell_namespace(self):
        session = self.session
        if session is not None and session.globals is not None:
            if session.paused and session.frames:
                index = min(self._frame_index, len(session.frames) - 1)
                return session.globals, session.frames[index].frame.f_locals
            return session.globals, session.globals
        return self._own_globals, self._own_globals

    # ------------------------------------------------------------------ 杂项
    def _show_shortcuts(self):
        messagebox.showinfo("快捷键", SHORTCUTS_TEXT, parent=self.root)

    def _show_about(self):
        messagebox.showinfo(
            "关于 Mini Thonny",
            "Mini Thonny\n\n"
            "一个受 Thonny 启发的简化版 Python 编辑器 / 调试器。\n"
            "界面使用 tkinter，调试器基于 sys.settrace。\n\n"
            "编辑辅助：语法补全（Alt+/）、括号自动配对、Ctrl+左键 / F12 跳转定义、\n"
            "Shift+F12 查看引用、Ctrl+F / Ctrl+H 查找替换、大纲与符号搜索面板。\n\n"
            f"Python {sys.version.split()[0]}",
            parent=self.root,
        )

    def on_close(self):
        if self.session is not None and self.session.running:
            if not messagebox.askyesno(
                "退出", "程序仍在运行，是否停止并退出？", parent=self.root
            ):
                return
            self.session.request_stop()
        for editor in list(self.editors):
            if not self._confirm_close(editor):
                return
        self._closing = True
        for job in (self._poll_job, self._sash_job, self._outline_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
        self.root.destroy()

    def _on_destroy(self, event):
        if event.widget is not self.root:
            return
        self._closing = True
        for job in (self._poll_job, self._sash_job, self._outline_job):
            if job is not None:
                try:
                    self.root.after_cancel(job)
                except tk.TclError:
                    pass
                self._poll_job = self._sash_job = None


def main():
    app = App(sys.argv[1:])
    app.root.mainloop()


if __name__ == "__main__":
    main()

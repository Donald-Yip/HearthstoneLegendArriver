# -*- coding: utf-8 -*-
"""Right-top live log overlay via tkinter (topmost, translucent, draggable).

start() launches a background thread with a tkinter window pinned to the
top-right corner. It streams the latest automation log lines; lines that mark
the own-turn start are highlighted green. The window can be dragged by holding
the left mouse button. Failures disable the overlay and never crash the caller.

Buttons:
  * ▶ 开始对战            — start automation
  * ⏹ 中止 / ▶ 恢复       — toggle stop/resume (state-aware)
  * ⏸ 本局结束后停止        — toggle; cancel anytime before the match ends
Every button hands the foreground back to Hearthstone afterwards.
"""
from __future__ import annotations

import re
import threading
import time
from collections import deque

_LOCK = threading.Lock()
_LINES: deque = deque(maxlen=64)
_STARTED = [False]
_REFRESH_MS = 350

# ---- flat dark palette ---------------------------------------------------
BG = "#171b24"
PANEL = "#202634"
TITLE_BG = "#10141c"
TEXT = "#e8ecf2"
DIM = "#8b93a3"
GREEN = "#5fd68a"
ACCENT = "#4aa3ff"
DANGER = "#e05e4b"
WARN = "#d98a2e"
OK = "#2ea06b"
DISABLED = "#394050"


def _turn_start(line: str) -> bool:
    return ("回合" in line and "延时" in line) or ("轮到己方" in line)


_DELAY = None
_delay_start_re = re.compile(r"(?:延时|等待)\s*(\d+(?:\.\d+)?)\s*s?\s*后")
_delay_end_markers = ("延时结束", "延时完毕")


def _update_delay_from_line(line: str) -> None:
    """从日志行识别延时起点/终点，驱动浮窗底部延时进度条。"""
    global _DELAY
    start = _delay_start_re.search(line)
    if start:
        total = float(start.group(1))
        if "换牌" in line:
            label = "换牌延时"
        elif "回合" in line:
            label = "回合延时"
        else:
            label = "延时"
        _DELAY = {"label": label, "total": total, "started": time.time()}
        return
    if any(marker in line for marker in _delay_end_markers):
        _DELAY = None


def push(line: str, _level: str = "INFO") -> None:
    if not _STARTED[0]:
        return
    line = str(line).rstrip()
    if not line.strip():
        return
    with _LOCK:
        _LINES.append((line, _turn_start(line)))
        _update_delay_from_line(line)


_STOP = threading.Event()


_ON_START = None
_ON_HALT = None
_IS_RUNNING = None
_ON_STOP_AFTER = None
_IS_STOP_AFTER = None
_IS_IN_GAME = None


def start(on_start=None, on_halt=None, is_running=None,
          on_stop_after=None, is_stop_after=None,
          is_in_game=None) -> None:
    global _ON_START, _ON_HALT, _IS_RUNNING, _ON_STOP_AFTER, _IS_STOP_AFTER
    global _IS_IN_GAME
    if _STARTED[0]:
        return
    _ON_START = on_start
    _ON_HALT = on_halt
    _IS_RUNNING = is_running
    _ON_STOP_AFTER = on_stop_after
    _IS_STOP_AFTER = is_stop_after
    _IS_IN_GAME = is_in_game
    _STOP.clear()
    _STARTED[0] = True
    threading.Thread(target=_run, name="hs-log-overlay", daemon=True).start()


def stop() -> None:
    """Signal the overlay thread to close its window."""
    _STOP.set()


def is_running() -> bool:
    return bool(_STARTED[0])


def _raise_hearthstone() -> None:
    """Return the foreground window to the Hearthstone main window.

    The topmost tkinter overlay grabs focus/activation when clicked, pushing
    Hearthstone off the foreground. Enumerate visible top-level windows, find
    the one whose title contains “炉石/Hearthstone”, and raise it. Failures are
    silent (they must not break the overlay).
    """
    try:
        import win32gui
        import win32con
    except Exception:
        return
    target = [None]

    def _enum(hwnd, _unused):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            return True
        low = title.lower()
        if low and ("hearthstone" in low or "炉石" in low):
            target[0] = hwnd
            return False  # stop at the first matching main window
        return True

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        return
    hwnd = target[0]
    if not hwnd:
        return
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass


def _run() -> None:
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"[overlay] tkinter 不可用: {type(exc).__name__}: {exc}")
        _STARTED[0] = False
        return

    try:
        root = tk.Tk()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.attributes("-alpha", 0.94)
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        W, H = 292, 372
        x = sw - W - 12
        y = 12
        root.geometry(f"{W}x{H}+{x}+{y}")
        root.configure(bg=TITLE_BG)

        def _hover(c):
            # lighten a hex color for hover feedback
            try:
                r = min(255, int(c[1:3], 16) + 22)
                g = min(255, int(c[3:5], 16) + 22)
                b = min(255, int(c[5:7], 16) + 22)
                return f"#{r:02x}{g:02x}{b:02x}"
            except Exception:
                return c

        def _make_btn(parent, text_, bg, command):
            btn = tk.Button(
                parent, text=text_, bg=bg, fg="white",
                font=("Microsoft YaHei", 10, "bold"),
                relief="flat", bd=0, pady=6, cursor="hand2",
                activebackground=bg, activeforeground="white",
                command=command)
            btn.bind(
                "<Enter>", lambda _e, b=bg: btn.config(bg=_hover(b)))
            btn.bind("<Leave>", lambda _e, b=bg: btn.config(bg=b))
            return btn

        # ---- header ----------------------------------------------------
        head = tk.Frame(root, bg=TITLE_BG)
        head.pack(fill="x")
        dot = tk.Label(head, text="●", bg=TITLE_BG, fg=GREEN,
                       font=("Segoe UI", 10))
        dot.pack(side="left", padx=(10, 4), pady=8)
        title = tk.Label(head, text="自动化日志", bg=TITLE_BG, fg=TEXT,
                         font=("Microsoft YaHei", 10, "bold"))
        title.pack(side="left", pady=8)
        tk.Frame(root, bg=PANEL, height=1).pack(fill="x")

        # ---- buttons ---------------------------------------------------
        btn_frame = tk.Frame(root, bg=BG)
        btn_frame.pack(fill="x", padx=8, pady=(8, 0))

        def _call_start():
            try:
                # 对局已开始时按钮应处于禁用态；这里再兜底一次，避免误触发。
                if _IS_IN_GAME is not None and _IS_IN_GAME():
                    return
                if _ON_START is not None:
                    _ON_START()
            finally:
                _raise_hearthstone()

        start_btn = _make_btn(btn_frame, "▶  开始对战", ACCENT, _call_start)
        start_btn.pack(fill="x", pady=3)

        def _call_halt():
            try:
                if _ON_HALT is not None:
                    _ON_HALT()
            finally:
                _raise_hearthstone()

        halt_btn = _make_btn(btn_frame, "⏹  中止", DANGER, _call_halt)
        halt_btn.pack(fill="x", pady=3)

        def _set_stop_after_state():
            active = bool(_IS_STOP_AFTER() if _IS_STOP_AFTER is not None else False)
            if active:
                stop_after_btn.config(
                    text="✓  本局结束后停止（点击取消）",
                    bg=OK, activebackground=OK)
            else:
                stop_after_btn.config(
                    text="⏸  本局结束后停止", bg=WARN, activebackground=WARN)

        def _call_stop_after():
            try:
                if _ON_STOP_AFTER is not None:
                    _ON_STOP_AFTER()
            finally:
                _raise_hearthstone()
                _set_stop_after_state()

        stop_after_btn = _make_btn(btn_frame, "⏸  本局结束后停止", WARN,
                                   _call_stop_after)
        stop_after_btn.pack(fill="x", pady=3)

        # ---- delay progress (bottom; 先占底部，日志区填剩余空间) ------
        delay_frame = tk.Frame(root, bg=BG)
        delay_frame.pack(side="bottom", fill="x", padx=8, pady=(0, 8))
        delay_label = tk.Label(delay_frame, text="延时：无", bg=BG, fg=DIM,
                               font=("Microsoft YaHei", 8), anchor="w")
        delay_label.pack(fill="x")
        delay_canvas = tk.Canvas(delay_frame, height=8, bg=PANEL,
                                 highlightthickness=0)
        delay_canvas.pack(fill="x", pady=(2, 0))

        # ---- log body --------------------------------------------------
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=8, pady=(6, 8))
        text = tk.Text(body, bg=BG, fg=TEXT, font=("Microsoft YaHei", 9),
                       bd=0, highlightthickness=0, wrap="none",
                       height=12, padx=2, pady=2, spacing1=2, spacing3=2)
        text.pack(side="left", fill="both", expand=True)
        scroll = tk.Scrollbar(body, orient="vertical", command=text.yview,
                              width=10)
        scroll.pack(side="right", fill="y")
        text.config(yscrollcommand=scroll.set)
        text.tag_config("turn", foreground=GREEN)
        text.tag_config("act", foreground=TEXT)
        text.tag_config("dim", foreground=DIM)

        # ---- drag ------------------------------------------------------
        _drag = {"x": 0, "y": 0}

        def _start_drag(event):
            _drag["x"], _drag["y"] = event.x, event.y

        def _on_drag(event):
            nx = root.winfo_x() + event.x - _drag["x"]
            ny = root.winfo_y() + event.y - _drag["y"]
            root.geometry(f"+{nx}+{ny}")

        root.bind("<Button-1>", _start_drag)
        root.bind("<B1-Motion>", _on_drag)

        def _update():
            if _STOP.is_set():
                root.destroy()
                _STARTED[0] = False
                return
            with _LOCK:
                lines = list(_LINES)[-40:]
            pos = text.yview()
            text.delete("1.0", "end")
            for ln, turn in lines:
                tag = "turn" if turn else (
                    "act" if ln.startswith(("[推荐]", "[执行]")) else "dim")
                text.insert("end", ln + "\n", tag)
            if _IS_RUNNING is not None:
                if _IS_RUNNING():
                    halt_btn.config(text="⏹  中止", bg=DANGER,
                                    activebackground=DANGER)
                else:
                    halt_btn.config(text="▶  恢复", bg=OK, activebackground=OK)
            if _IS_IN_GAME is not None and _IS_IN_GAME():
                start_btn.config(state="disabled", text="⏳  对局进行中",
                                 bg=DISABLED, activebackground=DISABLED,
                                 disabledforeground=DIM,
                                 cursor="arrow")
            else:
                start_btn.config(state="normal", text="▶  开始对战",
                                 bg=ACCENT, activebackground=ACCENT,
                                 disabledforeground=DIM,
                                 cursor="hand2")
            with _LOCK:
                delay = dict(_DELAY) if _DELAY is not None else None
            if delay is not None:
                now = time.time()
                elapsed = now - delay["started"]
                total = max(delay["total"], 0.001)
                frac = min(max(elapsed / total, 0.0), 1.0)
                remaining = max(total - elapsed, 0.0)
                delay_label.config(
                    text=f"⏳ {delay['label']}：{remaining:.0f}/{total:.0f}s",
                    fg=TEXT)
                w = max(delay_canvas.winfo_width(), 1)
                delay_canvas.delete("all")
                delay_canvas.create_rectangle(
                    0, 0, w * frac, 8, fill=ACCENT, outline="")
            else:
                delay_label.config(text="延时：无", fg=DIM)
                delay_canvas.delete("all")
            _set_stop_after_state()
            text.yview_moveto(pos[0])
            root.after(_REFRESH_MS, _update)

        _update()
        root.mainloop()
    except Exception as exc:
        print(f"[overlay] 日志浮窗禁用: {type(exc).__name__}: {exc}")
        _STARTED[0] = False

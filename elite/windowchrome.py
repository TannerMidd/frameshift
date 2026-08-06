"""Custom window chrome for the frameless desktop window (Windows only).

The desktop window is frameless so the title bar can be drawn in the cockpit
theme like everything else. Dropping the frame also drops everything Windows was
doing for us — edge resizing, Aero Snap, Snap Layouts, double-click to maximize,
and not covering the taskbar when maximized. This module hands those back by
answering the non-client messages itself.

Why ctypes and not a WinForms subclass: pywebview builds its form as a pythonnet
subclass of WinForms.Form, but pythonnet 3.1 never dispatches a `WndProc`
override — a traced override records zero calls, and a SendMessage probe comes
back with the default result. Subclassing the window procedure through
SetWindowLongPtrW and chaining to the original with CallWindowProcW is the only
mechanism that actually intercepts, so that is what this uses.

The geometry lives in `hit_test`, which is pure and platform-free so it can be
tested without ever opening a window.
"""

import ctypes
import logging
import queue
import sys
import threading

_logger = logging.getLogger(__name__)

# Win32 messages we answer.
WM_DESTROY = 0x0002
WM_SIZE = 0x0005
WM_GETMINMAXINFO = 0x0024
WM_DISPLAYCHANGE = 0x007E
WM_NCHITTEST = 0x0084
WM_NCLBUTTONDOWN = 0x00A1
WM_NCLBUTTONUP = 0x00A2
WM_NCMOUSELEAVE = 0x02A2

# Our own messages, used to hop a fullscreen change onto the GUI thread; see
# request_fullscreen. WM_APP rather than WM_USER because the window class is
# WinForms', and WM_USER + n is reserved for the class to use as it likes.
WM_APP = 0x8000
WM_APP_FULLSCREEN = WM_APP + 1
WM_APP_FULLSCREEN_SYNC = WM_APP + 2

# fullscreen_request codes, carried in the WPARAM of WM_APP_FULLSCREEN.
FULLSCREEN_LEAVE = 0
FULLSCREEN_ENTER = 1
FULLSCREEN_TOGGLE = 2

# Hit-test results. HTMAXBUTTON is what makes Windows 11 offer Snap Layouts.
HTCLIENT = 1
HTCAPTION = 2
HTMAXBUTTON = 9
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

GWLP_WNDPROC = -4
MONITOR_DEFAULTTONEAREST = 2

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWA_BORDER_COLOR = 34
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF
DWMWA_COLOR_NONE = 0xFFFFFFFE
DWMWCP_DEFAULT = 0
DWMWCP_DONOTROUND = 1

# SetWindowPos. HWND_TOP and not HWND_TOPMOST: topmost is the old exclusive
# fullscreen trick, and it breaks alt-tab and covers our own child windows.
HWND_TOP = 0
SWP_FRAMECHANGED = 0x0020
SWP_NOOWNERZORDER = 0x0200

SW_SHOWMAXIMIZED = 3
SW_RESTORE = 9

# Grab band around the window edge, in physical pixels. Windows' own frame is
# about this wide; wider feels sloppy, narrower is hard to hit.
RESIZE_INSET = 6

SIZE_RESTORED = 0
SIZE_MAXIMIZED = 2


def _inside(rect, x, y):
    """rect is (left, top, right, bottom) in window-relative physical pixels."""
    if not rect:
        return False
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def hit_test(
    x,
    y,
    width,
    height,
    *,
    drag_rects=(),
    max_button=None,
    resizable=True,
    inset=RESIZE_INSET,
    fullscreen=False,
):
    """Map a window-relative point to the HT* code Windows should act on.

    Every coordinate is in physical pixels, because that is what Windows works
    in; the browser side is responsible for multiplying its CSS rectangles by
    devicePixelRatio before reporting them (see set_metrics).

    Edges win over everything so a drag strip that reaches the window border
    still resizes at its outermost pixels. `resizable` is false when maximized,
    where a resize border would be wrong.
    """
    # A fullscreen window has no caption, no resize border and no maximize
    # button. Answering that here rather than waiting for the browser to report
    # empty rectangles closes the gap between the two: the notification that
    # tells it to re-measure crosses a thread, so for a moment the stale caption
    # would still drag the window from the top of the screen.
    if fullscreen:
        return HTCLIENT
    if resizable:
        left = x < inset
        right = x >= width - inset
        top = y < inset
        bottom = y >= height - inset
        if top and left:
            return HTTOPLEFT
        if top and right:
            return HTTOPRIGHT
        if bottom and left:
            return HTBOTTOMLEFT
        if bottom and right:
            return HTBOTTOMRIGHT
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
    if _inside(max_button, x, y):
        return HTMAXBUTTON
    for rect in drag_rects:
        if _inside(rect, x, y):
            return HTCAPTION
    # Anything the title bar did not claim stays client area, so the DOM keeps
    # receiving ordinary mouse events for its own buttons.
    return HTCLIENT


def max_rect(work, screen, fullscreen=False):
    """The (position, size) a maximized window should take, for WM_GETMINMAXINFO.

    Both rectangles are (left, top, right, bottom) in virtual-screen pixels. The
    position comes back relative to the monitor's own origin, which is what the
    subtractions are for — on a second monitor left of the primary those are
    negative numbers, and Windows wants the offset, not the coordinate.

    Maximizing normally stops at the work area, because a frameless window would
    otherwise cover the taskbar. While fullscreen that clamp is exactly wrong, so
    the whole monitor is reported and any route that maximizes us mid-fullscreen
    (Win+Up, Snap Layouts) lands on the rectangle we already occupy.
    """
    target = screen if fullscreen else work
    return (
        (target[0] - screen[0], target[1] - screen[1]),
        (target[2] - target[0], target[3] - target[1]),
    )


def rect_to_bounds(rect):
    """(left, top, right, bottom) -> the (x, y, cx, cy) SetWindowPos wants."""
    left, top, right, bottom = rect
    return left, top, right - left, bottom - top


def covers_monitor(window_rect, monitor_rect):
    """Whether the window still fills the monitor exactly.

    The self-heal check: a resolution change, a monitor unplug or Win+Down can
    move a fullscreen window without telling us, and this is how we notice.
    """
    return tuple(window_rect) == tuple(monitor_rect)


def fullscreen_request(code, current):
    """Resolve a WM_APP_FULLSCREEN wparam against the state it arrives at."""
    if code == FULLSCREEN_TOGGLE:
        return not current
    return code == FULLSCREEN_ENTER


def normalize_show_cmd(show_cmd):
    """The showCmd to replay when leaving fullscreen.

    Only maximized survives verbatim. Anything else becomes SW_RESTORE, because
    the placement may have been captured before the window was ever shown — its
    showCmd is SW_HIDE then, and replaying that would restore the window into
    invisibility with no way to get it back.
    """
    return SW_SHOWMAXIMIZED if show_cmd == SW_SHOWMAXIMIZED else SW_RESTORE


def _loword(value):
    """Low 16 bits of an LPARAM as a signed coordinate (screens go negative)."""
    return ctypes.c_short(value & 0xFFFF).value


def _hiword(value):
    return ctypes.c_short((value >> 16) & 0xFFFF).value


def hex_to_colorref(value):
    """'#ff7100' -> 0x000071FF. Win32 packs colours as 0x00BBGGRR, the reverse
    of the web order, which is a cheap and popular way to get a blue window."""
    text = str(value or "").strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        red, green, blue = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None
    return (blue << 16) | (green << 8) | red


def accent_border_color(accent, fullscreen):
    """What DWMWA_BORDER_COLOR should hold right now.

    A 1px accent line down the edge of a fullscreen window is a visible seam
    against the game, so fullscreen means no border at all — and it has to be
    recomputed rather than latched, because changing Color theme while
    fullscreen would otherwise put the border back.

    An unusable accent falls back to the system default instead of leaving the
    attribute alone, so leaving fullscreen always lands somewhere sane.
    """
    if fullscreen:
        return DWMWA_COLOR_NONE
    value = hex_to_colorref(accent)
    # Explicitly `is None`: black packs to 0, which is falsy and a real colour.
    return DWMWA_COLOR_DEFAULT if value is None else value


if sys.platform == "win32":
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_long, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class _POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class _MINMAXINFO(ctypes.Structure):
        _fields_ = [
            ("ptReserved", _POINT),
            ("ptMaxSize", _POINT),
            ("ptMaxPosition", _POINT),
            ("ptMinTrackSize", _POINT),
            ("ptMaxTrackSize", _POINT),
        ]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    class _WINDOWPLACEMENT(ctypes.Structure):
        # Six fields exactly: the SDK's seventh, rcDevice, is #ifdef _MAC and
        # adding it here would put every offset after length out by a rectangle.
        _fields_ = [
            ("length", wintypes.UINT),
            ("flags", wintypes.UINT),
            ("showCmd", wintypes.UINT),
            ("ptMinPosition", _POINT),
            ("ptMaxPosition", _POINT),
            ("rcNormalPosition", _RECT),
        ]
else:  # pragma: no cover - the desktop window is Windows-only
    _user32 = None
    _WNDPROC = None


def _configure_win32():
    """Pin argument/return types once. Left to call time so importing this
    module for the geometry tests never touches user32."""
    _user32.CallWindowProcW.restype = ctypes.c_long
    _user32.CallWindowProcW.argtypes = [
        ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
    _user32.MonitorFromWindow.restype = ctypes.c_void_p
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
    _user32.IsZoomed.argtypes = [wintypes.HWND]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(_WINDOWPLACEMENT)]
    _user32.SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(_WINDOWPLACEMENT)]
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    ]
    setter = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
    setter.restype = ctypes.c_void_p
    setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    return setter


def _rect_tuple(rect):
    """A ctypes RECT as the (left, top, right, bottom) the pure helpers speak."""
    return (rect.left, rect.top, rect.right, rect.bottom)


def _window_rect(hwnd):
    rect = _RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return _rect_tuple(rect)


def _monitor_rects(hwnd):
    """(work area, whole screen) for the monitor the window is mostly on."""
    monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None
    return _rect_tuple(info.rcWork), _rect_tuple(info.rcMonitor)


class WindowChrome:
    """Owns one window's non-client behaviour.

    Message handling runs on the GUI thread. Two rules follow from that and both
    matter: never raise out of the window procedure (a Python exception crossing
    back into Win32 takes the window with it), and never call evaluate_js from
    inside it — that re-enters the GUI thread and deadlocks. JS notifications go
    through a worker thread instead.
    """

    def __init__(self, window, fullscreen=False):
        self._window = window
        self._hwnd = None
        self._original = None
        self._callback = None  # must outlive the window; see attach()
        self._drag_rects = ()
        self._max_button = None
        self._max_hover = False
        self._maximized = False
        self._fullscreen = False
        self._initial_fullscreen = bool(fullscreen)
        self._placement = None
        self._accent = None
        # Told about every fullscreen change so the choice survives a restart.
        # Called on the notification worker, never on the GUI thread: it writes
        # a file, and the window procedure is no place to touch the disk.
        self.on_change = None
        self._lock = threading.Lock()
        self._notifications = queue.Queue()
        self._worker = None

    # -- lifecycle ---------------------------------------------------------
    def attach(self):
        """Subclass the window procedure. Idempotent."""
        if self._hwnd is not None:
            return
        native = getattr(self._window, "native", None)
        if native is None:
            _logger.warning("window chrome: no native form to attach to")
            return
        setter = _configure_win32()
        hwnd = native.Handle.ToInt32()
        callback = _WNDPROC(self._wndproc)
        original = setter(hwnd, GWLP_WNDPROC, ctypes.cast(callback, ctypes.c_void_p))
        if not original:
            _logger.warning("window chrome: could not subclass the window procedure")
            return
        # Both references are load-bearing: dropping the callback lets Python
        # collect a function Windows still calls, which crashes the process.
        self._callback = callback
        self._original = original
        self._hwnd = hwnd
        self._worker = threading.Thread(
            target=self._drain_notifications, name="window-chrome", daemon=True
        )
        self._worker.start()
        _logger.info("window chrome attached")
        # before_show runs after the form exists but before it is shown, so a
        # remembered fullscreen is in place ahead of the first paint rather than
        # snapping open at 1060x800 and then jumping to the screen.
        if self._initial_fullscreen:
            self._apply_fullscreen(True, persist=False)

    def _detach(self):
        if self._hwnd is None:
            return
        try:
            setter = getattr(_user32, "SetWindowLongPtrW", None) or _user32.SetWindowLongW
            setter(self._hwnd, GWLP_WNDPROC, self._original)
        except Exception:
            _logger.exception("window chrome: could not restore the window procedure")
        self._hwnd = None
        self._notifications.put(None)

    # -- metrics from the browser -----------------------------------------
    def set_metrics(self, drag_rects=None, max_button=None):
        """Record where the title bar is, in window-relative physical pixels.

        The browser side reports this because the title bar's height is not a
        constant: whole-app zoom and the per-device size sliders both move it,
        as does dragging the window to a monitor with a different DPI.
        """
        with self._lock:
            self._drag_rects = tuple(
                tuple(int(v) for v in rect) for rect in (drag_rects or ()) if rect
            )
            self._max_button = tuple(int(v) for v in max_button) if max_button else None

    def set_accent(self, accent):
        """Tint the parts of the window CSS cannot reach: the 1px system border
        and the form's own background, which is what shows during the moment
        between the window appearing and the page painting.

        The accent is remembered rather than applied and forgotten. The border
        it drives is suppressed while fullscreen, so leaving fullscreen has to
        be able to put it back — including after a theme change that happened
        while there was no border to change.
        """
        with self._lock:
            self._accent = accent
        self._apply_border()
        if hex_to_colorref(accent) is None:
            return
        try:
            from System.Drawing import ColorTranslator

            native = getattr(self._window, "native", None)
            if native is not None:
                native.BackColor = ColorTranslator.FromHtml(accent)
        except Exception:
            _logger.debug("window chrome: back colour unavailable", exc_info=True)

    def _dwm_set(self, attribute, value):
        if self._hwnd is None:
            return
        try:
            from webview.platforms.winforms import DwmSetWindowAttribute

            DwmSetWindowAttribute(self._hwnd, attribute, value)
        except Exception:
            _logger.debug("window chrome: attribute %s unavailable", attribute, exc_info=True)

    def _apply_border(self):
        with self._lock:
            accent, fullscreen = self._accent, self._fullscreen
        self._dwm_set(DWMWA_BORDER_COLOR, accent_border_color(accent, fullscreen))

    def _apply_corners(self):
        """Windows 11 rounds a frameless top-level window, which in fullscreen
        leaves four small views of the desktop in the corners of the screen."""
        with self._lock:
            fullscreen = self._fullscreen
        self._dwm_set(
            DWMWA_WINDOW_CORNER_PREFERENCE,
            DWMWCP_DONOTROUND if fullscreen else DWMWCP_DEFAULT,
        )

    # -- window state ------------------------------------------------------
    def is_maximized(self):
        """Asked from the window procedure on every hit-test, and from the JS
        bridge thread. IsZoomed is a plain Win32 query, so unlike reading
        Form.WindowState it is safe from either thread."""
        if self._hwnd is None:
            return False
        return bool(_user32.IsZoomed(self._hwnd))

    def toggle_maximize(self):
        """Route through pywebview's own methods rather than assigning
        WindowState: they wrap the change in Control.Invoke, which is required
        when this arrives from the JS bridge thread and harmless when it
        arrives from the window procedure."""
        try:
            if self.is_maximized():
                self._window.restore()
            else:
                self._window.maximize()
        except Exception:
            _logger.exception("window chrome: could not toggle maximize")

    # -- fullscreen --------------------------------------------------------
    def is_fullscreen(self):
        with self._lock:
            return self._fullscreen

    def request_fullscreen(self, requested=None):
        """Ask for a fullscreen change. Safe from any thread; None toggles.

        The JS bridge calls this on a pywebview worker thread, and everything
        that moves the window has to run where the window procedure runs.
        Posting to our own subclassed procedure is the cheapest way across, and
        unlike Control.Invoke it cannot deadlock: Invoke blocks until the GUI
        thread pumps, and that thread may be sitting in a native modal loop —
        a window drag, a Snap Layouts flyout — with the bridge waiting on us.
        """
        if self._hwnd is None:
            return
        if requested is None:
            code = FULLSCREEN_TOGGLE
        else:
            code = FULLSCREEN_ENTER if requested else FULLSCREEN_LEAVE
        _user32.PostMessageW(self._hwnd, WM_APP_FULLSCREEN, code, 0)

    def _apply_fullscreen(self, target, persist=True):
        """Enter or leave borderless fullscreen. GUI thread only.

        Deliberately not a maximized state. Maximizing keeps re-consulting
        WM_GETMINMAXINFO, so a DPI change or a taskbar resize would quietly snap
        the window back to the work area; Aero Snap and Win+Down act on zoomed
        windows; and IsZoomed being true would tell the rest of this class the
        window is maximized when it is not.
        """
        if self._hwnd is None or target == self._fullscreen:
            return
        if target:
            placement = _WINDOWPLACEMENT()
            placement.length = ctypes.sizeof(_WINDOWPLACEMENT)
            if not _user32.GetWindowPlacement(self._hwnd, ctypes.byref(placement)):
                return
            # Captured before the un-zoom below, so showCmd still carries
            # "was maximized" and rcNormalPosition the size to come back to.
            placement.showCmd = normalize_show_cmd(placement.showCmd)
            self._placement = placement
            # Set before the calls below rather than after: each of them
            # dispatches WM_SIZE and WM_GETMINMAXINFO straight back into
            # _handle on this same thread, and those handlers have to see the
            # new state or the first one clamps us to the work area again.
            with self._lock:
                self._fullscreen = True
            if _user32.IsZoomed(self._hwnd):
                _user32.ShowWindow(self._hwnd, SW_RESTORE)
            self._reassert_fullscreen()
        else:
            with self._lock:
                self._fullscreen = False
            placement, self._placement = self._placement, None
            if placement is not None:
                # Only ever replays a struct we captured. rcNormalPosition is in
                # workspace rather than screen coordinates, so composing one by
                # hand is a well-known multi-monitor trap.
                _user32.SetWindowPlacement(self._hwnd, ctypes.byref(placement))
        self._apply_border()
        self._apply_corners()
        self._notify("setFullscreen(%s)" % ("true" if target else "false"))
        if persist:
            self._notify_change(bool(target))

    def _reassert_fullscreen(self):
        """Put the window back over the whole monitor. GUI thread only."""
        rects = _monitor_rects(self._hwnd)
        if rects is None:
            return
        x, y, cx, cy = rect_to_bounds(rects[1])
        # HWND_TOP rather than HWND_TOPMOST: topmost is the old exclusive
        # fullscreen trick and it breaks alt-tab and covers our own Inara child
        # window. No SWP_NOACTIVATE either — the shell only gives up the taskbar
        # for the foreground window.
        _user32.SetWindowPos(
            self._hwnd, HWND_TOP, x, y, cx, cy, SWP_NOOWNERZORDER | SWP_FRAMECHANGED
        )

    def _covers_monitor(self):
        """Whether fullscreen still holds. True when unknown, so a failed query
        never sets off a repair the window did not need."""
        rects = _monitor_rects(self._hwnd)
        rect = _window_rect(self._hwnd)
        if rects is None or rect is None:
            return True
        return covers_monitor(rect, rects[1])

    def _recheck_fullscreen(self, hwnd):
        """Win+Down, a resolution change or unplugging a monitor can move the
        window out from under fullscreen without telling us. Posted rather than
        repaired in place: SetWindowPos from inside WM_SIZE re-enters here."""
        if self._fullscreen and not self._covers_monitor():
            _user32.PostMessageW(hwnd, WM_APP_FULLSCREEN_SYNC, 0, 0)

    # -- JS notifications --------------------------------------------------
    def _drain_notifications(self):
        while True:
            item = self._notifications.get()
            if item is None:
                return
            try:
                # Callables are how work that must not run on the GUI thread —
                # persisting the fullscreen choice writes a file — gets off it.
                if callable(item):
                    item()
                else:
                    self._window.evaluate_js(item)
            except Exception:
                _logger.debug("window chrome: notification failed", exc_info=True)

    def _notify(self, call):
        self._notifications.put(f"window.__frameshiftChrome && window.__frameshiftChrome.{call}")

    def _notify_change(self, fullscreen):
        callback = self.on_change
        if callback is None:
            return
        self._notifications.put(lambda: callback(fullscreen))

    def _notify_max_hover(self, hovering):
        self._notify("setMaxHover(%s)" % ("true" if hovering else "false"))

    # -- the window procedure ---------------------------------------------
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            handled = self._handle(hwnd, msg, wparam, lparam)
            if handled is not None:
                return handled
        except Exception:
            _logger.exception("window chrome: message %s failed", msg)
        return _user32.CallWindowProcW(self._original, hwnd, msg, wparam, lparam)

    def _handle(self, hwnd, msg, wparam, lparam):
        if msg == WM_NCHITTEST:
            return self._on_hit_test(hwnd, lparam)
        if msg == WM_NCLBUTTONDOWN and wparam == HTMAXBUTTON:
            return 0  # swallow; the click completes on button-up
        if msg == WM_NCLBUTTONUP and wparam == HTMAXBUTTON:
            self.toggle_maximize()
            return 0
        if msg == WM_NCMOUSELEAVE:
            self._set_max_hover(False)
            return None
        if msg == WM_APP_FULLSCREEN:
            self._apply_fullscreen(fullscreen_request(wparam, self._fullscreen))
            return 0
        if msg == WM_APP_FULLSCREEN_SYNC:
            if self._fullscreen and not self._covers_monitor():
                self._reassert_fullscreen()
            return 0
        if msg == WM_SIZE and wparam in (SIZE_RESTORED, SIZE_MAXIMIZED):
            # Win+Up, drag-to-top and Snap Layouts all maximize without going
            # through our button, so the glyph has to follow the window rather
            # than the click that we happened to see.
            self._set_maximized(wparam == SIZE_MAXIMIZED)
            self._recheck_fullscreen(hwnd)
            return None
        if msg == WM_DISPLAYCHANGE:
            self._recheck_fullscreen(hwnd)
            return None
        if msg == WM_GETMINMAXINFO:
            self._on_min_max_info(hwnd, lparam)
            return None
        if msg == WM_DESTROY:
            self._detach()
            return None
        return None

    def _on_hit_test(self, hwnd, lparam):
        rect = _window_rect(hwnd)
        if rect is None:
            return None
        left, top, width, height = rect_to_bounds(rect)
        with self._lock:
            drag_rects = self._drag_rects
            max_button = self._max_button
            fullscreen = self._fullscreen
        result = hit_test(
            _loword(lparam) - left,
            _hiword(lparam) - top,
            width,
            height,
            drag_rects=drag_rects,
            max_button=max_button,
            resizable=not fullscreen and not self.is_maximized(),
            fullscreen=fullscreen,
        )
        # Windows stops sending ordinary mouse messages over an HTMAXBUTTON
        # region, so the DOM never sees hover there and has to be told.
        self._set_max_hover(result == HTMAXBUTTON)
        return result

    def _set_max_hover(self, hovering):
        if hovering == self._max_hover:
            return
        self._max_hover = hovering
        self._notify_max_hover(hovering)

    def _set_maximized(self, maximized):
        if maximized == self._maximized:
            return
        self._maximized = maximized
        self._notify("setMaximized(%s)" % ("true" if maximized else "false"))

    def _on_min_max_info(self, hwnd, lparam):
        """Clamp the maximized rectangle to the monitor's work area. Without
        this a frameless window maximizes over the taskbar."""
        rects = _monitor_rects(hwnd)
        if rects is None:
            return
        work, screen = rects
        position, size = max_rect(work, screen, fullscreen=self._fullscreen)
        target = ctypes.cast(lparam, ctypes.POINTER(_MINMAXINFO)).contents
        target.ptMaxPosition.x, target.ptMaxPosition.y = position
        target.ptMaxSize.x, target.ptMaxSize.y = size


def attach(window, fullscreen=False):
    """Give `window` themed chrome once its native form exists.

    Returns the WindowChrome so callers can feed it metrics and accent changes,
    or None on platforms without a native frame to take over. `fullscreen` is
    the remembered state from the last session, applied before the first paint.
    """
    if sys.platform != "win32":
        return None
    chrome = WindowChrome(window, fullscreen=fullscreen)
    # before_show fires after the form is constructed but before it is shown, so
    # the subclass is in place ahead of the first paint.
    window.events.before_show += lambda _window=None: chrome.attach()
    return chrome

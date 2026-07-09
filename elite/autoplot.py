"""Plots a route in-game by driving the galaxy map with emulated keystrokes
(the same approach EDCopilot/EDAutopilot use): focus the Elite window, open the
galaxy map, type the system into the search box, then plot to the result.

Uses the player's own keybinds (bindings.py) and confirms the map actually
opened via Status.json GuiFocus before typing anything."""

import ctypes
import json
import os
import sys
import threading
import time

from .bindings import BindingsError, ed_key_to_pydirect, keyboard_key_name, load_keyboard_binds
from .errors import UserFacingError
from .journal import find_journal_dir

ED_WINDOW_TITLE = "Elite - Dangerous (CLIENT)"
GUI_FOCUS_NONE = 0
GUI_FOCUS_GALAXY_MAP = 6

NEEDED_ACTIONS = ["GalaxyMapOpen", "UI_Up", "UI_Down", "UI_Right", "UI_Select", "UI_Back"]

# Timing (seconds) - tweak here if the sequence outruns the game on your PC.
MAP_LOAD_DELAY = 3.0        # galaxy map opening animation
SEARCH_READY_DELAY = 1.0    # search box entering edit mode after selecting it
TYPE_TO_ENTER_DELAY = 1.3   # autocomplete populating; Enter too early does nothing
AFTER_SEARCH_DELAY = 4.0    # camera flying to the searched system
SEARCH_ROUNDS = 2           # full search redo if the first pass never navigated
STEP_DELAY = 0.4            # small pause between UI keypresses
PLOT_HOLD = 2.0             # holding UI_Select on a system = "plot route"
MAP_OPEN_TIMEOUT = 12.0
CLEAR_BACKSPACES = 40       # wipe leftover text in the search box before typing
PLOT_CONFIRM_WAIT = 3.0     # time for NavRoute.json to appear after the hold

# Characters that need shift on a US layout (rare in system names).
SHIFTED = {"+": "=", "_": "-", ":": ";", '"': "'", "?": "/", "!": "1", "*": "8", "(": "9", ")": "0"}

IS_WINDOWS = sys.platform == "win32"

_plot_lock = threading.Lock()
_cancel_event = threading.Event()
user32 = ctypes.windll.user32 if IS_WINDOWS else None

# Raw SendInput scancodes for keys pydirectinput cannot send (numpad).
NUMPAD_SCANCODES = {
    "Key_Numpad_Add": 0x4E,
    "Key_Numpad_Subtract": 0x4A,
    "Key_Numpad_Multiply": 0x37,
    "Key_Numpad_0": 0x52, "Key_Numpad_1": 0x4F, "Key_Numpad_2": 0x50,
    "Key_Numpad_3": 0x51, "Key_Numpad_4": 0x4B, "Key_Numpad_5": 0x4C,
    "Key_Numpad_6": 0x4D, "Key_Numpad_7": 0x47, "Key_Numpad_8": 0x48,
    "Key_Numpad_9": 0x49,
}

_KEYEVENTF_SCANCODE = 0x0008
_KEYEVENTF_KEYUP = 0x0002


class _KI(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ki", _KI),
                ("padding", ctypes.c_ubyte * 8)]


def _scancode_tap(scancode, hold=0.08):
    down = _INPUT(type=1, ki=_KI(0, scancode, _KEYEVENTF_SCANCODE, 0, None))
    up = _INPUT(type=1, ki=_KI(0, scancode, _KEYEVENTF_SCANCODE | _KEYEVENTF_KEYUP, 0, None))
    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(_INPUT))
    time.sleep(hold)
    user32.SendInput(1, ctypes.byref(up), ctypes.sizeof(_INPUT))


class AutoplotError(UserFacingError):
    pass


class AutoplotCancelled(AutoplotError):
    """Raised inside the plot sequence when the user asks to cancel a plot in
    progress. Subclasses AutoplotError so existing handlers still catch it, but
    the server distinguishes it to report a friendly 'cancelled' instead of an
    error."""

    def __init__(self, msg="Plot cancelled."):
        super().__init__(msg)


def _check_cancel():
    if _cancel_event.is_set():
        raise AutoplotCancelled()


def _sleep(seconds):
    """Like time.sleep, but aborts promptly (raising AutoplotCancelled) if a
    cancel was requested. Used for every wait in the plot sequence so a cancel
    takes effect within ~50ms instead of after the current multi-second delay."""
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        if _cancel_event.is_set():
            raise AutoplotCancelled()
        time.sleep(min(0.05, remaining))


def cancel_plot():
    """Ask an in-progress plot to abort at its next checkpoint. Returns True if a
    plot was actually running (so the caller can tell the user)."""
    if _plot_lock.locked():
        _cancel_event.set()
        return True
    return False


def _pydirectinput():
    import pydirectinput

    pydirectinput.PAUSE = 0.05
    pydirectinput.FAILSAFE = False  # moving the mouse to a corner must not abort mid-sequence
    return pydirectinput


def gui_focus():
    try:
        text = (find_journal_dir() / "Status.json").read_text(encoding="utf-8")
        return json.loads(text).get("GuiFocus")
    except (OSError, ValueError):
        return None


def _navroute_mtime():
    try:
        return (find_journal_dir() / "NavRoute.json").stat().st_mtime
    except OSError:
        return 0


def _route_plotted_since(baseline, timeout=PLOT_CONFIRM_WAIT):
    """The game rewrites NavRoute.json the moment a route is plotted."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _navroute_mtime() > baseline:
            return True
        _sleep(0.3)
    return False


def find_ed_window():
    return user32.FindWindowW(None, ED_WINDOW_TITLE)


def _focus_ed_window(hwnd):
    VK_MENU, KEYUP = 0x12, 0x0002
    for _ in range(2):
        # Tapping ALT first lets a background process call SetForegroundWindow.
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYUP, 0)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.4)
        if user32.GetForegroundWindow() == hwnd:
            return
    raise AutoplotError("Could not bring the Elite Dangerous window to the foreground.")


def _press(pdi, key, mods=(), hold=0.0):
    for m in mods:
        pdi.keyDown(m)
    try:
        if hold:
            # One clean continuous hold. Do NOT re-assert the keydown in a loop:
            # repeated keydown events read as auto-repeat and reset Elite's
            # hold-to-plot timer, so the circle never starts filling.
            pdi.keyDown(key, _pause=False)
            try:
                _sleep(hold)  # interruptible: a cancel here still releases the key
            finally:
                pdi.keyUp(key, _pause=False)
        else:
            pdi.press(key)
    finally:
        # Always release modifiers, even if a cancel interrupted the hold, so we
        # never leave a key stuck down in the game.
        for m in reversed(mods):
            pdi.keyUp(m)


def _type_text(pdi, text):
    for ch in text.lower():
        _check_cancel()
        if ch == " ":
            pdi.press("space")
        elif ch in SHIFTED:
            _press(pdi, SHIFTED[ch], mods=("shiftleft",))
        elif ch in pdi.KEYBOARD_MAPPING:
            pdi.press(ch)
        # anything unmappable is skipped; galaxy map search is fuzzy enough


def _wait_for_map(timeout=MAP_OPEN_TIMEOUT):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if gui_focus() == GUI_FOCUS_GALAXY_MAP:
            return True
        _sleep(0.4)
    return False


def plot_route(system, dry_run=False, close_map=True):
    """Returns a list of step descriptions. Raises AutoplotError with a
    user-facing message when preconditions fail."""
    if not IS_WINDOWS:
        raise AutoplotError(
            "Autoplot only works on Windows for now - it injects keystrokes into "
            "the game client. Everything else in the app works on Linux."
        )
    if not system or not system.strip():
        raise AutoplotError("No system name given.")
    system = system.strip()

    try:
        binds = load_keyboard_binds(NEEDED_ACTIONS)
    except BindingsError as exc:
        raise AutoplotError(str(exc)) from exc

    hwnd = find_ed_window()
    if not hwnd:
        raise AutoplotError("Elite Dangerous window not found - is the game running?")

    steps = [
        f"focus '{ED_WINDOW_TITLE}'",
        f"open galaxy map ({_desc(binds['GalaxyMapOpen'])})",
        f"focus search box ({_desc(binds['UI_Up'])} then {_desc(binds['UI_Select'])})",
        f"clear search box ({CLEAR_BACKSPACES}x backspace)",
        f"type '{system}', wait {TYPE_TO_ENTER_DELAY}s for autocomplete, enter",
        f"refocus panel + move to result ({_desc(binds['UI_Down'])}, {_desc(binds['UI_Up'])}, {_desc(binds['UI_Right'])})",
        f"TAP {_desc(binds['UI_Select'])} to commit selection, zoom in (CamZoomIn)",
        f"hold {_desc(binds['UI_Select'])} {PLOT_HOLD}s to plot (verified via NavRoute.json; "
        f"up to {SEARCH_ROUNDS} rounds with fallback variants)",
    ]
    if close_map:
        steps.append("close galaxy map")
    if dry_run:
        return steps

    if not _plot_lock.acquire(blocking=False):
        raise AutoplotError("A plot is already in progress - wait for it to finish.")
    _cancel_event.clear()  # fresh run; discard any stale cancel request
    try:
        pdi = _pydirectinput()
        _focus_ed_window(hwnd)

        already_open = gui_focus() == GUI_FOCUS_GALAXY_MAP
        if not already_open:
            _press(pdi, binds["GalaxyMapOpen"]["key"], binds["GalaxyMapOpen"]["mods"])
            if not _wait_for_map():
                raise AutoplotError(
                    "Galaxy map did not open (GuiFocus never changed). "
                    "Are you in a menu, or is the game not accepting keyboard input?"
                )
            _sleep(MAP_LOAD_DELAY)

        # NavRoute.json updating is the only reliable proof the plot happened,
        # so search + plot runs in verified rounds: if a round never navigates
        # (e.g. Enter pressed before the autocomplete was ready) or the holds
        # don't take, redo the search from scratch.
        baseline = _navroute_mtime()
        plot_keys = _plot_hold_keys(binds)
        plotted = False
        for rnd in range(SEARCH_ROUNDS):
            _check_cancel()
            if gui_focus() != GUI_FOCUS_GALAXY_MAP:
                raise AutoplotError("The galaxy map closed unexpectedly mid-sequence.")
            if rnd > 0:
                # Whatever state the failed round left: exit any edit mode,
                # then walk back up to the search row.
                _press(pdi, binds["UI_Back"]["key"], binds["UI_Back"]["mods"])
                _sleep(STEP_DELAY)
            _press(pdi, binds["UI_Up"]["key"], binds["UI_Up"]["mods"])  # focus the search field
            _sleep(STEP_DELAY)
            # Explicitly enter edit mode; typing too early swallows characters.
            _press(pdi, binds["UI_Select"]["key"], binds["UI_Select"]["mods"])
            _sleep(SEARCH_READY_DELAY)
            pdi.press("backspace", presses=CLEAR_BACKSPACES, interval=0.02)  # clear leftovers
            _sleep(STEP_DELAY)
            _type_text(pdi, system)
            _sleep(TYPE_TO_ENTER_DELAY)  # let the autocomplete populate
            pdi.press("enter")
            _sleep(AFTER_SEARCH_DELAY)  # camera flies to the system

            # EDAPGui's field-proven post-search sequence: refocus the panel
            # (UI_Down/UI_Up), UI_Right onto the result, TAP select once to
            # actually commit the system selection, zoom toward it, THEN hold
            # select to plot. The commit tap is what this setup was missing -
            # the visible "hold to plot" prompt is not enough by itself.
            # Every hold is verified against NavRoute.json, so trying several
            # variants is safe.
            def _attempt(pre, tap_select, zoom):
                _check_cancel()
                for action in pre:
                    _press(pdi, binds[action]["key"], binds[action]["mods"])
                    _sleep(STEP_DELAY)
                if tap_select:
                    _press(pdi, binds["UI_Select"]["key"], binds["UI_Select"]["mods"])
                    _sleep(0.6)
                if zoom:
                    _cam_zoom_in(pdi)
                    _sleep(0.5)
                for pk in plot_keys:
                    _press(pdi, pk["key"], pk["mods"], hold=PLOT_HOLD)
                    if _route_plotted_since(baseline):
                        return True
                return False

            for pre, tap, zoom in (
                (("UI_Down", "UI_Up", "UI_Right"), True, True),  # full EDAP sequence
                ((), True, False),                               # commit tap + hold in place
                ((), False, False),                              # bare hold
            ):
                if _attempt(pre, tap, zoom):
                    plotted = True
                    break
            if plotted:
                break

        if not plotted:
            raise AutoplotError(
                f"Searched and targeted '{system}' but the plot-route hold never registered "
                "(no NavRoute update). The map was left open so you can plot manually. "
                "Note: plotting to the system you are already in always fails."
            )
        if close_map:
            _press(pdi, binds["GalaxyMapOpen"]["key"], binds["GalaxyMapOpen"]["mods"])
        return steps
    finally:
        _plot_lock.release()


def _cam_zoom_in(pdi):
    """Tap the map zoom-in key if one is keyboard-bound (numpad keys go via
    raw scancodes, which pydirectinput cannot send)."""
    name = keyboard_key_name("CamZoomIn")
    if not name:
        return
    pd_key = ed_key_to_pydirect(name)
    if pd_key and pd_key in pdi.KEYBOARD_MAPPING:
        pdi.press(pd_key)
    elif name in NUMPAD_SCANCODES:
        _scancode_tap(NUMPAD_SCANCODES[name], hold=0.3)


def _plot_hold_keys(binds):
    """Keys to try holding on the PLOT ROUTE button, each verified via
    NavRoute.json. Override with ED_PLOT_KEY (comma-separated pydirectinput
    names) if your galaxy map uses a different control."""
    override = os.environ.get("ED_PLOT_KEY")
    if override:
        return [{"key": k.strip(), "mods": []} for k in override.split(",") if k.strip()]
    # UI_Select (the real plot binding) first, Enter as a fallback.
    candidates = [binds["UI_Select"], {"key": "enter", "mods": []}]
    seen, out = set(), []
    for k in candidates:
        if k["key"] not in seen:
            seen.add(k["key"])
            out.append(k)
    return out


def _desc(bind):
    return "+".join(bind["mods"] + [bind["key"]])

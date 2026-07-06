"""Plots a route in-game by driving the galaxy map with emulated keystrokes
(the same approach EDCopilot/EDAutopilot use): focus the Elite window, open the
galaxy map, type the system into the search box, then plot to the result.

Uses the player's own keybinds (bindings.py) and confirms the map actually
opened via Status.json GuiFocus before typing anything."""

import ctypes
import json
import threading
import time

from .bindings import BindingsError, load_keyboard_binds
from .journal import find_journal_dir

ED_WINDOW_TITLE = "Elite - Dangerous (CLIENT)"
GUI_FOCUS_NONE = 0
GUI_FOCUS_GALAXY_MAP = 6

NEEDED_ACTIONS = ["GalaxyMapOpen", "UI_Up", "UI_Right", "UI_Select", "UI_Back"]

# Timing (seconds) - tweak here if the sequence outruns the game on your PC.
MAP_LOAD_DELAY = 3.0        # galaxy map opening animation
SEARCH_READY_DELAY = 1.0    # search box entering edit mode after selecting it
TYPE_TO_ENTER_DELAY = 1.3   # autocomplete populating; Enter too early does nothing
AFTER_SEARCH_DELAY = 4.0    # camera flying to the searched system
SEARCH_ROUNDS = 2           # full search redo if the first pass never navigated
STEP_DELAY = 0.4            # small pause between UI keypresses
PLOT_HOLD = 1.5             # holding UI_Select on a system = "plot route"
MAP_OPEN_TIMEOUT = 12.0
CLEAR_BACKSPACES = 40       # wipe leftover text in the search box before typing
PLOT_CONFIRM_WAIT = 3.0     # time for NavRoute.json to appear after the hold

# Characters that need shift on a US layout (rare in system names).
SHIFTED = {"+": "=", "_": "-", ":": ";", '"': "'", "?": "/", "!": "1", "*": "8", "(": "9", ")": "0"}

_plot_lock = threading.Lock()
user32 = ctypes.windll.user32


class AutoplotError(Exception):
    pass


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
        time.sleep(0.3)
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
    if hold:
        # Elite Dangerous polls input DEVICE STATE, so a single synthetic
        # keydown held only by a sleep lapses and registers as a tap - the
        # hold-to-plot bar never fills. Re-assert the keydown continuously
        # until release so the game sees the key genuinely held down.
        end = time.monotonic() + hold
        pdi.keyDown(key)
        while time.monotonic() < end:
            pdi.keyDown(key)
            time.sleep(0.02)
        pdi.keyUp(key)
    else:
        pdi.press(key)
    for m in reversed(mods):
        pdi.keyUp(m)


def _type_text(pdi, text):
    for ch in text.lower():
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
        time.sleep(0.4)
    return False


def plot_route(system, dry_run=False, close_map=True):
    """Returns a list of step descriptions. Raises AutoplotError with a
    user-facing message when preconditions fail."""
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
        f"move to plot control ({_desc(binds['UI_Right'])})",
        f"hold {_desc(binds['UI_Select'])} {PLOT_HOLD}s to plot (verified via NavRoute.json; "
        f"up to {SEARCH_ROUNDS} full search rounds with fallback attempts)",
    ]
    if close_map:
        steps.append("close galaxy map")
    if dry_run:
        return steps

    if not _plot_lock.acquire(blocking=False):
        raise AutoplotError("A plot is already in progress - wait for it to finish.")
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
            time.sleep(MAP_LOAD_DELAY)

        # NavRoute.json updating is the only reliable proof the plot happened,
        # so search + plot runs in verified rounds: if a round never navigates
        # (e.g. Enter pressed before the autocomplete was ready) or the holds
        # don't take, redo the search from scratch.
        baseline = _navroute_mtime()
        plotted = False
        for rnd in range(SEARCH_ROUNDS):
            if gui_focus() != GUI_FOCUS_GALAXY_MAP:
                raise AutoplotError("The galaxy map closed unexpectedly mid-sequence.")
            if rnd > 0:
                # Whatever state the failed round left: exit any edit mode,
                # then walk back up to the search row.
                _press(pdi, binds["UI_Back"]["key"], binds["UI_Back"]["mods"])
                time.sleep(STEP_DELAY)
            _press(pdi, binds["UI_Up"]["key"], binds["UI_Up"]["mods"])  # focus the search field
            time.sleep(STEP_DELAY)
            # Explicitly enter edit mode; typing too early swallows characters.
            _press(pdi, binds["UI_Select"]["key"], binds["UI_Select"]["mods"])
            time.sleep(SEARCH_READY_DELAY)
            pdi.press("backspace", presses=CLEAR_BACKSPACES, interval=0.02)  # clear leftovers
            time.sleep(STEP_DELAY)
            _type_text(pdi, system)
            time.sleep(TYPE_TO_ENTER_DELAY)  # let the autocomplete populate
            pdi.press("enter")
            time.sleep(AFTER_SEARCH_DELAY)  # camera flies to the system

            # After search the system is selected with the "hold to plot"
            # prompt already focused, so the first attempt is a bare hold with
            # no navigation to move focus off it; later attempts nudge focus
            # in case the prompt wasn't where expected.
            for attempt_keys in ((), ("UI_Right",), ("UI_Back", "UI_Right")):
                for action in attempt_keys:
                    _press(pdi, binds[action]["key"], binds[action]["mods"])
                    time.sleep(STEP_DELAY)
                _press(pdi, binds["UI_Select"]["key"], binds["UI_Select"]["mods"], hold=PLOT_HOLD)
                if _route_plotted_since(baseline):
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


def _desc(bind):
    return "+".join(bind["mods"] + [bind["key"]])

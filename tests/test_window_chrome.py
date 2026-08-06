"""Frameless window chrome answers hit-tests the way Windows expects.

The geometry is pure, so the whole non-client contract is testable without ever
opening a window — which matters, because getting it wrong means a window that
cannot be resized or dragged and there is no unit-level signal otherwise.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite.windowchrome import (
    DWMWA_COLOR_DEFAULT,
    DWMWA_COLOR_NONE,
    FULLSCREEN_ENTER,
    FULLSCREEN_LEAVE,
    FULLSCREEN_TOGGLE,
    HTBOTTOM,
    HTBOTTOMLEFT,
    HTBOTTOMRIGHT,
    HTCAPTION,
    HTCLIENT,
    HTLEFT,
    HTMAXBUTTON,
    HTRIGHT,
    HTTOP,
    HTTOPLEFT,
    HTTOPRIGHT,
    SW_RESTORE,
    SW_SHOWMAXIMIZED,
    accent_border_color,
    covers_monitor,
    fullscreen_request,
    hex_to_colorref,
    hit_test,
    max_rect,
    normalize_show_cmd,
    rect_to_bounds,
)

W, H = 1000, 800
TITLE_BAR = (0, 0, W, 40)
MAX_BUTTON = (880, 4, 920, 36)


def probe(x, y, **kw):
    kw.setdefault("drag_rects", (TITLE_BAR,))
    kw.setdefault("max_button", MAX_BUTTON)
    return hit_test(x, y, W, H, **kw)


# Every edge and corner is grabbable, so the window resizes without a frame.
assert probe(0, 0) == HTTOPLEFT
assert probe(W - 1, 0) == HTTOPRIGHT
assert probe(0, H - 1) == HTBOTTOMLEFT
assert probe(W - 1, H - 1) == HTBOTTOMRIGHT
assert probe(0, 400) == HTLEFT
assert probe(W - 1, 400) == HTRIGHT
assert probe(500, 0) == HTTOP
assert probe(500, H - 1) == HTBOTTOM

# Edges beat the drag strip, so a title bar reaching the border still resizes at
# its outermost pixels rather than starting a window drag.
assert probe(500, 2) == HTTOP
assert probe(2, 20) == HTLEFT

# The title bar drags the window natively (this is what gives us Aero Snap).
assert probe(500, 20) == HTCAPTION

# The maximize button reports itself so Windows 11 offers Snap Layouts on hover.
assert probe(900, 20) == HTMAXBUTTON
assert probe(879, 20) == HTCAPTION, "max button leaked one pixel left"
assert probe(920, 20) == HTCAPTION, "max button is inclusive on its right edge"

# Anything the title bar did not claim stays client area, so the DOM keeps its
# own mouse events — that is how the minimize and close buttons still work.
assert probe(500, 400) == HTCLIENT
assert probe(500, 39) == HTCAPTION
assert probe(500, 40) == HTCLIENT

# Maximized windows get no resize border; dragging a maximized edge would be
# meaningless and Windows draws no frame there.
assert probe(0, 400, resizable=False) == HTCLIENT, "maximized edge still resizes"
assert probe(W - 1, H - 1, resizable=False) == HTCLIENT, "maximized corner still resizes"
assert probe(500, 400, resizable=False) == HTCLIENT
# The title bar keeps working while maximized — that is how you drag back out.
assert probe(0, 0, resizable=False) == HTCAPTION
assert probe(900, 20, resizable=False) == HTMAXBUTTON

# A window with no title bar reported yet is inert rather than undraggable-and-
# unclickable: everything falls through to the client area.
assert hit_test(500, 20, W, H) == HTCLIENT
assert hit_test(0, 0, W, H) == HTTOPLEFT

# A fullscreen window has no non-client area at all: no resize border, no drag
# strip, no maximize button. Every one of the probes above collapses to client
# area, which is what keeps the top of the screen from silently dragging the
# window in the moment before the browser reports its rectangles are gone.
for x, y in ((0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1), (500, 0), (0, 400),
             (500, 20), (900, 20), (500, 400)):
    assert probe(x, y, fullscreen=True) == HTCLIENT, (x, y)
# Fullscreen wins even when the caller still believes the window is resizable.
assert probe(0, 0, fullscreen=True, resizable=True) == HTCLIENT

# Maximizing stops at the work area so a frameless window never covers the
# taskbar. Position is relative to the monitor's own origin, not the desktop's.
assert max_rect((0, 0, 1920, 1040), (0, 0, 1920, 1080)) == ((0, 0), (1920, 1040))
# A taskbar docked left moves the origin as well as shrinking the size.
assert max_rect((80, 0, 1920, 1080), (0, 0, 1920, 1080)) == ((80, 0), (1840, 1080))
# A second monitor left of the primary sits at negative coordinates; reporting
# those verbatim instead of as an offset puts a maximized window off-screen.
assert max_rect((-1920, 0, 0, 1040), (-1920, 0, 0, 1080)) == ((0, 0), (1920, 1040))
assert max_rect((-1920, 30, 0, 1080), (-1920, 0, 0, 1080)) == ((0, 30), (1920, 1050))
# Fullscreen suspends the clamp, so anything that maximizes us mid-fullscreen
# lands on the rectangle we already occupy rather than shrinking by a taskbar.
assert max_rect((0, 0, 1920, 1040), (0, 0, 1920, 1080), fullscreen=True) == (
    (0, 0),
    (1920, 1080),
)

# SetWindowPos wants an origin and a size, not a second corner.
assert rect_to_bounds((0, 0, 1920, 1080)) == (0, 0, 1920, 1080)
assert rect_to_bounds((-1920, -200, 0, 880)) == (-1920, -200, 1920, 1080)

# The self-heal predicate. Off by one on any side means something moved us.
MONITOR = (0, 0, 2560, 1440)
assert covers_monitor((0, 0, 2560, 1440), MONITOR)
assert not covers_monitor((1, 0, 2560, 1440), MONITOR)
assert not covers_monitor((0, 1, 2560, 1440), MONITOR)
assert not covers_monitor((0, 0, 2559, 1440), MONITOR)
assert not covers_monitor((0, 0, 2560, 1439), MONITOR)
# Maximized-to-work-area is exactly the case this has to catch.
assert not covers_monitor((0, 0, 2560, 1392), MONITOR)

# A 1px accent line down the edge of a fullscreen window is a visible seam, so
# the border goes away — and stays away across a theme change, because this is
# recomputed rather than latched.
assert accent_border_color("#ff7100", False) == 0x000071FF
assert accent_border_color("#ff7100", True) == DWMWA_COLOR_NONE
# Black packs to 0, which is falsy and still a real colour.
assert accent_border_color("#000000", False) == 0, "black fell through as unset"
# An unusable accent falls back to the system default rather than leaving the
# attribute alone, so leaving fullscreen always lands somewhere sane.
assert accent_border_color("junk", False) == DWMWA_COLOR_DEFAULT
assert accent_border_color(None, False) == DWMWA_COLOR_DEFAULT
assert accent_border_color("junk", True) == DWMWA_COLOR_NONE

# The wparam protocol carried by WM_APP_FULLSCREEN.
assert fullscreen_request(FULLSCREEN_TOGGLE, False) is True
assert fullscreen_request(FULLSCREEN_TOGGLE, True) is False
assert fullscreen_request(FULLSCREEN_ENTER, False) is True
assert fullscreen_request(FULLSCREEN_ENTER, True) is True
assert fullscreen_request(FULLSCREEN_LEAVE, True) is False
assert fullscreen_request(FULLSCREEN_LEAVE, False) is False

# Only maximized survives verbatim. The placement can be captured before the
# window has ever been shown, and replaying that SW_HIDE would restore the
# window into invisibility with no way left to bring it back.
assert normalize_show_cmd(SW_SHOWMAXIMIZED) == SW_SHOWMAXIMIZED
assert normalize_show_cmd(SW_RESTORE) == SW_RESTORE
assert normalize_show_cmd(0) == SW_RESTORE, "SW_HIDE would hide the window on exit"
assert normalize_show_cmd(1) == SW_RESTORE
assert normalize_show_cmd(2) == SW_RESTORE, "minimized would come back minimized"

# Win32 packs colours backwards from the web. Getting this wrong tints the
# window border blue and looks like a theming bug rather than a byte-order one.
assert hex_to_colorref("#ff7100") == 0x000071FF
assert hex_to_colorref("35a7ff") == 0x00FFA735
assert hex_to_colorref("#000000") == 0
assert hex_to_colorref("#ffffff") == 0x00FFFFFF
for junk in ("", None, "#fff", "not-a-colour", "#gggggg"):
    assert hex_to_colorref(junk) is None, junk

print("window chrome OK: resize edges, native drag, snap layouts, fullscreen, colour packing")

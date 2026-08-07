"""Frameshift - lightweight Elite Dangerous companion.

Desktop window (pywebview) + LAN-visible web app backed by one Flask server.
Run `python app.py --headless` for server-only mode (view from any browser).
"""

import argparse
import json
import logging
import os
import signal
import sys
import threading
import urllib.request
import webbrowser

from elite.journal import JournalWatcher
from elite.server import ServerThread
from elite.state import AppState

DEFAULT_PORT = int(os.environ.get("ET_PORT", "8666"))


def startup_pairing_url(path, port):
    """Use the same LAN choice as Settings/API pairing surfaces."""
    from elite.network import pairing_urls

    urls = pairing_urls(path, port)
    return urls[0] if urls else f"http://127.0.0.1:{port}{path}"


def instance_already_running(port):
    """True if a Frameshift server is already answering on this port. Prevents
    a second launch from double-binding the port (SO_REUSEADDR would otherwise
    let two servers coexist and fight over requests — the zombie-process trap)."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1.5) as r:
            if r.status != 200:
                return False
            if r.headers.get("X-Frameshift-Product") != "Frameshift":
                return False
            if not r.headers.get("X-Frameshift-Version"):
                return False
            raw = r.read(64 * 1024 + 1)
            if len(raw) > 64 * 1024:
                return False
            payload = json.loads(raw.decode("utf-8"))
            return isinstance(payload, dict) and {
                "journal_rebuild", "galaxy_mode",
            }.issubset(payload)
    except Exception:
        return False


def start_window():
    """Show the desktop window. pywebview defaults to private mode, which
    wipes localStorage every launch — that silently reset the per-device
    interface-size sliders (and any other browser-side preference) in the
    main window. Persist the profile in data\\webview next to the rest."""
    import webview

    from elite import marketdb

    storage = marketdb.DATA_DIR / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(private_mode=False, storage_path=str(storage))


class WindowApi:
    """Bridge between the page and the native window.

    Originally just "open this link in the real browser"; it now also carries the
    custom title bar, because a frameless window has no system buttons and the
    page is the only thing that knows where its own title bar ended up.
    """

    def __init__(self):
        self._window = None
        self._chrome = None

    def bind(self, window, chrome):
        """Called once the window exists. The api has to be constructed first to
        be passed as js_api, so the wiring runs in this direction."""
        self._window = window
        self._chrome = chrome

    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)

    def open_inline(self, url, title=None):
        """Open a site in a child window inside the app (Inara results 'inline').
        A real WebView2 browser, so Inara's bot protection is not an issue."""
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            return
        import webview

        # Deliberately left with its native frame: this shows someone else's
        # site, and cockpit chrome around third-party pages reads as a spoof.
        webview.create_window(str(title or "Browser"), url, width=1150, height=850)

    # -- custom title bar --------------------------------------------------
    def window_minimize(self):
        if self._window:
            self._window.minimize()

    def window_toggle_maximize(self):
        if self._chrome:
            self._chrome.toggle_maximize()

    def window_close(self):
        if self._window:
            self._window.destroy()

    def window_is_maximized(self):
        return bool(self._chrome and self._chrome.is_maximized())

    # -- fullscreen --------------------------------------------------------
    def window_toggle_fullscreen(self):
        """F11. Borderless over the whole monitor, taskbar and title bar gone."""
        if self._chrome:
            self._chrome.request_fullscreen(None)

    def window_set_fullscreen(self, on):
        """Esc and leaving Panel mode both want "out", not "the other one"."""
        if self._chrome:
            self._chrome.request_fullscreen(bool(on))

    def window_is_fullscreen(self):
        """Read once as the page mounts, so a remembered fullscreen does not
        paint a title bar for a frame before hiding it again."""
        return bool(self._chrome and self._chrome.is_fullscreen())

    def set_chrome_metrics(self, metrics):
        """Where the title bar is right now, in physical pixels.

        Reported by the page rather than assumed here: whole-app zoom, the
        per-device size sliders and a move to a different-DPI monitor all change
        it, and a stale value leaves the drag strip somewhere the title bar
        isn't.
        """
        if not (self._chrome and isinstance(metrics, dict)):
            return
        self._chrome.set_metrics(
            drag_rects=metrics.get("drag") or (),
            max_button=metrics.get("maxButton"),
        )

    def set_accent(self, accent):
        """Follow the per-device Color theme into the parts CSS cannot reach."""
        if self._chrome:
            self._chrome.set_accent(accent)


def open_main_window(local_url):
    """Open the desktop window and run the GUI loop until it closes.

    Frameless, so the title bar can be drawn in the cockpit theme like the rest
    of the app; elite.windowchrome hands back what a frame was giving us for
    free — edge resizing, Aero Snap, Snap Layouts, and a maximize that respects
    the taskbar.

    easy_drag has to stay off. On the Edge Chromium backend pywebview implements
    it as a mousedown listener across the entire window, so it would drag the app
    on every click anywhere. The chrome module answers HTCAPTION over the title
    bar instead, which gets the real native drag, snapping included.
    """
    import webview

    from elite import settings, windowchrome

    api = WindowApi()
    window = webview.create_window(
        "Frameshift",
        local_url,
        js_api=api,
        width=1060,
        height=800,
        min_size=(760, 560),
        frameless=True,
        easy_drag=False,
    )
    # Read before the window is shown, so a remembered fullscreen opens that way
    # instead of appearing at 1060x800 and jumping to the screen a moment later.
    chrome = windowchrome.attach(window, fullscreen=settings.get("window_fullscreen", False))
    if chrome is not None:
        chrome.on_change = _remember_fullscreen
    api.bind(window, chrome)
    start_window()


def _remember_fullscreen(on):
    """Persist the fullscreen choice for the next launch.

    Called on the chrome's notification thread rather than the GUI thread: this
    writes a file, and the window procedure is no place to touch the disk.
    """
    from elite import settings

    try:
        settings.update({"window_fullscreen": bool(on)})
    except Exception:
        logging.getLogger(__name__).debug("could not remember fullscreen", exc_info=True)


def _shutdown_runtime(
    server=None, watcher=None, listener=None, extensions=None, uploader=None,
):
    """Release background resources in dependency order.

    The HTTP listener closes first. The journal producer then stops before its
    bounded EDDN upload queue is drained; the inbound EDDN database listener
    and optional extension workers follow. Logging closes last, after every
    component had a chance to report a fault.
    """
    logger = logging.getLogger(__name__)
    logger.info("Frameshift shutdown started")
    cleanup = (
        ("HTTP server", getattr(server, "shutdown", None), {}),
        ("journal watcher", getattr(watcher, "stop", None), {"timeout": 5}),
        ("EDDN uploader", getattr(uploader, "shutdown", None), {
            "wait": True, "timeout": 5, "drain": True,
        }),
        ("EDDN listener", getattr(listener, "stop", None), {"timeout": 5}),
        ("extension host", getattr(extensions, "shutdown", None), {"wait": True}),
    )
    try:
        for name, action, kwargs in cleanup:
            if action is None:
                continue
            try:
                result = action(**kwargs)
                if name in {"journal watcher", "EDDN uploader", "EDDN listener"} \
                        and result is False:
                    logger.warning("%s did not stop within the shutdown timeout", name)
                else:
                    logger.info("Stopped %s", name)
            except Exception:
                logger.exception("Failed to stop %s", name)
    finally:
        logger.info("Frameshift shutdown complete")
        logging.shutdown()


def _wait_for_shutdown_signal(server=None, poll_seconds=0.25):
    """Wait for a console/service stop without relying on a long sleep.

    Windows terminal hosts may deliver Ctrl+C as either SIGINT or SIGBREAK,
    and a pending Python signal does not reliably unwind a long native sleep.
    Converting supported signals to an Event keeps shutdown bounded while
    preserving KeyboardInterrupt as a fallback.
    """
    requested = threading.Event()

    def request_shutdown(_signum, _frame):
        requested.set()

    handled = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        handled.append(signal.SIGBREAK)
    previous = {}
    try:
        for value in handled:
            try:
                previous[value] = signal.signal(value, request_shutdown)
            except (OSError, RuntimeError, ValueError):
                continue
        while not requested.wait(poll_seconds):
            # If the HTTP loop dies (including a console host cancelling its
            # blocking accept without delivering Python a signal), the process
            # has no useful foreground service left. Treat that as a shutdown
            # request instead of becoming a portless DB/log-owning zombie.
            if server is not None and not server.running():
                logging.getLogger(__name__).warning(
                    "HTTP server stopped unexpectedly; shutting down runtime"
                )
                break
    finally:
        for value, handler in previous.items():
            try:
                signal.signal(value, handler)
            except (OSError, RuntimeError, ValueError):
                pass


def main():
    parser = argparse.ArgumentParser(description="Elite Dangerous companion app")
    parser.add_argument("--headless", action="store_true", help="run the web server only (no window)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    # The packaged desktop build has no console.  Start bounded local logging
    # before any background component so failures can be diagnosed in-app.
    from elite.diagnostics import configure as configure_diagnostics

    configure_diagnostics()

    # Declarative local extension packs are discovered automatically. Missing
    # or invalid packs remain visible in diagnostics and never block startup.
    from elite.extensions import EXTENSIONS

    EXTENSIONS.reload()

    from elite.updater import UPDATER

    local_url = f"http://127.0.0.1:{args.port}"

    # Single-instance guard: if we're already running, just show the window that
    # points at the existing server instead of starting a second, conflicting one.
    if instance_already_running(args.port):
        print(f"Frameshift is already running on {local_url} — opening a window to it.")
        if not args.headless:
            open_main_window(local_url)
        return

    server = None
    watcher = None
    listener = None
    uploader = None
    try:
        state = AppState()
        watcher = JournalWatcher(state)
        watcher.start()

        from elite import galaxywatch
        from elite.eddn import LISTENER
        from elite.eddn_upload import UPLOADER as EDDN_UPLOADER

        uploader = EDDN_UPLOADER

        # The EDDN feed carries every commander's travel, so a watched system
        # name can surface there long before it reaches this commander's own
        # journal. Wire the tripwire before the listener starts consuming.
        galaxywatch.subscribe(lambda hit: state.push_alert(
            "critical",
            "galaxy-watch",
            f"Galaxy watch. {hit['system']} has been reached.",
            f"GALAXY WATCH — {hit['system']} just appeared on the live EDDN feed"
            f" ({hit['event']}).",
        ))

        listener = LISTENER
        listener.start()  # keeps the local market DB fresh; no-ops until seeded

        server = ServerThread(state, port=args.port)
        server.start()
        # Keep the previous executable until the replacement has reached a live
        # server. If startup fails before here, its rollback copy remains intact.
        UPDATER.cleanup_leftovers()

        pairing_path = server.pairing_path()
        pairing_url = startup_pairing_url(pairing_path, args.port)
        print(f"Frameshift running:")
        print(f"  this machine:  {local_url}")
        print(f"  pair a device: {pairing_url}")
        print("                   (one-time link; the tablet stays paired afterwards)")

        if args.headless:
            _wait_for_shutdown_signal(server)
        else:
            open_main_window(local_url)
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_runtime(server, watcher, listener, EXTENSIONS, uploader)


if __name__ == "__main__":
    main()

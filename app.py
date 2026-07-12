"""Frameshift - lightweight Elite Dangerous companion.

Desktop window (pywebview) + LAN-visible web app backed by one Flask server.
Run `python app.py --headless` for server-only mode (view from any browser).
"""

import argparse
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
            return r.status == 200
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
    """Bridge so links clicked inside the pywebview window open in the real browser."""

    def open_url(self, url):
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)

    def open_inline(self, url, title=None):
        """Open a site in a child window inside the app (Inara results 'inline').
        A real WebView2 browser, so Inara's bot protection is not an issue."""
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            return
        import webview

        webview.create_window(str(title or "Browser"), url, width=1150, height=850)


def _shutdown_runtime(server=None, watcher=None, listener=None, extensions=None):
    """Release background resources in dependency order.

    The HTTP listener closes first so no new work can arrive while the EDDN
    database connection and optional extension workers are being drained.
    Logging closes last, after every component had a chance to report a fault.
    """
    logger = logging.getLogger(__name__)
    logger.info("Frameshift shutdown started")
    cleanup = (
        ("HTTP server", getattr(server, "shutdown", None), {}),
        ("journal watcher", getattr(watcher, "stop", None), {"timeout": 5}),
        ("EDDN listener", getattr(listener, "stop", None), {"timeout": 5}),
        ("extension host", getattr(extensions, "shutdown", None), {"wait": True}),
    )
    try:
        for name, action, kwargs in cleanup:
            if action is None:
                continue
            try:
                result = action(**kwargs)
                if name in {"journal watcher", "EDDN listener"} and result is False:
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
            import webview

            webview.create_window("Frameshift", local_url, js_api=WindowApi(),
                                  width=1060, height=800, min_size=(760, 560))
            start_window()
        return

    server = None
    watcher = None
    listener = None
    try:
        state = AppState()
        watcher = JournalWatcher(state)
        watcher.start()

        from elite.eddn import LISTENER

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
            import webview

            webview.create_window(
                "Frameshift",
                local_url,
                js_api=WindowApi(),
                width=1060,
                height=800,
                min_size=(760, 560),
            )
            start_window()
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_runtime(server, watcher, listener, EXTENSIONS)


if __name__ == "__main__":
    main()

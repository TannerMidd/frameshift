"""Elite Trader - lightweight Elite Dangerous companion.

Desktop window (pywebview) + LAN-visible web app backed by one Flask server.
Run `python app.py --headless` for server-only mode (view from any browser).
"""

import argparse
import os
import socket
import time
import webbrowser

from elite.journal import JournalWatcher
from elite.server import ServerThread
from elite.state import AppState

DEFAULT_PORT = int(os.environ.get("ET_PORT", "8666"))


def lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # no packets sent; just picks the LAN interface
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


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


def main():
    parser = argparse.ArgumentParser(description="Elite Dangerous companion app")
    parser.add_argument("--headless", action="store_true", help="run the web server only (no window)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    from elite.updater import UPDATER

    UPDATER.cleanup_leftovers()  # remove any staging files from a prior update

    state = AppState()
    JournalWatcher(state).start()

    from elite.eddn import LISTENER

    LISTENER.start()  # keeps the local market DB fresh; no-ops until it's seeded

    server = ServerThread(state, port=args.port)
    server.start()

    local_url = f"http://127.0.0.1:{args.port}"
    print(f"Elite Trader running:")
    print(f"  this machine:  {local_url}")
    print(f"  on your LAN:   http://{lan_ip()}:{args.port}")

    if args.headless:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    else:
        import webview

        webview.create_window(
            "Elite Trader",
            local_url,
            js_api=WindowApi(),
            width=1060,
            height=800,
            min_size=(760, 560),
        )
        webview.start()

    server.shutdown()


if __name__ == "__main__":
    main()

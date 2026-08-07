"""Serve the real Frameshift Flask/UI boundary for Playwright smoke tests."""

import json
import os
import sys
import threading
from pathlib import Path

from werkzeug.serving import make_server


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


def main():
    data_dir = os.environ.get("ET_DATA_DIR")
    if not data_dir:
        raise RuntimeError("ET_DATA_DIR must point at the test's temporary data directory")

    # Import only after ET_DATA_DIR is established: storage paths are resolved
    # when these modules load and must never touch a developer's real profile.
    from elite import marketdb
    from elite._version import VERSION
    from elite.server import create_app
    from elite.state import AppState
    from elite.updater import UPDATER

    commander = "Boundary Pilot"
    commander_id = marketdb.ensure_commander_profile(commander, galaxy_mode="live")
    state = AppState()
    state.update(
        commander=commander,
        commander_id=commander_id,
        ship_type="Sidewinder",
        ship_name="Flask Boundary",
        system="Sol",
        star_pos=[0.0, 0.0, 0.0],
        credits=123_456,
        cargo_capacity=4,
        cargo_tons=0,
        game_running=False,
        galaxy_mode="live",
    )

    # The real UI polls this endpoint during bootstrap. Keep the Flask route
    # and serialization intact while replacing only its external GitHub call.
    UPDATER.check = lambda force=False: {
        "current": VERSION,
        "latest": VERSION,
        "available": False,
        "supported": False,
    }

    server = make_server("127.0.0.1", 0, create_app(state), threaded=True)

    def stop_on_stdin():
        for line in sys.stdin:
            if line.strip() == "stop":
                break
        # BaseServer.shutdown must run on a different thread from
        # serve_forever, which is why stdin is watched here.
        server.shutdown()

    control = threading.Thread(target=stop_on_stdin, name="e2e-control", daemon=True)
    control.start()
    host, port = server.server_address[:2]
    print(
        "FRAMESHIFT_E2E_READY "
        + json.dumps({"baseURL": f"http://{host}:{port}/", "dataDir": data_dir}),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.05)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

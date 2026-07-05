"""Flask server: serves the UI and the JSON API (bound to the LAN)."""

import logging
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.serving import make_server

from . import links, marketdb, routes, spansh
from .eddn import LISTENER
from .seed import SEEDER

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def create_app(state):
    app = Flask(__name__, static_folder=str(UI_DIR), static_url_path="")

    @app.get("/")
    def index():
        return send_from_directory(str(UI_DIR), "index.html")

    @app.get("/api/state")
    def api_state():
        snap = state.snapshot()
        snap["links"] = links.build_links(snap.get("system"), snap.get("station"))
        resp = jsonify(snap)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/trade-route")
    def api_trade_route():
        snap = state.snapshot()
        body = request.get_json(silent=True) or {}

        def num(key, default, cast=float):
            try:
                return cast(body.get(key, default))
            except (TypeError, ValueError):
                return default

        params = {
            "system": body.get("system") or snap.get("system"),
            "station": body.get("station") or (snap.get("station") if snap.get("docked") else None),
            "capital": num("capital", snap.get("credits") or 100000, int),
            "max_cargo": num("max_cargo", snap.get("cargo_capacity") or 8, int),
            "max_hop_distance": num("max_hop_distance", snap.get("max_jump_range") or 25.0),
            "max_hops": num("max_hops", 4, int),
            "max_system_distance": num("max_system_distance", 1000, int),
            "max_price_age_days": num("max_price_age_days", 30, int),
            "requires_large_pad": bool(body.get("requires_large_pad", False)),
        }

        source = body.get("source") or "auto"
        if source == "auto":
            conn = marketdb.connect()
            try:
                source = "local" if marketdb.status(conn)["ready"] else "spansh"
            finally:
                conn.close()

        if source == "local":
            try:
                hops = routes.plan_route_local(star_pos=snap.get("star_pos"), **params)
            except routes.RouteError as exc:
                return jsonify({"error": str(exc), "source": "local"}), 502
        else:
            try:
                hops = spansh.plan_route(
                    allow_planetary=bool(body.get("allow_planetary", True)),
                    unique=bool(body.get("unique", False)),
                    **params,
                )
            except spansh.SpanshError as exc:
                return jsonify({"error": str(exc), "source": "spansh"}), 502
        return jsonify({"hops": hops, "source": source})

    @app.get("/api/commodities")
    def api_commodities():
        return jsonify({"commodities": routes.list_commodities()})

    @app.get("/api/commodity-search")
    def api_commodity_search():
        snap = state.snapshot()
        args = request.args

        def num(key, default, cast=float):
            try:
                return cast(args.get(key, default))
            except (TypeError, ValueError):
                return default

        try:
            result = routes.search_commodity(
                query=args.get("q", ""),
                mode=args.get("mode", "sell"),
                system=args.get("system") or snap.get("system"),
                star_pos=snap.get("star_pos"),
                radius=num("radius", 50.0),
                min_units=num("min_units", 1, int),
                max_price_age_days=num("max_price_age_days", 30, int),
                requires_large_pad=args.get("large_pad") == "1",
            )
        except routes.RouteError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.get("/api/marketdb/status")
    def api_marketdb_status():
        conn = marketdb.connect()
        try:
            info = marketdb.status(conn)
        finally:
            conn.close()
        info["seeding"] = SEEDER.progress()
        info["eddn"] = LISTENER.stats()
        resp = jsonify(info)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/marketdb/seed")
    def api_marketdb_seed():
        if not SEEDER.start():
            return jsonify({"error": "A database build is already running."}), 409
        return jsonify({"ok": True})

    @app.post("/api/plot")
    def api_plot():
        body = request.get_json(silent=True) or {}
        system = (body.get("system") or "").strip()
        if not system:
            return jsonify({"error": "No system name given."}), 400
        # Imported lazily so an input-emulation problem can't take the server down.
        from . import autoplot

        try:
            steps = autoplot.plot_route(
                system,
                dry_run=bool(body.get("dry_run", False)),
                close_map=bool(body.get("close_map", True)),
            )
        except autoplot.AutoplotError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"ok": True, "system": system, "steps": steps})

    return app


class ServerThread:
    def __init__(self, state, host="0.0.0.0", port=8666):
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        self._server = make_server(host, port, create_app(state), threaded=True)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="http-server", daemon=True
        )

    def start(self):
        self._thread.start()

    def shutdown(self):
        self._server.shutdown()

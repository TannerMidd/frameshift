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

        mode = body.get("mode") or "loop"
        if mode == "loop":
            if source != "local":
                return jsonify({
                    "error": "Loop routes need the local market database - build it from the Market Database panel.",
                    "source": source,
                }), 502
            try:
                loops = routes.plan_loops(
                    system=params["system"],
                    star_pos=snap.get("star_pos"),
                    capital=params["capital"],
                    max_cargo=params["max_cargo"],
                    radius=num("radius", 100.0),
                    max_price_age_days=params["max_price_age_days"],
                    max_system_distance=params["max_system_distance"],
                    requires_large_pad=params["requires_large_pad"],
                    min_supply=num("min_supply", 1, int),
                    jump_range=num("jump_range", snap.get("max_jump_range") or 20.0),
                    max_leg=num("max_leg", 0) or None,
                    top_n=max(1, min(25, num("results", 8, int))),
                )
            except routes.RouteError as exc:
                return jsonify({"error": str(exc), "source": "local"}), 502
            return jsonify({"loops": loops, "source": "local", "mode": "loop"})

        if source == "local":
            try:
                hops = routes.plan_route_local(
                    star_pos=snap.get("star_pos"),
                    min_supply=num("min_supply", 1, int),
                    **params,
                )
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
        return jsonify({"hops": hops, "source": source, "mode": "chain"})

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

    @app.post("/api/riches")
    def api_riches():
        snap = state.snapshot()
        body = request.get_json(silent=True) or {}

        def num(key, default, cast=float):
            try:
                return cast(body.get(key, default))
            except (TypeError, ValueError):
                return default

        try:
            systems = spansh.riches_route(
                from_system=body.get("from") or snap.get("system"),
                to_system=body.get("to") or None,
                jump_range=num("jump_range", snap.get("max_jump_range") or 30.0),
                radius=num("radius", 50, int),
                max_results=num("max_results", 30, int),
                max_distance=num("max_distance", 1000, int),
                min_value=num("min_value", 300000, int),
                loop=bool(body.get("loop", True)),
            )
        except spansh.SpanshError as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify({"systems": systems})

    @app.post("/api/neutron")
    def api_neutron():
        snap = state.snapshot()
        body = request.get_json(silent=True) or {}
        to_system = (body.get("to") or "").strip()
        if not to_system:
            return jsonify({"error": "No destination system given."}), 400

        def num(key, default, cast=float):
            try:
                return cast(body.get(key, default))
            except (TypeError, ValueError):
                return default

        try:
            route = spansh.neutron_route(
                from_system=body.get("from") or snap.get("system"),
                to_system=to_system,
                jump_range=num("jump_range", snap.get("max_jump_range") or 30.0),
                efficiency=num("efficiency", 60, int),
            )
        except spansh.SpanshError as exc:
            return jsonify({"error": str(exc)}), 502
        return jsonify(route)

    @app.get("/api/cargo-sell")
    def api_cargo_sell():
        snap = state.snapshot()
        args = request.args

        def num(key, default, cast=float):
            try:
                return cast(args.get(key, default))
            except (TypeError, ValueError):
                return default

        try:
            results = routes.sell_cargo(
                items=snap.get("cargo_inventory") or [],
                system=snap.get("system"),
                star_pos=snap.get("star_pos"),
                radius=num("radius", 50.0),
                max_price_age_days=num("max_price_age_days", 30, int),
                requires_large_pad=args.get("large_pad") == "1",
            )
        except routes.RouteError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"results": results})

    @app.get("/api/analytics")
    def api_analytics():
        try:
            days = max(1, min(365, int(request.args.get("days", 30))))
        except ValueError:
            days = 30
        now = marketdb.now_epoch()
        since = now - days * 86400
        conn = marketdb.connect()
        try:
            balance = conn.execute(
                "SELECT ts, balance FROM balance_log WHERE ts >= ? ORDER BY ts", (since,)
            ).fetchall()
            if len(balance) > 400:  # downsample, keep first/last
                step = len(balance) // 400 + 1
                balance = balance[::step] + [balance[-1]]
            daily = conn.execute(
                """SELECT date(ts, 'unixepoch') AS d,
                          SUM(CASE WHEN event = 'sell' THEN COALESCE(profit, 0) ELSE 0 END),
                          SUM(CASE WHEN event = 'sell' THEN count ELSE 0 END)
                   FROM trade_log WHERE ts >= ? GROUP BY d ORDER BY d""",
                (since,),
            ).fetchall()

            def profit_since(cutoff):
                row = conn.execute(
                    "SELECT SUM(COALESCE(profit, 0)), SUM(count), COUNT(*) FROM trade_log"
                    " WHERE event = 'sell' AND ts >= ?", (cutoff,)
                ).fetchone()
                return {"profit": row[0] or 0, "tons": row[1] or 0, "sales": row[2] or 0}

            top = conn.execute(
                """SELECT symbol, name, SUM(COALESCE(profit, 0)) AS p, SUM(count) AS c
                   FROM trade_log WHERE event = 'sell' AND ts >= ?
                   GROUP BY symbol ORDER BY p DESC LIMIT 8""",
                (since,),
            ).fetchall()
            day_start = now - (now % 86400)
            today = profit_since(day_start)
            week = profit_since(now - 7 * 86400)
            period = profit_since(since)
        finally:
            conn.close()
        resp = jsonify({
            "balance": [{"ts": t, "balance": b} for t, b in balance],
            "daily": [{"date": d, "profit": p or 0, "tons": t or 0} for d, p, t in daily],
            "today": today,
            "week": week,
            "period": period,
            "top": [{"symbol": s, "name": n, "profit": p or 0, "tons": c or 0} for s, n, p, c in top],
        })
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/marketdb/status")
    def api_marketdb_status():
        conn = marketdb.connect()
        try:
            info = marketdb.status(conn)
        finally:
            conn.close()
        from .eddn_upload import UPLOADER

        info["seeding"] = SEEDER.progress()
        info["eddn"] = LISTENER.stats()
        info["eddn_upload"] = UPLOADER.stats()
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

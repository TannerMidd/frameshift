"""Flask server: serves the UI and the JSON API (bound to the LAN)."""

import logging
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_file, send_from_directory
from werkzeug.serving import make_server

from . import alerts, biovalues, launcher, links, marketdb, routes, settings, spansh, tts
from .eddn import LISTENER
from .errors import UserFacingError
from .seed import SEEDER

UI_DIR = Path(__file__).resolve().parent.parent / "ui"


def error_response(exc, status, **extra):
    """JSON error response for an expected failure. Only messages explicitly
    written for the player (UserFacingError.user_message) are echoed to the
    client; anything else stays generic so internal details never leak."""
    if isinstance(exc, UserFacingError):
        message = exc.user_message
    else:
        logging.getLogger(__name__).warning("unexpected API error: %r", exc)
        message = "Unexpected server error."
    return jsonify({"error": message, **extra}), status


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
                return error_response(exc, 502, source="local")
            return jsonify({"loops": loops, "source": "local", "mode": "loop"})

        if source == "local":
            try:
                hops = routes.plan_route_local(
                    star_pos=snap.get("star_pos"),
                    min_supply=num("min_supply", 1, int),
                    **params,
                )
            except routes.RouteError as exc:
                return error_response(exc, 502, source="local")
        else:
            try:
                hops = spansh.plan_route(
                    allow_planetary=bool(body.get("allow_planetary", True)),
                    unique=bool(body.get("unique", False)),
                    **params,
                )
            except spansh.SpanshError as exc:
                return error_response(exc, 502, source="spansh")
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
            return error_response(exc, 400)
        return jsonify(result)

    @app.get("/api/mining")
    def api_mining():
        snap = state.snapshot()
        args = request.args

        def num(key, default, cast=float):
            try:
                return cast(args.get(key, default))
            except (TypeError, ValueError):
                return default

        try:
            result = routes.mining_advisor(
                system=args.get("system") or snap.get("system"),
                star_pos=snap.get("star_pos"),
                radius=num("radius", 50.0),
                min_price=num("min_price", 0, int),
                max_price_age_days=num("max_price_age_days", 30, int),
                requires_large_pad=args.get("large_pad") == "1",
            )
        except routes.RouteError as exc:
            return error_response(exc, 400)
        return jsonify(result)

    @app.get("/api/mining/hotspots")
    def api_mining_hotspots():
        snap = state.snapshot()
        mineral = (request.args.get("mineral") or "").strip()
        if not mineral:
            return jsonify({"error": "No mineral given."}), 400
        ref = request.args.get("system") or snap.get("system")
        try:
            hotspots = spansh.mining_hotspots(ref, mineral, size=15)
        except spansh.SpanshError as exc:
            return error_response(exc, 502)
        return jsonify({"mineral": mineral, "reference": ref, "hotspots": hotspots})

    @app.get("/api/exobio-route")
    def api_exobio_route():
        snap = state.snapshot()
        args = request.args

        def num(key, default, cast=float):
            try:
                return cast(args.get(key, default))
            except (TypeError, ValueError):
                return default

        ref = args.get("system") or snap.get("system")
        genera = [g.strip() for g in (args.get("genera") or "").split(",") if g.strip()]
        try:
            systems, relaxed = spansh.exobio_bodies(
                ref,
                max_gravity=num("max_gravity", 0.5),
                min_value=num("min_value", 1_000_000, int),
                genera=genera,
            )
        except spansh.SpanshError as exc:
            return error_response(exc, 502)
        total = sum(s["value"] for s in systems)
        return jsonify({"reference": ref, "systems": systems, "genera": genera,
                        "total_value": total, "relaxed": relaxed})

    @app.get("/api/system-stations")
    def api_system_stations():
        """Stations of a system: Spansh station facts (services, economy,
        pads) merged with local-DB market freshness."""
        snap = state.snapshot()
        name = (request.args.get("system") or snap.get("system") or "").strip()
        if not name:
            return jsonify({"error": "No system known yet."}), 400
        conn = marketdb.connect()
        try:
            row = marketdb.find_system(conn, name)
            local = marketdb.system_station_markets(conn, name)
        finally:
            conn.close()
        id64 = row[0] if row else (
            state.system_address if name == snap.get("system") else None
        )
        stations = spansh.system_stations(id64)
        for s in stations:
            s["local_market"] = s.get("market_id") in local
            if s.get("market_id") in local:
                s["market_updated"] = local[s["market_id"]]
        if not stations and not local:
            return jsonify({"system": name, "stations": [],
                            "note": "No stations known for this system."})
        return jsonify({"system": name, "stations": stations})

    @app.get("/api/station-market")
    def api_station_market():
        market = marketdb.station_market(request.args.get("market_id", type=int))
        if not market:
            return jsonify({"error": "No local market data for this station yet."}), 404
        return jsonify(market)

    @app.get("/api/engineering")
    def api_engineering():
        """Blueprint catalog + deficit plans for the pinned ones."""
        from elite import blueprints

        snap = state.snapshot()
        inventory = {}
        for cat in ("raw", "manufactured", "encoded"):
            for m in (snap.get("materials") or {}).get(cat) or []:
                inventory[m.get("symbol")] = m.get("count", 0)
        pinned = settings.get("pinned_blueprints", [])
        plans = []
        for p in pinned:
            try:
                plans.append(blueprints.plan(p["name"], p.get("grade", 5), inventory))
            except KeyError:
                continue  # blueprint removed from the catalog; skip quietly
        return jsonify({
            "blueprints": {name: sorted(g) for name, g in
                           ((n, bp.keys()) for n, bp in blueprints.BLUEPRINTS.items())},
            "info": blueprints.BLUEPRINT_INFO,
            "rolls_per_grade": blueprints.ROLLS_PER_GRADE,
            "pinned": plans,
        })

    @app.post("/api/engineering/pin")
    def api_engineering_pin():
        from elite import blueprints

        body = request.get_json(silent=True) or {}
        name, grade = body.get("name"), int(body.get("grade") or 5)
        if body.get("action") == "unpin":
            pinned = [p for p in settings.get("pinned_blueprints", []) if p["name"] != name]
        else:
            if name not in blueprints.BLUEPRINTS:
                return jsonify({"error": f"Unknown blueprint: {name}"}), 400
            pinned = [p for p in settings.get("pinned_blueprints", []) if p["name"] != name]
            pinned.append({"name": name, "grade": grade})
        settings.update({"pinned_blueprints": pinned})
        return jsonify({"ok": True, "pinned": pinned})

    @app.get("/api/material-traders")
    def api_material_traders():
        snap = state.snapshot()
        ref = request.args.get("system") or snap.get("system")
        kind = request.args.get("kind", "raw")
        try:
            traders = spansh.material_traders(ref, kind, coords=snap.get("star_pos"))
        except spansh.SpanshError as exc:
            return error_response(exc, 502)
        return jsonify({"kind": kind.title(), "reference": ref, "traders": traders})

    @app.get("/api/sell-data")
    def api_sell_data():
        """Nearest ports to sell exploration data (Universal Cartographics) and
        bio samples (Vista Genomics) — the deep-space 'get me home' search."""
        snap = state.snapshot()
        ref = request.args.get("system") or snap.get("system")
        include_carriers = request.args.get("carriers") == "1"
        out = {}
        for key, service in (("carto", "Universal Cartographics"), ("bio", "Vista Genomics")):
            try:
                rows = spansh.service_stations(ref, service, size=24, coords=snap.get("star_pos"))
            except spansh.SpanshError as exc:
                return error_response(exc, 502)
            if not include_carriers:
                rows = [r for r in rows if not r["carrier"]]
            out[key] = rows[:5]
        return jsonify({"reference": ref, **out})

    @app.post("/api/launch-game")
    def api_launch_game():
        """Start Elite Dangerous via its store launcher (Steam / Frontier).
        The target is fixed in launcher.py — nothing here executes input."""
        if launcher.is_running():
            state.update(game_running=True)
            return jsonify({"ok": True, "already_running": True})
        try:
            via = launcher.launch()
        except launcher.LaunchError as exc:
            return error_response(exc, 502)
        except OSError as exc:  # protocol handler missing / launcher refused
            return error_response(exc, 502)
        return jsonify({"ok": True, "via": via})

    @app.get("/api/tts/status")
    def api_tts_status():
        return jsonify(tts.status())

    @app.post("/api/tts/download")
    def api_tts_download():
        try:
            tts.start_download()
        except tts.TTSError as exc:
            return error_response(exc, 400)
        return jsonify(tts.status())

    @app.get("/api/speak")
    def api_speak():
        """Synthesize a callout with the local neural voice (cached WAVs)."""
        try:
            wav = tts.synthesize(request.args.get("text", ""))
        except tts.TTSError as exc:
            return error_response(exc, 409)
        return send_file(wav, mimetype="audio/wav", max_age=86400)

    @app.get("/api/price-history")
    def api_price_history():
        """Recorded price points for one tracked market (docked-at or watched)."""
        mid = request.args.get("market_id", type=int)
        return jsonify({"market_id": mid, "history": marketdb.price_history(mid)})

    @app.get("/api/exobio-genera")
    def api_exobio_genera():
        """Genus names the exobiology route can be filtered by (for the UI)."""
        return jsonify({"genera": sorted(biovalues.GENUS_VALUE_RANGE)})

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
            return error_response(exc, 502)
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
            return error_response(exc, 502)
        return jsonify(route)

    @app.get("/api/station-search")
    def api_station_search():
        snap = state.snapshot()
        q = (request.args.get("q") or "").strip()
        kind = request.args.get("type", "module")
        if not q:
            return jsonify({"error": "Nothing to search for."}), 400
        try:
            results = spansh.station_search(
                reference_system=request.args.get("system") or snap.get("system"),
                module=q if kind == "module" else None,
                ship=q if kind == "ship" else None,
                coords=snap.get("star_pos"),
            )
        except spansh.SpanshError as exc:
            return error_response(exc, 502)
        return jsonify({"results": results})

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
            return error_response(exc, 400)
        return jsonify({"results": results})

    @app.get("/api/colonisation-sources")
    def api_colonisation_sources():
        snap = state.snapshot()
        try:
            market_id = int(request.args.get("market_id", 0))
        except ValueError:
            market_id = 0
        depot = next((d for d in snap.get("colonisation") or [] if d["market_id"] == market_id), None)
        if not depot:
            return jsonify({"error": "Unknown construction depot."}), 404
        needed = [r for r in depot["resources"] if r["remaining"] > 0][:10]
        out = []
        for res in needed:
            try:
                found = routes.search_commodity(
                    query=res["symbol"], mode="buy",
                    system=snap.get("system"), star_pos=snap.get("star_pos"),
                    radius=float(request.args.get("radius", 50)),
                    min_units=1, limit=2,
                )
                sources = found["results"]
            except routes.RouteError:
                sources = []
            out.append({**res, "sources": sources})
        return jsonify({"commodities": out})

    @app.get("/api/alerts")
    def api_alerts():
        resp = jsonify(alerts.snapshot())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/watch")
    def api_watch():
        body = request.get_json(silent=True) or {}
        try:
            watch = alerts.add_loop_watch(body.get("loop") or {})
        except (UserFacingError, ValueError) as exc:
            return error_response(exc, 400)
        return jsonify({"ok": True, "watch": {"id": watch["id"], "label": watch["label"]}})

    @app.post("/api/watch/remove")
    def api_watch_remove():
        body = request.get_json(silent=True) or {}
        return jsonify({"ok": alerts.remove_watch(body.get("id", 0))})

    @app.post("/api/alerts/clear")
    def api_alerts_clear():
        alerts.clear_alerts()
        return jsonify({"ok": True})

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

            def earnings_since(cutoff):
                """Unified income breakdown: trade profit plus every non-trade
                source, keyed by category."""
                out = {c: 0 for c in ("trade",) + marketdb.INCOME_CATEGORIES}
                out["trade"] = conn.execute(
                    "SELECT COALESCE(SUM(profit), 0) FROM trade_log"
                    " WHERE event = 'sell' AND ts >= ?", (cutoff,)
                ).fetchone()[0] or 0
                for cat, amt in conn.execute(
                    "SELECT category, SUM(amount) FROM income_log WHERE ts >= ? GROUP BY category",
                    (cutoff,),
                ).fetchall():
                    out[cat] = (out.get(cat) or 0) + (amt or 0)
                out["total"] = sum(out.values())
                return out

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
            period_earnings = earnings_since(since)

            # Live session: earnings since the current game launch.
            sess = state.snapshot().get("session") or {}
            session = dict(sess)
            if sess.get("start_ts"):
                sp = profit_since(sess["start_ts"])
                session["trade_profit"] = sp["profit"]
                session["tons_sold"] = sp["tons"]
                session["earnings"] = earnings_since(sess["start_ts"])
        finally:
            conn.close()
        resp = jsonify({
            "balance": [{"ts": t, "balance": b} for t, b in balance],
            "daily": [{"date": d, "profit": p or 0, "tons": t or 0} for d, p, t in daily],
            "today": today,
            "week": week,
            "period": period,
            "earnings": period_earnings,
            "session": session,
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

    @app.get("/api/update/check")
    def api_update_check():
        from .updater import UPDATER

        force = request.args.get("force") == "1"
        info = {k: v for k, v in UPDATER.check(force=force).items() if not k.startswith("_")}
        resp = jsonify(info)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/update/apply")
    def api_update_apply():
        from .updater import UPDATER

        ok, err = UPDATER.start_update()
        if not ok:
            return jsonify({"error": err}), 400
        return jsonify({"ok": True})

    @app.get("/api/update/status")
    def api_update_status():
        from .updater import UPDATER

        resp = jsonify(UPDATER.progress())
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/api/settings")
    def api_settings_get():
        import sys

        from . import journal
        from ._version import VERSION

        info = {
            "version": VERSION,
            "journal_dir": str(journal.find_journal_dir()),
            "data_dir": str(marketdb.DATA_DIR),
            "auto_update_supported": bool(getattr(sys, "frozen", False)) and sys.platform == "win32",
        }
        resp = jsonify({"settings": settings.all_settings(), "info": info})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/settings")
    def api_settings_set():
        body = request.get_json(silent=True) or {}
        return jsonify({"settings": settings.update(body)})

    @app.get("/api/journal-dir/validate")
    def api_journal_dir_validate():
        """Live validation for the journal-folder setting. Empty path = show
        what auto-detection (env var included) would resolve to.

        The API is reachable from the whole LAN, so a typed path is only
        probed when it lies inside a plausible journal location (user profile,
        Saved Games, the auto-detected folder) — anything else is reported as
        unchecked rather than touched, closing off arbitrary-path probing.
        SAVE never depends on this check."""
        import os.path
        from pathlib import Path

        from . import journal

        raw = (request.args.get("path") or "").strip()
        auto = not raw
        if auto:
            path = journal.find_journal_dir()
            exists = path.is_dir()
            files = len(journal.journal_files(path)) if exists else 0
            resp = jsonify({"path": str(path), "auto": True, "exists": exists, "files": files})
            resp.headers["Cache-Control"] = "no-store"
            return resp

        # Windows filesystems are case-insensitive; fold both sides there so
        # the containment check can't be dodged by casing.
        norm = os.path.normpath(os.path.abspath(raw))
        display = norm
        roots = tuple(os.path.normpath(str(r)) + os.sep for r in journal.probe_roots())
        if os.name == "nt":
            norm = norm.lower()
            roots = tuple(r.lower() for r in roots)
        if norm.startswith(roots):
            path = Path(norm)
            exists = path.is_dir()
            files = len(journal.journal_files(path)) if exists else 0
            unchecked = False
        else:
            exists, files, unchecked = None, 0, True
        resp = jsonify({"path": display, "auto": False, "exists": exists,
                        "files": files, "unchecked": unchecked})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/plot")
    def api_plot():
        body = request.get_json(silent=True) or {}
        system = (body.get("system") or "").strip()
        if not system:
            return jsonify({"error": "No system name given."}), 400
        # Autoplot types into the live game window; if we positively know the
        # game is down, say so instead of hunting for a window that isn't there.
        # (None = not probed yet — let autoplot try and report what it finds.)
        if state.game_running is False:
            return jsonify({
                "error": "The game is offline — press ▲ LAUNCH ELITE DANGEROUS first, then plot."
            }), 409
        # Imported lazily so an input-emulation problem can't take the server down.
        from . import autoplot

        try:
            steps = autoplot.plot_route(
                system,
                dry_run=bool(body.get("dry_run", False)),
                close_map=bool(body.get("close_map", True)),
            )
        except autoplot.AutoplotCancelled:
            return jsonify({"cancelled": True, "system": system}), 200
        except autoplot.AutoplotError as exc:
            return error_response(exc, 409)
        return jsonify({"ok": True, "system": system, "steps": steps})

    @app.post("/api/plot/cancel")
    def api_plot_cancel():
        # Imported lazily so an input-emulation problem can't take the server down.
        from . import autoplot

        running = autoplot.cancel_plot()
        return jsonify({"ok": True, "cancelling": bool(running)})

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

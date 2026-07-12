"""Flask server: serves the UI and the JSON API (bound to the LAN)."""

import ipaddress
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request, send_file, send_from_directory
from werkzeug.serving import make_server

from . import alerts, biovalues, launcher, links, marketdb, routes, settings, shipexport, spansh, tts
from .eddn import LISTENER
from .errors import UserFacingError
from .network import pairing_urls as build_pairing_urls
from .security import (ALL_SCOPES, COOKIE_NAME, RateLimiter, SecurityManager,
                       SecurityStoreError, is_loopback, normalize_scopes)
from .seed import SEEDER

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
DEFAULT_BODY_LIMIT = 64 * 1024
OPERATIONS_IMPORT_LIMIT = 20 * 1024 * 1024
_LIVE_GALAXY_ENDPOINTS = {
    "/api/trade-route", "/api/commodity-search", "/api/mining",
    "/api/mining/hotspots", "/api/exobio-route", "/api/system-stations",
    "/api/station-market", "/api/material-traders", "/api/sell-data",
    "/api/interstellar-factors", "/api/price-history", "/api/riches", "/api/neutron",
    "/api/station-search", "/api/cargo-sell", "/api/cargo-recovery",
    "/api/colonisation-sources", "/api/watch",
}

_PUBLIC_API = {"/api/security/status", "/api/security/pair"}
_READ_ONLY_POSTS = {
    "/api/trade-route", "/api/riches", "/api/neutron",
    "/api/objectives/plan", "/api/cargo-recovery",
}
_ADMIN_ENDPOINTS = {
    "/api/marketdb/seed", "/api/update/apply", "/api/settings",
    "/api/tts/download", "/api/tts/voice", "/api/security/pairing-code",
    "/api/security/devices", "/api/journal-dir/validate",
    "/api/diagnostics/health", "/api/diagnostics/bundle",
    "/api/extensions", "/api/extensions/reload",
}
_CONTROL_ENDPOINTS = {
    "/api/engineering/pin", "/api/launch-game", "/api/speak",
    "/api/watch", "/api/watch/remove", "/api/alerts/clear",
    "/api/plot", "/api/plot/cancel",
}
_COMMANDER_SCOPED_PREFIXES = (
    "/api/engineering",
    "/api/objectives",
    "/api/timings",
    "/api/history",
    "/api/operations",
    "/api/specialists",
    "/api/alerts",
    "/api/watch",
    "/api/analytics",
)


def _host_allowed(host):
    """Is this a Host header a browser on our own machine or LAN would send?

    Random websites can fire cross-site requests at http://localhost:8666 (the
    browser happily delivers simple POSTs without preflight) and DNS-rebinding
    pages can point their own hostname at us. Requiring the Host to be either
    a literal IP (loopback or private-range, i.e. this machine or the home
    LAN) or this machine's own hostname blocks both, while every legitimate
    access path — desktop window, localhost, tablet via 192.168.x.x — still
    works. Public IPs are rejected: this server must never be port-forwarded."""
    if not host:
        return False
    host = (urlsplit(f"//{host}").hostname or "").lower()
    own = _own_hostname()
    # Accept the bare machine name and its mDNS form (tablet browsers often
    # reach the PC as "desktop-pc.local").
    if host in ("localhost", own, own + ".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local


def _own_hostname():
    import socket

    return socket.gethostname().lower()


def _pairing_urls(path):
    parsed_host = urlsplit(f"//{request.host}")
    port = parsed_host.port or (443 if request.scheme == "https" else 80)
    return build_pairing_urls(
        path, port, scheme=request.scheme, preferred_host=parsed_host.hostname or ""
    )


def _request_token():
    """Extract a bearer credential, falling back to the HttpOnly browser cookie."""
    authorization = request.headers.get("Authorization", "")
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip(), True
        return "", True  # malformed Authorization must not fall back to a cookie
    return request.cookies.get(COOKIE_NAME, ""), False


def _required_scope():
    """Default-deny policy for API routes, including routes added in future."""
    path = request.path
    if not path.startswith("/api/") or path in _PUBLIC_API:
        return None
    if path == "/api/security/session":
        return "read"  # every device may revoke its own credential
    if (path.startswith("/api/security/devices/")
            or path.startswith("/api/extensions/") or path in _ADMIN_ENDPOINTS):
        return "admin"
    if path in _CONTROL_ENDPOINTS:
        return "control"
    if request.method not in ("GET", "HEAD", "OPTIONS") and path not in _READ_ONLY_POSTS:
        return "control"
    return "read"


def _is_side_effect():
    # Keep legacy GET attempts at /api/speak under the strict browser checks
    # even though the route is POST-only now.
    return request.method not in ("GET", "HEAD", "OPTIONS") or request.path == "/api/speak"


def _origin_matches_host(origin):
    try:
        parsed = urlsplit(origin)
    except ValueError:
        return False
    return (parsed.scheme in ("http", "https") and parsed.netloc
            and parsed.netloc.lower() == request.host.lower()
            and not parsed.username and not parsed.password)


def _journal_path(raw):
    """Return a safe normalized journal path or (None, player-facing reason).

    Elite journals normally live below the user profile (including Steam
    Proton) or the Windows Saved Games known folder.  An explicit
    ED_JOURNAL_DIR is trusted as an additional root.  Resolving existing
    symlinks/junctions before containment prevents a seemingly safe profile
    path from escaping to an arbitrary filesystem location.
    """
    from . import journal

    text = str(raw or "").strip()
    if not text:
        return "", None
    if "\x00" in text or len(text) > 4096:
        return None, "That journal folder path is not valid."
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None, "Choose an absolute journal folder path."
    try:
        candidate = candidate.resolve(strict=False)
    except (OSError, RuntimeError):
        return None, "That journal folder path could not be resolved safely."

    roots = [(Path.home(), False)]
    known = journal._windows_saved_games()
    if known:
        roots.append((known, True))
    override = os.environ.get("ED_JOURNAL_DIR")
    if override:
        roots.append((Path(override).expanduser(), True))
    allowed = False
    for root, allow_exact in roots:
        try:
            resolved_root = root.resolve(strict=False)
            if ((allow_exact and candidate == resolved_root)
                    or (candidate != resolved_root and candidate.is_relative_to(resolved_root))):
                allowed = True
                break
        except (OSError, RuntimeError, ValueError):
            continue
    if not allowed:
        return None, "Journal folders must be inside your user profile or Saved Games folder."
    if candidate.exists() and not candidate.is_dir():
        return None, "The journal path points to a file, not a folder."
    return str(candidate), None


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


def create_app(state, security_manager=None):
    app = Flask(__name__, static_folder=str(UI_DIR), static_url_path="")
    # Operations boards can be exchanged as a bounded local JSON document.
    # Every other API retains a much tighter per-request limit below.
    app.config["MAX_CONTENT_LENGTH"] = OPERATIONS_IMPORT_LIMIT
    security = security_manager or SecurityManager(marketdb.DATA_DIR)
    limiter = RateLimiter()
    app.extensions["frameshift_security"] = security

    def commander_id():
        """Use only the identity currently established in the live snapshot.

        The process-wide active DB profile deliberately lags during a journal
        handoff. Falling back to it here would serve the previous commander's
        objectives and workflows beneath the new, temporarily empty cockpit.
        """
        value = str(getattr(g, "frameshift_commander_id", None) or "").strip()
        if not value:
            raise RuntimeError("commander profile is not established")
        return value

    def request_state_snapshot():
        """Return the one live-state image captured when this request began."""
        return g.frameshift_state_snapshot

    @app.before_request
    def _validate_request_source():
        request.max_content_length = (
            OPERATIONS_IMPORT_LIMIT
            if request.path == "/api/operations/import"
            else DEFAULT_BODY_LIMIT
        )
        if request.content_length is not None and request.content_length > request.max_content_length:
            return jsonify({"error": "Request body is too large."}), 413
        # DNS rebinding: an attacker page whose domain resolves to us arrives
        # with a foreign Host header.
        if not _host_allowed(request.host):
            return jsonify({"error": "Request blocked: unrecognized Host."}), 403
        local = is_loopback(request.remote_addr)
        g.frameshift_local = local
        g.frameshift_device = None
        g.frameshift_bearer = False
        # Pin one complete state image for the whole request. A journal
        # handoff can reset or replace AppState while a threaded request is
        # between its read and write phases; every commander-owned operation
        # in that request must nevertheless use one discriminator or fail.
        g.frameshift_state_snapshot = state.snapshot()
        g.frameshift_commander_id = str(
            g.frameshift_state_snapshot.get("commander_id") or "").strip() or None

        # Reject cross-origin browser requests even before authentication.  In
        # particular, Sec-Fetch-Site covers tags/forms that omit Origin.
        origin = request.headers.get("Origin")
        fetch_site = request.headers.get("Sec-Fetch-Site", "").lower()
        if origin and not _origin_matches_host(origin):
            return jsonify({"error": "Request blocked: cross-origin."}), 403
        if fetch_site == "cross-site" and _is_side_effect():
            return jsonify({"error": "Request blocked: cross-site."}), 403

        token, bearer = _request_token()
        if token:
            g.frameshift_device = security.authenticate(token, request.remote_addr)
            g.frameshift_bearer = bearer

        # Static assets and the pairing/status bootstrap are intentionally
        # reachable before enrollment. The remote page needs them in order to
        # exchange its one-time capability link automatically.
        scope = _required_scope()
        if scope is None:
            if request.path == "/api/security/pair":
                if (not local and not origin
                        and fetch_site not in ("same-origin", "same-site")):
                    return jsonify({"error": "Request blocked: missing same-origin proof."}), 403
                ok, retry = limiter.check((request.remote_addr, "pair"), 8, 300)
                if not ok:
                    response = jsonify({"error": "Too many pairing attempts. Try again shortly."})
                    response.status_code = 429
                    response.headers["Retry-After"] = str(retry)
                    return response
            return None

        if local:
            g.frameshift_scopes = list(ALL_SCOPES)
        else:
            device = g.frameshift_device
            if not device:
                ok, retry = limiter.check((request.remote_addr, "unauthorized"), 120, 60)
                if not ok:
                    response = jsonify({"error": "Too many unauthorized requests."})
                    response.status_code = 429
                    response.headers["Retry-After"] = str(retry)
                    return response
                response = jsonify({
                    "error": "This device is not paired with Frameshift.",
                    "pairing_required": True,
                })
                response.status_code = 401
                response.headers["WWW-Authenticate"] = 'Bearer realm="Frameshift"'
                return response
            g.frameshift_scopes = device["scopes"]
            if scope not in device["scopes"]:
                return jsonify({
                    "error": f"This device does not have {scope} permission.",
                    "required_scope": scope,
                }), 403

        # Cookie-authenticated side effects from another machine must include
        # the same-origin browser signal. Bearer clients and localhost scripts
        # do not rely on cookies and remain compatible without Origin.
        if (_is_side_effect() and not local and not bearer and not origin
                and fetch_site not in ("same-origin", "same-site")):
            return jsonify({"error": "Request blocked: missing same-origin proof."}), 403

        # Layered, dependency-free protection against accidental loops and LAN
        # abuse. Pairing has its own much tighter limiter above.
        key = device["id"] if not local else request.remote_addr
        ok, retry = limiter.check((key, "all-api"), 900, 60)
        if not ok:
            response = jsonify({"error": "Too many requests. Try again shortly."})
            response.status_code = 429
            response.headers["Retry-After"] = str(retry)
            return response
        limits = {
            "/api/update/apply": (3, 300),
            "/api/marketdb/seed": (3, 300),
            "/api/launch-game": (10, 60),
            "/api/plot": (30, 60),
            "/api/speak": (30, 60),
            "/api/settings": (30, 60),
            "/api/trade-route": (30, 60),
            "/api/commodity-search": (60, 60),
            "/api/mining": (60, 60),
            "/api/mining/hotspots": (30, 60),
            "/api/exobio-route": (30, 60),
            "/api/riches": (30, 60),
            "/api/neutron": (30, 60),
            "/api/colonisation-sources": (30, 60),
        }
        endpoint_limit = limits.get(request.path)
        if _is_side_effect() or endpoint_limit:
            limit, window = endpoint_limit or (120, 60)
            ok, retry = limiter.check((key, request.path), limit, window)
            if not ok:
                response = jsonify({"error": "Too many requests. Try again shortly."})
                response.status_code = 429
                response.headers["Retry-After"] = str(retry)
                return response
        if (request.path in _LIVE_GALAXY_ENDPOINTS
                and request_state_snapshot().get("galaxy_mode") == "legacy"):
            return jsonify({
                "error": (
                    "This tool uses anonymous Live-galaxy community data and is disabled while "
                    "Elite Dangerous Legacy is running. Commander history remains safely separated."
                ),
                "galaxy_mode": "legacy",
            }), 409
        if (request.path.startswith(_COMMANDER_SCOPED_PREFIXES)
                and not g.frameshift_commander_id):
            return jsonify({
                "error": "Commander profile is changing. Try again after the journal identity is established.",
                "profile_pending": True,
            }), 409
        if request.path.startswith(_COMMANDER_SCOPED_PREFIXES):
            expected_commander = str(
                request.headers.get("X-Frameshift-Commander") or ""
            ).strip()
            if _is_side_effect() and not expected_commander:
                return jsonify({
                    "error": "Commander confirmation is required. Refresh Frameshift and try again.",
                    "profile_changed": True,
                }), 409
            if expected_commander and expected_commander != g.frameshift_commander_id:
                return jsonify({
                    "error": "Commander changed before this action arrived. Nothing was modified.",
                    "profile_changed": True,
                    "commander_id": g.frameshift_commander_id,
                }), 409
        return None

    @app.after_request
    def _security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy",
                                    "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; script-src 'self'; "
            # The dashboard uses dynamic inline width/color properties for
            # gauges and charts. Scripts remain strictly external/self-only.
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; "
            "connect-src 'self'",
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/security/status")
    def api_security_status():
        local = bool(g.frameshift_local)
        device = g.frameshift_device
        payload = {
            "local": local,
            "authenticated": local or bool(device),
            "pairing_required": not local and not bool(device),
            "device": device,
            "scopes": list(ALL_SCOPES) if local else (device or {}).get("scopes", []),
        }
        # A trusted desktop/admin view gets a ready-to-share one-time link.
        if local or (device and "admin" in device["scopes"]):
            # Status polling must never invalidate a deliberately restricted
            # link created through /pairing-code by replacing it with an admin
            # grant.  Create the default only when no live grant exists.
            grant = security.current_pairing() or security.issue_pairing()
            pairing = {
                "path": "/?pair=" + grant["code"],
                "expires_at": grant["expires_at"],
                "scopes": grant["scopes"],
            }
            pairing["urls"] = _pairing_urls(pairing["path"])
            if pairing["urls"]:
                from .qrcode import svg as pairing_qr_svg

                pairing["qr_svg"] = pairing_qr_svg(pairing["urls"][0])
            payload["pairing"] = pairing
            payload["paired_devices"] = len(security.list_devices())
        return jsonify(payload)

    @app.post("/api/security/pair")
    def api_security_pair():
        body = request.get_json(silent=True) or {}
        try:
            result = security.pair(body.get("code"), body.get("device_name"),
                                   request.remote_addr)
        except SecurityStoreError as exc:
            return error_response(exc, 500)
        if not result:
            return jsonify({
                "error": "That pairing link is invalid, expired, or has already been used.",
                "pairing_required": True,
            }), 403
        token, device = result
        payload = {"ok": True, "device": device, "scopes": device["scopes"]}
        if body.get("return_token") is True:
            payload["token"] = token
        response = jsonify(payload)
        response.set_cookie(
            COOKIE_NAME, token, max_age=365 * 86400, httponly=True,
            samesite="Strict", secure=False, path="/",
        )
        return response

    @app.post("/api/security/pairing-code")
    def api_security_pairing_code():
        body = request.get_json(silent=True) or {}
        try:
            scopes = normalize_scopes(body.get("scopes") or ALL_SCOPES)
            ttl = int(body.get("ttl_seconds") or 900)
            grant = security.rotate_pairing(scopes, ttl)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        pairing = {
            "path": "/?pair=" + grant["code"],
            "expires_at": grant["expires_at"],
            "scopes": grant["scopes"],
        }
        # API clients and future pairing surfaces get the exact same ordered
        # links as the Settings copy button and QR code; callers never need to
        # repeat the formerly error-prone adapter-selection logic.
        pairing["urls"] = _pairing_urls(pairing["path"])
        if pairing["urls"]:
            from .qrcode import svg as pairing_qr_svg

            pairing["qr_svg"] = pairing_qr_svg(pairing["urls"][0])
        return jsonify(pairing)

    @app.get("/api/security/devices")
    def api_security_devices():
        return jsonify({"devices": security.list_devices()})

    @app.patch("/api/security/devices/<device_id>")
    def api_security_device_update(device_id):
        body = request.get_json(silent=True) or {}
        try:
            device = security.update_device(
                device_id,
                name=body.get("name") if "name" in body else None,
                scopes=body.get("scopes") if "scopes" in body else None,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        except SecurityStoreError as exc:
            return error_response(exc, 500)
        if not device:
            return jsonify({"error": "Paired device not found."}), 404
        return jsonify({"ok": True, "device": device})

    @app.delete("/api/security/devices/<device_id>")
    @app.post("/api/security/devices/<device_id>/revoke")
    def api_security_device_revoke(device_id):
        try:
            revoked = security.revoke(device_id)
        except SecurityStoreError as exc:
            return error_response(exc, 500)
        if not revoked:
            return jsonify({"error": "Paired device not found."}), 404
        return jsonify({"ok": True})

    @app.delete("/api/security/session")
    def api_security_session_delete():
        if g.frameshift_local:
            return jsonify({"ok": True})
        device = g.frameshift_device
        try:
            security.revoke(device["id"])
        except SecurityStoreError as exc:
            return error_response(exc, 500)
        response = jsonify({"ok": True})
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/")
    def index():
        return send_from_directory(str(UI_DIR), "index.html")

    @app.get("/api/state")
    def api_state():
        snap = request_state_snapshot()
        snap["links"] = links.build_links(snap.get("system"), snap.get("station"))
        resp = jsonify(snap)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/trade-route")
    def api_trade_route():
        snap = request_state_snapshot()
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
                source = "local" if marketdb.is_ready(conn) else "spansh"
            finally:
                conn.close()

        mode = body.get("mode") or "loop"
        if mode == "loop":
            if source != "local":
                return jsonify({
                    "error": "Loop routes need the local market database - build it from the Settings page (Market Database card).",
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
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
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
        """Complete local catalog and a shared material plan for the wishlist."""
        from elite import blueprints, engineering_catalog, wishlist

        inventory = blueprints.inventory_from_snapshot(request_state_snapshot())
        legacy, _legacy_changed = blueprints.normalize_wishlist(
            settings.get("pinned_blueprints", []))
        saved, adopted = wishlist.load(commander_id(), legacy_items=legacy)
        pinned, migrated = blueprints.normalize_wishlist(saved)
        if migrated:
            wishlist.save(commander_id(), pinned)
        if adopted:
            # The DB transaction owns the migration marker. Clearing the old
            # JSON only after commit makes a settings-write failure harmless:
            # another commander can never adopt the same legacy pins.
            settings.update({"pinned_blueprints": []})
        wishlist = blueprints.plan_wishlist(pinned, inventory)
        return jsonify({
            "commander_id": commander_id(),
            "catalog": engineering_catalog.catalog_payload(),
            "wishlist": wishlist,
            "info": blueprints.BLUEPRINT_INFO,
            "rolls_per_grade": blueprints.ROLLS_PER_GRADE,
            # Compatibility keys keep older browser assets safe during an
            # in-place executable update.
            "blueprints": {name: sorted(grade for grade in recipes if grade)
                           for name, recipes in blueprints.BLUEPRINTS.items()},
            "pinned": wishlist["items"],
        })

    @app.post("/api/engineering/pin")
    def api_engineering_pin():
        from elite import blueprints, wishlist

        body = request.get_json(silent=True) or {}
        candidate, _changed = blueprints.normalize_wishlist([body])
        saved, _adopted = wishlist.load(commander_id())
        saved, _migrated = blueprints.normalize_wishlist(saved)
        if body.get("action") == "unpin":
            remove_id = candidate[0]["id"] if candidate else (body.get("id") or body.get("name"))
            pinned = [p for p in saved if p["id"] != remove_id]
        else:
            if not candidate:
                return jsonify({"error": "Unknown engineering catalog item."}), 400
            item = candidate[0]
            pinned = [p for p in saved if p["id"] != item["id"]]
            pinned.append(item)
        wishlist.save(commander_id(), pinned)
        return jsonify({"ok": True, "commander_id": commander_id(), "pinned": pinned})

    @app.get("/api/objectives")
    def api_objectives():
        from .objectives import ObjectiveStore

        raw = request.args.get("statuses")
        statuses = tuple(part.strip() for part in raw.split(",") if part.strip()) if raw else (
            None if request.args.get("all") == "1" else ("open", "active", "blocked")
        )
        return jsonify({"objectives": ObjectiveStore(commander_id()).list(statuses=statuses)})

    @app.post("/api/objectives")
    def api_objectives_create():
        from .objectives import ObjectiveStore

        body = request.get_json(silent=True) or {}
        allowed = {
            key: body.get(key) for key in (
                "category", "priority", "system", "station", "body", "estimated_seconds",
                "deadline", "reward", "risk", "payload", "dependencies",
            ) if key in body
        }
        try:
            objective = ObjectiveStore(commander_id()).create(
                body.get("title"), source="user", source_ref=body.get("source_ref"), **allowed)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"objective": objective}), 201

    @app.patch("/api/objectives/<objective_id>")
    def api_objectives_update(objective_id):
        from .objectives import ObjectiveStore

        try:
            objective = ObjectiveStore(commander_id()).update(
                objective_id, **(request.get_json(silent=True) or {}))
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        if not objective:
            return jsonify({"error": "Objective not found."}), 404
        return jsonify({"objective": objective})

    @app.delete("/api/objectives/<objective_id>")
    def api_objectives_delete(objective_id):
        from .objectives import ObjectiveStore

        objective = ObjectiveStore(commander_id()).update(objective_id, status="dismissed")
        if not objective:
            return jsonify({"error": "Objective not found."}), 404
        return jsonify({"objective": objective})

    @app.post("/api/objectives/plan")
    def api_objectives_plan():
        from . import blueprints, wishlist
        from .objectives import ObjectiveEngine

        body = request.get_json(silent=True) or {}
        try:
            minutes = max(5, min(24 * 60, int(
                body.get("minutes") or body.get("time_budget_minutes") or 60)))
            max_tasks = max(1, min(30, int(body.get("max_tasks") or 12)))
        except (TypeError, ValueError):
            return jsonify({"error": "Time budget and task count must be numbers."}), 400
        snapshot = request_state_snapshot()
        inventory = blueprints.inventory_from_snapshot(snapshot)
        legacy, _ = blueprints.normalize_wishlist(settings.get("pinned_blueprints", []))
        stored, adopted = wishlist.load(commander_id(), legacy_items=legacy)
        pins, migrated = blueprints.normalize_wishlist(stored)
        if migrated:
            wishlist.save(commander_id(), pins)
        if adopted:
            settings.update({"pinned_blueprints": []})
        snapshot["engineering_plans"] = blueprints.plan_wishlist(pins, inventory).get("items", [])
        context = body.get("context") or {}
        if not isinstance(context, dict):
            return jsonify({"error": "Planning context must be an object."}), 400
        # These are optional results from Frameshift's own local/public search
        # tools. Arbitrary state replacement is intentionally not accepted.
        for key in (
            "cargo_rescue", "cargo_options", "colonisation_sources", "powerplay_tasks",
            "exploration_cash_in",
        ):
            if key in context:
                snapshot[key] = context[key]
        return jsonify(ObjectiveEngine(commander_id()).plan(
            minutes, snapshot, max_tasks=max_tasks))

    @app.get("/api/timings")
    def api_timings():
        from .timings import TimingModel

        return jsonify(TimingModel(commander_id()).snapshot())

    @app.get("/api/history/summary")
    def api_history_summary():
        from .eventledger import EventLedger

        return jsonify(EventLedger(commander_id()).lifetime_summary())

    @app.get("/api/history/events")
    def api_history_events():
        from .eventledger import EventLedger

        def parts(name):
            value = request.args.get(name) or ""
            return [part.strip() for part in value.split(",") if part.strip()] or None

        try:
            limit = max(1, min(500, int(request.args.get("limit") or 100)))
            since = int(request.args["since"]) if request.args.get("since") else None
            until = int(request.args["until"]) if request.args.get("until") else None
        except ValueError:
            return jsonify({"error": "History bounds must be numeric."}), 400
        events = EventLedger(commander_id()).query(
            categories=parts("categories"), event_types=parts("types"), since=since,
            until=until, system=request.args.get("system") or None, limit=limit,
            ascending=request.args.get("ascending") == "1",
        )
        return jsonify({"events": events})

    @app.get("/api/operations")
    def api_operations():
        from .operations import OperationsBoard

        ops = OperationsBoard(commander_id())
        board_id = request.args.get("board_id")
        if not board_id:
            return jsonify({"boards": ops.list_boards(), "conflicts": ops.conflicts(limit=50)})
        try:
            snapshot = ops.snapshot(board_id)
        except KeyError:
            return jsonify({"error": "Operations board not found."}), 404
        snapshot["conflicts"] = ops.conflicts(board_id, limit=100)
        return jsonify(snapshot)

    @app.post("/api/operations")
    def api_operations_create():
        from .operations import OperationsBoard

        body = request.get_json(silent=True) or {}
        action = body.get("action") or "create_board"
        ops = OperationsBoard(commander_id())
        try:
            if action == "create_board":
                result = ops.create_board(body.get("title"), body.get("description") or "")
            elif action == "add_objective":
                result = ops.add_objective(
                    body.get("board_id"), body.get("title"), description=body.get("description") or "",
                    priority=body.get("priority", 50), system=body.get("system"),
                    station=body.get("station"), deadline=body.get("deadline"), payload=body.get("payload"),
                )
            elif action == "assign":
                result = ops.assign(
                    body.get("board_id"), body.get("assignee"), objective_id=body.get("objective_id"),
                    role=body.get("role") or "", payload=body.get("payload"),
                )
            elif action == "reserve":
                result = ops.reserve(
                    body.get("board_id"), body.get("resource_type"), body.get("resource_key"),
                    body.get("amount"), objective_id=body.get("objective_id"), unit=body.get("unit") or "",
                    assignee=body.get("assignee"), payload=body.get("payload"),
                )
            elif action == "contribute":
                result = ops.contribute(
                    body.get("board_id"), body.get("contributor"), body.get("kind"), body.get("amount"),
                    objective_id=body.get("objective_id"), unit=body.get("unit") or "",
                    note=body.get("note") or "", evidence=body.get("evidence"), payload=body.get("payload"),
                )
            else:
                return jsonify({"error": "Unsupported operations action."}), 400
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"record": result}), 201

    @app.patch("/api/operations/<kind>/<record_id>")
    def api_operations_update(kind, record_id):
        from .operations import OperationsBoard

        try:
            result = OperationsBoard(commander_id()).update(
                kind, record_id, **(request.get_json(silent=True) or {}))
        except KeyError:
            return jsonify({"error": "Operations record not found."}), 404
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"record": result})

    @app.delete("/api/operations/<kind>/<record_id>")
    def api_operations_delete(kind, record_id):
        from .operations import OperationsBoard

        try:
            result = OperationsBoard(commander_id()).remove(kind, record_id)
        except KeyError:
            return jsonify({"error": "Operations record not found."}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify({"record": result})

    @app.get("/api/operations/export")
    def api_operations_export():
        from .operations import OperationsBoard

        try:
            document = OperationsBoard(commander_id()).export_json(
                request.args.get("board_id") or None)
        except KeyError:
            return jsonify({"error": "Operations board not found."}), 404
        response = app.response_class(document, mimetype="application/json")
        response.headers["Content-Disposition"] = 'attachment; filename="frameshift-operations.json"'
        return response

    @app.post("/api/operations/import")
    def api_operations_import():
        from .operations import OperationsBoard

        body = request.get_json(silent=True)
        document = body.get("document") if isinstance(body, dict) and "document" in body else body
        try:
            report = OperationsBoard(commander_id()).import_json(document)
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(report)

    @app.post("/api/cargo-recovery")
    def api_cargo_recovery():
        body = request.get_json(silent=True) or {}
        snapshot = request_state_snapshot()
        try:
            result = routes.recover_cargo(
                snapshot.get("cargo_inventory") or [], system=snapshot.get("system"),
                star_pos=snapshot.get("star_pos"), radius=float(body.get("radius") or 100),
                max_price_age_days=int(body.get("max_age_days") or 7),
                requires_large_pad=bool(body.get("large_pad")),
                failed_market_id=body.get("failed_market_id"), limit=int(body.get("limit") or 5),
            )
        except routes.RouteError as exc:
            return error_response(exc, 400)
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    def _specialist_facade():
        from .specialists import SpecialistWorkflows

        workflows = SpecialistWorkflows(commander_id())
        workflows.exobiology.update_position(request_state_snapshot().get("pos"))
        return workflows

    def _specialist_response(workflows):
        def positive_int(name, default, maximum):
            try:
                return max(1, min(maximum, int(request.args.get(name) or default)))
            except (TypeError, ValueError):
                return default

        payload = workflows.snapshot(exobiology_options={
            "survey_page": positive_int("survey_page", 1, 1_000_000),
            "survey_page_size": positive_int("survey_page_size", 50, 200),
            "pin_page": positive_int("pin_page", 1, 1_000_000),
            "pin_page_size": positive_int("pin_page_size", 200, 500),
        })
        payload["mining"]["history"] = workflows.mining.history(20)
        payload["combat"]["history"] = workflows.combat.history(20)
        payload["commander_id"] = commander_id()
        state.update_for_commander(commander_id(), specialists=payload)
        return payload

    @app.get("/api/specialists")
    def api_specialists():
        return jsonify(_specialist_response(_specialist_facade()))

    @app.post("/api/specialists/mining/<action>")
    def api_specialists_mining(action):
        workflows = _specialist_facade()
        body = request.get_json(silent=True) or {}
        if action == "start":
            workflows.mining.start(context=body.get("context"), force=bool(body.get("force")))
        elif action == "end":
            workflows.mining.end(body.get("reason") or "manual")
        else:
            return jsonify({"error": "Unsupported mining action."}), 400
        return jsonify(_specialist_response(workflows))

    @app.post("/api/specialists/combat/<action>")
    def api_specialists_combat(action):
        workflows = _specialist_facade()
        body = request.get_json(silent=True) or {}
        if action == "start":
            workflows.combat.start(force=bool(body.get("force")))
        elif action == "end":
            workflows.combat.end(body.get("reason") or "manual")
        else:
            return jsonify({"error": "Unsupported combat action."}), 400
        return jsonify(_specialist_response(workflows))

    @app.post("/api/specialists/carrier/config")
    def api_specialists_carrier_config():
        workflows = _specialist_facade()
        body = request.get_json(silent=True) or {}
        try:
            workflows.carrier.configure_upkeep(
                body.get("weekly_upkeep_cr"), body.get("target_weeks", 8),
                source="commander input",
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_specialist_response(workflows))

    @app.post("/api/specialists/carrier/route")
    def api_specialists_carrier_route():
        workflows = _specialist_facade()
        body = request.get_json(silent=True) or {}
        try:
            workflows.carrier.plan_route(
                body.get("legs") or [], tritium_per_jump_t=body.get("tritium_per_jump_t"),
                reserve_t=body.get("reserve_t"),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_specialist_response(workflows))

    @app.post("/api/specialists/carrier/inventory")
    def api_specialists_carrier_inventory():
        workflows = _specialist_facade()
        body = request.get_json(silent=True) or {}
        try:
            workflows.carrier.set_inventory(body.get("items") or {}, source="commander input")
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_specialist_response(workflows))

    @app.post("/api/specialists/exobiology/pins")
    def api_specialists_exobiology_pin():
        workflows = _specialist_facade()
        body = request.get_json(silent=True) or {}
        try:
            workflows.exobiology.add_pin(
                body.get("label") or "Waypoint", kind=body.get("kind") or "waypoint",
                position=body.get("position"), metadata=body.get("metadata"),
            )
        except (TypeError, ValueError) as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(_specialist_response(workflows)), 201

    @app.delete("/api/specialists/exobiology/pins/<pin_id>")
    def api_specialists_exobiology_pin_delete(pin_id):
        workflows = _specialist_facade()
        if not workflows.exobiology.remove_pin(pin_id):
            return jsonify({"error": "Surface pin not found."}), 404
        return jsonify(_specialist_response(workflows))

    @app.get("/api/specialists/exobiology/geojson")
    def api_specialists_exobiology_geojson():
        workflows = _specialist_facade()
        return jsonify(workflows.exobiology.geojson(request.args.get("body") or None))

    @app.get("/api/material-traders")
    def api_material_traders():
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
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

    @app.get("/api/interstellar-factors")
    def api_interstellar_factors():
        """Nearest stations with an Interstellar Factors contact — the service
        that clears bounties and fines (for a 25% cut) without flying to the
        issuing faction's space."""
        snap = request_state_snapshot()
        ref = request.args.get("system") or snap.get("system")
        try:
            rows = spansh.service_stations(
                ref, "Interstellar Factors Contact", size=12, coords=snap.get("star_pos")
            )
        except spansh.SpanshError as exc:
            return error_response(exc, 502)
        # Carriers don't offer IF; drop any oddities the search returns.
        return jsonify({"reference": ref,
                        "stations": [r for r in rows if not r["carrier"]][:6]})

    @app.get("/api/loadout-export")
    def api_loadout_export():
        """The current ship as an EDSY import link + SLEF JSON (Coriolis/Inara),
        from the last Loadout journal event."""
        loadout = state.get_loadout()
        if not loadout:
            return error_response(
                UserFacingError("No ship loadout seen yet — it arrives when you "
                                "launch the game or switch ships."), 404)
        return jsonify({
            "ship_type": loadout.get("Ship"),
            "ship_name": loadout.get("ShipName"),
            "ship_ident": loadout.get("ShipIdent"),
            "edsy_url": shipexport.edsy_url(loadout),
            "slef": shipexport.slef(loadout),
        })

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

    @app.post("/api/tts/voice")
    def api_tts_voice():
        """Switch the callout voice (kicks off its download when needed)."""
        body = request.get_json(silent=True) or {}
        try:
            tts.set_voice(body.get("voice") or "")
        except tts.TTSError as exc:
            return error_response(exc, 400)
        return jsonify(tts.status())

    @app.post("/api/speak")
    def api_speak():
        """Synthesize a callout with the local neural voice (cached WAVs)."""
        body = request.get_json(silent=True) or {}
        try:
            wav = tts.synthesize(body.get("text", ""))
        except tts.TTSError as exc:
            return error_response(exc, 409)
        # The URL doesn't encode which voice is active, so the browser must
        # never cache it — a voice switch would keep replaying the old voice.
        # (The server's per-voice WAV cache already makes repeats instant.)
        resp = send_file(wav, mimetype="audio/wav")
        resp.cache_control.no_store = True
        return resp

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
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
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
        snap = request_state_snapshot()
        try:
            market_id = int(request.args.get("market_id", 0))
        except ValueError:
            market_id = 0
        depot = next((d for d in snap.get("colonisation") or [] if d["market_id"] == market_id), None)
        if not depot:
            return jsonify({"error": "Unknown construction depot."}), 404
        needed = [r for r in depot["resources"] if r["remaining"] > 0][:10]
        radius = float(request.args.get("radius", 50))

        def lookup(res):
            try:
                found = routes.search_commodity(
                    query=res["symbol"], mode="buy",
                    system=snap.get("system"), star_pos=snap.get("star_pos"),
                    radius=radius, min_units=1, limit=2,
                )
                sources = found["results"]
            except routes.RouteError:
                sources = []
            return {**res, "sources": sources}

        # Each search opens its own SQLite connection, so run the commodities
        # in parallel — ten sequential ~3s searches felt like a dead button.
        with ThreadPoolExecutor(max_workers=len(needed) or 1) as pool:
            out = list(pool.map(lookup, needed))
        return jsonify({"commodities": out})

    @app.get("/api/alerts")
    def api_alerts():
        active_commander_id = commander_id()
        payload = alerts.snapshot(active_commander_id)
        payload["commander_id"] = active_commander_id
        resp = jsonify(payload)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/watch")
    def api_watch():
        body = request.get_json(silent=True) or {}
        try:
            watch = alerts.add_loop_watch(body.get("loop") or {}, commander_id())
        except (UserFacingError, ValueError) as exc:
            return error_response(exc, 400)
        return jsonify({"ok": True, "watch": {"id": watch["id"], "label": watch["label"]}})

    @app.post("/api/watch/remove")
    def api_watch_remove():
        body = request.get_json(silent=True) or {}
        return jsonify({"ok": alerts.remove_watch(body.get("id", 0), commander_id())})

    @app.post("/api/alerts/clear")
    def api_alerts_clear():
        alerts.clear_alerts(commander_id())
        return jsonify({"ok": True})

    @app.get("/api/analytics")
    def api_analytics():
        try:
            days = max(1, min(365, int(request.args.get("days", 30))))
        except ValueError:
            days = 30
        now = marketdb.now_epoch()
        since = now - days * 86400
        active_commander_id = commander_id()
        conn = marketdb.connect()
        try:
            balance = conn.execute(
                "SELECT ts, balance FROM balance_log WHERE commander_id = ? AND ts >= ? ORDER BY ts",
                (active_commander_id, since),
            ).fetchall()
            if len(balance) > 400:  # downsample, keep first/last
                step = len(balance) // 400 + 1
                balance = balance[::step] + [balance[-1]]
            daily = conn.execute(
                """SELECT date(ts, 'unixepoch') AS d,
                          SUM(CASE WHEN event = 'sell' THEN COALESCE(profit, 0) ELSE 0 END),
                          SUM(CASE WHEN event = 'sell' THEN count ELSE 0 END)
                   FROM trade_log WHERE commander_id = ? AND ts >= ? GROUP BY d ORDER BY d""",
                (active_commander_id, since),
            ).fetchall()

            def profit_since(cutoff):
                row = conn.execute(
                    "SELECT SUM(COALESCE(profit, 0)), SUM(count), COUNT(*) FROM trade_log"
                    " WHERE commander_id = ? AND event = 'sell' AND ts >= ?",
                    (active_commander_id, cutoff),
                ).fetchone()
                return {"profit": row[0] or 0, "tons": row[1] or 0, "sales": row[2] or 0}

            def earnings_since(cutoff):
                """Unified income breakdown: trade profit plus every non-trade
                source, keyed by category."""
                out = {c: 0 for c in ("trade",) + marketdb.INCOME_CATEGORIES}
                out["trade"] = conn.execute(
                    "SELECT COALESCE(SUM(profit), 0) FROM trade_log"
                    " WHERE commander_id = ? AND event = 'sell' AND ts >= ?",
                    (active_commander_id, cutoff),
                ).fetchone()[0] or 0
                for cat, amt in conn.execute(
                    "SELECT category, SUM(amount) FROM income_log"
                    " WHERE commander_id = ? AND ts >= ? GROUP BY category",
                    (active_commander_id, cutoff),
                ).fetchall():
                    out[cat] = (out.get(cat) or 0) + (amt or 0)
                out["total"] = sum(out.values())
                return out

            top = conn.execute(
                """SELECT symbol, name, SUM(COALESCE(profit, 0)) AS p, SUM(count) AS c
                   FROM trade_log WHERE commander_id = ? AND event = 'sell' AND ts >= ?
                   GROUP BY symbol ORDER BY p DESC LIMIT 8""",
                (active_commander_id, since),
            ).fetchall()
            day_start = now - (now % 86400)
            today = profit_since(day_start)
            week = profit_since(now - 7 * 86400)
            period = profit_since(since)
            period_earnings = earnings_since(since)

            # Live session: earnings since the current game launch.
            sess = request_state_snapshot().get("session") or {}
            session = dict(sess)
            if sess.get("start_ts"):
                sp = profit_since(sess["start_ts"])
                session["trade_profit"] = sp["profit"]
                session["tons_sold"] = sp["tons"]
                session["earnings"] = earnings_since(sess["start_ts"])
        finally:
            conn.close()
        resp = jsonify({
            "commander_id": active_commander_id,
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
            # Fresh counts while a build is running (they ARE the progress);
            # the 5-minute cache otherwise — see marketdb.status.
            info = marketdb.status(conn, max_age=10 if SEEDER.running() else 300)
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
        # Honor the carrier preference at build time too, so the toggle
        # controls what the database *contains*, not just what queries show.
        if not SEEDER.start(include_carriers=not settings.get("exclude_carriers", True)):
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

    @app.get("/api/diagnostics/health")
    def api_diagnostics_health():
        from .diagnostics import health_snapshot

        return jsonify(health_snapshot())

    @app.post("/api/diagnostics/bundle")
    def api_diagnostics_bundle():
        from .diagnostics import create_bundle

        try:
            bundle = create_bundle()
        except Exception as exc:
            return error_response(exc, 500)
        return send_file(
            bundle,
            mimetype="application/zip",
            as_attachment=True,
            download_name=bundle.name,
            max_age=0,
        )

    @app.get("/api/extensions")
    def api_extensions():
        from .extensions import EXTENSIONS

        return jsonify(EXTENSIONS.snapshot())

    @app.post("/api/extensions/reload")
    def api_extensions_reload():
        from .extensions import EXTENSIONS

        return jsonify(EXTENSIONS.reload())

    @app.post("/api/extensions/<extension_id>/approve")
    def api_extension_approve(extension_id):
        from .extensions import EXTENSIONS, ExtensionError

        try:
            return jsonify(EXTENSIONS.approve_process(extension_id))
        except ExtensionError as exc:
            return jsonify({"error": str(exc)}), 400

    @app.post("/api/extensions/<extension_id>/revoke")
    def api_extension_revoke(extension_id):
        from .extensions import EXTENSIONS, ExtensionError

        try:
            return jsonify(EXTENSIONS.revoke_process(extension_id))
        except ExtensionError as exc:
            return jsonify({"error": str(exc)}), 400

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
        if "journal_dir" in body:
            safe_path, reason = _journal_path(body.get("journal_dir"))
            if reason:
                return jsonify({"error": reason}), 400
            body["journal_dir"] = safe_path
        try:
            saved = settings.update(body)
        except settings.SettingsError as exc:
            return error_response(exc, 500)
        return jsonify({"settings": saved})

    @app.get("/api/journal-dir/validate")
    def api_journal_dir_validate():
        """Live validation for the journal-folder setting. Empty path = show
        what auto-detection (env var included) would resolve to.

        The API is reachable from the whole LAN, so a typed path is only
        probed when it lies inside a plausible journal location (user profile,
        Saved Games, the auto-detected folder) — anything else is reported as
        unchecked rather than touched, closing off arbitrary-path probing.
        SAVE never depends on this check."""
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

        safe_path, reason = _journal_path(raw)
        if reason:
            resp = jsonify({"path": raw, "auto": False, "exists": None,
                            "files": 0, "unchecked": True, "error": reason})
            resp.status_code = 400
            resp.headers["Cache-Control"] = "no-store"
            return resp
        path = Path(safe_path)
        exists = path.is_dir()
        files = len(journal.journal_files(path)) if exists else 0
        resp = jsonify({"path": safe_path, "auto": False, "exists": exists,
                        "files": files, "unchecked": False})
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
        self.security = SecurityManager(marketdb.DATA_DIR)
        self._server = make_server(
            host, port, create_app(state, security_manager=self.security), threaded=True
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="http-server", daemon=True
        )
        self._lifecycle_lock = threading.Lock()
        self._started = False
        self._closed = False

    def pairing_path(self):
        """One-time capability path for the startup LAN link / desktop QR."""
        grant = self.security.issue_pairing()
        return "/?pair=" + grant["code"]

    def start(self):
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("server is already closed")
            if self._started:
                return self._thread
            self._started = True
            self._thread.start()
            return self._thread

    def running(self):
        with self._lifecycle_lock:
            return self._started and not self._closed and self._thread.is_alive()

    def shutdown(self):
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            started = self._started
        # BaseServer.shutdown() deadlocks if serve_forever() was never started.
        try:
            if started:
                self._server.shutdown()
        finally:
            self._server.server_close()
            if started and self._thread is not threading.current_thread():
                self._thread.join(timeout=5)

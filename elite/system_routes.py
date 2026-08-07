"""System and administration route registration for the Flask application.

These endpoints share process-level services rather than commander domain
logic. Keeping their registration together leaves ``server.create_app`` in
charge of the global request/security boundary without making it the owner of
every administrative workflow.
"""

import sys
from pathlib import Path

from flask import jsonify, request, send_file

from . import marketdb, settings
from .eddn import LISTENER
from .seed import SEEDER


def register_system_routes(
    app,
    state,
    *,
    commander_id,
    commander_scoped_when,
    error_response,
    journal_path,
    json_object_body,
):
    """Register process-wide database, admin, settings, and control routes."""

    def extension_history_uses_commander():
        """Only history-backed extension tests consume commander-owned data."""
        body = json_object_body()
        return not isinstance(body.get("sample_event"), dict)

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
        info = {
            key: value
            for key, value in UPDATER.check(force=force).items()
            if not key.startswith("_")
        }
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
            return error_response(exc, 400)

    @app.post("/api/extensions/<extension_id>/revoke")
    def api_extension_revoke(extension_id):
        from .extensions import EXTENSIONS, ExtensionError

        try:
            return jsonify(EXTENSIONS.revoke_process(extension_id))
        except ExtensionError as exc:
            return error_response(exc, 400)

    @app.get("/api/extensions/<extension_id>/manifest")
    def api_extension_manifest(extension_id):
        from .extensions import EXTENSIONS, ExtensionError

        try:
            return jsonify({"manifest": EXTENSIONS.read_manifest(extension_id)})
        except ExtensionError as exc:
            return error_response(exc, 404)

    @app.post("/api/extensions/save")
    def api_extension_save():
        from .extensions import EXTENSIONS, ExtensionError

        body = json_object_body()
        try:
            snapshot = EXTENSIONS.save_declarative(body.get("manifest"))
        except ExtensionError as exc:
            return error_response(exc, 400)
        return jsonify(snapshot)

    @app.delete("/api/extensions/<extension_id>")
    def api_extension_delete(extension_id):
        from .extensions import EXTENSIONS, ExtensionError

        try:
            return jsonify(EXTENSIONS.delete_pack(extension_id))
        except ExtensionError as exc:
            return error_response(exc, 400)

    @app.post("/api/extensions/test")
    @commander_scoped_when(extension_history_uses_commander)
    def api_extension_test():
        from .extensions import ExtensionError, dry_run_rules

        body = json_object_body()
        sample = body.get("sample_event")
        if isinstance(sample, dict):
            events = [{"event": sample, "timestamp": sample.get("timestamp")}]
        else:
            from .eventledger import EventLedger

            events = EventLedger(commander_id()).query(limit=1000, ascending=False)
        try:
            return jsonify(dry_run_rules(body.get("manifest"), events))
        except ExtensionError as exc:
            return error_response(exc, 400)

    @app.get("/api/settings")
    def api_settings_get():
        from . import journal
        from ._version import VERSION

        info = {
            "version": VERSION,
            "journal_dir": str(journal.find_journal_dir()),
            "data_dir": str(marketdb.DATA_DIR),
            "auto_update_supported": (
                bool(getattr(sys, "frozen", False)) and sys.platform == "win32"
            ),
        }
        resp = jsonify({"settings": settings.all_settings(), "info": info})
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/settings")
    def api_settings_set():
        body = json_object_body()
        if "journal_dir" in body:
            safe_path, reason = journal_path(body.get("journal_dir"))
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
        """Validate journal settings without enabling arbitrary-path probing."""
        from . import journal

        raw = (request.args.get("path") or "").strip()
        auto = not raw
        if auto:
            path = journal.find_journal_dir()
            exists = path.is_dir()
            files = len(journal.journal_files(path)) if exists else 0
            resp = jsonify(
                {"path": str(path), "auto": True, "exists": exists, "files": files}
            )
            resp.headers["Cache-Control"] = "no-store"
            return resp

        safe_path, reason = journal_path(raw)
        if reason:
            resp = jsonify(
                {
                    "path": raw,
                    "auto": False,
                    "exists": None,
                    "files": 0,
                    "unchecked": True,
                    "error": reason,
                }
            )
            resp.status_code = 400
            resp.headers["Cache-Control"] = "no-store"
            return resp
        path = Path(safe_path)
        exists = path.is_dir()
        files = len(journal.journal_files(path)) if exists else 0
        resp = jsonify(
            {
                "path": safe_path,
                "auto": False,
                "exists": exists,
                "files": files,
                "unchecked": False,
            }
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.post("/api/plot")
    def api_plot():
        body = json_object_body()
        system = (body.get("system") or "").strip()
        if not system:
            return jsonify({"error": "No system name given."}), 400
        # Autoplot types into the live game window; if we positively know the
        # game is down, say so instead of hunting for a window that isn't there.
        # (None = not probed yet — let autoplot try and report what it finds.)
        if state.game_running is False:
            return jsonify(
                {
                    "error": (
                        "The game is offline — press ▲ LAUNCH ELITE DANGEROUS "
                        "first, then plot."
                    )
                }
            ), 409
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

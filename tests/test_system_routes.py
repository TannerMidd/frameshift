"""System-route extraction preserves the Flask URL and endpoint contract."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite.server import commander_scope_policy, create_app  # noqa: E402
from elite.state import AppState  # noqa: E402


EXPECTED = {
    ("GET", "/api/marketdb/status"): "api_marketdb_status",
    ("POST", "/api/marketdb/seed"): "api_marketdb_seed",
    ("GET", "/api/update/check"): "api_update_check",
    ("POST", "/api/update/apply"): "api_update_apply",
    ("GET", "/api/update/status"): "api_update_status",
    ("GET", "/api/diagnostics/health"): "api_diagnostics_health",
    ("POST", "/api/diagnostics/bundle"): "api_diagnostics_bundle",
    ("GET", "/api/extensions"): "api_extensions",
    ("POST", "/api/extensions/reload"): "api_extensions_reload",
    ("POST", "/api/extensions/<extension_id>/approve"): "api_extension_approve",
    ("POST", "/api/extensions/<extension_id>/revoke"): "api_extension_revoke",
    ("GET", "/api/extensions/<extension_id>/manifest"): "api_extension_manifest",
    ("POST", "/api/extensions/save"): "api_extension_save",
    ("DELETE", "/api/extensions/<extension_id>"): "api_extension_delete",
    ("POST", "/api/extensions/test"): "api_extension_test",
    ("GET", "/api/settings"): "api_settings_get",
    ("POST", "/api/settings"): "api_settings_set",
    ("GET", "/api/journal-dir/validate"): "api_journal_dir_validate",
    ("POST", "/api/plot"): "api_plot",
    ("POST", "/api/plot/cancel"): "api_plot_cancel",
}


def test_system_route_contract_and_ownership():
    app = create_app(AppState())
    actual = {}
    for rule in app.url_map.iter_rules():
        view = app.view_functions[rule.endpoint]
        if view.__module__ != "elite.system_routes":
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            actual[(method, rule.rule)] = rule.endpoint

    assert actual == EXPECTED
    for endpoint in EXPECTED.values():
        policy = commander_scope_policy(app.view_functions[endpoint])
        assert policy == ("conditional" if endpoint == "api_extension_test" else None)


def test_update_route_response_contract():
    from elite import updater

    calls = []

    class FakeUpdater:
        def check(self, force=False):
            calls.append(("check", force))
            return {"available": True, "version": "9.9.9", "_private_asset": "hidden"}

        def start_update(self):
            calls.append(("apply",))
            return False, "Update already running."

        def progress(self):
            calls.append(("status",))
            return {"running": True, "stage": "download"}

    original = updater.UPDATER
    updater.UPDATER = FakeUpdater()
    try:
        client = create_app(AppState()).test_client()
        check = client.get("/api/update/check?force=1")
        assert check.status_code == 200
        assert check.get_json() == {"available": True, "version": "9.9.9"}
        assert check.headers["Cache-Control"] == "no-store"

        apply = client.post("/api/update/apply")
        assert apply.status_code == 400
        assert apply.get_json() == {"error": "Update already running."}

        status = client.get("/api/update/status")
        assert status.status_code == 200
        assert status.get_json() == {"running": True, "stage": "download"}
        assert status.headers["Cache-Control"] == "no-store"
        assert calls == [("check", True), ("apply",), ("status",)]
    finally:
        updater.UPDATER = original


def test_autoplot_still_uses_injected_live_state():
    state = AppState()
    state.update(game_running=False)
    response = create_app(state).test_client().post(
        "/api/plot", json={"system": "Sol"}
    )
    assert response.status_code == 409
    assert "game is offline" in response.get_json()["error"].lower()


test_system_route_contract_and_ownership()
test_update_route_response_contract()
test_autoplot_still_uses_injected_live_state()

print("system routes OK: URL, updater, autoplot and commander contracts preserved")

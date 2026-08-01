"""Commander handoff APIs fail closed and engineering plans stay per profile."""

import os
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb, settings, spansh, wishlist  # noqa: E402
from elite.server import create_app  # noqa: E402
from elite.state import AppState  # noqa: E402


alpha = marketdb.ensure_commander_profile("Alpha")
settings.update({
    "pinned_blueprints": [
        {"name": "FSD Increased Range", "grade": 4},
        {"name": "FSD Increased Range", "grade": 5},
    ],
})
state = AppState()
state.update(commander="Alpha", commander_id=alpha)
app = create_app(state)
app.testing = True
client = app.test_client()
alpha_headers = {"X-Frameshift-Commander": alpha}

first = client.get("/api/engineering", headers=alpha_headers)
assert first.status_code == 200, first.get_json()
assert first.get_json()["commander_id"] == alpha
assert [row["id"] for row in first.get_json()["wishlist"]["entries"]] == [
    "frame-shift-drive--increased-fsd-range"
]
assert first.get_json()["wishlist"]["entries"][0]["target_grade"] == 5
assert settings.get("pinned_blueprints") == [], "legacy pins were not retired after DB commit"

beta = marketdb.ensure_commander_profile("Beta")
state.update(commander="Beta", commander_id=beta)
beta_headers = {"X-Frameshift-Commander": beta}
second = client.get("/api/engineering", headers=beta_headers)
assert second.status_code == 200
assert second.get_json()["commander_id"] == beta
assert second.get_json()["wishlist"]["entries"] == [], "Alpha wishlist leaked to Beta"

stale = client.post("/api/engineering/pin", headers={
    "X-Frameshift-Commander": alpha,
}, json={"id": "suit--artemis", "current_grade": 2, "target_grade": 4})
assert stale.status_code == 409 and stale.get_json()["profile_changed"] is True
assert client.get(
    "/api/engineering", headers=beta_headers
).get_json()["wishlist"]["entries"] == []
stale_read = client.get(
    "/api/objectives", headers={"X-Frameshift-Commander": alpha})
assert stale_read.status_code == 409 and stale_read.get_json()["profile_changed"] is True
for path in (
    "/api/loadout-export", "/api/cargo-sell",
    "/api/colonisation-sources", "/api/material-traders",
    "/api/material-bodies",
):
    stale_personal_read = client.get(
        path, headers={"X-Frameshift-Commander": alpha})
    assert stale_personal_read.status_code == 409, (
        path, stale_personal_read.status_code, stale_personal_read.get_json())
    assert stale_personal_read.get_json()["profile_changed"] is True
for path in ("/api/cargo-recovery", "/api/trade-route"):
    stale_personal_post = client.post(
        path, headers={"X-Frameshift-Commander": alpha}, json={})
    assert stale_personal_post.status_code == 409, (
        path, stale_personal_post.status_code, stale_personal_post.get_json())
    assert stale_personal_post.get_json()["profile_changed"] is True
for path in (
    "/api/commodity-search?q=gold",
    "/api/mining",
    "/api/mining/hotspots?mineral=Painite",
    "/api/exobio-route",
    "/api/sell-data",
    "/api/interstellar-factors",
    "/api/station-search?q=Fuel+Scoop",
    "/api/system-stations?system=Sol",
):
    stale_discovery = client.get(
        path, headers={"X-Frameshift-Commander": alpha})
    assert stale_discovery.status_code == 409, (
        path, stale_discovery.status_code, stale_discovery.get_json())
    assert stale_discovery.get_json()["profile_changed"] is True
for path, payload in (
    ("/api/riches", {}),
    ("/api/neutron", {"to": "Sol"}),
):
    stale_planner = client.post(
        path, headers={"X-Frameshift-Commander": alpha}, json=payload)
    assert stale_planner.status_code == 409, (
        path, stale_planner.status_code, stale_planner.get_json())
    assert stale_planner.get_json()["profile_changed"] is True

# A System Stations request must use one pinned location image. Simulate a
# handoff after request capture but before the endpoint resolves its fallback
# id64; Alpha's system name must never combine with Beta's live address.
state.update(
    commander="Alpha", commander_id=alpha, system="Sol", system_address=111,
)
original_find_system = marketdb.find_system
original_system_station_markets = marketdb.system_station_markets
original_system_stations = spansh.system_stations
seen_station_addresses = []


def switch_during_system_lookup(_conn, name):
    assert name == "Sol"
    state.update(
        commander="Beta", commander_id=beta, system="Achenar", system_address=222,
    )
    return None


marketdb.find_system = switch_during_system_lookup
marketdb.system_station_markets = lambda _conn, _name: {}
spansh.system_stations = lambda id64: seen_station_addresses.append(id64) or []
try:
    pinned_stations = client.get(
        "/api/system-stations?system=Sol",
        headers={"X-Frameshift-Commander": alpha},
    )
    assert pinned_stations.status_code == 200, pinned_stations.get_json()
    assert seen_station_addresses == [111], seen_station_addresses
finally:
    marketdb.find_system = original_find_system
    marketdb.system_station_markets = original_system_station_markets
    spansh.system_stations = original_system_stations

added = client.post("/api/engineering/pin", headers={
    "X-Frameshift-Commander": beta,
}, json={
    "id": "suit--artemis", "current_grade": 2, "target_grade": 4,
})
assert added.status_code == 200 and added.get_json()["commander_id"] == beta

state.update(commander="Alpha", commander_id=alpha)
alpha_again = client.get("/api/engineering", headers=alpha_headers).get_json()
assert [row["id"] for row in alpha_again["wishlist"]["entries"]] == [
    "frame-shift-drive--increased-fsd-range"
]

# Flask pins the profile at request entry. Simulate a Fileheader handoff while
# the engineering handler is between its load and response construction.
original_load = wishlist.load


def switch_during_load(commander_id, **kwargs):
    assert commander_id == alpha
    state.update(commander="Beta", commander_id=beta)
    return original_load(commander_id, **kwargs)


wishlist.load = switch_during_load
try:
    raced = client.get("/api/engineering", headers=alpha_headers)
    assert raced.status_code == 200 and raced.get_json()["commander_id"] == alpha
finally:
    wishlist.load = original_load

# Analytics must use the same state image and commander identity captured at
# request entry, even if Fileheader hands the process to another profile while
# the database query is running.
state.update(
    commander="Alpha", commander_id=alpha, session_start_ts=1,
    session_jumps=7, session_ly=42.5,
)
alert_payload = client.get("/api/alerts", headers=alpha_headers)
assert alert_payload.status_code == 200
assert alert_payload.get_json()["commander_id"] == alpha
original_snapshot = state.snapshot


def switch_after_snapshot():
    snapshot = original_snapshot()
    state.update(commander="Beta", commander_id=beta, session_jumps=99, session_ly=999.0)
    return snapshot


state.snapshot = switch_after_snapshot
try:
    analytics = client.get("/api/analytics?days=7", headers=alpha_headers)
    assert analytics.status_code == 200, analytics.get_json()
    assert analytics.get_json()["commander_id"] == alpha
    assert analytics.get_json()["session"]["jumps"] == 7
    assert analytics.get_json()["session"]["ly"] == 42.5
finally:
    state.snapshot = original_snapshot

# During Fileheader -> Commander handoff, marketdb still calls Beta active. The
# HTTP layer must never use that process-wide fallback for commander-owned APIs.
state.reset_commander_context()
assert marketdb.active_commander_id() == beta
for path in (
    "/api/engineering", "/api/objectives", "/api/timings",
    "/api/history/summary", "/api/operations", "/api/specialists",
    "/api/alerts", "/api/analytics", "/api/loadout-export",
    "/api/cargo-sell",
    "/api/colonisation-sources",
    "/api/material-traders",
    "/api/material-bodies",
    "/api/commodity-search", "/api/mining", "/api/mining/hotspots",
    "/api/exobio-route", "/api/sell-data", "/api/interstellar-factors",
    "/api/station-search",
    "/api/system-stations",
):
    response = client.get(path)
    assert response.status_code == 409, (path, response.status_code, response.get_json())
    assert response.get_json()["profile_pending"] is True
for path in (
    "/api/cargo-recovery", "/api/trade-route", "/api/riches", "/api/neutron",
):
    response = client.post(path, json={})
    assert response.status_code == 409, (path, response.status_code, response.get_json())
    assert response.get_json()["profile_pending"] is True

print("profile API boundary OK: fail-closed handoff and commander-scoped wishlist migration")

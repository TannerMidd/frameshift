"""Secured adapters for objectives, timings, history, ops and local services."""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb  # noqa: E402
from elite.eventledger import EventLedger  # noqa: E402
from elite.server import create_app  # noqa: E402
from elite.state import AppState  # noqa: E402


commander_id = marketdb.ensure_commander_profile("API Tester")
state = AppState()
state.update(
    commander="API Tester", commander_id=commander_id, system="Sol", star_pos=[0, 0, 0],
    missions={1: {
        "id": 1, "name": "Deliver tea", "dest_system": "Achenar",
        "dest_station": "Dawes Hub", "reward": 123456,
    }},
)
EventLedger(commander_id).record({
    "timestamp": "2026-07-12T12:00:00Z", "event": "FSDJump", "StarSystem": "Sol",
})

app = create_app(state)
app.testing = True
client = app.test_client()
profile_headers = {"X-Frameshift-Commander": commander_id}


def post(path, **kwargs):
    return client.post(path, headers=profile_headers, **kwargs)


def patch(path, **kwargs):
    return client.patch(path, headers=profile_headers, **kwargs)

created = post("/api/objectives", json={
    "title": "Visit the engineer", "category": "engineering", "system": "Deciat",
})
assert created.status_code == 201, created.get_json()
objective = created.get_json()["objective"]
assert client.get("/api/objectives").get_json()["objectives"][0]["id"] == objective["id"]
assert patch(f"/api/objectives/{objective['id']}", json={"status": "active"}).status_code == 200

plan = post("/api/objectives/plan", json={"minutes": 60}).get_json()
assert plan["budget_minutes"] == 60 and plan["graph"]["nodes"], plan
assert client.get("/api/timings").get_json()["commander_id"] == commander_id

summary = client.get("/api/history/summary").get_json()
assert summary["commander_id"] == commander_id and summary["events"] == 1, summary
events = client.get("/api/history/events?types=FSDJump&limit=5").get_json()["events"]
assert len(events) == 1 and events[0]["event"]["StarSystem"] == "Sol", events

board_response = post("/api/operations", json={
    "action": "create_board", "title": "Local wing night",
})
assert board_response.status_code == 201, board_response.get_json()
board = board_response.get_json()["record"]
added = post("/api/operations", json={
    "action": "add_objective", "board_id": board["id"], "title": "Deliver supplies",
})
assert added.status_code == 201, added.get_json()
snapshot = client.get("/api/operations?board_id=" + board["id"]).get_json()
assert snapshot["board"]["id"] == board["id"] and len(snapshot["objectives"]) == 1
exported = client.get("/api/operations/export?board_id=" + board["id"])
assert exported.status_code == 200 and json.loads(exported.data)["format"] == "frameshift.operations"
assert post("/api/operations/import", json={"document": json.loads(exported.data)}).status_code == 200
large_document = json.loads(exported.data)
large_document["local_note"] = "x" * 70_000
assert post("/api/operations/import", json=large_document).status_code == 200
assert post("/api/settings", json={"journal_dir": "x" * 70_000}).status_code == 413

recovery = post("/api/cargo-recovery", json={})
assert recovery.status_code == 400 and "empty" in recovery.get_json()["error"].lower()

security = client.get("/api/security/status").get_json()
assert security["local"] and security["pairing"]["qr_svg"].startswith("<?xml")
assert security["pairing"]["urls"][0] not in security["pairing"]["qr_svg"]
assert client.get("/api/extensions").status_code == 200
assert client.get("/api/diagnostics/health").status_code == 200

specialists = client.get("/api/specialists")
assert specialists.status_code == 200 and "mining" in specialists.get_json()
started = post("/api/specialists/mining/start", json={}).get_json()
assert started["mining"]["active"] is True
assert post("/api/specialists/mining/end", json={}).get_json()["mining"]["active"] is False
configured = post("/api/specialists/carrier/config", json={
    "weekly_upkeep_cr": 10_000_000, "target_weeks": 8,
})
assert configured.status_code == 200
state.update(pos={
    "lat": 1.0, "lon": 2.0, "body": "Sol A 1", "radius_m": 1_000_000,
    "heading": 90,
})
pin = post("/api/specialists/exobiology/pins", json={"label": "Ship"})
assert pin.status_code == 201, pin.get_json()
geo = client.get("/api/specialists/exobiology/geojson").get_json()
assert geo["features"] and geo["features"][0]["properties"]["label"] == "Ship"

bundle = post("/api/diagnostics/bundle")
assert bundle.status_code == 200 and bundle.mimetype == "application/zip"
bundle.close()

print("intelligence API OK: objectives, plan, history, timings, ops, QR, diagnostics")

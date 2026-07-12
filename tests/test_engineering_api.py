"""Engineering API serves the packaged catalog and migrates v1 pins locally."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite import marketdb, settings  # noqa: E402
from elite.server import create_app  # noqa: E402
from elite.state import AppState  # noqa: E402


settings.update({"pinned_blueprints": [{"name": "FSD Increased Range", "grade": 5}]})
state = AppState()
commander_id = marketdb.ensure_commander_profile("Engineering API Test")
state.update(commander="Engineering API Test", commander_id=commander_id)
state.materials["Encoded"]["disruptedwakeechoes"] = {
    "symbol": "disruptedwakeechoes", "name": "Atypical Disrupted Wake Echoes", "count": 3,
}
state.ship_locker = {
    "items": [{"symbol": "suitschematic", "name": "Suit Schematic", "count": 20}],
    "components": [], "data": [], "consumables": [], "total": 20,
}
state.cargo_inventory = [{"symbol": "hnshockmount", "name": "HN Shock Mount", "count": 8}]

app = create_app(state)
app.testing = True
client = app.test_client()
profile_headers = {"X-Frameshift-Commander": commander_id}

response = client.get("/api/engineering")
assert response.status_code == 200, response.get_json()
body = response.get_json()
assert body["catalog"]["stats"]["groups"] == 505
assert len(body["catalog"]["groups"]) == 505
assert body["rolls_per_grade"] == {"1": 1, "2": 2, "3": 3, "4": 4, "5": 5}
assert body["wishlist"]["entries"][0]["id"] == "frame-shift-drive--increased-fsd-range"
assert body["commander_id"] == commander_id
assert settings.get("pinned_blueprints") == []

response = client.post("/api/engineering/pin", headers=profile_headers, json={
    "id": "suit--artemis", "current_grade": 2, "target_grade": 4, "quantity": 2,
})
assert response.status_code == 200, response.get_json()
assert len(response.get_json()["pinned"]) == 2
response = client.get("/api/engineering")
items = {item["id"]: item for item in response.get_json()["wishlist"]["items"]}
assert items["suit--artemis"]["current_grade"] == 2
assert items["suit--artemis"]["target_grade"] == 4
assert items["suit--artemis"]["quantity"] == 2

response = client.post(
    "/api/engineering/pin", headers=profile_headers, json={"id": "not-a-recipe"})
assert response.status_code == 400
response = client.post("/api/engineering/pin", headers=profile_headers, json={
    "id": "frame-shift-drive--increased-fsd-range", "action": "unpin",
})
assert response.status_code == 200
assert [item["id"] for item in response.get_json()["pinned"]] == ["suit--artemis"]

print("ALL COMPLETE ENGINEERING API TESTS PASSED")

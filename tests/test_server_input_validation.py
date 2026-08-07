"""API request bodies and numeric query parameters fail closed as JSON 400s."""

import os
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite.server import create_app  # noqa: E402
from elite.state import AppState  # noqa: E402


state = AppState()
state.update(
    commander="Input Tester",
    commander_id="input-tester",
    system="Sol",
    star_pos=[0, 0, 0],
    colonisation={42: {
        "market_id": 42,
        "resources": [],
    }},
)
app = create_app(state)
app.testing = True
client = app.test_client()
commander_headers = {"X-Frameshift-Commander": "input-tester"}


# Public, admin, commander-owned, large-import, and conditionally scoped
# handlers all use the same object-only JSON contract.
invalid_requests = (
    ("POST", "/api/security/pair", "pairing-code", {}),
    ("POST", "/api/security/pairing-code", ["admin"], {}),
    ("POST", "/api/settings", 7, {}),
    ("POST", "/api/objectives", 0, commander_headers),
    ("PATCH", "/api/objectives/missing", "title", commander_headers),
    ("POST", "/api/operations/import", [], commander_headers),
    ("POST", "/api/extensions/test", True, commander_headers),
)
for method, path, payload, headers in invalid_requests:
    response = client.open(path, method=method, json=payload, headers=headers)
    assert response.status_code == 400, (method, path, response.status_code)
    assert response.is_json, (method, path, response.content_type)
    assert response.get_json() == {
        "error": "Request body must be a valid JSON object."
    }, (method, path, response.get_json())

# Malformed JSON receives the same bounded client error instead of being
# silently replaced by an empty object.
malformed = client.post(
    "/api/settings",
    data="{not-json",
    content_type="application/json",
)
assert malformed.status_code == 400
assert malformed.get_json() == {
    "error": "Request body must be a valid JSON object."
}

json_null = client.post(
    "/api/objectives",
    data="null",
    content_type="application/json",
    headers=commander_headers,
)
assert json_null.status_code == 400
assert json_null.get_json() == {
    "error": "Request body must be a valid JSON object."
}

# Preserve the established behavior for endpoints where the body is optional.
empty = client.post("/api/security/pairing-code")
assert empty.status_code == 200, empty.get_json()

# Numeric query failures are JSON 400s, including values float() accepts but
# downstream search/database code cannot use meaningfully.
for radius in ("not-a-number", "nan", "inf", "-inf"):
    response = client.get(
        f"/api/colonisation-sources?market_id=42&radius={radius}",
        headers=commander_headers,
    )
    assert response.status_code == 400, (radius, response.status_code)
    assert response.is_json
    assert "radius" in response.get_json()["error"]

valid = client.get(
    "/api/colonisation-sources?market_id=42&radius=25.5",
    headers=commander_headers,
)
assert valid.status_code == 200, valid.get_json()
assert valid.get_json() == {"commodities": []}

for share in ("nan", "inf", "-inf"):
    response = client.get(
        f"/api/material-bodies?material=iron&min_share={share}",
        headers=commander_headers,
    )
    assert response.status_code == 400, (share, response.status_code)
    assert "min_share" in response.get_json()["error"]

print("server input validation OK: JSON objects and finite numeric queries")

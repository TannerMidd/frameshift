"""Commander ownership is explicit route metadata and always confirmed."""

import os
import sys
import tempfile
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name

from elite.server import commander_scope_policy, create_app  # noqa: E402
from elite.state import AppState  # noqa: E402


REQUIRED = {
    ("POST", "/api/trade-route"),
    ("GET", "/api/commodity-search"),
    ("GET", "/api/mining"),
    ("GET", "/api/mining/hotspots"),
    ("GET", "/api/exobio-route"),
    ("GET", "/api/system-stations"),
    ("GET", "/api/engineering"),
    ("POST", "/api/engineering/pin"),
    ("GET", "/api/objectives"),
    ("POST", "/api/objectives"),
    ("PATCH", "/api/objectives/<objective_id>"),
    ("DELETE", "/api/objectives/<objective_id>"),
    ("POST", "/api/objectives/plan"),
    ("GET", "/api/timings"),
    ("GET", "/api/history/summary"),
    ("GET", "/api/history/events"),
    ("GET", "/api/operations"),
    ("POST", "/api/operations"),
    ("PATCH", "/api/operations/<kind>/<record_id>"),
    ("DELETE", "/api/operations/<kind>/<record_id>"),
    ("GET", "/api/operations/export"),
    ("POST", "/api/operations/import"),
    ("POST", "/api/cargo-recovery"),
    ("GET", "/api/specialists"),
    ("POST", "/api/specialists/mining/<action>"),
    ("POST", "/api/specialists/combat/<action>"),
    ("POST", "/api/specialists/carrier/config"),
    ("POST", "/api/specialists/carrier/route"),
    ("POST", "/api/specialists/carrier/inventory"),
    ("POST", "/api/specialists/exobiology/pins"),
    ("DELETE", "/api/specialists/exobiology/pins/<pin_id>"),
    ("GET", "/api/specialists/exobiology/geojson"),
    ("GET", "/api/material-traders"),
    ("GET", "/api/material-bodies"),
    ("GET", "/api/sell-data"),
    ("GET", "/api/interstellar-factors"),
    ("GET", "/api/loadout-export"),
    ("POST", "/api/riches"),
    ("POST", "/api/neutron"),
    ("GET", "/api/station-search"),
    ("GET", "/api/cargo-sell"),
    ("GET", "/api/colonisation-sources"),
    ("GET", "/api/alerts"),
    ("POST", "/api/watch"),
    ("POST", "/api/watch/remove"),
    ("POST", "/api/alerts/clear"),
    ("GET", "/api/analytics"),
}
CONDITIONAL = {("POST", "/api/extensions/test")}


state = AppState()
state.update(commander="Alpha", commander_id="alpha")
app = create_app(state)
app.testing = True
client = app.test_client()

policies = {}
rules = {}
for rule in app.url_map.iter_rules():
    policy = commander_scope_policy(app.view_functions[rule.endpoint])
    for method in rule.methods - {"HEAD", "OPTIONS"}:
        key = (method, rule.rule)
        rules[key] = rule
        if policy:
            policies[key] = policy

assert {key for key, policy in policies.items() if policy == "required"} == REQUIRED
assert {key for key, policy in policies.items() if policy == "conditional"} == CONDITIONAL

# Every required route is fail-closed before its handler can read or mutate
# data. Dynamic segments receive inert values because the policy runs first.
adapter = app.url_map.bind("localhost")
for method, route in sorted(REQUIRED):
    rule = rules[(method, route)]
    values = {name: "contract-test" for name in rule.arguments}
    url = adapter.build(rule.endpoint, values, method=method)
    kwargs = {"json": {}} if method not in {"GET", "HEAD"} else {}
    response = client.open(url, method=method, **kwargs)
    assert response.status_code == 409, (method, route, response.status_code)
    payload = response.get_json()
    assert payload["profile_changed"] is True, (method, route, payload)

stale = client.get(
    "/api/engineering",
    headers={"X-Frameshift-Commander": "beta"},
)
assert stale.status_code == 409
assert stale.get_json()["commander_id"] == "alpha"

state.reset_commander_context()
pending = client.get("/api/engineering")
assert pending.status_code == 409
assert pending.get_json()["profile_pending"] is True

# A supplied extension sample is generic; omitting the sample switches the
# same endpoint to its explicit conditional commander policy.
sample = client.post(
    "/api/extensions/test",
    json={"manifest": {}, "sample_event": {"event": "Test"}},
)
assert sample.status_code != 409, sample.get_json()
state.update(commander="Alpha", commander_id="alpha")
history = client.post("/api/extensions/test", json={"manifest": {}})
assert history.status_code == 409
assert history.get_json()["profile_changed"] is True

print(
    "commander route policy OK: "
    f"{len(REQUIRED)} required routes and {len(CONDITIONAL)} conditional route"
)

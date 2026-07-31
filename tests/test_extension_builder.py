"""In-app extension builder: save/read/delete declarative packs and the
dry-run tester. The builder must never create or clobber process adapters,
and everything it writes goes through the installed-pack validator."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite.extensions import (  # noqa: E402
    ExtensionError, ExtensionManager, dry_run_rules,
)


ROOT = Path(tempfile.mkdtemp(prefix="fs-ext-builder-")) / "extensions"
MANAGER = ExtensionManager(ROOT)

VALID = {
    "id": "big-bounty",
    "name": "Big bounty callout",
    "version": "1",
    "permissions": ["read:journal", "emit:alert"],
    "rules": [{
        "event": "Bounty",
        "when": {"Reward": {"min": 100000}},
        "action": {"type": "alert", "level": "info", "code": "user.big-bounty",
                   "text": "Bounty {Reward} cr — {Target}", "say": "Bounty {Reward} credits"},
    }],
}


def expect_error(callable_, fragment):
    try:
        callable_()
    except ExtensionError as exc:
        assert fragment in str(exc), (fragment, str(exc))
    else:
        raise AssertionError(f"expected ExtensionError containing {fragment!r}")


# --- save: happy path -------------------------------------------------------
snapshot = MANAGER.save_declarative(dict(VALID))
loaded = {item["id"]: item for item in snapshot["loaded"]}
assert "big-bounty" in loaded, snapshot
assert loaded["big-bounty"]["mode"] == "declarative"
assert loaded["big-bounty"]["editable"] is True
assert loaded["big-bounty"]["rules"] == 1
on_disk = json.loads((ROOT / "big-bounty" / "manifest.json").read_text(encoding="utf-8"))
assert on_disk["created_with"] == "frameshift-builder"
assert on_disk["api_version"] == 1

# Update in place: same id, new rule set.
updated = dict(VALID)
updated["rules"] = VALID["rules"] + [{
    "event": "MissionCompleted",
    "action": {"type": "alert", "text": "{Faction} paid {Reward} cr"},
}]
snapshot = MANAGER.save_declarative(updated)
loaded = {item["id"]: item for item in snapshot["loaded"]}
assert loaded["big-bounty"]["rules"] == 2

# --- save: rejections leave no residue --------------------------------------
expect_error(lambda: MANAGER.save_declarative({**VALID, "id": "Bad Id!"}), "id must be")
expect_error(lambda: MANAGER.save_declarative({**VALID, "id": "runner",
    "command": ["run.exe"]}), "declarative")
bad_rules = {**VALID, "id": "broken", "rules": [{"event": "Bounty",
    "action": {"type": "alert"}}]}
expect_error(lambda: MANAGER.save_declarative(bad_rules), "needs text")
assert not (ROOT / "broken").exists(), "failed save must not leave a pack behind"
assert not list(ROOT.glob(".staging-*")), "staging directories must be cleaned up"

# --- read manifest -----------------------------------------------------------
raw = MANAGER.read_manifest("big-bounty")
assert raw["name"] == "Big bounty callout" and len(raw["rules"]) == 2
expect_error(lambda: MANAGER.read_manifest("missing"), "not installed")
expect_error(lambda: MANAGER.read_manifest("../evil"), "id must be")

# --- process packs are untouchable ------------------------------------------
process_dir = ROOT / "proc-pack"
process_dir.mkdir(parents=True)
(process_dir / "run.py").write_text("print('[]')\n", encoding="utf-8")
(process_dir / "manifest.json").write_text(json.dumps({
    "id": "proc-pack", "api_version": 1, "permissions": ["read:journal", "emit:alert"],
    "command": ["run.py"], "rules": [],
}), encoding="utf-8")
MANAGER.reload()
expect_error(lambda: MANAGER.save_declarative({**VALID, "id": "proc-pack"}),
             "process-adapter")
expect_error(lambda: MANAGER.delete_pack("proc-pack"), "manually")
assert (process_dir / "run.py").exists()

# --- delete ------------------------------------------------------------------
snapshot = MANAGER.delete_pack("big-bounty")
assert "big-bounty" not in {item["id"] for item in snapshot["loaded"]}
assert not (ROOT / "big-bounty").exists()
expect_error(lambda: MANAGER.delete_pack("big-bounty"), "not installed")

# --- dry run -----------------------------------------------------------------
events = [
    {"timestamp": "2026-07-13T20:00:00Z", "event_type": "Bounty", "system": "Sol",
     "event": {"event": "Bounty", "Reward": 250000, "Target": "pirate"}},
    {"timestamp": "2026-07-13T20:01:00Z", "event_type": "Bounty", "system": "Sol",
     "event": {"event": "Bounty", "Reward": 500, "Target": "sidewinder"}},
    {"timestamp": "2026-07-13T20:02:00Z", "event_type": "FSDJump", "system": "Barnard's Star",
     "event": {"event": "FSDJump", "StarSystem": "Barnard's Star", "JumpDist": 5.9}},
]
result = dry_run_rules(VALID, events)
assert result["scanned"] == 3 and len(result["matches"]) == 1, result
match = result["matches"][0]
assert match["event_type"] == "Bounty"
assert match["action"]["text"] == "Bounty 250000 cr — pirate"
assert match["action"]["say"] == "Bounty 250000 credits"
assert result["truncated"] is False

# Objective actions render too, and validation errors surface.
objective = {"id": "obj", "permissions": ["read:journal", "emit:objective"],
             "rules": [{"event": "FSDJump",
                        "action": {"type": "objective", "title": "Map {StarSystem}"}}]}
result = dry_run_rules(objective, events)
assert result["matches"][0]["action"]["title"] == "Map Barnard's Star"
expect_error(lambda: dry_run_rules({"rules": [{"event": "x", "action": {"type": "nope"}}]},
                                   events), "unsupported action")

# Truncation cap holds.
flood = [events[0]] * 200
result = dry_run_rules(VALID, flood, limit=10)
assert result["truncated"] is True and len(result["matches"]) == 10

# --- HTTP surface -------------------------------------------------------------
from elite.server import create_app  # noqa: E402  (import late: heavy)
from elite.state import AppState  # noqa: E402
import elite.eventledger as eventledger_module  # noqa: E402
import elite.extensions as ext_module  # noqa: E402

ext_module.EXTENSIONS = ExtensionManager(ROOT)
state = AppState()
app = create_app(state)
app.config["TESTING"] = True
client = app.test_client()

resp = client.post("/api/extensions/save", json={"manifest": dict(VALID)})
assert resp.status_code == 200, resp.get_json()
assert "big-bounty" in {item["id"] for item in resp.get_json()["loaded"]}

resp = client.get("/api/extensions/big-bounty/manifest")
assert resp.status_code == 200 and resp.get_json()["manifest"]["id"] == "big-bounty"

resp = client.post("/api/extensions/test", json={
    "manifest": dict(VALID),
    "sample_event": {"event": "Bounty", "Reward": 999999, "Target": "anaconda"},
})
assert resp.status_code == 200, resp.get_json()
body = resp.get_json()
assert body["matches"] and body["matches"][0]["action"]["text"] == "Bounty 999999 cr — anaconda"

# Explicit sample events are generic and remain usable without a loaded
# commander. History mode, however, must use only the request-entry identity
# and must never trust a stale header as the ledger discriminator.
ledger_commanders = []
original_event_ledger = eventledger_module.EventLedger


class RecordingEventLedger:
    def __init__(self, commander_id):
        ledger_commanders.append(commander_id)

    def query(self, **_kwargs):
        return []


eventledger_module.EventLedger = RecordingEventLedger
try:
    pending = client.post("/api/extensions/test", json={"manifest": dict(VALID)})
    assert pending.status_code == 409, pending.get_json()
    assert pending.get_json()["profile_pending"] is True
    assert ledger_commanders == []

    state.update(commander="Alpha", commander_id="alpha-id")
    unconfirmed = client.post(
        "/api/extensions/test", json={"manifest": dict(VALID)})
    assert unconfirmed.status_code == 409, unconfirmed.get_json()
    assert unconfirmed.get_json()["profile_changed"] is True

    stale = client.post(
        "/api/extensions/test",
        headers={"X-Frameshift-Commander": "beta-id"},
        json={"manifest": dict(VALID)},
    )
    assert stale.status_code == 409, stale.get_json()
    assert stale.get_json()["profile_changed"] is True
    assert stale.get_json()["commander_id"] == "alpha-id"
    assert ledger_commanders == []

    history = client.post(
        "/api/extensions/test",
        headers={"X-Frameshift-Commander": "alpha-id"},
        json={"manifest": dict(VALID)},
    )
    assert history.status_code == 200, history.get_json()
    assert history.get_json()["scanned"] == 0
    assert ledger_commanders == ["alpha-id"]

    generic = client.post(
        "/api/extensions/test",
        headers={"X-Frameshift-Commander": "stale-id"},
        json={
            "manifest": dict(VALID),
            "sample_event": {
                "event": "Bounty", "Reward": 250000, "Target": "python"
            },
        },
    )
    assert generic.status_code == 200, generic.get_json()
    assert generic.get_json()["matches"]
    assert ledger_commanders == ["alpha-id"]
finally:
    eventledger_module.EventLedger = original_event_ledger

resp = client.post("/api/extensions/save", json={"manifest": {"id": "x"}})
assert resp.status_code == 400

resp = client.delete("/api/extensions/big-bounty")
assert resp.status_code == 200
assert "big-bounty" not in {item["id"] for item in resp.get_json()["loaded"]}
resp = client.delete("/api/extensions/big-bounty")
assert resp.status_code == 400

# --- shipped "Raxxla Scanner" template ---------------------------------------
# Byte-for-byte what the builder emits for the shipped template defined in the
# frontend extension-builder catalog module. The JS unit tests prove the UI
# compiles to this manifest; this proves the host accepts it and that every rule
# actually matches a real journal event. A template whose conditions never fire
# is worse than no template, because it looks like it is working.
RAXXLA = {
    "id": "raxxla-scanner",
    "api_version": 1,
    "name": "Raxxla Scanner",
    "version": "1",
    "permissions": ["read:journal", "emit:alert"],
    "rules": [
        {
            "event": "FSDJump",
            "action": {
                "type": "alert",
                "text": "RAXXLA — you have jumped into {StarSystem}. Record everything.",
                "level": "critical",
                "code": "user.raxxla-scanner",
                "say": "RAXXLA — you have jumped into {StarSystem}. Record everything.",
            },
            "when": {"StarSystem": {"in": ["Raxxla", "RAXXLA"]}},
        },
        {
            "event": "Location",
            "action": {
                "type": "alert",
                "text": "RAXXLA — you are in {StarSystem}, at {Body}. Record everything.",
                "level": "critical",
                "code": "user.raxxla-scanner",
                "say": "RAXXLA — you are in {StarSystem}, at {Body}. Record everything.",
            },
            "when": {"StarSystem": {"in": ["Raxxla", "RAXXLA"]}},
        },
        {
            "event": "Scan",
            "action": {
                "type": "alert",
                "text": "Undiscovered black hole {BodyName} — nobody has scanned it before.",
                "level": "warn",
                "code": "user.raxxla-scanner",
                "say": "Undiscovered black hole {BodyName}.",
            },
            "when": {"StarType": {"eq": "H"}, "WasDiscovered": {"eq": False}},
        },
        {
            "event": "CodexEntry",
            "action": {
                "type": "alert",
                "text": "Codex anomaly in {System}: {Name_Localised}.",
                "level": "warn",
                "code": "user.raxxla-scanner",
            },
            "when": {
                "SubCategory": {"eq": "$Codex_SubCategory_Geology_and_Anomalies;"},
                "IsNewEntry": {"exists": True},
            },
        },
        {
            "event": "Scan",
            "action": {
                "type": "alert",
                "text": "First-discovery Earth-like: {BodyName}.",
                "level": "info",
                "code": "user.raxxla-scanner",
            },
            "when": {"PlanetClass": {"eq": "Earthlike body"}, "WasDiscovered": {"eq": False}},
        },
    ],
}

snapshot = MANAGER.save_declarative(dict(RAXXLA))
raxxla = {item["id"]: item for item in snapshot["loaded"]}["raxxla-scanner"]
assert raxxla["mode"] == "declarative", raxxla
assert raxxla["editable"] is True
assert raxxla["rules"] == 5, raxxla
assert raxxla["permissions"] == ["emit:alert", "read:journal"], raxxla

# Each rule against the journal shape that is supposed to trigger it. Field
# names and enum values below were taken from real recorded events, not guessed.
TRIPWIRE_EVENTS = [
    ({"event": "FSDJump", "StarSystem": "Raxxla", "JumpDist": 12.3}, 1, "critical"),
    ({"event": "FSDJump", "StarSystem": "RAXXLA"}, 1, "critical"),
    ({"event": "Location", "StarSystem": "Raxxla", "Body": "Raxxla A 1"}, 2, "critical"),
    (
        {"event": "Scan", "BodyName": "Praea Euq QI-T d3-9 A",
         "StarType": "H", "WasDiscovered": False},
        3, "warn",
    ),
    (
        {"event": "CodexEntry", "System": "Byeia Eurk NN-B d13-40",
         "Name_Localised": "Lava Spout", "IsNewEntry": True,
         "SubCategory": "$Codex_SubCategory_Geology_and_Anomalies;"},
        4, "warn",
    ),
    (
        {"event": "Scan", "BodyName": "Wregoe QC-D d12-0 3",
         "PlanetClass": "Earthlike body", "WasDiscovered": False},
        5, "info",
    ),
]
for event, expected_rule, expected_level in TRIPWIRE_EVENTS:
    result = dry_run_rules(RAXXLA, [{"event": event, "timestamp": "2026-07-31T00:00:00Z"}])
    assert len(result["matches"]) == 1, (event, result)
    match = result["matches"][0]
    assert match["rule"] == expected_rule, (event, match)
    assert match["action"]["level"] == expected_level, (event, match)
    # Placeholders must resolve; a leftover "{" means a renamed journal field.
    assert "{" not in match["action"]["text"], match

# Near-misses must stay silent: a different system, a star that is not a black
# hole, an already-discovered body, and a codex entry outside the anomaly
# subcategory are all ordinary events a commander sees constantly.
QUIET_EVENTS = [
    {"event": "FSDJump", "StarSystem": "Raxxlar"},
    {"event": "FSDJump", "StarSystem": "Sol"},
    {"event": "Location", "StarSystem": "raxxla"},
    {"event": "Scan", "BodyName": "X", "StarType": "H", "WasDiscovered": True},
    {"event": "Scan", "BodyName": "X", "StarType": "M", "WasDiscovered": False},
    {"event": "Scan", "BodyName": "X", "PlanetClass": "Earthlike body", "WasDiscovered": True},
    {"event": "CodexEntry", "System": "S", "IsNewEntry": True,
     "SubCategory": "$Codex_SubCategory_Stars;"},
    {"event": "CodexEntry", "System": "S",
     "SubCategory": "$Codex_SubCategory_Geology_and_Anomalies;"},
]
quiet = dry_run_rules(
    RAXXLA, [{"event": event, "timestamp": "2026-07-31T00:00:00Z"} for event in QUIET_EVENTS])
assert quiet["scanned"] == len(QUIET_EVENTS), quiet
assert quiet["matches"] == [], quiet

resp = client.delete("/api/extensions/raxxla-scanner")
assert resp.status_code == 200

shutil.rmtree(ROOT.parent, ignore_errors=True)
print("extension builder OK: save/validate, edit round-trip, process-pack guardrails, dry run, "
      "Raxxla template fires and stays quiet, HTTP surface")

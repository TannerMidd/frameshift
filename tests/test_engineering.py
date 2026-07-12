"""Engineering planner: requirement/deficit math, trade conversions, and the
ready-to-engineer callout when a pickup completes a pinned blueprint."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_tmp = tempfile.TemporaryDirectory()
os.environ["ET_DATA_DIR"] = _tmp.name  # isolate settings.json/db

from elite import blueprints, marketdb, wishlist  # noqa: E402
from elite.journal import JournalWatcher  # noqa: E402
from elite.state import AppState  # noqa: E402

# ---------- requirements: deterministic post-rebalance costs, G1->target ----------

req = blueprints.requirements("FSD Increased Range", 5)
# ADWE: G1 (1 application) + G2 (2) = 3; Chemical Processors: G2 (2) + G3 (3) = 5
assert req["Atypical Disrupted Wake Echoes"] == 3, req
assert req["Chemical Processors"] == 5, req
assert req["Datamined Wake Exceptions"] == 5, req  # G5 only, 5 rolls
req3 = blueprints.requirements("FSD Increased Range", 3)
assert "Datamined Wake Exceptions" not in req3 and req3["Strange Wake Solutions"] == 3, req3

# ---------- trade conversion math (same family, 6:1 up / 1:3 down) ----------

assert blueprints.convertible(2, 5, 3) == 18   # 2x G5 -> 3^2 x2 = 18 G3
assert blueprints.convertible(35, 1, 3) == 0   # 35 G1 -> 36 needed for one G3
assert blueprints.convertible(36, 1, 3) == 1
assert blueprints._cost_for(5, 4, 3) == 2      # need 5 G3 from G4: ceil(5/3) = 2
assert blueprints._cost_for(2, 2, 4) == 72     # 2 G4 from G2: 2 x 36

# ---------- plan: deficits + best same-family trade suggestion ----------

inv = {
    "disruptedwakeechoes": 10,       # legacy journal symbol alias; plenty (need 3)
    "chemicalprocessors": 2,         # short 3 (need 5)
    "chemicalmanipulators": 30,      # G4 chemical surplus (need 5 for G5)
    "wakesolutions": 3,              # exact (need 3)
    "hyperspacetrajectories": 0,     # short 4
    "dataminedwake": 5,              # legacy journal symbol alias; exact
    "phasealloys": 3, "phosphorus": 3, "manganese": 4, "arsenic": 5,
}
p = blueprints.plan("FSD Increased Range", 5, inv)
assert not p["craftable"]
rows = {r["name"]: r for r in p["materials"]}
assert rows["Chemical Processors"]["deficit"] == 3, rows["Chemical Processors"]
# every material row carries a where-to-find-it hint for new players
assert all(r["source"] for r in p["materials"]), [r["name"] for r in p["materials"] if not r["source"]]
assert "salvage" in rows["Chemical Processors"]["source"], rows["Chemical Processors"]["source"]
assert "Surface prospecting" in rows["Manganese"]["source"], rows["Manganese"]["source"]
# Chemical Manipulators are G4 chemical: 25 spare after their own need of 5;
# trading down covers the 3-deficit easily.
trade = rows["Chemical Processors"]["trade"]
assert trade and trade["from"] == "Chemical Manipulators" and trade["direction"] == "down", trade
assert trade["covers"] == 3 and trade["spend"] == 1, trade  # 1x G4 -> 9x G2, need 3
# EHT deficit: only wake-scan family sources; DWE has 0 spare (5 needed, 5 held),
# ADWE spare is 6 -> not enough to trade up (36 G1 per G4 -> covers 0). No trade.
assert rows["Eccentric Hyperspace Trajectories"]["trade"] is None, rows["Eccentric Hyperspace Trajectories"]

# Full inventory -> craftable.
inv_full = dict(inv, chemicalprocessors=5, hyperspacetrajectories=4, chemicaldistillery=4)
assert blueprints.plan("FSD Increased Range", 5, inv_full)["craftable"]

print("blueprint math OK: deterministic requirements, conversions, deficits, trade suggestions")

# ---------- journal: ready-to-engineer one-shot callout ----------

with tempfile.TemporaryDirectory() as td:
    state = AppState()
    w = JournalWatcher(state, journal_dir=td)
    w._activate_commander("Engineering Test")
    pins, _ = blueprints.normalize_wishlist([
        {"name": "FSD Increased Range", "grade": 5}
    ])
    wishlist.save(marketdb.commander_profile_id("Engineering Test"), pins)
    w._live = True
    mats = {"Raw": {}, "Manufactured": {}, "Encoded": {}}
    for sym, count in inv_full.items():
        material = __import__("elite.engineering_catalog", fromlist=["material"]).material(sym)
        info = blueprints.MATERIALS[material["name"]]
        cat = {"raw": "Raw", "manufactured": "Manufactured", "encoded": "Encoded"}[info[1]]
        mats[cat][sym] = {"symbol": sym, "name": sym, "count": count}
    mats["Manufactured"]["chemicalprocessors"]["count"] = 4  # one short of ready
    state.update(materials=mats)

    w.handle_event({"timestamp": "t", "event": "MaterialCollected",
                    "Category": "Manufactured", "Name": "chemicalprocessors", "Count": 1})
    alerts = [a for a in state.snapshot()["alerts"] if a["code"] == "blueprint"]
    assert len(alerts) == 1 and "Increased FSD Range" in alerts[0]["say"], alerts

    # Further pickups while already craftable must not repeat the callout.
    w.handle_event({"timestamp": "t", "event": "MaterialCollected",
                    "Category": "Manufactured", "Name": "chemicalprocessors", "Count": 1})
    assert len([a for a in state.snapshot()["alerts"] if a["code"] == "blueprint"]) == 1

print("journal callout OK: fires once when the pinned climb completes")
print("ALL ENGINEERING TESTS PASSED")

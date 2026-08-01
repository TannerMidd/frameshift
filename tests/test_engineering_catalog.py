"""Full offline engineering catalog, planning, inventory and migration."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elite import blueprints
from elite import engineering_catalog as catalog


data = catalog.load()
assert data["stats"] == {"groups": 505, "recipes": 1172, "materials": 369}
payload = catalog.catalog_payload()
assert payload["stats"]["groups"] == 505  # auditable upstream groups
assert payload["stats"]["planner_items"] == 504  # one upstream spelling alias merged
assert sum(payload["stats"]["categories"].values()) == 504
assert payload["stats"]["categories"]["ship-engineering"] >= 150
assert payload["stats"]["categories"]["synthesis"] >= 60
assert payload["stats"]["categories"]["odyssey-modification"] >= 30

# The source rarity labels Common/Standard must become the actual G2/G3
# material-trader grades.  These pairs catch a historically easy swap.
assert catalog.material("Chemical Processors")["grade"] == 2
assert catalog.material("Chemical Distillery")["grade"] == 3
assert catalog.material("Strange Wake Solutions")["grade"] == 3
assert catalog.material("Anomalous FSD Telemetry")["grade"] == 2
assert catalog.material("Chromium")["grade"] == 2
assert catalog.material("Molybdenum")["grade"] == 3

# Classification normalises graded synthesis and separates Odyssey mods from
# ship experimentals.
assert catalog.group("fsd-injection--25-jump-range")["kind"] == "synthesis"
assert catalog.group("guardian--guardian-fsd-booster")["kind"] == "unlock"
assert catalog.group("suit--improved-jump-assist")["kind"] == "odyssey-modification"
oppressor = catalog.group("weapon--manticore-oppressor")
assert list(oppressor["grades"]) == [2, 3, 4, 5]
assert len(oppressor["recipes"]) == 4
assert not any(row["id"] == "weapon--manticore-opressor" for row in catalog.search("oppressor"))
dirty = next(row for row in catalog.catalog_payload()["groups"]
             if row["id"] == "thrusters--dirty-drive-tuning")
access = {row["name"]: row["max_grade"] for row in dirty["engineer_access"]}
assert access["Felicity Farseer"] == 3 and access["Professor Palin"] == 5
assert any(row["id"] == "frame-shift-drive--increased-fsd-range"
           for row in catalog.search("arsenic wake", kinds=["ship-engineering"]))

# Maximum engineer access after the engineering rebalance is deterministic:
# G1..G5 cost 1..5 applications.  Current G3 -> target G5 does not rebuy G1-3.
full = blueprints.requirements("FSD Increased Range", 5)
assert full["Atypical Disrupted Wake Echoes"] == 3
assert full["Datamined Wake Exceptions"] == 5
late = blueprints.requirements("FSD Increased Range", 5, current_grade=3)
assert "Atypical Disrupted Wake Echoes" not in late
assert late["Eccentric Hyperspace Trajectories"] == 4
assert late["Datamined Wake Exceptions"] == 5

# Synthesis, an experimental and a one-time unlock use exact recipes, not ship
# engineering roll multipliers.  Quantity intentionally scales multi-item work.
synth_group = catalog.group("fsd-injection--25-jump-range")
synth_one = {i["name"]: i["quantity"] for i in synth_group["recipes"][0]["ingredients"]}
synth_three = blueprints.requirements(synth_group["id"], 1, quantity=3)
assert synth_three == {name: count * 3 for name, count in synth_one.items()}
experimental = catalog.group("armour--angled-plating")
experimental_one = {i["name"]: i["quantity"] for i in experimental["recipes"][0]["ingredients"]}
assert blueprints.requirements(experimental["id"], 0, quantity=2) == {
    name: count * 2 for name, count in experimental_one.items()
}
unlock = blueprints.requirements("guardian--guardian-fsd-booster", 0)
assert unlock["HN Shock Mount"] == 8

# Odyssey grade steps and modifications are covered by the same planner.
artemis = blueprints.requirements("suit--artemis", 4, current_grade=2, quantity=2)
artemis_group = catalog.group("suit--artemis")
expected_schematics = sum(
    next(i["quantity"] for i in recipe["ingredients"] if i["name"] == "Suit Schematic")
    for recipe in artemis_group["recipes"] if 2 < recipe["grade"] <= 4
) * 2
assert artemis["Suit Schematic"] == expected_schematics
od_mod = blueprints.requirements("suit--improved-jump-assist", 0)
assert od_mod and all(count > 0 for count in od_mod.values())

# Internal symbols, display names, Odyssey locker and cargo are one inventory.
inventory = blueprints.inventory_from_snapshot({
    "materials": {"encoded": [{"symbol": "disruptedwakeechoes", "count": 7}]},
    "ship_locker": {"items": [{"symbol": "suitschematic", "name": "Suit Schematic", "count": 4}]},
    "cargo_inventory": [{"symbol": "hnshockmount", "name": "HN Shock Mount", "count": 8}],
})
assert inventory[catalog.canonical_material_symbol("Atypical Disrupted Wake Echoes")] == 7
assert inventory[catalog.canonical_material_symbol("Suit Schematic")] == 4
assert inventory[catalog.canonical_material_symbol("HN Shock Mount")] == 8

# Both old pin names migrate to stable IDs with no user input.
legacy = [{"name": "FSD Increased Range", "grade": 5},
          {"name": "Thrusters Dirty Tuning", "grade": 4}]
migrated, changed = blueprints.normalize_wishlist(legacy)
assert changed and [row["id"] for row in migrated] == [
    "frame-shift-drive--increased-fsd-range", "thrusters--dirty-drive-tuning"
]
assert all(row["quantity"] == 1 and row["current_grade"] == 0 for row in migrated)

# A shared wishlist reserves inventory once.  A single surplus same-column
# material may be suggested at a trader; Odyssey and commodity rows never are.
inv = {
    "disruptedwakeechoes": 3, "chemicalprocessors": 2,
    "chemicalmanipulators": 6, "wakesolutions": 3,
    "hyperspacetrajectories": 4, "dataminedwake": 5,
    "phosphorus": 3, "chemicaldistillery": 4, "manganese": 4, "arsenic": 5,
}
single = blueprints.plan("FSD Increased Range", 5, inv)
rows = {row["name"]: row for row in single["materials"]}
trade = rows["Chemical Processors"]["trade"]
assert trade and trade["from"] == "Chemical Manipulators" and trade["spend"] == 1
wishlist = blueprints.plan_wishlist(migrated, inv)
assert len(wishlist["items"]) == 2 and wishlist["required_units"] > 0
assert all(row["source"] for row in wishlist["materials"])
assert all(row["trade"] is None for row in blueprints.plan(
    "guardian--guardian-fsd-booster", 0, {"metaalloys": 100}
)["materials"] if row["kind"] == "commodity")

# The material reference the browser resolves "where do I get Phosphorus" from,
# entirely offline.  108 of the 139 raw/manufactured/encoded materials sit in a
# real trader column; Guardian and Thargoid families are collected, never traded.
reference = blueprints.material_reference()
assert len(reference["materials"]) == 139
assert sum(1 for row in reference["materials"] if row["tradeable"]) == 108
assert len(reference["material_columns"]) == 30
assert reference["trade_ratios"] == {"up": 6, "down": 3}
assert all(row["source"] for row in reference["materials"])

by_name = {row["name"]: row for row in reference["materials"]}
phosphorus = by_name["Phosphorus"]
assert phosphorus["kind"] == "raw" and phosphorus["family"] == "Category2"
assert phosphorus["grade"] == 1 and phosphorus["tradeable"]
column2 = sorted(
    (row for row in reference["materials"]
     if row["kind"] == "raw" and row["family"] == "Category2"),
    key=lambda row: row["grade"],
)
assert [row["name"] for row in column2] == [
    "Phosphorus", "Chromium", "Molybdenum", "Technetium"
]

# Column labels are what a commander reads, never the internal family id.
labels = {key: value["label"] for key, value in reference["material_columns"].items()}
assert labels[blueprints.column_key("raw", "Category2")] == "Raw category 2"
assert labels[blueprints.column_key("manufactured", "MechanicalComponents")] == \
    "Mechanical components"
assert labels[blueprints.column_key("encoded", "WakeScans")] == "Wake scans"
assert not any("Category2" in label or "WakeScans" in label for label in labels.values())

# GuardianRuinsActive, ThargoidShip and ThargoidSite each exist under two kinds
# and must stay separate columns rather than silently merging.
for family in ("GuardianRuinsActive", "ThargoidShip", "ThargoidSite"):
    manufactured = reference["material_columns"][blueprints.column_key("manufactured", family)]
    encoded = reference["material_columns"][blueprints.column_key("encoded", family)]
    assert not manufactured["tradeable"] and not encoded["tradeable"]
assert not by_name["Sensor Fragment"]["tradeable"]
assert not by_name["Pattern Beta Obelisk Data"]["tradeable"]

# The shipped ratios must stay in step with the planner's own trade math.
assert blueprints.convertible(1, 2, 1) == reference["trade_ratios"]["down"]
assert blueprints._cost_for(1, 1, 2) == reference["trade_ratios"]["up"]
assert blueprints.convertible(1, 4, 1) == reference["trade_ratios"]["down"] ** 3
assert blueprints._cost_for(1, 1, 4) == reference["trade_ratios"]["up"] ** 3

print("ALL COMPLETE ENGINEERING CATALOG TESTS PASSED")
